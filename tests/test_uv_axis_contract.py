"""Regression tests for persisted AUTO geometry-axis contracts."""

from __future__ import annotations

import importlib.util
import json
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
        "vuv_axis_contract_regression", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VUV = _load_module()


def _quad_object(name: str):
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
    for loop_index, uv in zip(
        mesh.polygons[0].loop_indices,
        ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0)),
    ):
        layer.data[loop_index].uv = uv
    mesh.update()
    return obj, mesh, layer


def _remove(obj, mesh):
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _settings(**kwargs):
    values = {
        "align_geometry_direction": True,
        "direction_axis": "AUTO",
        "direction_space": "OBJECT",
    }
    values.update(kwargs)
    return VUV.GroupLayoutOptions(**values)


def _payload(
    mesh,
    layer,
    *,
    space="OBJECT",
    auto_priority="ZXY",
    faces=None,
    version=1,
):
    return {
        "version": version,
        "layer": layer.name,
        "space": space,
        "auto_priority": auto_priority,
        "topology": VUV._mesh_topology_contract(mesh),
        "faces": {"0": "Z"} if faces is None else faces,
    }


def _write_payload(mesh, layer, payload):
    mesh[VUV._geometry_axis_contract_key(layer.name)] = json.dumps(payload)


def _single_analysis(obj, mesh, layer, settings):
    islands, face_map, adjacency, diagonal = VUV.compute_active_uv_islands(
        obj, settings
    )
    return VUV.UVLayoutAnalysis(
        object_name=obj.name,
        mesh_name=mesh.name,
        uv_layer_name=layer.name,
        object_diagonal=diagonal,
        islands=islands,
        adjacency=adjacency,
        repeat_candidates=[],
        repeat_groups=[],
        layout_groups=[],
        face_to_island=face_map,
    )


def _test_malformed_contract_and_explicit_override_are_ignored():
    obj, mesh, layer = _quad_object("VUV_AxisContractMalformed")
    try:
        settings = _settings()
        key = VUV._geometry_axis_contract_key(layer.name)
        mesh[key] = '{"version":"not-an-int"}'
        assert VUV._load_geometry_axis_contract(mesh, layer, settings) == {}

        _write_payload(mesh, layer, _payload(mesh, layer, space="INVALID"))
        assert VUV._load_geometry_axis_contract(mesh, layer, settings) == {}

        _write_payload(mesh, layer, _payload(mesh, layer, space="WORLD"))
        force_rebuild = _settings(
            restore_persisted_direction_contract=False
        )
        assert VUV._load_geometry_axis_contract(
            mesh, layer, force_rebuild
        ) == {}

        explicit_override = _settings(
            direction_contract_settings_explicit=True
        )
        assert VUV._load_geometry_axis_contract(
            mesh, layer, explicit_override
        ) == {}

        _write_payload(mesh, layer, _payload(mesh, layer))
        explicit = _settings(direction_axis="Z")
        assert VUV._load_geometry_axis_contract(mesh, layer, explicit) == {}
    finally:
        _remove(obj, mesh)


def _test_saved_space_and_priority_override_scene_defaults():
    """Generated-file contracts survive default UI settings after reopen."""

    obj, mesh, layer = _quad_object("VUV_AxisContractSavedSettings")
    try:
        settings = _settings(
            direction_space="OBJECT",
            direction_auto_priority="ZXY",
        )
        _write_payload(
            mesh,
            layer,
            _payload(
                mesh,
                layer,
                space="WORLD",
                auto_priority="YZX",
            ),
        )
        contract = VUV._load_geometry_axis_contract(mesh, layer, settings)
        assert contract == {(0,): ("Z", "WORLD")}, contract

        # Blender's is_property_set() remains true after reopening.  The
        # integration records that separately from the master restore switch;
        # an identical WORLD/YZX signature still replays exact per-face axes.
        matching_explicit = _settings(
            direction_space="WORLD",
            direction_auto_priority="YZX",
            direction_contract_settings_explicit=True,
        )
        assert VUV._load_geometry_axis_contract(
            mesh, layer, matching_explicit
        ) == {(0,): ("Z", "WORLD")}

        analysis = _single_analysis(obj, mesh, layer, settings)
        original = VUV._geometry_direction_record
        calls = []

        def record_saved_axis(*args, **kwargs):
            calls.append((kwargs.get("space"), kwargs.get("fixed_axis_name")))
            return "Z", Vector((0.0, 1.0)), 0.0, 1.0

        VUV._geometry_direction_record = record_saved_axis
        try:
            count = VUV._apply_persisted_geometry_axis_contract(
                obj, mesh, layer, analysis, settings
            )
        finally:
            VUV._geometry_direction_record = original
        assert count == 1
        assert calls == [("WORLD", "Z")], calls
        assert analysis.islands[0].geometry_direction_space == "WORLD"
        assert VUV._persist_geometry_axis_contract(
            mesh, layer, analysis, settings
        )
        persisted = json.loads(
            mesh[VUV._geometry_axis_contract_key(layer.name)]
        )
        assert persisted["space"] == "WORLD", persisted
        assert persisted["auto_priority"] == "YZX", persisted
    finally:
        _remove(obj, mesh)


def _test_face_set_mismatch_rejects_entire_contract():
    obj, mesh, layer = _quad_object("VUV_AxisContractFaceSet")
    try:
        settings = _settings()
        analysis = _single_analysis(obj, mesh, layer, settings)
        analysis.islands[0].geometry_axis_name = "X"
        _write_payload(mesh, layer, _payload(mesh, layer, faces={"1": "Z"}))
        count = VUV._apply_persisted_geometry_axis_contract(
            obj, mesh, layer, analysis, settings
        )
        assert count == 0
        assert analysis.islands[0].geometry_axis_name == "X"
    finally:
        _remove(obj, mesh)


def _test_round_trip_rebinds_saved_axis():
    obj, mesh, layer = _quad_object("VUV_AxisContractRoundTrip")
    try:
        settings = _settings()
        source = VUV.analyze_active_uv(obj, settings)
        island = source.islands[0]
        island.geometry_axis_name = "Z"
        island.geometry_direction_vector = Vector((0.0, 1.0))
        island.geometry_direction_confidence = 1.0
        island.geometry_rotation_angle = 0.0
        assert VUV._persist_geometry_axis_contract(
            mesh, layer, source, settings
        )

        analysis = _single_analysis(obj, mesh, layer, settings)
        analysis.islands[0].geometry_axis_name = "X"
        original = VUV._geometry_direction_record
        fixed_axes = []

        def fake_record(*args, **kwargs):
            fixed_axis = kwargs.get("fixed_axis_name")
            fixed_axes.append(fixed_axis)
            if fixed_axis == "Z":
                return "Z", Vector((0.0, 1.0)), 0.0, 1.0
            return "X", Vector((1.0, 0.0)), math.pi * 0.5, 1.0

        VUV._geometry_direction_record = fake_record
        try:
            count = VUV._apply_persisted_geometry_axis_contract(
                obj, mesh, layer, analysis, settings
            )
        finally:
            VUV._geometry_direction_record = original
        assert count == 1
        assert analysis.islands[0].geometry_axis_name == "Z"
        assert "Z" in fixed_axes
    finally:
        _remove(obj, mesh)


def _test_none_and_unavailable_axes_stay_unresolved():
    obj, mesh, layer = _quad_object("VUV_AxisContractUnresolved")
    try:
        settings = _settings()
        analysis = _single_analysis(obj, mesh, layer, settings)
        analysis.islands[0].geometry_axis_name = "Z"
        _write_payload(mesh, layer, _payload(mesh, layer, faces={"0": None}))
        count = VUV._apply_persisted_geometry_axis_contract(
            obj, mesh, layer, analysis, settings
        )
        island = analysis.islands[0]
        assert count == 0
        assert island.geometry_axis_name is None
        assert island.geometry_frame_downgrade_reason == (
            "persisted_axis_unresolved"
        )

        _write_payload(mesh, layer, _payload(mesh, layer, faces={"0": "Z"}))
        original = VUV._geometry_direction_record

        def unavailable(*args, **kwargs):
            if kwargs.get("fixed_axis_name") == "Z":
                return None, Vector((0.0, 0.0)), 0.0, 0.0
            return original(*args, **kwargs)

        VUV._geometry_direction_record = unavailable
        try:
            count = VUV._apply_persisted_geometry_axis_contract(
                obj, mesh, layer, analysis, settings
            )
        finally:
            VUV._geometry_direction_record = original
        assert count == 0
        assert island.geometry_axis_name is None
        assert island.geometry_frame_downgrade_reason == (
            "persisted_axis_unavailable"
        )
    finally:
        _remove(obj, mesh)


def _test_metrics_do_not_reresolve_persisted_unresolved_axes():
    """Audit must honor an explicit unresolved replay marker."""

    obj, mesh, layer = _quad_object("VUV_AxisContractMetrics")
    try:
        settings = _settings()
        analysis = _single_analysis(obj, mesh, layer, settings)
        island = analysis.islands[0]
        original = VUV._geometry_direction_record
        calls = []

        def auto_would_choose_x(*args, **kwargs):
            calls.append(kwargs.get("fixed_axis_name"))
            # This is the regression trigger: a fresh AUTO pass would find a
            # valid alternate axis and make the old unresolved marker vanish.
            return "X", Vector((1.0, 0.0)), 0.0, 1.0

        VUV._geometry_direction_record = auto_would_choose_x
        try:
            for reason, required in (
                ("persisted_axis_unresolved", 0),
                ("persisted_axis_unavailable", 1),
            ):
                island.geometry_axis_name = None
                island.geometry_frame_downgrade_reason = reason
                island.geometry_direction_space = "OBJECT"
                metrics = VUV._directed_geometry_metrics(
                    obj, mesh, layer, analysis, settings
                )
                assert calls == [], (reason, calls)
                assert metrics["unresolved_islands"] == 1, metrics
                assert metrics["required_unresolved_islands"] == required, metrics
                assert metrics["frame_unresolved_islands"] == 1, metrics
                assert metrics["unresolved_ids"] == [0], metrics
                assert metrics["required_unresolved_ids"] == (
                    [0] if required else []
                ), metrics
                record = metrics["records"]["0"]
                assert record["axis"] is None, record
                assert record["frame_reason"] == reason, record
        finally:
            VUV._geometry_direction_record = original
    finally:
        _remove(obj, mesh)


_test_malformed_contract_and_explicit_override_are_ignored()
_test_saved_space_and_priority_override_scene_defaults()
_test_face_set_mismatch_rejects_entire_contract()
_test_round_trip_rebinds_saved_axis()
_test_none_and_unavailable_axes_stay_unresolved()
_test_metrics_do_not_reresolve_persisted_unresolved_axes()
print("VUV_AXIS_CONTRACT_OK")
