"""Blender 3.3/5.2 regression for strict planar checker-frame repair."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy
from mathutils import Vector


ADDON_PARENT = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDON_PARENT))

from visibility_uv_optimizer import uv_group_layout  # noqa: E402
from visibility_uv_optimizer import uv_optimize  # noqa: E402


SHEARED_UV = (
    (0.20, 0.10),
    (0.50, 0.10),
    (0.65, 0.70),
    (0.35, 0.70),
)


def _object(name, vertices, faces, uv_points=SHEARED_UV):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    layer = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for position, loop_index in enumerate(polygon.loop_indices):
            layer.data[loop_index].uv = Vector(uv_points[position])
    mesh.update()
    return obj, mesh, layer


def _remove(obj, mesh):
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _options(**overrides):
    values = dict(
        margin=0.01,
        align_geometry_direction=True,
        align_geometry_frame=True,
        use_geometry_frame_rotation=True,
        direction_space="OBJECT",
        direction_axis="Z",
        direction_auto_priority="YZX",
        restore_persisted_direction_contract=False,
        strict_source_overlap=False,
        small_island_scale_boost=1.0,
        preserve_source_layout=False,
        allow_group_quarter_turn=False,
    )
    values.update(overrides)
    return uv_group_layout.GroupLayoutOptions(**values).validated()


def _settings():
    return SimpleNamespace(max_p95_stretch=1.35, max_stretch=2.0)


def _uv_center(layer):
    return sum(
        (item.uv.copy() for item in layer.data), Vector((0.0, 0.0))
    ) / len(layer.data)


def _signed_area(obj, mesh, layer, face_indices=(0,)):
    triangles = uv_optimize._mesh_chart_triangles(
        obj, mesh, layer, face_indices, "OBJECT")
    return uv_optimize._triangle_uv_signed_area(triangles)


def _test_sheared_planar_chart_is_reparameterized():
    obj, mesh, layer = _object(
        "VUV_RefinePlanarShear",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
        ((0, 1, 2, 3),),
    )
    try:
        options = _options()
        source = uv_group_layout.analyze_active_uv(obj, options)
        assert len(source.islands) == 1
        assert not uv_group_layout._geometry_frame_is_reliable(
            source.islands[0], options)
        center_before = _uv_center(layer)
        area_before = _signed_area(obj, mesh, layer)
        faces_before = tuple(source.islands[0].face_indices)

        summary, contract = uv_optimize._repair_refine_planar_frame_failures(
            obj, _settings(), options)

        assert summary["frame_failures"] == 1, summary
        assert summary["strict_planar_failures"] == 1, summary
        assert summary["attempted"] == 1, summary
        assert summary["accepted"] == 1, summary
        assert summary["rejected"] == 0, summary
        assert contract[faces_before] == ("Z", "OBJECT"), contract
        repaired = uv_group_layout.analyze_active_uv(obj, options)
        repaired = uv_group_layout.rebind_geometry_axis_contract(
            obj, repaired, contract, options)
        assert tuple(repaired.islands[0].face_indices) == faces_before
        assert uv_group_layout._geometry_frame_is_reliable(
            repaired.islands[0], options), repaired.islands[0].to_dict()
        assert (_uv_center(layer) - center_before).length <= 1.0e-6
        area_after = _signed_area(obj, mesh, layer)
        assert area_before > 0.0 and area_after > 0.0
        assert abs(area_after / area_before - 1.0) <= 1.0e-4
        triangles = uv_optimize._mesh_chart_triangles(
            obj, mesh, layer, faces_before, "OBJECT")
        _p95, _maximum, flipped = uv_optimize._measure_distortion(triangles)
        assert not flipped
        assert not uv_optimize._has_overlap(
            triangles, ignore_same_face=False)
    finally:
        _remove(obj, mesh)


def _test_warped_ngon_is_not_reparameterized():
    obj, mesh, layer = _object(
        "VUV_RefineWarpedNgon",
        (
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.05, 2.0),
            (0.0, 0.0, 2.0),
        ),
        ((0, 1, 2, 3),),
    )
    try:
        options = _options()
        before = tuple(item.uv.copy() for item in layer.data)
        summary, contract = uv_optimize._repair_refine_planar_frame_failures(
            obj, _settings(), options)
        after = tuple(item.uv.copy() for item in layer.data)

        assert summary["frame_failures"] == 1, summary
        assert summary["strict_planar_failures"] == 0, summary
        assert summary["attempted"] == 0, summary
        assert summary["accepted"] == 0, summary
        assert summary["rejected"] == 0, summary
        assert summary["reason"] == "no_strict_planar_frame_failures", summary
        assert contract[(0,)] == ("Z", "OBJECT"), contract
        assert all(
            (left - right).length == 0.0
            for left, right in zip(before, after)
        )
    finally:
        _remove(obj, mesh)


def _test_adaptive_layout_honors_frozen_auto_axis():
    # The YZ plane exposes both +Y and +Z tangents. AUTO/YZX naturally picks
    # Y, but a frozen source contract choosing Z must remain authoritative.
    obj, mesh, _layer = _object(
        "VUV_RefineFrozenAxis",
        (
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 1.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
        ((0, 1, 2, 3),),
        uv_points=(
            (0.10, 0.10),
            (0.70, 0.10),
            (0.70, 0.90),
            (0.10, 0.90),
        ),
    )
    try:
        options = _options(
            direction_axis="AUTO",
            align_geometry_frame=False,
            use_geometry_frame_rotation=False,
        )
        automatic = uv_group_layout.analyze_active_uv(obj, options)
        assert automatic.islands[0].geometry_axis_name == "Y", (
            automatic.islands[0].to_dict())
        frozen = {(0,): ("Z", "OBJECT")}
        result = uv_group_layout.layout_active_uv_adaptive(
            obj, options, direction_contract=frozen)

        assert result.analysis.islands[0].geometry_axis_name == "Z", (
            result.analysis.islands[0].to_dict())
        assert result.after_audit["valid"], result.after_audit
        quality = uv_group_layout.evaluate_layout_quality(
            obj,
            analysis=result.analysis,
            face_to_island=result.analysis.face_to_island,
            audit=result.after_audit,
            options=options,
        )
        directed = quality["directed_geometry"]
        assert directed["records"]["0"]["axis"] == "Z", directed
        assert directed["misaligned_islands"] == 0, directed
    finally:
        _remove(obj, mesh)


_test_sheared_planar_chart_is_reparameterized()
_test_warped_ngon_is_not_reparameterized()
_test_adaptive_layout_honors_frozen_auto_axis()
print("VUV_REFINE_PLANAR_FRAME_REPAIR_OK")
