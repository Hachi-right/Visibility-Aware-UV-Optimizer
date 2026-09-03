"""Regression tests for the complete signed UV tangent-frame contract."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "visibility_uv_optimizer"
    / "uv_group_layout.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "vuv_complete_frame_regression", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VUV = _load_module()


def _quad_object(name, uv_points):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    layer = mesh.uv_layers.new(name="UVMap")
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, uv_points):
        layer.data[loop_index].uv = uv
    mesh.update()
    return obj, mesh, layer


def _remove(obj, mesh):
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _test_positive_and_180_keep_positive_parity():
    upright = ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0))
    inverted = tuple((1.0 - u, 2.0 - v) for u, v in upright)
    objects = [_quad_object("VUV_Frame_Upright", upright), _quad_object("VUV_Frame_180", inverted)]
    try:
        records = [
            VUV._geometry_frame_record(
                obj,
                mesh,
                layer,
                (0,),
                space="OBJECT",
                axis_name="Z",
                min_projection=1.0e-4,
            )
            for obj, mesh, layer in objects
        ]
        assert all(record["parity"] == 1 for record in records), records
        assert all(record["confidence"] >= 0.75 for record in records), records
        assert all(abs(float(record["residual"])) < 1.0e-6 for record in records), records
        assert abs(abs(float(records[1]["rotation"])) - math.pi) < 1.0e-6, records
    finally:
        for obj, mesh, _layer in objects:
            _remove(obj, mesh)


def _test_mirror_is_reported_without_reflection():
    # Reversing only V changes the signed frame parity.  The helper must
    # report the issue; no caller may repair it by silently mirroring UVs.
    mirrored = ((0.0, 0.0), (1.0, 0.0), (1.0, -2.0), (0.0, -2.0))
    obj, mesh, layer = _quad_object("VUV_Frame_Mirrored", mirrored)
    try:
        before = tuple(layer.data[index].uv.copy() for index in range(len(layer.data)))
        record = VUV._geometry_frame_record(
            obj,
            mesh,
            layer,
            (0,),
            space="OBJECT",
            axis_name="Z",
            min_projection=1.0e-4,
        )
        after = tuple(layer.data[index].uv.copy() for index in range(len(layer.data)))
        assert record["parity"] == -1, record
        assert record["reason"] == "negative_parity_no_reflection", record
        assert all((left - right).length == 0.0 for left, right in zip(before, after))
    finally:
        _remove(obj, mesh)


def _test_frame_can_be_opted_out_without_changing_axis_contract():
    upright = ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0))
    obj, mesh, _layer = _quad_object("VUV_Frame_OptOut", upright)
    try:
        options = VUV.GroupLayoutOptions(
            align_geometry_direction=True,
            align_geometry_frame=False,
            direction_axis="Z",
        )
        analysis = VUV.analyze_active_uv(obj, options)
        island = analysis.islands[0]
        assert island.geometry_axis_name == "Z", island.to_dict()
        assert island.geometry_frame_downgrade_reason == "disabled", island.to_dict()
    finally:
        _remove(obj, mesh)


def _test_frame_axis_drift_falls_back_to_strict_plus_v():
    """Reject a frame that is internally valid but rotates away from +V."""

    upright = ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0))
    obj, mesh, _layer = _quad_object("VUV_Frame_Axis_Drift", upright)
    try:
        options = VUV.GroupLayoutOptions(
            align_geometry_direction=True,
            align_geometry_frame=True,
            direction_axis="Z",
        )
        analysis = VUV.analyze_active_uv(obj, options)
        island = analysis.islands[0]
        strict_rotation = float(island.geometry_rotation_angle)
        island.geometry_frame_rotation_angle = strict_rotation + math.radians(8.0)
        # The frame's perpendicular residual is untouched, but its signed
        # rotation no longer honors the selected model-axis +V contract.
        assert VUV._geometry_frame_axis_residual(island) > math.radians(3.0)
        assert not VUV._geometry_frame_is_reliable(island, options)
        angles = VUV._orientation_angles(analysis, options)
        assert abs(
            VUV._angle_wrap(float(angles[island.island_id]) - strict_rotation)
        ) < 1.0e-6, angles
    finally:
        _remove(obj, mesh)


def _test_layout_writeback_uses_strict_axis_when_frame_has_small_drift():
    """A frame may pass its loose gate yet must not override the +V axis."""

    upright = ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0))
    obj, mesh, _layer = _quad_object("VUV_Frame_Small_Drift", upright)
    try:
        options = VUV.GroupLayoutOptions(
            align_geometry_direction=True,
            align_geometry_frame=True,
            direction_axis="Z",
        )
        analysis = VUV.analyze_active_uv(obj, options)
        island = analysis.islands[0]
        strict_rotation = float(island.geometry_rotation_angle)
        island.geometry_frame_rotation_angle = strict_rotation + math.radians(2.0)
        assert VUV._geometry_frame_is_reliable(island, options)
        angles = VUV._orientation_angles(analysis, options)
        assert abs(
            VUV._angle_wrap(float(angles[island.island_id]) - strict_rotation)
        ) < 1.0e-6, angles
    finally:
        _remove(obj, mesh)


def _test_positive_shear_is_downgraded_without_hard_failure():
    # A positive-signed shear keeps the selected +Z axis resolvable but makes
    # the perpendicular U direction non-orthogonal after the strict +V
    # rotation.  This is a valid curved/sheared chart diagnostic, not a reason
    # to reject the whole atlas.
    sheared = (
        (0.20, 0.10),
        (0.50, 0.10),
        (0.65, 0.70),
        (0.35, 0.70),
    )
    obj, mesh, layer = _quad_object("VUV_Frame_Shear", sheared)
    try:
        options = VUV.GroupLayoutOptions(
            align_geometry_direction=True,
            align_geometry_frame=True,
            direction_axis="Z",
            margin=0.0,
        )
        analysis = VUV.analyze_active_uv(obj, options)
        island = analysis.islands[0]
        assert island.geometry_frame_residual is not None, island.to_dict()
        assert (
            abs(float(island.geometry_frame_residual))
            > options.direction_residual_tolerance
        ), island.to_dict()
        # The complete frame is intentionally rejected for this chart.  The
        # orientation planner must use the already-valid single-axis +V
        # correction instead of applying the skewed perpendicular frame.
        assert not VUV._geometry_frame_is_reliable(island, options)
        angles = VUV._orientation_angles(analysis, options)
        assert abs(
            VUV._angle_wrap(
                float(angles[island.island_id])
                - float(island.geometry_rotation_angle)
            )
        ) < 1.0e-6, angles
        # Apply the existing strict +V rotation once, as layout_active_uv does.
        delta = float(island.geometry_rotation_angle)
        center = island.uv_centroid
        for loop_index in island.loop_indices:
            layer.data[loop_index].uv = VUV._rotate_point(
                layer.data[loop_index].uv, center, delta
            )
        mesh.update()
        replay = VUV.analyze_active_uv(obj, options)
        metrics = VUV.evaluate_layout_quality(
            obj,
            analysis=replay,
            face_to_island=replay.face_to_island,
            options=options,
        )
        directed = metrics["directed_geometry"]
        assert directed["misaligned_islands"] == 0, directed
        assert directed["frame_misaligned_islands"] == 1, directed
        assert directed["frame_fallback_islands"] == 1, directed
        assert directed["frame_fallback_ids"] == [0], directed
        assert directed["frame_negative_parity_islands"] == 0, directed
        assert directed["frame_contract_valid"], directed
        assert directed["frame_unresolved_islands"] == 0, directed
        assert directed["records"]["0"]["frame_selected"] is False, directed
        assert directed["records"]["0"]["frame_fallback"] is True, directed
        assert (
            directed["records"]["0"]["frame_reason"]
            == "frame_residual_exceeds_tolerance"
        ), directed
        assert metrics["directed_geometry_valid"], metrics
    finally:
        _remove(obj, mesh)


def _test_strict_planar_frame_gate_rejects_checker_skew():
    """A planar hard-surface chart cannot silently use the +V fallback."""

    sheared = (
        (0.20, 0.10),
        (0.50, 0.10),
        (0.65, 0.70),
        (0.35, 0.70),
    )
    obj, mesh, layer = _quad_object("VUV_Frame_Strict_Planar", sheared)
    try:
        options = VUV.GroupLayoutOptions(
            align_geometry_direction=True,
            align_geometry_frame=True,
            direction_axis="Z",
            strict_geometry_frame_quality=True,
            strict_geometry_frame_planar_only=True,
            strict_geometry_frame_planar_tolerance=math.radians(5.0),
            strict_geometry_frame_residual_tolerance=math.radians(3.0),
            margin=0.0,
        )
        source = VUV.analyze_active_uv(obj, options)
        island = source.islands[0]
        center = island.uv_centroid
        for loop_index in island.loop_indices:
            layer.data[loop_index].uv = VUV._rotate_point(
                layer.data[loop_index].uv,
                center,
                float(island.geometry_rotation_angle),
            )
        mesh.update()

        replay = VUV.analyze_active_uv(obj, options)
        metrics = VUV.evaluate_layout_quality(
            obj,
            analysis=replay,
            face_to_island=replay.face_to_island,
            options=options,
        )
        directed = metrics["directed_geometry"]
        assert directed["misaligned_islands"] == 0, directed
        assert directed["strict_frame_contract_enabled"], directed
        assert directed["strict_frame_required_ids"] == [0], directed
        assert directed["strict_frame_violation_ids"] == [0], directed
        assert directed["frame_fallback_ids"] == [0], directed
        assert not directed["frame_contract_valid"], directed
        assert not metrics["directed_geometry_valid"], metrics
        assert not metrics["valid"], metrics
    finally:
        _remove(obj, mesh)


_test_positive_and_180_keep_positive_parity()
_test_mirror_is_reported_without_reflection()
_test_frame_can_be_opted_out_without_changing_axis_contract()
_test_frame_axis_drift_falls_back_to_strict_plus_v()
_test_layout_writeback_uses_strict_axis_when_frame_has_small_drift()
_test_positive_shear_is_downgraded_without_hard_failure()
_test_strict_planar_frame_gate_rejects_checker_skew()
print("VUV_COMPLETE_FRAME_OK")
