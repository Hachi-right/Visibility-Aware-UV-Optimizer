"""Cross-version Blender smoke tests for UV semantic group-layout regressions."""

from __future__ import annotations

from collections import defaultdict
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
        "vuv_group_layout_regression_smoke", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VUV = _load_module()


def _center_3d(bounds):
    return Vector((
        (bounds[0] + bounds[3]) * 0.5,
        (bounds[1] + bounds[4]) * 0.5,
        (bounds[2] + bounds[5]) * 0.5,
    ))


def _island(
    island_id,
    model_bounds,
    *,
    small=False,
    neighbors=(),
    principal_angle=0.0,
    anisotropy=1.0,
    direction_angle=None,
    direction_confidence=0.0,
    materials=(0,),
):
    uv_bounds = (0.0, 0.0, 1.0, 0.5)
    return VUV.IslandRecord(
        island_id=island_id,
        face_indices=(island_id,),
        loop_indices=(island_id * 4, island_id * 4 + 1),
        vertex_indices=(island_id * 2, island_id * 2 + 1),
        edge_indices=(island_id,),
        material_indices=tuple(materials),
        area_3d=0.01 if small else 4.0,
        area_3d_ratio=0.0001 if small else 0.4,
        uv_area=0.01 if small else 0.4,
        uv_area_ratio=0.0001 if small else 0.4,
        uv_centroid=Vector((0.5, 0.25)),
        uv_bounds=uv_bounds,
        model_centroid=_center_3d(model_bounds),
        model_bounds=model_bounds,
        average_normal=Vector((0.0, 0.0, 1.0)),
        principal_angle=principal_angle,
        anisotropy=anisotropy,
        direction_vector=(
            Vector((1.0, 0.0))
            if direction_angle is None
            else Vector((math.cos(direction_angle), math.sin(direction_angle)))
        ),
        direction_confidence=direction_confidence,
        landmark_vertex=None,
        geometry_signature="repeat" if not small else "small-{}".format(island_id),
        geometry_payload={},
        neighbor_ids=tuple(neighbors),
        is_small=small,
    )


def _rectangle(loop_start, width, height):
    return {
        loop_start: Vector((0.0, 0.0)),
        loop_start + 1: Vector((width, 0.0)),
        loop_start + 2: Vector((width, height)),
        loop_start + 3: Vector((0.0, height)),
    }


def _bounds_center(points):
    bounds = VUV._uv_bounds(points.values())
    return Vector(((bounds[0] + bounds[2]) * 0.5, (bounds[1] + bounds[3]) * 0.5))


def _bounds_distance(left, right):
    gap_x = max(left[0] - right[2], right[0] - left[2], 0.0)
    gap_y = max(left[1] - right[3], right[1] - left[3], 0.0)
    return math.hypot(gap_x, gap_y)


def _bounds_diagonal(bounds):
    return math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])


def _test_repeat_anchor_order_and_packing():
    anchor_a = _island(10, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2))
    anchor_b = _island(20, (10.0, 0.0, 0.0, 12.0, 2.0, 0.2))
    unrelated = _island(30, (20.0, 0.0, 0.0, 22.0, 2.0, 0.2))
    small_a = _island(
        11,
        (2.05, 0.5, 0.0, 2.20, 0.65, 0.1),
        small=True,
        neighbors=(10,),
    )
    small_b = _island(
        21,
        (9.80, 0.5, 0.0, 9.95, 0.65, 0.1),
        small=True,
        neighbors=(20,),
    )
    repeat = VUV.RepeatGroup(0, (11, 21), "TRANSLATED", 0.99, "small-repeat")
    groups = VUV.build_layout_groups(
        (anchor_a, anchor_b, unrelated, small_a, small_b),
        (repeat,),
        object_diagonal=22.0,
        options=VUV.GroupLayoutOptions(proximity_radius_ratio=0.01),
    )
    membership = {
        member_id: group.group_id
        for group in groups
        for member_id in group.member_ids
    }
    assert len(groups) == 2, [group.member_ids for group in groups]
    assert membership[10] == membership[11] == membership[20] == membership[21]
    assert membership[30] != membership[11]

    group = next(group for group in groups if 11 in group.member_ids)
    small_owners = dict(zip(group.small_member_ids, group.small_anchor_ids))
    assert small_owners == {11: 10, 21: 20}, small_owners
    assert group.owner_cohorts == ((10, 20),), group.owner_cohorts
    assert group.member_ids.index(11) == group.member_ids.index(10) + 1
    assert group.member_ids.index(21) == group.member_ids.index(20) + 1

    oriented = {
        10: _rectangle(100, 1.0, 1.0),
        11: _rectangle(110, 0.1, 0.1),
        20: _rectangle(200, 1.0, 1.0),
        21: _rectangle(210, 0.1, 0.1),
        30: _rectangle(300, 1.0, 1.0),
    }
    plan = VUV._pack_plan(
        oriented, groups, gap=0.05, allow_group_quarter_turn=False
    )
    centers = {
        island_id: _bounds_center(coordinates)
        for island_id, coordinates in plan.coordinates.items()
    }
    assert (centers[11] - centers[10]).length < (centers[11] - centers[20]).length
    assert (centers[21] - centers[20]).length < (centers[21] - centers[10]).length


def _polygon_object(name, polygons, uv_polygons):
    vertices = []
    faces = []
    for polygon in polygons:
        start = len(vertices)
        vertices.extend(polygon)
        faces.append(tuple(range(start, start + len(polygon))))

    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for face, uv_points in zip(mesh.polygons, uv_polygons):
        assert len(face.loop_indices) == len(uv_points)
        for loop_index, uv in zip(face.loop_indices, uv_points):
            uv_layer.data[loop_index].uv = uv
    mesh.update()
    return obj, mesh


def _transformed_uv(points, scale, offset, angle=0.0):
    source = [Vector(point) for point in points]
    center = sum(source, Vector((0.0, 0.0))) / len(source)
    return [
        VUV._rotate_point(point, center, angle) * scale + Vector(offset)
        for point in source
    ]


def _remove_object_and_mesh(obj, mesh):
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _test_small_island_requires_small_model_and_uv_area():
    model_polygons = (
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)),
        ((11.0, 0.0, 0.0), (11.1, 0.0, 0.0), (11.1, 0.1, 0.0), (11.0, 0.1, 0.0)),
        ((12.0, 0.0, 0.0), (12.1, 0.0, 0.0), (12.1, 0.1, 0.0), (12.0, 0.1, 0.0)),
    )
    uv_polygons = (
        ((0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)),
        ((0.15, 0.0), (0.95, 0.0), (0.95, 0.8), (0.15, 0.8)),
        ((0.0, 0.2), (0.01, 0.2), (0.01, 0.21), (0.0, 0.21)),
    )
    obj, mesh = _polygon_object(
        "VUV_Dual_Domain_Small", model_polygons, uv_polygons
    )
    options = VUV.GroupLayoutOptions(
        small_face_count=1,
        small_area_ratio=0.001,
        small_uv_area_ratio=0.001,
    )
    try:
        islands, _face_map, _adjacency, _diagonal = (
            VUV.compute_active_uv_islands(obj, options)
        )
        assert [island.is_small for island in islands] == [False, False, True]
    finally:
        _remove_object_and_mesh(obj, mesh)


def _direction_angle(island):
    return math.atan2(island.direction_vector.y, island.direction_vector.x)


def _test_directed_u_repeats_use_360_orientation():
    shape = (
        (0.0, 0.0),
        (3.0, 0.0),
        (3.0, 3.0),
        (2.0, 3.0),
        (2.0, 1.0),
        (1.0, 1.0),
        (1.0, 3.0),
        (0.0, 3.0),
    )
    model_a = [(x, y, 0.0) for x, y in shape]
    model_b = [(x + 6.0, y, 0.0) for x, y in shape]
    uv_a = _transformed_uv(shape, 0.12, (0.05, 0.05))
    uv_b = _transformed_uv(shape, 0.12, (0.55, 0.05), math.pi)
    obj, mesh = _polygon_object("VUV_Directed_U", (model_a, model_b), (uv_a, uv_b))
    options = VUV.GroupLayoutOptions(allow_group_quarter_turn=True)
    try:
        before = VUV.analyze_active_uv(obj, options)
        assert len(before.islands) == 2
        assert any(set(group.member_ids) == {0, 1} for group in before.repeat_groups)
        assert all(
            island.direction_confidence >= options.min_direction_confidence
            for island in before.islands
        )
        before_delta = VUV._angle_wrap(
            _direction_angle(before.islands[0]) - _direction_angle(before.islands[1])
        )
        assert abs(abs(before_delta) - math.pi) < 1.0e-6, before_delta

        VUV.layout_active_uv(obj, options)
        after = VUV.analyze_active_uv(obj, options)
        assert all(
            island.direction_confidence >= options.min_direction_confidence
            for island in after.islands
        )
        after_delta = VUV._angle_wrap(
            _direction_angle(after.islands[0]) - _direction_angle(after.islands[1])
        )
        assert abs(after_delta) < 1.0e-6, after_delta
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_directed_repeats_resolve_positive_negative_angle():
    angle = math.radians(20.0)
    island_a = _island(
        40,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=angle,
        direction_angle=angle,
        direction_confidence=0.9,
    )
    island_b = _island(
        41,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=-angle,
        direction_angle=-angle,
        direction_confidence=0.9,
    )
    repeat = VUV.RepeatGroup(0, (40, 41), "TRANSLATED", 0.99, "repeat")
    rotations = VUV._orientation_angles(
        _analysis((island_a, island_b), (repeat,)),
        VUV.GroupLayoutOptions(),
    )
    resolved_a = VUV._angle_wrap(angle + rotations[40])
    resolved_b = VUV._angle_wrap(-angle + rotations[41])
    assert abs(VUV._angle_wrap(resolved_a - resolved_b)) < 1.0e-6
    assert abs(resolved_a) < 1.0e-6


def _test_directed_repeats_resolve_near_180_wrap():
    first = math.radians(134.9)
    second = math.radians(-44.9)
    island_a = _island(
        47,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=math.radians(44.0),
        direction_angle=first,
        direction_confidence=0.9,
    )
    island_b = _island(
        48,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=math.radians(44.2),
        direction_angle=second,
        direction_confidence=0.9,
    )
    repeat = VUV.RepeatGroup(0, (47, 48), "TRANSLATED", 0.99, "repeat")
    rotations = VUV._orientation_angles(
        _analysis((island_a, island_b), (repeat,)),
        VUV.GroupLayoutOptions(),
    )
    resolved_a = VUV._angle_wrap(first + rotations[47])
    resolved_b = VUV._angle_wrap(second + rotations[48])
    assert abs(VUV._angle_wrap(resolved_a - resolved_b)) < 1.0e-6


def _test_mixed_direction_confidence_uses_group_line_fallback():
    angle = math.radians(20.0)
    island_a = _island(
        42,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=angle,
        direction_angle=angle,
        direction_confidence=0.9,
    )
    island_b = _island(
        43,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=-angle,
        direction_angle=-angle,
        direction_confidence=0.19,
    )
    repeat = VUV.RepeatGroup(0, (42, 43), "TRANSLATED", 0.99, "repeat")
    rotations = VUV._orientation_angles(
        _analysis((island_a, island_b), (repeat,)),
        VUV.GroupLayoutOptions(),
    )
    assert abs(rotations[42] + angle) < 1.0e-12
    assert abs(rotations[43] - angle) < 1.0e-12


def _test_owner_cohort_uses_one_directed_orientation():
    owner_a = _island(
        10,
        (0.0, 0.0, 0.0, 2.0, 1.0, 0.1),
        principal_angle=0.0,
        direction_angle=0.0,
        direction_confidence=0.9,
    )
    owner_b = _island(
        20,
        (4.0, 0.0, 0.0, 5.0, 2.0, 0.1),
        principal_angle=math.pi * 0.5,
        direction_angle=math.pi * 0.5,
        direction_confidence=0.9,
    )
    small_a = _island(
        11,
        (2.02, 0.2, 0.0, 2.10, 0.28, 0.05),
        small=True,
        neighbors=(10,),
    )
    small_b = _island(
        21,
        (3.90, 0.2, 0.0, 3.98, 0.28, 0.05),
        small=True,
        neighbors=(20,),
    )
    repeat = VUV.RepeatGroup(0, (11, 21), "TRANSLATED", 0.99, "small-repeat")
    groups = VUV.build_layout_groups(
        (owner_a, owner_b, small_a, small_b),
        (repeat,),
        object_diagonal=5.0,
        options=VUV.GroupLayoutOptions(proximity_radius_ratio=0.01),
    )
    assert len(groups) == 1, [group.member_ids for group in groups]
    assert groups[0].owner_cohorts == ((10, 20),), groups[0].owner_cohorts

    rotations = VUV._orientation_angles(
        _analysis((owner_a, owner_b, small_a, small_b), (repeat,), groups),
        VUV.GroupLayoutOptions(),
    )
    resolved_a = VUV._angle_wrap(_direction_angle(owner_a) + rotations[10])
    resolved_b = VUV._angle_wrap(_direction_angle(owner_b) + rotations[20])
    assert abs(VUV._angle_wrap(resolved_a - resolved_b)) < 1.0e-9, (
        rotations,
        resolved_a,
        resolved_b,
    )


def _test_cross_layout_repeat_keeps_quarter_turn_parity():
    repeat_a = _island(1, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2), small=False)
    owner_b = _island(2, (10.0, 0.0, 0.0, 11.0, 4.0, 0.2), small=False)
    repeat_b = _island(
        3,
        (11.02, 1.0, 0.0, 11.10, 1.08, 0.05),
        small=True,
        neighbors=(2,),
    )
    repeat = VUV.RepeatGroup(0, (1, 3), "TRANSLATED", 0.99, "mixed-repeat")
    groups = VUV.build_layout_groups(
        (repeat_a, owner_b, repeat_b),
        (repeat,),
        object_diagonal=12.0,
        options=VUV.GroupLayoutOptions(),
    )
    member_group = {
        member_id: group.group_id
        for group in groups
        for member_id in group.member_ids
    }
    assert member_group[1] != member_group[3], member_group

    oriented = {
        1: _rectangle(10, 0.4, 0.2),
        2: _rectangle(20, 1.0, 4.0),
        3: _rectangle(30, 0.2, 0.1),
    }
    plan = VUV._pack_plan(
        oriented,
        groups,
        gap=0.05,
        allow_group_quarter_turn=True,
        repeat_groups=(repeat,),
    )
    repeat_group_ids = {member_group[1], member_group[3]}
    parities = {
        plan.group_placements[group_id].quarter_turn
        for group_id in repeat_group_ids
    }
    assert parities == {False}, plan.group_placements
    edge_a = plan.coordinates[1][11] - plan.coordinates[1][10]
    edge_b = plan.coordinates[3][31] - plan.coordinates[3][30]
    assert edge_a.normalized().dot(edge_b.normalized()) > 1.0 - 1.0e-9, (
        plan.group_placements,
        edge_a,
        edge_b,
    )


def _test_cross_layout_repeat_stays_near_with_unrelated_groups():
    repeat_a = _island(1, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2))
    unrelated = tuple(
        _island(
            100 + index,
            (
                10.0 + index * 2.0,
                0.0,
                0.0,
                11.0 + index * 2.0,
                1.0,
                0.2,
            ),
        )
        for index in range(8)
    )
    owner_b = _island(2, (30.0, 0.0, 0.0, 31.0, 1.0, 0.2))
    repeat_b = _island(
        3,
        (31.02, 0.2, 0.0, 31.10, 0.28, 0.05),
        small=True,
        neighbors=(2,),
    )
    repeat = VUV.RepeatGroup(0, (1, 3), "TRANSLATED", 0.99, "mixed-repeat")
    groups = VUV.build_layout_groups(
        (repeat_a,) + unrelated + (owner_b, repeat_b),
        (repeat,),
        object_diagonal=32.0,
        options=VUV.GroupLayoutOptions(),
    )
    member_group = {
        member_id: group.group_id
        for group in groups
        for member_id in group.member_ids
    }
    related_group_ids = (member_group[1], member_group[3])
    group_positions = {
        group.group_id: position for position, group in enumerate(groups)
    }
    unrelated_between = (
        abs(
            group_positions[related_group_ids[0]]
            - group_positions[related_group_ids[1]]
        )
        - 1
    )
    assert related_group_ids[0] != related_group_ids[1], member_group
    assert unrelated_between >= 8, [group.member_ids for group in groups]

    oriented = {
        1: _rectangle(10, 0.4, 0.2),
        2: _rectangle(20, 0.8, 0.8),
        3: _rectangle(30, 0.4, 0.2),
    }
    for island in unrelated:
        oriented[island.island_id] = _rectangle(
            island.island_id * 10, 0.9, 0.9
        )

    gap = 0.05
    plan = VUV._pack_plan(
        oriented,
        groups,
        gap=gap,
        allow_group_quarter_turn=True,
        repeat_groups=(repeat,),
    )
    edge_a = plan.coordinates[1][11] - plan.coordinates[1][10]
    edge_b = plan.coordinates[3][31] - plan.coordinates[3][30]
    assert edge_a.normalized().dot(edge_b.normalized()) > 1.0 - 1.0e-9, (
        plan.group_placements,
        edge_a,
        edge_b,
    )

    island_bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    group_bounds = {}
    groups_by_id = {group.group_id: group for group in groups}
    for group_id in related_group_ids:
        bounds = [
            island_bounds[island_id]
            for island_id in groups_by_id[group_id].member_ids
        ]
        group_bounds[group_id] = (
            min(item[0] for item in bounds),
            min(item[1] for item in bounds),
            max(item[2] for item in bounds),
            max(item[3] for item in bounds),
        )

    near_limit = max(
        _bounds_diagonal(group_bounds[related_group_ids[0]]),
        _bounds_diagonal(group_bounds[related_group_ids[1]]),
    ) + 2.0 * gap
    group_distance = _bounds_distance(
        group_bounds[related_group_ids[0]],
        group_bounds[related_group_ids[1]],
    )
    member_distance = _bounds_distance(island_bounds[1], island_bounds[3])
    assert group_distance <= near_limit + 1.0e-9, (
        group_distance,
        near_limit,
        related_group_ids,
        group_bounds,
        plan.group_placements,
    )
    assert member_distance <= near_limit + 1.0e-9, (
        member_distance,
        near_limit,
        (island_bounds[1], island_bounds[3]),
        group_bounds,
    )


def _test_model_distance_precedes_material_for_small_owner():
    near = _island(
        44,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        materials=(0,),
    )
    far_same_material = _island(
        45,
        (4.0, 0.0, 0.0, 5.0, 1.0, 0.1),
        materials=(1,),
    )
    fragment = _island(
        46,
        (1.05, 0.2, 0.0, 1.15, 0.3, 0.1),
        small=True,
        materials=(1,),
    )
    groups = VUV.build_layout_groups(
        (near, far_same_material, fragment),
        (),
        object_diagonal=5.0,
        options=VUV.GroupLayoutOptions(proximity_radius_ratio=1.0),
    )
    owner_group = next(group for group in groups if 46 in group.small_member_ids)
    owners = dict(zip(owner_group.small_member_ids, owner_group.small_anchor_ids))
    assert owners[46] == 44, owners


def _test_topology_owner_is_not_rejected_by_fallback_capacity():
    owner = _island(0, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2))
    unrelated = _island(100, (20.0, 0.0, 0.0, 22.0, 2.0, 0.2))
    fragments = tuple(
        _island(
            index,
            (2.01, index * 0.01, 0.0, 2.02, index * 0.01 + 0.005, 0.05),
            small=True,
            neighbors=(0,),
        )
        for index in range(1, 18)
    )
    groups = VUV.build_layout_groups(
        (owner, unrelated) + fragments,
        (),
        object_diagonal=22.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            max_small_members_per_group=16,
        ),
    )
    owner_group = next(group for group in groups if 0 in group.anchor_ids)
    owners = dict(zip(owner_group.small_member_ids, owner_group.small_anchor_ids))
    assert owners == {index: 0 for index in range(1, 18)}, owners


def _test_fallback_capacity_is_independent_per_owner():
    owner_a = _island(200, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2))
    owner_b = _island(300, (10.0, 0.0, 0.0, 12.0, 2.0, 0.2))
    fragment_a = _island(
        201,
        (2.01, 0.2, 0.0, 2.02, 0.21, 0.05),
        small=True,
    )
    fragment_b = _island(
        301,
        (9.98, 0.2, 0.0, 9.99, 0.21, 0.05),
        small=True,
    )
    repeat = VUV.RepeatGroup(0, (200, 300), "TRANSLATED", 0.99, "repeat")
    groups = VUV.build_layout_groups(
        (owner_a, owner_b, fragment_a, fragment_b),
        (repeat,),
        object_diagonal=12.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            max_small_members_per_group=1,
        ),
    )
    owner_group = next(group for group in groups if 200 in group.anchor_ids)
    owners = dict(zip(owner_group.small_member_ids, owner_group.small_anchor_ids))
    assert owners == {201: 200, 301: 300}, owners


def _test_owner_cells_keep_many_fragments_local():
    anchors = (
        _island(50, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2)),
        _island(60, (10.0, 0.0, 0.0, 12.0, 2.0, 0.2)),
    )
    small_a = tuple(
        _island(
            51 + index,
            (2.05, index * 0.05, 0.0, 2.15, index * 0.05 + 0.04, 0.1),
            small=True,
            neighbors=(50,),
        )
        for index in range(8)
    )
    small_b = tuple(
        _island(
            61 + index,
            (9.85, index * 0.05, 0.0, 9.95, index * 0.05 + 0.04, 0.1),
            small=True,
            neighbors=(60,),
        )
        for index in range(8)
    )
    repeat = VUV.RepeatGroup(0, (50, 60), "TRANSLATED", 0.99, "repeat")
    groups = VUV.build_layout_groups(
        anchors + small_a + small_b,
        (repeat,),
        object_diagonal=12.0,
        options=VUV.GroupLayoutOptions(proximity_radius_ratio=0.05),
    )
    group = next(item for item in groups if 50 in item.anchor_ids)
    oriented = {
        50: _rectangle(500, 1.0, 1.0),
        60: _rectangle(600, 1.0, 1.0),
    }
    for island in small_a + small_b:
        oriented[island.island_id] = _rectangle(
            island.island_id * 10, 0.16, 0.12
        )
    plan = VUV._pack_plan(
        oriented, groups, gap=0.05, allow_group_quarter_turn=False
    )
    centers = {
        island_id: _bounds_center(coordinates)
        for island_id, coordinates in plan.coordinates.items()
    }
    bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    owners = dict(zip(group.small_member_ids, group.small_anchor_ids))
    for small_id, owner_id in owners.items():
        other_id = 60 if owner_id == 50 else 50
        assert (
            centers[small_id] - centers[owner_id]
        ).length < (centers[small_id] - centers[other_id]).length
        near_limit = max(
            _bounds_diagonal(bounds[small_id]),
            _bounds_diagonal(bounds[owner_id]),
        ) + 2.0 * 0.05
        assert _bounds_distance(
            bounds[small_id], bounds[owner_id]
        ) <= near_limit + 1.0e-9


def _test_repeat_owner_cohorts_stay_near_in_large_component():
    anchor_ids = (70, 80, 90, 100, 110, 120)
    anchors = tuple(
        _island(
            anchor_id,
            (position * 10.0, 0.0, 0.0, position * 10.0 + 2.0, 2.0, 0.2),
        )
        for position, anchor_id in enumerate(anchor_ids)
    )
    owner_pairs = ((70, 110), (70, 120), (80, 100), (90, 80), (110, 90))
    fragments = []
    repeats = []
    fragment_owners = {}
    next_id = 200
    for repeat_id, owner_pair in enumerate(owner_pairs):
        members = []
        for owner_id in owner_pair:
            fragment_id = next_id
            next_id += 1
            owner_position = anchor_ids.index(owner_id)
            fragments.append(_island(
                fragment_id,
                (
                    owner_position * 10.0 + 2.02,
                    repeat_id * 0.08,
                    0.0,
                    owner_position * 10.0 + 2.10,
                    repeat_id * 0.08 + 0.05,
                    0.05,
                ),
                small=True,
                neighbors=(owner_id,),
            ))
            fragment_owners[fragment_id] = owner_id
            members.append(fragment_id)
        repeats.append(VUV.RepeatGroup(
            repeat_id,
            tuple(members),
            "TRANSLATED",
            0.99,
            "cohort-{}".format(repeat_id),
        ))

    groups = VUV.build_layout_groups(
        anchors + tuple(fragments),
        tuple(repeats),
        object_diagonal=60.0,
        options=VUV.GroupLayoutOptions(proximity_radius_ratio=0.01),
    )
    group = next(item for item in groups if 70 in item.anchor_ids)
    assert set(group.anchor_ids) == set(anchor_ids), group.anchor_ids
    assert set(group.owner_cohorts) == set(owner_pairs), group.owner_cohorts
    assert dict(zip(group.small_member_ids, group.small_anchor_ids)) == fragment_owners

    owner_sizes = {
        70: (1.0, 0.8),
        80: (0.4, 2.2),
        90: (1.8, 0.5),
        100: (0.7, 1.7),
        110: (1.4, 0.9),
        120: (0.5, 2.5),
    }
    oriented = {
        owner_id: _rectangle(owner_id * 10, *owner_sizes[owner_id])
        for owner_id in anchor_ids
    }
    for fragment in fragments:
        oriented[fragment.island_id] = _rectangle(
            fragment.island_id * 10, 0.18, 0.11
        )
    gap = 0.05
    plan = VUV._pack_plan(
        oriented, groups, gap=gap, allow_group_quarter_turn=False
    )
    island_bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    small_by_owner = defaultdict(list)
    for small_id, owner_id in zip(
        group.small_member_ids, group.small_anchor_ids
    ):
        small_by_owner[owner_id].append(small_id)

    cell_bounds = {}
    for owner_id in group.anchor_ids:
        bounds = [
            island_bounds[island_id]
            for island_id in (owner_id,) + tuple(small_by_owner[owner_id])
        ]
        cell_bounds[owner_id] = (
            min(item[0] for item in bounds),
            min(item[1] for item in bounds),
            max(item[2] for item in bounds),
            max(item[3] for item in bounds),
        )

    for cohort in group.owner_cohorts:
        for owner_id in cohort:
            peers = [peer_id for peer_id in cohort if peer_id != owner_id]
            assert any(
                _bounds_distance(cell_bounds[owner_id], cell_bounds[peer_id])
                <= max(
                    _bounds_diagonal(cell_bounds[owner_id]),
                    _bounds_diagonal(cell_bounds[peer_id]),
                ) + 2.0 * gap + 1.0e-9
                for peer_id in peers
            ), (cohort, owner_id, cell_bounds)


def _test_split_weapon_repeat_owner_graph_is_solved():
    """Regression for the 13-owner Body graph that defeated greedy packing."""

    sizes = {
        34: (0.056821465492248535, 0.04332505166530609),
        175: (0.6286611407995224, 0.4609675407409668),
        236: (0.08907031267881393, 0.04248458892107010),
        267: (0.032992325723171234, 0.027480468153953552),
        354: (0.09694577381014824, 0.03823671489953995),
        396: (0.02213636040687561, 0.01917111873626709),
        405: (0.036396101117134094, 0.018339499831199646),
        549: (0.04027533531188965, 0.03872501850128174),
        551: (0.050768375396728516, 0.04829613119363785),
        555: (0.09193696454167366, 0.028098180890083313),
        557: (0.05928397178649902, 0.053592950105667114),
        649: (0.04072894901037216, 0.014818251132965088),
        677: (0.14761045202612877, 0.1332656741142273),
    }
    cohorts = (
        (267, 405),
        (236, 396),
        (551, 649),
        (236, 34),
        (557, 405),
        (555, 354, 677),
        (551, 677),
        (175, 555),
        (405, 555),
        (677, 396),
        (551, 549),
        (236, 175),
    )
    rectangles = {
        cell_id: VUV._Rect(cell_id, width, height, position)
        for position, (cell_id, (width, height)) in enumerate(sizes.items())
    }
    adjacency = {cell_id: set() for cell_id in rectangles}
    for cohort in cohorts:
        for cell_id in cohort:
            adjacency[cell_id].update(
                peer_id for peer_id in cohort if peer_id != cell_id
            )

    gap = 0.002
    placements, width, height = VUV._pack_affinity_component(
        rectangles, adjacency, cohorts, gap
    )
    assert set(placements) == set(rectangles), placements
    assert width > 0.0 and height > 0.0, (width, height)
    for left_id, left in placements.items():
        for right_id, right in placements.items():
            if left_id >= right_id:
                continue
            assert VUV._placements_clear(left, right, gap), (
                left_id, right_id, left, right
            )
    assert VUV._affinity_cohorts_valid(
        placements, cohorts, gap, True
    ), placements

    reversed_placements, reversed_width, reversed_height = (
        VUV._pack_affinity_component(
            dict(reversed(tuple(rectangles.items()))),
            dict(reversed(tuple(adjacency.items()))),
            tuple(reversed(cohorts)),
            gap,
        )
    )

    def normalized(records):
        minimum_x = min(item.x for item in records.values())
        minimum_y = min(item.y for item in records.values())
        return tuple(
            (
                cell_id,
                round(item.x - minimum_x, 10),
                round(item.y - minimum_y, 10),
            )
            for cell_id, item in sorted(records.items())
        )

    assert normalized(reversed_placements) == normalized(placements)
    assert abs(reversed_width - width) < 1.0e-12
    assert abs(reversed_height - height) < 1.0e-12


def _test_split_weapon_dual_domain_owner_graph_is_solved():
    """Regression for the 17-owner graph after dual-domain small filtering."""

    sizes = {
        6: (0.12384285032749176, 0.21723872423171997),
        7: (0.024624288082122803, 0.06296176463365555),
        34: (0.06176835298538208, 0.07723377645015717),
        38: (0.0494002103805542, 0.12977179884910583),
        175: (0.08662540093064308, 0.19808393716812134),
        177: (0.12062057852745056, 0.23737269639968872),
        184: (0.027251899242401123, 0.06296154856681824),
        188: (0.08044290542602539, 0.23489785194396973),
        204: (0.02462327480316162, 0.0479244589805603),
        207: (0.013537853956222534, 0.07908475399017334),
        236: (0.03996717929840088, 0.1464681625366211),
        271: (0.02868906408548355, 0.06533467769622803),
        354: (0.035537303192541, 0.03634154796600342),
        387: (0.08702960540540516, 0.23461052775382996),
        405: (0.03170451521873474, 0.06264996528625488),
        555: (0.05651238560676575, 0.16790255904197693),
        677: (0.25639303564094007, 0.288588538300246),
    }
    cohorts = (
        (236, 405),
        (188, 175),
        (188, 387),
        (177, 6),
        (184, 7),
        (207, 387),
        (677, 6),
        (188, 34),
        (387, 177),
        (188, 405),
        (555, 354, 677),
        (177, 204),
        (184, 387),
        (177, 387),
        (175, 555),
        (188, 6),
        (271, 38),
        (175, 188),
        (677, 175),
        (271, 6),
        (236, 175),
    )
    rectangles = {
        cell_id: VUV._Rect(cell_id, width, height, position)
        for position, (cell_id, (width, height)) in enumerate(sizes.items())
    }
    adjacency = {cell_id: set() for cell_id in rectangles}
    for cohort in cohorts:
        for cell_id in cohort:
            adjacency[cell_id].update(
                peer_id for peer_id in cohort if peer_id != cell_id
            )

    placements, width, height = VUV._pack_affinity_component(
        rectangles, adjacency, cohorts, 0.0
    )
    assert set(placements) == set(rectangles), placements
    assert width > 0.0 and height > 0.0, (width, height)
    assert VUV._affinity_cohorts_valid(
        placements, cohorts, 0.0, True
    ), placements


def _test_affinity_search_budget_is_shared_by_component():
    rectangles = {
        cell_id: VUV._Rect(cell_id, 1.0, 1.0, cell_id)
        for cell_id in range(3)
    }
    adjacency = {
        cell_id: {peer_id for peer_id in rectangles if peer_id != cell_id}
        for cell_id in rectangles
    }
    cohorts = ((0, 1, 2),)
    original_policies = VUV._AFFINITY_SEARCH_POLICIES
    original_candidates = VUV._affinity_candidate_placements
    original_valid = VUV._affinity_cohorts_valid
    original_state_key = VUV._affinity_state_key
    state_count = [0]
    partial_validations = [0]

    def candidates(cell_id, _rectangles, _adjacency, placements, _gap):
        rank = len(placements)
        return [
            VUV._Placement(
                cell_id * 100.0 + branch * 10.0,
                rank * 100.0 + branch,
                1.0,
                1.0,
            )
            for branch in range(100)
        ]

    def never_complete(placements, _cohorts, _gap, require_complete):
        if not require_complete:
            partial_validations[0] += 1
        return not (require_complete and len(placements) == len(rectangles))

    def counted_state_key(placements):
        state_count[0] += 1
        return original_state_key(placements)

    try:
        VUV._AFFINITY_SEARCH_POLICIES = (
            (3, None, 4),
        )
        VUV._affinity_candidate_placements = candidates
        VUV._affinity_cohorts_valid = never_complete
        VUV._affinity_state_key = counted_state_key
        try:
            VUV._pack_affinity_component(
                rectangles,
                adjacency,
                cohorts,
                0.0,
            )
        except RuntimeError as exc:
            assert "Could not solve" in str(exc), str(exc)
        else:
            raise AssertionError("Unsatisfied affinity component was accepted")
    finally:
        VUV._AFFINITY_SEARCH_POLICIES = original_policies
        VUV._affinity_candidate_placements = original_candidates
        VUV._affinity_cohorts_valid = original_valid
        VUV._affinity_state_key = original_state_key

    assert state_count[0] <= 4, state_count[0]
    assert partial_validations[0] <= 1, partial_validations[0]


def _test_centrosymmetric_repeats_remain_180_equivalent():
    shape = ((0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0))
    model_a = [(x, y, 0.0) for x, y in shape]
    model_b = [(x + 6.0, y, 0.0) for x, y in shape]
    uv_a = _transformed_uv(shape, 0.12, (0.05, 0.55))
    uv_b = _transformed_uv(shape, 0.12, (0.55, 0.55), math.pi)
    obj, mesh = _polygon_object(
        "VUV_Centrosymmetric", (model_a, model_b), (uv_a, uv_b)
    )
    options = VUV.GroupLayoutOptions(allow_group_quarter_turn=True)
    try:
        before = VUV.analyze_active_uv(obj, options)
        assert any(set(group.member_ids) == {0, 1} for group in before.repeat_groups)
        for island in before.islands:
            assert island.direction_vector.length_squared < 1.0e-12
            assert island.direction_confidence == 0.0
            assert island.to_dict()["direction_angle_degrees"] is None

        VUV.layout_active_uv(obj, options)
        after = VUV.analyze_active_uv(obj, options)
        for island in after.islands:
            assert island.direction_vector.length_squared < 1.0e-12
            assert island.direction_confidence == 0.0
            assert island.to_dict()["direction_angle_degrees"] is None
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_tiny_float32_similarity_tolerance():
    model = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.5, 1.0, 0.0))
    source_anchor = Vector((0.125, 0.125))
    source_uv = (
        source_anchor,
        source_anchor + Vector((0.0026, 0.0)),
        source_anchor + Vector((0.0012, 0.0010)),
    )
    obj, mesh = _polygon_object("VUV_Tiny_Float32", (model,), (source_uv,))
    try:
        uv_layer = mesh.uv_layers.active
        islands, _face_map, _adjacency, _diagonal = VUV.compute_active_uv_islands(obj)
        assert len(islands) == 1
        island = islands[0]
        before = [item.uv.copy() for item in uv_layer.data]
        anchor_index = island.loop_indices[0]
        after_anchor = Vector((0.25, 0.25))
        apparent_scale = 1.0 + 8.0e-5
        for loop_index in island.loop_indices:
            source_delta = before[loop_index] - before[anchor_index]
            uv_layer.data[loop_index].uv = after_anchor + source_delta * apparent_scale

        reference_index = max(
            island.loop_indices[1:],
            key=lambda index: (
                before[index] - before[anchor_index]
            ).length_squared,
        )
        source_delta = before[reference_index] - before[anchor_index]
        target_delta = (
            uv_layer.data[reference_index].uv
            - uv_layer.data[anchor_index].uv
        )
        measured_scale = target_delta.length / source_delta.length
        relative_error = VUV._relative_error(measured_scale, 1.0)
        absolute_error = (target_delta - source_delta).length
        assert 0.0025 < source_delta.length < 0.0027, source_delta.length
        assert 5.0e-5 < relative_error < 2.0e-4, relative_error
        assert absolute_error < 5.0e-7, absolute_error
        VUV._validate_island_similarity(before, uv_layer, islands, 1.0)

        # A genuine non-uniform scale remains well outside the same absolute
        # float32 budget and must not be accepted as a rigid transform.
        for loop_index in island.loop_indices:
            source_delta = before[loop_index] - before[anchor_index]
            uv_layer.data[loop_index].uv = after_anchor + Vector((
                source_delta.x * apparent_scale,
                source_delta.y * (apparent_scale + 2.0e-3),
            ))
        try:
            VUV._validate_island_similarity(before, uv_layer, islands, 1.0)
        except RuntimeError as exc:
            assert "not transformed rigidly" in str(exc), str(exc)
        else:
            raise AssertionError("Non-uniform tiny-island deformation was accepted")
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_quantized_winding_stabilization_and_audit_fields():
    # Diagnosed near-collinear ear triangle from face 830 of the gun-head mesh.
    model = (
        (-0.3229217827320099, -1.0228915214538574, 0.14895236492156982),
        (-0.3229217827320099, -1.0506467819213867, 0.14895212650299072),
        (-0.3229217827320099, -1.2846832275390625, 0.14895212650299072),
    )
    diagnosed_float32_uv = (
        (0.2067285180091858, 0.10258331894874573),
        (0.2058098018169403, 0.10713256895542145),
        (0.19806307554244995, 0.14549247920513153),
    )
    obj, mesh = _polygon_object(
        "VUV_Quantized_Winding", (model,), (diagnosed_float32_uv,)
    )
    face_to_island = (0,)
    try:
        uv_layer = mesh.uv_layers.active
        before_audit = VUV.audit_active_uv(obj, face_to_island)
        assert before_audit["negative"] + before_audit["degenerate"] == 1
        assert before_audit["positive"] == 0

        fitted = {0: {
            loop.index: uv_layer.data[loop.index].uv.copy()
            for loop in mesh.loops
        }}
        rotations = VUV._stabilize_quantized_winding(
            mesh, uv_layer, fitted, face_to_island
        )
        assert set(rotations) == {0}, rotations
        adjustment = rotations[0]
        assert 0.0 < abs(adjustment) <= 5.0e-5, adjustment

        after_audit = VUV.audit_active_uv(obj, face_to_island)
        assert after_audit["positive"] == after_audit["triangles"] == 1
        assert after_audit["negative"] == 0
        assert after_audit["degenerate"] == 0
        assert not after_audit["overlap"]
        VUV._validate_result_audit(after_audit)

        maximum_degrees = abs(math.degrees(adjustment))
        result = VUV.GroupLayoutResult(
            analysis=_analysis(()),
            uniform_scale=1.0,
            requested_margin=0.003,
            achieved_aabb_gap=0.003,
            rotated_islands=0,
            grouped_small_islands=0,
            before_audit=before_audit,
            after_audit=after_audit,
            quantization_adjusted_islands=len(rotations),
            max_quantization_rotation_degrees=maximum_degrees,
        )
        payload = result.to_dict()
        assert payload["quantization_adjusted_islands"] == 1
        assert payload["max_quantization_rotation_degrees"] == round(
            maximum_degrees, 9
        )
        assert payload["after_audit"] == after_audit

        true_negative_uv = (
            Vector((0.2, 0.2)),
            Vector((0.2, 0.4)),
            Vector((0.4, 0.2)),
        )
        true_negative_fitted = {0: {}}
        for loop, point in zip(mesh.loops, true_negative_uv):
            uv_layer.data[loop.index].uv = point
            true_negative_fitted[0][loop.index] = point.copy()
        mesh.update()
        unresolved = VUV._stabilize_quantized_winding(
            mesh, uv_layer, true_negative_fitted, face_to_island
        )
        assert unresolved == {}, unresolved
        negative_audit = VUV.audit_active_uv(obj, face_to_island)
        assert negative_audit["negative"] == 1
        try:
            VUV._validate_result_audit(negative_audit)
        except RuntimeError as exc:
            assert "changed positive UV winding" in str(exc), str(exc)
        else:
            raise AssertionError("A true negative winding passed the result audit")
    finally:
        _remove_object_and_mesh(obj, mesh)


def _analysis(islands, repeat_groups=(), layout_groups=()):
    return VUV.UVLayoutAnalysis(
        object_name="Regression",
        mesh_name="RegressionMesh",
        uv_layer_name="UVMap",
        object_diagonal=1.0,
        islands=list(islands),
        adjacency=[],
        repeat_candidates=[],
        repeat_groups=list(repeat_groups),
        layout_groups=list(layout_groups),
        face_to_island=(),
    )


def _test_non_repeat_cardinal_switch():
    angle_30 = math.radians(30.0)
    non_repeat = _island(
        1,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=angle_30,
    )
    disabled = VUV.GroupLayoutOptions(align_non_repeat_cardinal=False)
    angles = VUV._orientation_angles(_analysis((non_repeat,)), disabled)
    assert abs(angles[1]) < 1.0e-12, angles[1]

    enabled_angles = VUV._orientation_angles(
        _analysis((non_repeat,)), VUV.GroupLayoutOptions()
    )
    assert abs(enabled_angles[1] + angle_30) < 1.0e-12, enabled_angles[1]

    repeat_other = _island(
        2,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=-angle_30,
    )
    repeat = VUV.RepeatGroup(0, (1, 2), "TRANSLATED", 0.99, "repeat")
    repeat_angles = VUV._orientation_angles(
        _analysis((non_repeat, repeat_other), (repeat,)), disabled
    )
    target_a = VUV._line_angle_wrap(angle_30 + repeat_angles[1])
    target_b = VUV._line_angle_wrap(-angle_30 + repeat_angles[2])
    assert abs(VUV._line_angle_wrap(target_a - target_b)) < 1.0e-12


def _mesh_diagonal(name, include_loose_vertex):
    vertices = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    if include_loose_vertex:
        vertices.append((1000.0, 0.0, 0.0))
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_by_vertex = (
        Vector((0.0, 0.0)),
        Vector((1.0, 0.0)),
        Vector((1.0, 1.0)),
        Vector((0.0, 1.0)),
    )
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = uv_by_vertex[loop.vertex_index]
    try:
        _records, _face_map, _adjacency, diagonal = VUV.compute_active_uv_islands(obj)
        return diagonal
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _test_loose_vertex_does_not_change_radius():
    clean = _mesh_diagonal("VUV_Clean", False)
    loose = _mesh_diagonal("VUV_Loose", True)
    expected = math.sqrt(5.0)
    assert abs(clean - expected) < 1.0e-9, clean
    assert abs(loose - clean) < 1.0e-9, (clean, loose)
    ratio = VUV.GroupLayoutOptions().proximity_radius_ratio
    assert abs(clean * ratio - loose * ratio) < 1.0e-12


_test_repeat_anchor_order_and_packing()
_test_small_island_requires_small_model_and_uv_area()
_test_directed_u_repeats_use_360_orientation()
_test_directed_repeats_resolve_positive_negative_angle()
_test_directed_repeats_resolve_near_180_wrap()
_test_mixed_direction_confidence_uses_group_line_fallback()
_test_owner_cohort_uses_one_directed_orientation()
_test_cross_layout_repeat_keeps_quarter_turn_parity()
_test_cross_layout_repeat_stays_near_with_unrelated_groups()
_test_model_distance_precedes_material_for_small_owner()
_test_topology_owner_is_not_rejected_by_fallback_capacity()
_test_fallback_capacity_is_independent_per_owner()
_test_owner_cells_keep_many_fragments_local()
_test_repeat_owner_cohorts_stay_near_in_large_component()
_test_split_weapon_repeat_owner_graph_is_solved()
_test_split_weapon_dual_domain_owner_graph_is_solved()
_test_affinity_search_budget_is_shared_by_component()
_test_centrosymmetric_repeats_remain_180_equivalent()
_test_tiny_float32_similarity_tolerance()
_test_quantized_winding_stabilization_and_audit_fields()
_test_non_repeat_cardinal_switch()
_test_loose_vertex_does_not_change_radius()
print("VUV_GROUP_LAYOUT_REGRESSION_OK")
