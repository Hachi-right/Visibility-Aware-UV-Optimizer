"""Cross-version regressions for the full directed UV optimization pipeline."""

from __future__ import annotations

import math
from pathlib import Path
import sys
from types import SimpleNamespace

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


def _test_group_layout_script_settings_are_mapped():
    settings = SimpleNamespace(
        island_margin=0.007,
        uv_align_geometry_frame=False,
        uv_use_geometry_frame_rotation=True,
        uv_cohere_geometry_angle_groups=True,
        uv_strict_geometry_frame_quality=True,
        uv_strict_geometry_frame_planar_only=False,
        uv_strict_geometry_frame_planar_tolerance=math.radians(7.0),
        uv_strict_geometry_frame_residual_tolerance=math.radians(2.0),
        uv_preserve_source_layout=True,
        uv_source_layout_row_quantum=0.04,
        uv_source_layout_row_weight=0.7,
        uv_source_layout_order="AREA",
        uv_source_layout_affinity_weight=1.2,
        uv_source_layout_cell_enabled=False,
        uv_source_layout_compact_cells=False,
        uv_source_layout_cell_max_members=7,
        uv_source_layout_cell_diameter_ratio=0.23,
        uv_source_layout_cell_link_radius_ratio=0.19,
        uv_structure_group_enabled=False,
        uv_structure_group_max_members=9,
        uv_structure_group_max_degree=3,
        uv_structure_group_min_contact_ratio=0.21,
        uv_structure_group_max_diameter_ratio=0.42,
        uv_structure_group_max_normal_angle=math.radians(72.0),
        uv_repeat_group_max_members=18,
        uv_repeat_group_max_diameter_ratio=0.31,
        uv_max_repeat_group_size=14,
        uv_max_small_members_per_group=6,
    )

    options = uv_optimize._group_layout_options(settings)

    assert not options.align_geometry_frame
    assert options.use_geometry_frame_rotation
    assert options.cohere_geometry_angle_groups
    assert options.strict_geometry_frame_quality
    assert not options.strict_geometry_frame_planar_only
    assert math.isclose(
        options.strict_geometry_frame_planar_tolerance, math.radians(7.0))
    assert math.isclose(
        options.strict_geometry_frame_residual_tolerance, math.radians(2.0))
    assert options.restore_persisted_direction_contract
    assert not options.direction_contract_settings_explicit
    assert options.preserve_source_layout
    assert math.isclose(options.source_layout_row_quantum, 0.04)
    assert math.isclose(options.source_layout_row_weight, 0.7)
    assert options.source_layout_order == "AREA"
    assert math.isclose(options.source_layout_affinity_weight, 1.2)
    assert not options.source_layout_cell_enabled
    assert not options.source_layout_compact_cells
    assert options.source_layout_cell_max_members == 7
    assert math.isclose(options.source_layout_cell_diameter_ratio, 0.23)
    assert math.isclose(options.source_layout_cell_link_radius_ratio, 0.19)
    assert not options.structure_group_enabled
    assert options.structure_group_max_members == 9
    assert options.structure_group_max_degree == 3
    assert math.isclose(options.structure_group_min_contact_ratio, 0.21)
    assert math.isclose(options.structure_group_max_diameter_ratio, 0.42)
    assert math.isclose(
        options.structure_group_max_normal_angle, math.radians(72.0))
    assert options.repeat_group_max_members == 18
    assert math.isclose(options.repeat_group_max_diameter_ratio, 0.31)
    assert options.max_repeat_group_size == 14
    assert options.max_small_members_per_group == 6
    options.validated()

    defaults = uv_group_layout.GroupLayoutOptions()
    fallback = uv_optimize._group_layout_options(
        SimpleNamespace(island_margin=0.003))
    assert fallback.align_geometry_frame == defaults.align_geometry_frame
    assert (
        fallback.use_geometry_frame_rotation
        == defaults.use_geometry_frame_rotation
    )
    assert fallback.preserve_source_layout == defaults.preserve_source_layout
    assert fallback.structure_group_max_members == defaults.structure_group_max_members
    assert fallback.repeat_group_max_members == defaults.repeat_group_max_members


def _test_registered_group_layout_settings_match_defaults():
    settings = bpy.context.scene.vuv_settings
    defaults = uv_group_layout.GroupLayoutOptions()
    expected = {
        "uv_align_geometry_frame": defaults.align_geometry_frame,
        "uv_use_geometry_frame_rotation": defaults.use_geometry_frame_rotation,
        "uv_cohere_geometry_angle_groups": defaults.cohere_geometry_angle_groups,
        "uv_strict_geometry_frame_quality": defaults.strict_geometry_frame_quality,
        "uv_strict_geometry_frame_planar_only": (
            defaults.strict_geometry_frame_planar_only),
        "uv_strict_geometry_frame_planar_tolerance": (
            defaults.strict_geometry_frame_planar_tolerance),
        "uv_strict_geometry_frame_residual_tolerance": (
            defaults.strict_geometry_frame_residual_tolerance),
        "uv_preserve_source_layout": defaults.preserve_source_layout,
        "uv_source_layout_row_quantum": defaults.source_layout_row_quantum,
        "uv_source_layout_row_weight": defaults.source_layout_row_weight,
        "uv_source_layout_order": defaults.source_layout_order,
        "uv_source_layout_affinity_weight": defaults.source_layout_affinity_weight,
        "uv_source_layout_cell_enabled": defaults.source_layout_cell_enabled,
        "uv_source_layout_compact_cells": defaults.source_layout_compact_cells,
        "uv_source_layout_cell_max_members": defaults.source_layout_cell_max_members,
        "uv_source_layout_cell_diameter_ratio": (
            defaults.source_layout_cell_diameter_ratio),
        "uv_source_layout_cell_link_radius_ratio": (
            defaults.source_layout_cell_link_radius_ratio),
        "uv_structure_group_enabled": defaults.structure_group_enabled,
        "uv_structure_group_max_members": defaults.structure_group_max_members,
        "uv_structure_group_max_degree": defaults.structure_group_max_degree,
        "uv_structure_group_min_contact_ratio": (
            defaults.structure_group_min_contact_ratio),
        "uv_structure_group_max_diameter_ratio": (
            defaults.structure_group_max_diameter_ratio),
        "uv_structure_group_max_normal_angle": (
            defaults.structure_group_max_normal_angle),
        "uv_repeat_group_max_members": defaults.repeat_group_max_members,
        "uv_repeat_group_max_diameter_ratio": (
            defaults.repeat_group_max_diameter_ratio),
        "uv_max_repeat_group_size": defaults.max_repeat_group_size,
        "uv_max_small_members_per_group": defaults.max_small_members_per_group,
    }
    for name, expected_value in expected.items():
        actual = getattr(settings, name)
        if isinstance(expected_value, float):
            assert math.isclose(actual, expected_value, rel_tol=1.0e-6), (
                name, actual, expected_value)
        else:
            assert actual == expected_value, (name, actual, expected_value)

    options = uv_optimize._group_layout_options(settings)
    assert options.restore_persisted_direction_contract
    assert not options.direction_contract_settings_explicit
    for name, expected_value in expected.items():
        option_name = name[3:]
        actual = getattr(options, option_name)
        if isinstance(expected_value, float):
            assert math.isclose(actual, expected_value, rel_tol=1.0e-6), (
                option_name, actual, expected_value)
        else:
            assert actual == expected_value, (
                option_name, actual, expected_value)

    settings.uv_direction_space = "WORLD"
    try:
        explicit_options = uv_optimize._group_layout_options(settings)
        assert explicit_options.restore_persisted_direction_contract
        assert explicit_options.direction_contract_settings_explicit
    finally:
        settings.property_unset("uv_direction_space")


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
    _test_group_layout_script_settings_are_mapped()
    _test_registered_group_layout_settings_match_defaults()
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
