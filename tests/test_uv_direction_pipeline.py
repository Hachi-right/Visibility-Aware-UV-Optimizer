"""Cross-version regressions for the full directed UV optimization pipeline."""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector


ADDON_PARENT = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDON_PARENT))

import visibility_uv_optimizer as addon
from visibility_uv_optimizer import hard_surface, uv_group_layout, uv_optimize


def _clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _object(name, vertices):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def _settings(space, group):
    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = "HARD_SURFACE"
    settings.uv_usage = "UNIQUE"
    settings.max_merge_tests = 0
    settings.preserve_seams = False
    settings.small_cleanup_enabled = False
    settings.uv_group_layout_enabled = group
    settings.hard_surface_direction_lock = True
    settings.hard_surface_align_cardinal = True
    settings.uv_direction_space = space
    settings.uv_direction_axis = "AUTO"
    return settings


def _final_hard_report(obj, *, geometry_matrix=None):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.active
    report = hard_surface.align_island_geometry_report(
        list(bm.faces),
        uv_layer,
        axis_priority=("Z", "X", "Y"),
        geometry_matrix=geometry_matrix,
        write=False,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    return report


def _test_auto_axis_stays_stable_through_full_optimize():
    _clear_scene()
    angle = math.radians(10.0)
    tangent = Vector((math.cos(angle), 0.0, -math.sin(angle)))
    obj = _object(
        "VUV_DualResolver",
        (
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            tuple(tangent + Vector((0.0, 1.0, 0.0))),
            tuple(tangent),
        ),
    )
    settings = _settings("OBJECT", True)
    original_align = hard_surface.align_island_geometry
    original_layout = uv_group_layout.layout_active_uv_adaptive
    hard_axes = []
    group_before = []

    def traced_align(faces, uv_layer, **kwargs):
        result = original_align(faces, uv_layer, **kwargs)
        report = hard_surface.align_island_geometry_report(
            faces,
            uv_layer,
            axis_priority=kwargs.get(
                "axis_priority", hard_surface.DEFAULT_GEOMETRY_AXIS_PRIORITY
            ),
            geometry_matrix=kwargs.get("geometry_matrix"),
            write=False,
        )
        hard_axes.append(report.selected_axis)
        return result

    def traced_layout(active_obj, options):
        island = uv_group_layout.analyze_active_uv(
            active_obj, options
        ).islands[0]
        group_before.append((
            island.geometry_axis_name,
            island.geometry_rotation_angle,
            island.geometry_direction_confidence,
        ))
        return original_layout(active_obj, options)

    hard_surface.align_island_geometry = traced_align
    uv_group_layout.layout_active_uv_adaptive = traced_layout
    try:
        result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings
        )
    finally:
        hard_surface.align_island_geometry = original_align
        uv_group_layout.layout_active_uv_adaptive = original_layout

    final = _final_hard_report(obj)
    assert hard_axes and hard_axes[-1] == "Z", hard_axes
    assert group_before and group_before[0][0] == "Z", group_before
    assert abs(group_before[0][1]) < math.radians(3.0), group_before
    assert final.selected_axis == "Z", final.to_dict()
    assert abs(final.angle_delta) < math.radians(3.0), final.to_dict()
    assert result.group_layout_applied


def _test_world_direction_without_group_layout():
    _clear_scene()
    obj = _object(
        "VUV_World_GroupOff",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
    )
    obj.rotation_euler[1] = math.radians(37.0)
    settings = _settings("WORLD", False)
    result = uv_optimize.optimize_active_object(bpy.context, obj, settings)
    final = _final_hard_report(
        obj, geometry_matrix=obj.matrix_world.to_3x3()
    )
    assert final.selected_axis == "Z", final.to_dict()
    assert abs(final.angle_delta) < math.radians(3.0), final.to_dict()
    assert not result.group_layout_applied


def _test_group_off_direction_failure_rolls_back():
    _clear_scene()
    obj = _object(
        "VUV_GroupOff_DirectionRollback",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
    )
    settings = _settings("OBJECT", False)
    source_seams = tuple(edge.use_seam for edge in obj.data.edges)
    assert len(obj.data.uv_layers) == 0
    original_record = uv_group_layout._geometry_direction_record

    def unresolved(*args, **kwargs):
        return None, Vector((0.0, 0.0)), 0.0, 0.0

    uv_group_layout._geometry_direction_record = unresolved
    try:
        try:
            uv_optimize.optimize_active_object(bpy.context, obj, settings)
        except RuntimeError as exc:
            assert "direction gate failed" in str(exc), str(exc)
        else:
            raise AssertionError("Unresolved group-off direction was committed")
    finally:
        uv_group_layout._geometry_direction_record = original_record
    assert obj.mode == "OBJECT"
    assert len(obj.data.uv_layers) == 0
    assert tuple(edge.use_seam for edge in obj.data.edges) == source_seams


def _test_group_off_manual_axis_loss_rolls_back():
    _clear_scene()
    obj = _object(
        "VUV_GroupOff_ManualAxisRollback",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
    )
    settings = _settings("OBJECT", False)
    settings.uv_direction_axis = "Z"
    source_seams = tuple(edge.use_seam for edge in obj.data.edges)
    original_record = uv_group_layout._geometry_direction_record
    fixed_axes = []

    def lose_fixed_axis(*args, **kwargs):
        fixed_axis = kwargs.get("fixed_axis_name")
        fixed_axes.append(fixed_axis)
        if fixed_axis == "Z":
            return None, Vector((0.0, 0.0)), 0.0, 0.0
        return original_record(*args, **kwargs)

    uv_group_layout._geometry_direction_record = lose_fixed_axis
    try:
        try:
            uv_optimize.optimize_active_object(bpy.context, obj, settings)
        except RuntimeError as exc:
            assert "direction gate failed" in str(exc), str(exc)
        else:
            raise AssertionError("Lost manual axis was committed")
    finally:
        uv_group_layout._geometry_direction_record = original_record
    assert "Z" in fixed_axes, fixed_axes
    assert obj.mode == "OBJECT"
    assert len(obj.data.uv_layers) == 0
    assert tuple(edge.use_seam for edge in obj.data.edges) == source_seams


def _test_group_off_auto_axis_switch_rolls_back():
    _clear_scene()
    obj = _object(
        "VUV_GroupOff_AutoAxisRollback",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
    )
    settings = _settings("OBJECT", False)
    source_seams = tuple(edge.use_seam for edge in obj.data.edges)
    original_record = uv_group_layout._geometry_direction_record
    fixed_axes = []

    def switch_auto_axis(*args, **kwargs):
        fixed_axis = kwargs.get("fixed_axis_name")
        fixed_axes.append(fixed_axis)
        if fixed_axis == "Z":
            return "Z", Vector((1.0, 0.0)), math.pi * 0.5, 1.0
        return "X", Vector((0.0, 1.0)), 0.0, 1.0

    uv_group_layout._geometry_direction_record = switch_auto_axis
    try:
        try:
            uv_optimize.optimize_active_object(bpy.context, obj, settings)
        except RuntimeError as exc:
            assert "direction gate failed" in str(exc), str(exc)
        else:
            raise AssertionError("AUTO axis switch was committed")
    finally:
        uv_group_layout._geometry_direction_record = original_record
    assert "Z" in fixed_axes, fixed_axes
    assert obj.mode == "OBJECT"
    assert len(obj.data.uv_layers) == 0
    assert tuple(edge.use_seam for edge in obj.data.edges) == source_seams


addon.register()
try:
    _test_auto_axis_stays_stable_through_full_optimize()
    _test_world_direction_without_group_layout()
    _test_group_off_direction_failure_rolls_back()
    _test_group_off_manual_axis_loss_rolls_back()
    _test_group_off_auto_axis_switch_rolls_back()
    print("VUV_DIRECTION_PIPELINE_OK")
finally:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    addon.unregister()
