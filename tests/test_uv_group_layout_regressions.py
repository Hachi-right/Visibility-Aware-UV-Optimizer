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


def _test_structure_adjacency_builds_bounded_macro_group():
    chain = (
        _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1)),
        _island(1, (1.0, 0.0, 0.0, 2.0, 1.0, 0.1)),
        _island(2, (2.0, 0.0, 0.0, 3.0, 1.0, 0.1)),
    )
    remote = _island(3, (15.0, 0.0, 0.0, 16.0, 1.0, 0.1))
    adjacency = (
        VUV.IslandAdjacency(0, 1, 1, 1.0, 1),
        VUV.IslandAdjacency(1, 2, 1, 1.0, 1),
    )
    options = VUV.GroupLayoutOptions(
        structure_group_enabled=True,
        structure_group_max_members=4,
        structure_group_max_diameter_ratio=0.20,
    )
    groups = VUV.build_layout_groups(
        chain + (remote,),
        (),
        object_diagonal=16.0,
        options=options,
        adjacency=adjacency,
    )
    macro = next(group for group in groups if 0 in group.member_ids)
    assert set(macro.member_ids) == {0, 1, 2}, macro.member_ids
    assert macro.reason == "MACRO_ADJACENT", macro.reason
    assert macro.affinity_pairs == ((0, 1), (1, 2)), macro.affinity_pairs
    assert all(3 not in group.member_ids for group in groups if group is macro)

    oriented = {
        0: _rectangle(0, 1.0, 1.0),
        1: _rectangle(10, 0.8, 1.6),
        2: _rectangle(20, 1.2, 0.6),
        3: _rectangle(30, 2.0, 2.0),
    }
    gap = 0.05
    plan = VUV._pack_plan(
        oriented,
        groups,
        gap=gap,
        allow_group_quarter_turn=False,
    )
    bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    for left_id, right_id in macro.affinity_pairs:
        near_limit = max(
            _bounds_diagonal(bounds[left_id]),
            _bounds_diagonal(bounds[right_id]),
        ) + 2.0 * gap
        assert _bounds_distance(
            bounds[left_id], bounds[right_id]
        ) <= near_limit + 1.0e-9, (left_id, right_id, bounds)

    disabled_groups = VUV.build_layout_groups(
        chain + (remote,),
        (),
        object_diagonal=16.0,
        options=VUV.GroupLayoutOptions(structure_group_enabled=False),
        adjacency=adjacency,
    )
    assert len(disabled_groups) == 4, [
        group.member_ids for group in disabled_groups
    ]
    assert all(not group.affinity_pairs for group in disabled_groups)


def _test_repeat_micro_chain_does_not_merge_whole_asset():
    owners = (
        _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1)),
        _island(10, (10.0, 0.0, 0.0, 11.0, 1.0, 0.1)),
        _island(20, (20.0, 0.0, 0.0, 21.0, 1.0, 0.1)),
    )
    fragments = (
        _island(1, (1.01, 0.1, 0.0, 1.05, 0.15, 0.05), small=True, neighbors=(0,)),
        _island(11, (9.95, 0.1, 0.0, 9.99, 0.15, 0.05), small=True, neighbors=(10,)),
        _island(12, (11.01, 0.2, 0.0, 11.05, 0.25, 0.05), small=True, neighbors=(10,)),
        _island(21, (19.95, 0.2, 0.0, 19.99, 0.25, 0.05), small=True, neighbors=(20,)),
    )
    repeats = (
        VUV.RepeatGroup(0, (1, 11), "TRANSLATED", 0.99, "left-link"),
        VUV.RepeatGroup(1, (12, 21), "TRANSLATED", 0.99, "right-link"),
    )
    groups = VUV.build_layout_groups(
        owners + fragments,
        repeats,
        object_diagonal=21.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=0.01,
            repeat_group_max_members=12,
            repeat_group_max_diameter_ratio=0.20,
        ),
    )
    assert not any(
        {0, 10, 20}.issubset(set(group.anchor_ids)) for group in groups
    ), [group.anchor_ids for group in groups]
    assert max(len(group.anchor_ids) for group in groups) == 2


def _test_small_topology_chain_records_owner_affinity():
    # The middle fragments have no direct anchor edge.  Their real mesh
    # chain must still resolve to the two repeated owners, and the owner pair
    # (rather than the small ids) is what the rigid packer consumes.
    left = _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1))
    right = _island(10, (4.0, 0.0, 0.0, 5.0, 1.0, 0.1))
    first = _island(
        1,
        (1.01, 0.2, 0.0, 1.08, 0.27, 0.05),
        small=True,
        neighbors=(0, 2),
    )
    middle = _island(
        2,
        (2.35, 0.2, 0.0, 2.42, 0.27, 0.05),
        small=True,
        neighbors=(1, 3),
    )
    last = _island(
        3,
        (3.92, 0.2, 0.0, 3.99, 0.27, 0.05),
        small=True,
        neighbors=(2, 10),
    )
    adjacency = (
        VUV.IslandAdjacency(0, 1, 1, 0.10, 0),
        VUV.IslandAdjacency(1, 2, 1, 0.10, 0),
        VUV.IslandAdjacency(2, 3, 1, 0.10, 0),
        VUV.IslandAdjacency(3, 10, 1, 0.10, 0),
    )
    repeat = VUV.RepeatGroup(0, (0, 10), "TRANSLATED", 0.99, "owners")
    groups = VUV.build_layout_groups(
        (left, right, first, middle, last),
        (repeat,),
        object_diagonal=5.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            structure_group_max_members=12,
            structure_group_max_degree=2,
            structure_group_max_diameter_ratio=0.20,
        ),
        adjacency=adjacency,
    )
    assert len(groups) == 1, [group.member_ids for group in groups]
    group = groups[0]
    assert set(group.member_ids) == {0, 1, 2, 3, 10}
    assert (0, 10) in group.affinity_pairs, group.to_dict()
    assert all(
        small_id not in pair
        for pair in group.affinity_pairs
        for small_id in (1, 2, 3)
    ), group.affinity_pairs

    oriented = {
        0: _rectangle(0, 0.8, 0.8),
        1: _rectangle(10, 0.12, 0.10),
        2: _rectangle(20, 0.12, 0.10),
        3: _rectangle(30, 0.12, 0.10),
        10: _rectangle(100, 0.8, 0.8),
    }
    plan = VUV._pack_plan(
        oriented, groups, gap=0.04, allow_group_quarter_turn=False
    )
    bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    assert _bounds_distance(bounds[0], bounds[10]) <= (
        max(_bounds_diagonal(bounds[0]), _bounds_diagonal(bounds[10]))
        + 2.0 * 0.04
        + 1.0e-9
    ), bounds


def _test_topology_continuity_cross_group_merge_is_bounded():
    left = _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1))
    right = _island(10, (1.5, 0.0, 0.0, 2.5, 1.0, 0.1))
    left_small = _island(
        1,
        (1.01, 0.2, 0.0, 1.08, 0.27, 0.05),
        small=True,
        neighbors=(0, 2),
    )
    right_small = _island(
        11,
        (1.42, 0.2, 0.0, 1.49, 0.27, 0.05),
        small=True,
        neighbors=(1, 10),
    )
    adjacency = (
        VUV.IslandAdjacency(0, 1, 1, 0.10, 0),
        VUV.IslandAdjacency(1, 11, 1, 0.10, 0),
        VUV.IslandAdjacency(11, 10, 1, 0.10, 0),
    )
    islands = (left, right, left_small, right_small)

    merged = VUV.build_layout_groups(
        islands,
        (),
        object_diagonal=3.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            structure_group_max_members=4,
            structure_group_max_diameter_ratio=1.0,
        ),
        adjacency=adjacency,
    )
    assert len(merged) == 1, [group.member_ids for group in merged]
    assert merged[0].reason == "TOPOLOGY_CONTINUITY", merged[0].to_dict()
    assert merged[0].affinity_pairs == ((0, 10),), merged[0].to_dict()

    over_budget = VUV.build_layout_groups(
        islands,
        (),
        object_diagonal=3.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            structure_group_max_members=3,
            structure_group_max_diameter_ratio=1.0,
        ),
        adjacency=adjacency,
    )
    assert len(over_budget) == 2, [group.member_ids for group in over_budget]
    assert all(not group.affinity_pairs for group in over_budget)

    over_diameter = VUV.build_layout_groups(
        islands,
        (),
        object_diagonal=3.0,
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=1.0,
            structure_group_max_members=4,
            structure_group_max_diameter_ratio=0.20,
        ),
        adjacency=adjacency,
    )
    assert len(over_diameter) == 2, [group.member_ids for group in over_diameter]
    assert all(not group.affinity_pairs for group in over_diameter)


def _test_geometry_continuity_blocks_are_bounded_and_packed_nearby():
    # Exercise the coarser block pass directly with pre-existing layout groups
    # so the earlier structure pass cannot consume the whole chain first.
    islands = tuple(
        _island(
            island_id,
            (
                float(island_id), 0.0, 0.0,
                float(island_id + 1), 1.0, 0.1,
            ),
        )
        for island_id in range(8)
    )
    groups = [
        {
            "members": [island.island_id],
            "anchors": [island.island_id],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        }
        for island in islands
    ]
    adjacency = tuple(
        VUV.IslandAdjacency(index, index + 1, 1, 1.0, 0)
        for index in range(7)
    )
    settings = VUV.GroupLayoutOptions(
        continuity_block_target_members=3,
        continuity_block_max_members=4,
        structure_group_max_diameter_ratio=1.0,
    )
    blocked = VUV._build_geometry_continuity_blocks(
        groups,
        islands,
        adjacency,
        object_diagonal=8.0,
        settings=settings,
    )
    assert len(blocked) == 2, [item["members"] for item in blocked]
    assert all(len(item["members"]) <= 4 for item in blocked), blocked
    assert all(item.get("_continuity_component") for item in blocked), blocked
    assert any(item.get("_continuity_peers") for item in blocked), blocked

    key_to_id = {
        int(item["_continuity_key"]): index
        for index, item in enumerate(blocked)
    }
    layout_groups = []
    for index, item in enumerate(blocked):
        peers = tuple(sorted(
            key_to_id[int(peer)]
            for peer in item.get("_continuity_peers", ())
            if int(peer) in key_to_id
        ))
        layout_groups.append(VUV.LayoutGroup(
            group_id=index,
            member_ids=tuple(item["members"]),
            reason=item["reason"],
            anchor_ids=tuple(item["anchors"]),
            continuity_component=int(item["_continuity_component"]),
            continuity_peers=peers,
        ))
    oriented = {
        island.island_id: _rectangle(island.island_id * 10, 0.8, 0.5)
        for island in islands
    }
    plan = VUV._pack_plan(
        oriented,
        layout_groups,
        gap=0.04,
        allow_group_quarter_turn=False,
    )
    placements = plan.group_placements
    assert placements[0].x < placements[1].x or placements[0].y != placements[1].y
    distance = VUV._placement_distance(placements[0], placements[1])
    near_limit = max(
        math.hypot(placements[0].width, placements[0].height),
        math.hypot(placements[1].width, placements[1].height),
    ) + 2.0 * 0.04
    assert distance <= near_limit + 1.0e-9, (placements, distance, near_limit)


def _test_continuity_block_uses_coarse_diameter_ratio():
    """The second-layer continuity envelope is not locked to 0.20."""

    islands = (
        _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1)),
        _island(1, (1.5, 0.0, 0.0, 2.5, 1.0, 0.1)),
    )
    groups = [
        {
            "members": [island.island_id],
            "anchors": [island.island_id],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        }
        for island in islands
    ]
    adjacency = (VUV.IslandAdjacency(0, 1, 1, 1.0, 0),)
    blocked = VUV._build_geometry_continuity_blocks(
        groups,
        islands,
        adjacency,
        object_diagonal=4.0,
        settings=VUV.GroupLayoutOptions(
            # The legacy owner envelope alone (0.20 * 4) is too small for
            # this pair; the continuity envelope intentionally is not.
            structure_group_max_diameter_ratio=0.20,
            continuity_block_max_diameter_ratio=0.80,
            continuity_block_target_members=2,
            continuity_block_max_members=4,
        ),
    )
    assert len(blocked) == 1, [item["members"] for item in blocked]
    assert set(blocked[0]["members"]) == {0, 1}, blocked


def _test_soft_topology_pairs_are_bounded_and_deterministic():
    """Seam/material-separated contacts become one-shot layout pairs only."""

    islands = (
        _island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 0.1), materials=(0,)),
        _island(1, (1.2, 0.0, 0.0, 2.2, 1.0, 0.1), materials=(1,)),
        _island(2, (4.0, 0.0, 0.0, 5.0, 1.0, 0.1), materials=(2,)),
        _island(3, (5.2, 0.0, 0.0, 6.2, 1.0, 0.1), materials=(3,)),
    )
    groups = [
        {
            "members": [island.island_id],
            "anchors": [island.island_id],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        }
        for island in islands
    ]
    # Every edge is an all-seam boundary between different materials, so the
    # strict structure score rejects it.  The relaxed score should still keep
    # the two real local pairs together.
    adjacency = (
        VUV.IslandAdjacency(0, 1, 1, 0.20, 1),
        VUV.IslandAdjacency(2, 3, 1, 0.20, 1),
    )
    settings = VUV.GroupLayoutOptions(
        continuity_block_target_members=2,
        continuity_block_max_members=4,
        continuity_component_max_groups=2,
        continuity_component_max_members=4,
        continuity_block_max_diameter_ratio=0.80,
    )
    blocked = VUV._build_geometry_continuity_blocks(
        groups,
        islands,
        adjacency,
        object_diagonal=8.0,
        settings=settings,
    )
    assert len(blocked) == 4, [item["members"] for item in blocked]
    peer_pairs = {
        tuple(sorted((int(item["_continuity_key"]), int(peer))))
        for item in blocked
        for peer in item.get("_continuity_peers", ())
        if int(item["_continuity_key"]) < int(peer)
    }
    assert peer_pairs == {(0, 1), (2, 3)}, blocked
    component_by_key = {
        int(item["_continuity_key"]): int(item["_continuity_component"])
        for item in blocked
        if item.get("_continuity_component") is not None
    }
    assert len(component_by_key) == 4, blocked
    assert len(set(component_by_key.values())) == 2, blocked

    # The same soft relation is rejected when either component envelope is
    # exceeded; no merge or affinity is allowed to bypass those limits.
    rejected = VUV._build_geometry_continuity_blocks(
        groups,
        islands,
        adjacency,
        object_diagonal=8.0,
        settings=VUV.GroupLayoutOptions(
            continuity_block_target_members=2,
            continuity_block_max_members=4,
            continuity_component_max_groups=2,
            continuity_component_max_members=2,
            continuity_block_max_diameter_ratio=0.05,
        ),
    )
    assert all(item.get("_continuity_component") is None for item in rejected), rejected
    assert all(not item.get("_continuity_peers") for item in rejected), rejected


def _test_cross_component_peer_uses_local_endpoint_bounds():
    """A local boundary to a rigid block is retained after component gating."""

    islands = tuple(
        _island(
            island_id,
            (float(island_id) * 1.2, 0.0, 0.0,
             float(island_id) * 1.2 + 1.0, 1.0, 0.1),
        )
        for island_id in range(5)
    )
    # The first two pre-existing groups form a strong two-block component;
    # the final group is adjacent to its right endpoint but remains outside
    # the full component AABB.  The soft peer must use endpoints 3/4 rather
    # than rejecting the relation because of the distant member 0.
    groups = [
        {
            "members": [0, 1],
            "anchors": [0, 1],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        },
        {
            "members": [2, 3],
            "anchors": [2, 3],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        },
        {
            "members": [4],
            "anchors": [4],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
            "affinity_pairs": [],
        },
    ]
    adjacency = (
        VUV.IslandAdjacency(1, 2, 1, 1.0, 0),
        VUV.IslandAdjacency(3, 4, 1, 1.0, 0),
    )
    blocked = VUV._build_geometry_continuity_blocks(
        groups,
        islands,
        adjacency,
        object_diagonal=6.0,
        settings=VUV.GroupLayoutOptions(
            continuity_block_target_members=2,
            continuity_block_max_members=2,
            continuity_component_max_groups=2,
            continuity_component_max_members=4,
            continuity_block_max_diameter_ratio=0.80,
            structure_group_max_diameter_ratio=0.80,
        ),
    )
    by_key = {int(item["_continuity_key"]): item for item in blocked}
    assert by_key[0].get("_continuity_component") is not None, blocked
    assert by_key[1].get("_continuity_component") == by_key[0].get(
        "_continuity_component"
    ), blocked
    assert by_key[2].get("_continuity_component") is None, blocked
    assert 2 in set(by_key[1].get("_continuity_peers", ())), blocked
    assert 1 in set(by_key[2].get("_continuity_peers", ())), blocked

    key_to_id = {
        int(item["_continuity_key"]): index
        for index, item in enumerate(blocked)
    }
    layout_groups = []
    for index, item in enumerate(blocked):
        peers = tuple(sorted(
            key_to_id[int(peer)]
            for peer in item.get("_continuity_peers", ())
            if int(peer) in key_to_id
        ))
        layout_groups.append(VUV.LayoutGroup(
            group_id=index,
            member_ids=tuple(item["members"]),
            reason=item["reason"],
            anchor_ids=tuple(item["anchors"]),
            continuity_component=item.get("_continuity_component"),
            continuity_peers=peers,
        ))
    oriented = {
        island.island_id: _rectangle(island.island_id * 10, 0.7, 0.45)
        for island in islands
    }
    plan = VUV._pack_plan(
        oriented,
        layout_groups,
        gap=0.03,
        allow_group_quarter_turn=False,
    )
    left = plan.group_placements[1]
    right = plan.group_placements[2]
    distance = VUV._placement_distance(left, right)
    near_limit = max(
        math.hypot(left.width, left.height),
        math.hypot(right.width, right.height),
    ) + 2.0 * 0.03
    assert distance <= near_limit + 1.0e-9, (
        distance,
        near_limit,
        plan.group_placements,
    )


def _test_continuity_component_uses_centroid_ordered_rigid_grid():
    """Continuity groups form one ordered, translation-only top-level block."""

    groups = tuple(
        VUV.LayoutGroup(
            group_id=index,
            member_ids=(index,),
            reason="TOPOLOGY_CONTINUITY",
            anchor_ids=(index,),
            continuity_component=17,
        )
        for index in range(3)
    )
    oriented = {
        0: _rectangle(0, 0.90, 0.35),
        1: _rectangle(10, 0.45, 0.80),
        2: _rectangle(20, 0.60, 0.55),
    }
    # Deliberately make source ids disagree with model-space order.
    centroids = {
        0: Vector((10.0, 0.0, 0.0)),
        1: Vector((20.0, 0.0, 0.0)),
        2: Vector((-5.0, 0.0, 0.0)),
    }
    gap = 0.03
    plan = VUV._pack_plan(
        oriented,
        groups,
        gap=gap,
        allow_group_quarter_turn=True,
        group_model_centroids=centroids,
    )
    placements = plan.group_placements
    assert set(placements) == {0, 1, 2}
    assert all(not item.quarter_turn for item in placements.values()), placements
    expected_sizes = {0: (0.90, 0.35), 1: (0.45, 0.80), 2: (0.60, 0.55)}
    for group_id, placement in placements.items():
        width, height = expected_sizes[group_id]
        assert abs(placement.width - width) <= 1.0e-6, placements
        assert abs(placement.height - height) <= 1.0e-6, placements
    # The regular grid is row-major in the supplied centroid order.  A
    # top-level translation must not change that order.
    grid_order = tuple(sorted(
        placements,
        key=lambda group_id: (
            round(placements[group_id].y, 10),
            round(placements[group_id].x, 10),
        ),
    ))
    assert grid_order == (2, 0, 1), (grid_order, placements)
    for left_index, left_id in enumerate(sorted(placements)):
        for right_id in sorted(placements)[left_index + 1:]:
            assert VUV._placements_clear(
                placements[left_id], placements[right_id], gap
            ), (left_id, right_id, placements)


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


def _topology_object(name, vertices, faces, uv_polygons):
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
    options = VUV.GroupLayoutOptions(
        allow_group_quarter_turn=True,
        align_repeat_local_frame=True,
    )
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

        result = VUV.layout_active_uv(obj, options)
        assert result.after_audit["valid"]
        assert not result.after_audit["overlap"]
        assert result.after_audit["negative"] == 0
        assert result.analysis.face_to_island == before.face_to_island
        assert result.quality_metrics["repeat_local_frame_valid"]
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


def _test_near_180_directed_conflict_uses_line_equivalence():
    # The two landmarks straddle the +/-180 wrap, but both disagree with the
    # geometric -45-degree panel axis by more than the 3-degree cardinal
    # tolerance.  The component must therefore use its modulo-180 line
    # contract instead of forcing a false modulo-360 sign.
    first = math.radians(139.9)
    second = math.radians(-40.0)
    island_a = _island(
        47,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=math.radians(-45.0),
        direction_angle=first,
        direction_confidence=0.9,
    )
    island_b = _island(
        48,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=math.radians(-45.0),
        direction_angle=second,
        direction_confidence=0.9,
    )
    repeat = VUV.RepeatGroup(0, (47, 48), "TRANSLATED", 0.99, "repeat")
    analysis = _analysis((island_a, island_b), (repeat,))
    settings = VUV.GroupLayoutOptions(
        directed_cardinal_tolerance=math.radians(3.0),
    )
    VUV._apply_orientation_policy(analysis, settings)
    assert len(analysis.orientation_downgrades) == 1
    assert analysis.orientation_downgrades[0]["policy"] == (
        "center_symmetric_modulo_180"
    )
    assert all(item.direction_mode == "center_symmetric" for item in analysis.islands)

    rotations = VUV._orientation_angles(analysis, settings)
    resolved_a = VUV._line_angle_wrap(
        island_a.principal_angle + rotations[47]
    )
    resolved_b = VUV._line_angle_wrap(
        island_b.principal_angle + rotations[48]
    )
    assert abs(VUV._line_angle_wrap(resolved_a - resolved_b)) < 1.0e-6
    assert abs(VUV._line_angle_wrap(resolved_a - math.pi * 0.5)) < 1.0e-6


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
    assert abs(
        VUV._line_angle_wrap(
            angle + rotations[42] - math.pi * 0.5
        )
    ) < 1.0e-12
    assert abs(
        VUV._line_angle_wrap(
            -angle + rotations[43] - math.pi * 0.5
        )
    ) < 1.0e-12


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


def _test_owner_attachment_rack_follows_model_centroids_without_rotation():
    owner = _island(500, (0.0, 0.0, 0.0, 2.0, 2.0, 0.2))
    centroid_order = (509, 503, 511, 502, 507, 505)
    fragments = tuple(
        _island(
            island_id,
            (
                2.05 + position * 0.02,
                0.1 + position * 0.03,
                0.0,
                2.06 + position * 0.02,
                0.11 + position * 0.03,
                0.05,
            ),
            small=True,
            neighbors=(500,),
        )
        for position, island_id in enumerate(centroid_order)
    )
    shuffled = (
        fragments[4],
        owner,
        fragments[1],
        fragments[5],
        fragments[0],
        fragments[3],
        fragments[2],
    )
    options = VUV.GroupLayoutOptions(proximity_radius_ratio=1.0)
    first = VUV.build_layout_groups(
        shuffled,
        (),
        object_diagonal=3.0,
        options=options,
    )
    second = VUV.build_layout_groups(
        tuple(reversed(shuffled)),
        (),
        object_diagonal=3.0,
        options=options,
    )
    first_group = next(group for group in first if 500 in group.anchor_ids)
    second_group = next(group for group in second if 500 in group.anchor_ids)
    assert first_group.small_member_ids == centroid_order, first_group.to_dict()
    assert second_group.small_member_ids == centroid_order, second_group.to_dict()

    sizes = {
        509: (0.19, 0.11),
        503: (0.13, 0.17),
        511: (0.21, 0.09),
        502: (0.15, 0.14),
        507: (0.12, 0.19),
        505: (0.18, 0.10),
    }
    oriented = {500: _rectangle(5000, 1.0, 1.0)}
    for island_id in centroid_order:
        oriented[island_id] = _rectangle(
            island_id * 10,
            sizes[island_id][0],
            sizes[island_id][1],
        )
    plan = VUV._pack_plan(
        oriented,
        first,
        gap=0.04,
        allow_group_quarter_turn=False,
    )
    bounds = {
        island_id: VUV._uv_bounds(coordinates.values())
        for island_id, coordinates in plan.coordinates.items()
    }
    owner_bounds = bounds[500]
    all_right = all(
        bounds[island_id][0] >= owner_bounds[2] + 0.04 - 1.0e-6
        for island_id in centroid_order
    )
    all_above = all(
        bounds[island_id][1] >= owner_bounds[3] + 0.04 - 1.0e-6
        for island_id in centroid_order
    )
    assert all_right or all_above, bounds

    rack_order = tuple(sorted(
        centroid_order,
        key=lambda island_id: (
            round(bounds[island_id][1], 9),
            round(bounds[island_id][0], 9),
        ),
    ))
    assert rack_order == centroid_order, (rack_order, centroid_order, bounds)
    for island_id in centroid_order:
        width = bounds[island_id][2] - bounds[island_id][0]
        height = bounds[island_id][3] - bounds[island_id][1]
        assert abs(width - sizes[island_id][0]) < 1.0e-6
        assert abs(height - sizes[island_id][1]) < 1.0e-6


def _test_regular_owner_attachment_grid_preserves_order_and_density():
    """A compact regular rack may replace a ragged shelf without rotation."""

    rectangles = tuple(
        VUV._Rect(index, 0.04, 0.04, index)
        for index in range(3)
    )
    gap = 0.003
    shelf, shelf_width, shelf_height = VUV._best_shelf_pack(
        rectangles,
        gap,
        allow_rotate=False,
        preserve_order=True,
    )
    chosen, chosen_width, chosen_height = VUV._select_owner_attachment_rack(
        0.15,
        0.15,
        rectangles,
        gap,
    )
    tolerance = max(gap * 1.0e-4, 1.0e-8)
    shelf_alignment = VUV._rack_boundary_alignment(shelf, tolerance)
    chosen_alignment = VUV._rack_boundary_alignment(chosen, tolerance)
    assert chosen_alignment >= shelf_alignment + 0.10
    shelf_extent = VUV._owner_cell_extent(
        0.15,
        0.15,
        shelf_width,
        shelf_height,
        gap,
    )
    chosen_extent = VUV._owner_cell_extent(
        0.15,
        0.15,
        chosen_width,
        chosen_height,
        gap,
    )
    assert chosen_extent[0] <= shelf_extent[0] + 1.0e-9
    assert chosen_extent[1] <= shelf_extent[1] + 1.0e-9
    assert all(not placement.quarter_turn for placement in chosen.values())
    # Row-major insertion order is retained, so a UV editor can read the rack
    # in model-centroid order after the regularization pass.
    ordered = sorted(
        chosen.values(),
        key=lambda placement: (
            round(float(placement.y), 9),
            round(float(placement.x), 9),
        ),
    )
    assert tuple(placement.x for placement in ordered) == tuple(
        chosen[index].x for index in sorted(chosen, key=lambda value: value)
    )


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
        options=VUV.GroupLayoutOptions(
            proximity_radius_ratio=0.01,
            # This test deliberately exercises one full-asset affinity graph.
            # Production defaults use a smaller bound to reject transitive
            # repeat chains; opt in here so the solver regression keeps its
            # original scope.
            repeat_group_max_diameter_ratio=1.0,
        ),
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


def _map_similarity(point, source_a, source_b, target_a, target_b):
    source_delta = source_b - source_a
    target_delta = target_b - target_a
    denominator = source_delta.length_squared
    real = source_delta.dot(target_delta) / denominator
    imaginary = (
        source_delta.x * target_delta.y
        - source_delta.y * target_delta.x
    ) / denominator
    relative = point - source_a
    return target_a + Vector((
        relative.x * real - relative.y * imaginary,
        relative.x * imaginary + relative.y * real,
    ))


def _quantized_welded_strip(name, face_count, geometry, source_uv, post_uv):
    vertices = [Vector(point) for point in geometry]
    source_points = [Vector(point) for point in source_uv]
    post_points = [Vector(point) for point in post_uv]
    faces = [(0, 1, 2)]
    edge = source_points[1] - source_points[0]
    direction = edge.normalized()
    left = Vector((-direction.y, direction.x))
    cross = (
        edge.x * (source_points[2] - source_points[0]).y
        - edge.y * (source_points[2] - source_points[0]).x
    )
    outward = left * (-1.0 if cross > 0.0 else 1.0)
    previous_left, previous_right = 0, 1
    for row in range(1, (face_count - 2) // 2 + 1):
        left_index = len(vertices)
        right_index = left_index + 1
        vertices.extend((
            vertices[0] + Vector((0.025 * row, 0.0, 0.0)),
            vertices[1] + Vector((0.025 * row, 0.0, 0.0)),
        ))
        source_points.extend((
            source_points[0] + outward * (0.0025 * row),
            source_points[1] + outward * (0.0025 * row),
        ))
        for index in (left_index, right_index):
            post_points.append(_map_similarity(
                source_points[index],
                source_points[0],
                source_points[1],
                post_points[0],
                post_points[1],
            ))
        faces.extend((
            (previous_right, previous_left, left_index),
            (previous_right, left_index, right_index),
        ))
        previous_left, previous_right = left_index, right_index

    cap_index = len(vertices)
    vertices.append(
        (vertices[previous_left] + vertices[previous_right]) * 0.5
        + Vector((0.025, 0.0, 0.0))
    )
    source_points.append(
        (source_points[previous_left] + source_points[previous_right]) * 0.5
        + outward * 0.0025
    )
    post_points.append(_map_similarity(
        source_points[cap_index],
        source_points[0],
        source_points[1],
        post_points[0],
        post_points[1],
    ))
    faces.append((previous_right, previous_left, cap_index))
    assert len(faces) == face_count
    obj, mesh = _topology_object(
        name,
        vertices,
        faces,
        [tuple(post_points[index] for index in face) for face in faces],
    )
    source_loops = [
        source_points[int(loop.vertex_index)].copy() for loop in mesh.loops
    ]
    return obj, mesh, source_loops


def _welded_by_loop(groups):
    return {
        int(loop_index): tuple(int(value) for value in members)
        for members in groups.values()
        for loop_index in members
    }


def _test_welded_quantized_repair_preserves_real_chart_forms():
    fixtures = (
        (
            40,
            (
                (0.26565274596214294, -1.0228915214538574, 0.13709914684295654),
                (0.26565274596214294, -0.7888550758361816, 0.13709914684295654),
                (0.26565274596214294, -0.7611002922058105, 0.13709938526153564),
            ),
            (
                (0.19679389894008636, 0.33462753891944885),
                (0.23845897614955902, 0.33463042974472046),
                (0.24340012669563293, 0.3346308171749115),
            ),
            (
                (0.41963106393814087, 0.8920152187347412),
                (0.4337378442287445, 0.8920161724090576),
                (0.4354107975959778, 0.8920162320137024),
            ),
        ),
        (
            42,
            (
                (-0.2786858379840851, -0.7611002922058105, 0.13709962368011475),
                (-0.2786858379840851, -0.7888550758361816, 0.13709938526153564),
                (-0.2786858379840851, -1.0228915214538574, 0.13709962368011475),
            ),
            (
                (0.4363405406475067, 0.18813258409500122),
                (0.4410538077354431, 0.18813206255435944),
                (0.4807974696159363, 0.1881280541419983),
            ),
            (
                (0.469956636428833, 0.8852251172065735),
                (0.47155243158340454, 0.8852249979972839),
                (0.4850086569786072, 0.8852236270904541),
            ),
        ),
    )
    for face_count, geometry, source_uv, post_uv in fixtures:
        obj, mesh, source = _quantized_welded_strip(
            "VUV_Quantized_Welded_{}".format(face_count),
            face_count,
            geometry,
            source_uv,
            post_uv,
        )
        try:
            uv_layer = mesh.uv_layers.active
            face_map = tuple(0 for _face in mesh.polygons)
            assert len(VUV._nonpositive_winding_records(
                mesh, uv_layer, face_map
            )) == 1
            before = [item.uv.copy() for item in uv_layer.data]
            groups, _loop_faces = VUV._source_welded_loop_groups(
                mesh, source, face_map, 1.0e-6
            )
            direction_checks = []
            repaired = VUV._try_welded_quantized_winding_repair(
                mesh,
                uv_layer,
                0,
                {index: point.copy() for index, point in enumerate(before)},
                face_map,
                source,
                _welded_by_loop(groups),
                1.0e-6,
                direction_validator=lambda: direction_checks.append(True) or True,
            )
            assert repaired is not None, face_count
            assert direction_checks, face_count
            assert not VUV._nonpositive_winding_records(
                mesh, uv_layer, face_map
            )
            VUV._validate_source_island_partition(
                mesh, uv_layer, face_map, 1.0e-6
            )
            moved = [
                index for index, point in enumerate(before)
                if (uv_layer.data[index].uv - point).length_squared > 0.0
            ]
            assert len(moved) >= 2, (face_count, moved)
            assert {
                int(mesh.loops[index].vertex_index) for index in moved
            } == {1}, (face_count, moved)
        finally:
            _remove_object_and_mesh(obj, mesh)


def _test_welded_repair_boundaries_and_full_rollback():
    vertices = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
    )
    faces = ((0, 1, 2), (1, 3, 2))
    by_vertex = tuple(Vector((point[0], point[1])) for point in vertices)
    obj, mesh = _topology_object(
        "VUV_Welded_Repair_Boundaries",
        vertices,
        faces,
        [tuple(by_vertex[index] for index in face) for face in faces],
    )
    try:
        source = [
            by_vertex[int(loop.vertex_index)].copy() for loop in mesh.loops
        ]

        def loop_for(face_index, vertex_index):
            return next(
                int(index) for index in mesh.polygons[face_index].loop_indices
                if int(mesh.loops[index].vertex_index) == vertex_index
            )

        left, right = loop_for(0, 1), loop_for(1, 1)

        def joined(source_uvs, face_map):
            groups, _loop_faces = VUV._source_welded_loop_groups(
                mesh, source_uvs, face_map, 1.0e-6
            )
            lookup = _welded_by_loop(groups)
            return lookup[left] == lookup[right]

        assert joined(source, (0, 0))
        shared = next(
            edge for edge in mesh.edges
            if set(map(int, edge.vertices)) == {1, 2}
        )
        shared.use_seam = True
        assert not joined(source, (0, 0))
        shared.use_seam = False
        split = [point.copy() for point in source]
        split[right] += Vector((0.01, 0.0))
        assert not joined(split, (0, 0))
        assert not joined(source, (0, 1))
    finally:
        _remove_object_and_mesh(obj, mesh)

    geometry = (
        (-0.2786858379840851, -0.7611002922058105, 0.13709962368011475),
        (-0.2786858379840851, -0.7888550758361816, 0.13709938526153564),
        (-0.2786858379840851, -1.0228915214538574, 0.13709962368011475),
    )
    source_uv = (
        (0.4363405406475067, 0.18813258409500122),
        (0.4410538077354431, 0.18813206255435944),
        (0.4807974696159363, 0.1881280541419983),
    )
    post_uv = (
        (0.469956636428833, 0.8852251172065735),
        (0.47155243158340454, 0.8852249979972839),
        (0.4850086569786072, 0.8852236270904541),
    )
    obj, mesh, source = _quantized_welded_strip(
        "VUV_Welded_Repair_Rollback",
        42,
        geometry,
        source_uv,
        post_uv,
    )
    try:
        uv_layer = mesh.uv_layers.active
        face_map = tuple(0 for _face in mesh.polygons)
        before = [item.uv.copy() for item in uv_layer.data]
        groups, _loop_faces = VUV._source_welded_loop_groups(
            mesh, source, face_map, 1.0e-6
        )
        direction_checks = []
        repaired = VUV._try_welded_quantized_winding_repair(
            mesh,
            uv_layer,
            0,
            {index: point.copy() for index, point in enumerate(before)},
            face_map,
            source,
            _welded_by_loop(groups),
            1.0e-6,
            direction_validator=lambda: direction_checks.append(True) and False,
        )
        assert repaired is None
        assert direction_checks
        assert all(
            (item.uv - point).length_squared == 0.0
            for item, point in zip(uv_layer.data, before)
        )
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_writeback_snap_preserves_source_island_count():
    obj, mesh = _topology_object(
        "VUV_Writeback_Continuity",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 1.0, 0.0),
        ),
        ((0, 1, 2, 3), (1, 4, 5, 2)),
        (
            ((0.0, 0.0), (0.4, 0.0), (0.4, 0.4), (0.0, 0.4)),
            ((0.4, 0.0), (0.8, 0.0), (0.8, 0.4), (0.4, 0.4)),
        ),
    )
    options = VUV.GroupLayoutOptions(
        uv_epsilon=1.0e-7,
        align_geometry_direction=False,
        align_non_repeat_cardinal=False,
        small_island_scale_boost=1.0,
    )
    original_plan = VUV._plan_with_margin
    try:
        source = VUV.analyze_active_uv(obj, options)
        assert len(source.islands) == 1, source.to_dict()
        shared_right_loops = [
            int(loop_index)
            for loop_index in mesh.polygons[1].loop_indices
            if int(mesh.loops[loop_index].vertex_index) in {1, 2}
        ]
        assert len(shared_right_loops) == 2, shared_right_loops

        def perturbed_plan(*args, **kwargs):
            plan, fitted, scale = original_plan(*args, **kwargs)
            for loop_index in shared_right_loops:
                fitted[0][loop_index] = (
                    fitted[0][loop_index] + Vector((2.5e-7, 0.0))
                )
            return plan, fitted, scale

        VUV._plan_with_margin = perturbed_plan
        result = VUV.layout_active_uv(obj, options)
        replay, _face_map, _adjacency, _diagonal = (
            VUV.compute_active_uv_islands(obj, options)
        )
        assert len(replay) == len(result.analysis.islands) == 1, (
            len(replay), len(result.analysis.islands)
        )
    finally:
        VUV._plan_with_margin = original_plan
        _remove_object_and_mesh(obj, mesh)


def _test_writeback_snap_skips_seams_and_non_manifold_edges():
    def exercise(name, vertices, faces, uv_polygons, mark_seam=False):
        obj, mesh = _topology_object(name, vertices, faces, uv_polygons)
        try:
            source = [item.uv.copy() for item in mesh.uv_layers.active.data]
            face_to_island = tuple(0 for _face in mesh.polygons)
            fitted = {0: {
                int(loop.index): source[int(loop.index)].copy()
                for loop in mesh.loops
            }}
            shared_edge = next(
                edge for edge in mesh.edges
                if set(map(int, edge.vertices)) == {0, 1}
            )
            shared_edge.use_seam = bool(mark_seam)
            incident = []
            for polygon in mesh.polygons:
                for loop_index in polygon.loop_indices:
                    if int(mesh.loops[loop_index].vertex_index) == 0:
                        incident.append(int(loop_index))
                        fitted[0][int(loop_index)] += Vector((
                            len(incident) * 1.0e-4, 0.0
                        ))
                        break
            before = [fitted[0][index].copy() for index in incident]
            snapped = VUV._snap_source_continuous_loops(
                mesh, source, fitted, face_to_island, 1.0e-6
            )
            after = [fitted[0][index].copy() for index in incident]
            assert snapped == 0, snapped
            assert after == before, (before, after)
        finally:
            _remove_object_and_mesh(obj, mesh)

    seam_vertices = (
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
        (0.5, 1.0, 0.0), (0.5, -1.0, 0.0),
    )
    seam_uv = (
        ((0.0, 0.0), (1.0, 0.0), (0.5, 1.0)),
        ((1.0, 0.0), (0.0, 0.0), (0.5, -1.0)),
    )
    exercise(
        "VUV_Writeback_Seam",
        seam_vertices,
        ((0, 1, 2), (1, 0, 3)),
        seam_uv,
        mark_seam=True,
    )

    non_manifold_vertices = seam_vertices + ((0.5, 0.0, 1.0),)
    exercise(
        "VUV_Writeback_NonManifold",
        non_manifold_vertices,
        ((0, 1, 2), (1, 0, 3), (0, 1, 4)),
        seam_uv + (((0.0, 0.0), (1.0, 0.0), (0.5, 1.0)),),
    )


def _test_rotated_shared_boundary_uses_semantic_uv_epsilon():
    """Ignore float32 edge-touch noise without hiding real overlap."""

    angle = math.radians(31.7)
    origin = Vector((0.0, 0.0))
    offset = Vector((0.37, 0.21))

    def transformed(points):
        return tuple(
            VUV._rotate_point(Vector(point), origin, angle) * 0.23 + offset
            for point in points
        )

    left_quad = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    # Two topology-adjacent quads have independently stored UV loops.  Their
    # shared edge intrudes by 5e-7 before scale and float32 storage, matching
    # the edge-touch noise seen after rigidly rotating Gun Head.
    edge_noise_quad = (
        (1.0 - 5.0e-7, 0.0),
        (2.0, 0.0),
        (2.0, 1.0),
        (1.0 - 5.0e-7, 1.0),
    )
    left_triangle = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    real_overlap = (
        (1.0 - 5.0e-3, 0.0),
        (1.0, 1.0),
        (0.0, 1.0 - 5.0e-3),
    )

    noisy_obj, noisy_mesh = _topology_object(
        "VUV_Rotated_Edge_Noise",
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
            (2.0, 1.0, 0.0),
        ),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
        (transformed(left_quad), transformed(edge_noise_quad)),
    )
    overlap_obj, overlap_mesh = _polygon_object(
        "VUV_Rotated_Real_Overlap",
        (
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
        ),
        (transformed(left_triangle), transformed(real_overlap)),
    )
    try:
        strict_noise = VUV.audit_active_uv(
            noisy_obj, (0, 0), epsilon=1.0e-7
        )
        semantic_noise = VUV.audit_active_uv(
            noisy_obj, (0, 0), epsilon=1.0e-6
        )
        semantic_overlap = VUV.audit_active_uv(
            overlap_obj, (0, 1), epsilon=1.0e-6
        )
        assert strict_noise["overlap"], strict_noise
        assert not semantic_noise["overlap"], semantic_noise
        assert semantic_noise["valid"], semantic_noise
        assert semantic_overlap["overlap"], semantic_overlap
        assert not semantic_overlap["valid"], semantic_overlap

        options = VUV.GroupLayoutOptions(
            uv_epsilon=1.0e-6,
            align_geometry_direction=False,
            small_island_scale_boost=1.0,
        )
        islands, face_map, _adjacency, _diagonal = (
            VUV.compute_active_uv_islands(noisy_obj, options)
        )
        assert len(islands) == 1, [item.face_indices for item in islands]
        assert tuple(face_map) == (0, 0), face_map
        result = VUV.layout_active_uv(noisy_obj, options)
        assert not result.before_audit["overlap"], result.before_audit
        assert not result.after_audit["overlap"], result.after_audit
        VUV._validate_result_audit(result.after_audit)
    finally:
        _remove_object_and_mesh(noisy_obj, noisy_mesh)
        _remove_object_and_mesh(overlap_obj, overlap_mesh)


def _test_adaptive_replay_reaudits_committed_coordinates():
    model = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    uv = ((0.1, 0.1), (0.6, 0.1), (0.6, 0.35), (0.1, 0.35))
    obj, mesh = _polygon_object("VUV_Adaptive_Replay_Audit", (model,), (uv,))
    options = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        small_island_scale_boost=1.0,
    )
    source = [item.uv.copy() for item in mesh.uv_layers.active.data]
    original_quality = VUV.evaluate_layout_quality
    calls = {"count": 0}

    def fail_only_replay(*args, **kwargs):
        calls["count"] += 1
        result = original_quality(*args, **kwargs)
        if calls["count"] == 3:
            result = dict(result)
            result["valid"] = False
        return result

    VUV.evaluate_layout_quality = fail_only_replay
    try:
        try:
            VUV.layout_active_uv_adaptive(obj, options)
        except RuntimeError as exc:
            assert "replay failed final quality audit" in str(exc), str(exc)
        else:
            raise AssertionError("Invalid adaptive replay was committed")
    finally:
        VUV.evaluate_layout_quality = original_quality
    assert calls["count"] == 3, calls
    after = [item.uv.copy() for item in mesh.uv_layers.active.data]
    assert all((left - right).length <= 1.0e-12 for left, right in zip(source, after))
    _remove_object_and_mesh(obj, mesh)


def _test_adaptive_replay_exception_restores_source():
    model = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    uv = ((0.1, 0.1), (0.6, 0.1), (0.6, 0.35), (0.1, 0.35))
    obj, mesh = _polygon_object("VUV_Adaptive_Replay_Exception", (model,), (uv,))
    options = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        small_island_scale_boost=1.0,
    )
    source = [item.uv.copy() for item in mesh.uv_layers.active.data]
    original_quality = VUV.evaluate_layout_quality
    calls = {"count": 0}

    def raise_only_replay(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic replay evaluator failure")
        return original_quality(*args, **kwargs)

    VUV.evaluate_layout_quality = raise_only_replay
    try:
        try:
            VUV.layout_active_uv_adaptive(obj, options)
        except RuntimeError as exc:
            assert "synthetic replay evaluator failure" in str(exc), str(exc)
        else:
            raise AssertionError("Exceptional adaptive replay was committed")
    finally:
        VUV.evaluate_layout_quality = original_quality
    assert calls["count"] == 3, calls
    after = [item.uv.copy() for item in mesh.uv_layers.active.data]
    assert all((left - right).length <= 1.0e-12 for left, right in zip(source, after))
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


def _test_source_cells_use_compact_soft_ordering():
    islands = tuple(
        _island(
            island_id,
            (float(island_id), 0.0, 0.0,
             float(island_id) + 0.5, 0.5, 0.1),
        )
        for island_id in range(4)
    )
    groups = tuple(
        VUV.LayoutGroup(
            group_id=island_id,
            member_ids=(island_id,),
            reason="SINGLE_ANCHOR",
            anchor_ids=(island_id,),
        )
        for island_id in range(4)
    )
    analysis = _analysis(islands, layout_groups=groups)
    oriented = {
        island_id: {
            loop_index: point + Vector((float(island_id) * 5.0, 0.0))
            for loop_index, point in _rectangle(
                island_id * 10, 0.8, 0.5
            ).items()
        }
        for island_id in range(4)
    }
    common = dict(
        source_layout_cell_enabled=True,
        source_layout_cell_max_members=2,
        source_layout_cell_diameter_ratio=0.01,
        source_layout_cell_link_radius_ratio=0.01,
        square_pack_bias=0.35,
    )
    legacy = VUV._pack_source_cell_layout_once(
        oriented,
        analysis,
        0.05,
        VUV.GroupLayoutOptions(
            source_layout_compact_cells=False,
            **common
        ),
    )
    compact = VUV._pack_source_cell_layout_once(
        oriented,
        analysis,
        0.05,
        VUV.GroupLayoutOptions(
            source_layout_compact_cells=True,
            **common
        ),
    )
    assert compact.strategy == "source_cell_compact_layout", compact.strategy
    assert max(compact.width, compact.height) < max(
        legacy.width, legacy.height
    ) * 0.5, (compact.width, compact.height, legacy.width, legacy.height)
    for island_id, source in oriented.items():
        before = source[max(source)] - source[min(source)]
        after_values = compact.coordinates[island_id]
        after = after_values[max(after_values)] - after_values[min(after_values)]
        assert (before - after).length <= 1.0e-6, (island_id, before, after)


def _test_source_cell_variable_shelf_preserves_order_and_density():
    gap = 0.1
    sizes = {
        0: (1.0, 4.0),
        1: (1.0, 4.0),
        2: (1.0, 4.0),
        3: (6.0, 1.0),
        4: (6.0, 1.0),
    }
    members = tuple(sizes)
    local_coordinates = {
        island_id: _rectangle(island_id * 10, width, height)
        for island_id, (width, height) in sizes.items()
    }
    source_centers = {
        island_id: Vector((float(island_id), 0.0))
        for island_id in members
    }
    edges = tuple(
        (0, 0.0, left, left + 1)
        for left in range(len(members) - 1)
    )
    rectangles = tuple(
        VUV._Rect(
            key=island_id,
            width=sizes[island_id][0],
            height=sizes[island_id][1],
            sort_rank=island_id,
        )
        for island_id in members
    )
    _grid, grid_width, grid_height = VUV._regular_grid_rack(
        rectangles, gap
    )
    rack, width, height = VUV._source_cell_rack(
        members,
        local_coordinates,
        sizes,
        source_centers,
        edges,
        gap,
        0.01,
    )

    grid_longest = max(grid_width, grid_height)
    packed_longest = max(width, height)
    assert packed_longest < grid_longest * 0.6, (
        width, height, grid_width, grid_height
    )
    assert width * height < grid_width * grid_height * 0.5, (
        width, height, grid_width, grid_height
    )
    assert 1.0 / packed_longest > 1.0 / grid_longest, (
        packed_longest, grid_longest
    )
    ordered_placements = tuple(sorted(
        rack,
        key=lambda island_id: (
            round(float(rack[island_id].y), 9),
            round(float(rack[island_id].x), 9),
        ),
    ))
    assert ordered_placements == members, ordered_placements
    for island_id, placement in rack.items():
        expected_width, expected_height = sizes[island_id]
        assert not placement.quarter_turn, (island_id, placement)
        assert abs(placement.width - expected_width) <= 1.0e-12
        assert abs(placement.height - expected_height) <= 1.0e-12
    placements = tuple(rack.values())
    for left_index, left in enumerate(placements):
        for right in placements[left_index + 1:]:
            assert VUV._placements_clear(left, right, gap), rack


def _test_source_cell_variable_shelf_respects_density_floor():
    sizes = {0: (2.0, 1.0), 1: (2.0, 1.0)}
    members = tuple(sizes)
    local_coordinates = {
        island_id: _rectangle(island_id * 10, width, height)
        for island_id, (width, height) in sizes.items()
    }
    source_centers = {
        island_id: Vector((float(island_id), 0.0))
        for island_id in members
    }
    original_shelf = VUV._best_shelf_pack

    def expanded_shelf(*_args, **_kwargs):
        return ({
            0: VUV._Placement(0.0, 0.0, 2.0, 1.0, False),
            1: VUV._Placement(0.0, 1.1, 2.0, 1.0, False),
        }, 2.0, 20.0)

    VUV._best_shelf_pack = expanded_shelf
    try:
        rack, width, height = VUV._source_cell_rack(
            members,
            local_coordinates,
            sizes,
            source_centers,
            (),
            0.1,
            0.01,
        )
    finally:
        VUV._best_shelf_pack = original_shelf
    assert max(width, height) < 20.0, (width, height)
    assert all(not placement.quarter_turn for placement in rack.values())


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
    assert abs(
        VUV._line_angle_wrap(
            angle_30 + enabled_angles[1] - math.pi * 0.5
        )
    ) < 1.0e-12, enabled_angles[1]

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


def _test_long_rectangles_prefer_vertical_cardinal_axis():
    long_panel = _island(
        901,
        (0.0, 0.0, 0.0, 2.0, 0.5, 0.1),
        principal_angle=math.radians(22.0),
    )
    square = _island(
        902,
        (3.0, 0.0, 0.0, 4.0, 1.0, 0.1),
        principal_angle=math.radians(22.0),
    )
    square.uv_bounds = (0.0, 0.0, 1.0, 1.0)
    settings = VUV.GroupLayoutOptions()
    analysis = _analysis((long_panel, square))
    angles = VUV._orientation_angles(analysis, settings)
    long_axis = VUV._line_angle_wrap(
        long_panel.principal_angle + angles[long_panel.island_id]
    )
    square_axis = VUV._line_angle_wrap(
        square.principal_angle + angles[square.island_id]
    )
    assert abs(VUV._line_angle_wrap(long_axis - math.pi * 0.5)) < 1.0e-12
    assert abs(square_axis) < 1.0e-12

    rectangles = (
        VUV._Rect(0, 0.8, 0.25, 0),
        VUV._Rect(1, 0.25, 0.8, 1),
        VUV._Rect(2, 0.35, 0.30, 2),
    )
    placements, width, height = VUV._pack_layout_group_rectangles(
        rectangles,
        repeat_cohorts=(),
        gap=0.01,
        allow_rotate=True,
        prefer_vertical_long_rectangles=True,
    )
    values = tuple(placements.values())
    for index, left in enumerate(values):
        for right in values[index + 1:]:
            assert VUV._placements_clear(left, right, 0.01)
    assert placements[0].quarter_turn
    assert not placements[1].quarter_turn


def _test_vertical_preference_can_be_disabled():
    panel = _island(
        903,
        (0.0, 0.0, 0.0, 2.0, 0.5, 0.1),
        principal_angle=0.0,
    )
    settings = VUV.GroupLayoutOptions(
        prefer_vertical_long_rectangles=False,
    )
    angles = VUV._orientation_angles(_analysis((panel,)), settings)
    assert abs(angles[panel.island_id]) < 1.0e-12


def _test_incompatible_directed_repeat_downgrades_to_cardinal_geometry():
    # A landmark that points diagonally away from a hard-surface panel's
    # geometric axis is not a stable orientation cue.  The whole component is
    # downgraded so its PCA lines can be snapped upright and its exported
    # metadata cannot make the independent audit apply a false 360 contract.
    angle = math.radians(35.0)
    left = _island(
        1,
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
        principal_angle=angle,
        direction_angle=math.radians(5.0),
        direction_confidence=0.9,
    )
    right = _island(
        2,
        (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
        principal_angle=-angle,
        direction_angle=math.radians(-5.0),
        direction_confidence=0.9,
    )
    repeat = VUV.RepeatGroup(0, (1, 2), "REPEATED", 0.99, "directed")
    analysis = _analysis((left, right), (repeat,))
    enabled = VUV.GroupLayoutOptions(align_directed_cardinal=True)
    VUV._apply_orientation_policy(analysis, enabled)
    assert len(analysis.orientation_downgrades) == 1
    assert analysis.orientation_downgrades[0]["policy"] == (
        "center_symmetric_modulo_180"
    )
    assert all(
        island.direction_mode == "center_symmetric"
        and island.direction_confidence == 0.0
        for island in analysis.islands
    )
    angles = VUV._orientation_angles(analysis, enabled)
    final_lines = [
        VUV._line_angle_wrap(island.principal_angle + angles[island.island_id])
        for island in analysis.islands
    ]
    assert all(
        abs(VUV._line_angle_wrap(line - math.pi * 0.5)) < 1.0e-12
        for line in final_lines
    ), final_lines

    # A generous tolerance keeps the original directed behavior available for
    # genuinely directional parts whose landmark and geometric axis agree
    # within the caller's declared policy.
    retained = _analysis((
        _island(
            1,
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
            principal_angle=angle,
            direction_angle=math.radians(5.0),
            direction_confidence=0.9,
        ),
        _island(
            2,
            (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
            principal_angle=-angle,
            direction_angle=math.radians(-5.0),
            direction_confidence=0.9,
        ),
    ), (repeat,))
    retained_settings = VUV.GroupLayoutOptions(
        directed_cardinal_tolerance=math.radians(40.0),
    )
    VUV._apply_orientation_policy(retained, retained_settings)
    assert retained.orientation_downgrades == []
    assert all(item.direction_mode == "directed" for item in retained.islands)
    retained_angles = VUV._orientation_angles(retained, retained_settings)
    retained_directions = [
        VUV._angle_wrap(
            math.atan2(island.direction_vector.y, island.direction_vector.x)
            + retained_angles[island.island_id]
        )
        for island in retained.islands
    ]
    assert abs(VUV._angle_wrap(retained_directions[0] - retained_directions[1])) < 1.0e-12


def _test_directed_repeat_can_disable_cardinal_compatibility_gate():
    angle = math.radians(35.0)
    islands = (
        _island(
            1,
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
            principal_angle=angle,
            direction_angle=math.radians(5.0),
            direction_confidence=0.9,
        ),
        _island(
            2,
            (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
            principal_angle=-angle,
            direction_angle=math.radians(-5.0),
            direction_confidence=0.9,
        ),
    )
    repeat = VUV.RepeatGroup(0, (1, 2), "REPEATED", 0.99, "directed")
    analysis = _analysis(islands, (repeat,))
    settings = VUV.GroupLayoutOptions(align_directed_cardinal=False)
    VUV._apply_orientation_policy(analysis, settings)
    assert analysis.orientation_downgrades == []
    assert all(item.direction_mode == "directed" for item in analysis.islands)


def _test_post_layout_owner_partition_reapplies_direction_policy():
    """A restored owner cohort must retain the executed cardinal downgrade.

    The transaction reuses the pre-boost semantic partition after analysing
    the final UV layer.  This fixture mirrors that shape: there is no repeat
    record on the post pass, only an owner cohort.  One landmark points almost
    horizontal while its geometric panel is vertical, so both owners must be
    exported as center-symmetric.  A legitimate 90-degree owner pair remains
    directed because owner cohorts may represent quarter-turned parts.
    """
    conflict = (
        _island(
            189,
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
            principal_angle=math.pi * 0.5,
            direction_angle=math.radians(0.46),
            direction_confidence=0.9,
        ),
        _island(
            195,
            (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
            principal_angle=math.pi * 0.5,
            direction_angle=math.radians(-77.53),
            direction_confidence=1.0,
        ),
    )
    owner_group = VUV.LayoutGroup(
        group_id=101,
        member_ids=(189, 195),
        reason="ROTATIONAL",
        anchor_ids=(189, 195),
        owner_cohorts=((195, 189),),
    )
    settings = VUV.GroupLayoutOptions(
        directed_cardinal_tolerance=math.radians(5.0),
    )
    analysis = _analysis(conflict, layout_groups=(owner_group,))
    VUV._apply_orientation_policy(analysis, settings)
    assert len(analysis.orientation_downgrades) == 1
    downgrade = analysis.orientation_downgrades[0]
    assert set(downgrade["members"]) == {189, 195}
    assert downgrade["reason"] == (
        "landmark_geometry_residual_exceeds_cardinal_tolerance"
    )
    assert all(item.direction_mode == "center_symmetric" for item in analysis.islands)
    assert all(item.direction_confidence == 0.0 for item in analysis.islands)

    legal = (
        _island(
            289,
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.1),
            principal_angle=0.0,
            direction_angle=0.0,
            direction_confidence=0.9,
        ),
        _island(
            295,
            (2.0, 0.0, 0.0, 3.0, 1.0, 0.1),
            principal_angle=math.pi * 0.5,
            direction_angle=math.pi * 0.5,
            direction_confidence=0.9,
        ),
    )
    legal_group = VUV.LayoutGroup(
        group_id=102,
        member_ids=(289, 295),
        reason="ROTATIONAL",
        anchor_ids=(289, 295),
        owner_cohorts=((289, 295),),
    )
    legal_analysis = _analysis(legal, layout_groups=(legal_group,))
    VUV._apply_orientation_policy(legal_analysis, settings)
    assert legal_analysis.orientation_downgrades == []
    assert all(item.direction_mode == "directed" for item in legal_analysis.islands)


def _test_small_island_boost_is_uniform_and_bounded():
    small = _island(
        1,
        (0.0, 0.0, 0.0, 0.2, 0.2, 0.1),
        small=True,
    )
    large = _island(
        2,
        (1.0, 0.0, 0.0, 3.0, 2.0, 0.1),
        small=False,
    )
    analysis = _analysis((small, large))
    oriented = {
        1: _rectangle(0, 0.2, 0.1),
        2: _rectangle(10, 1.0, 0.5),
    }
    weighted, factors, count = VUV._boost_small_island_coordinates(
        oriented,
        analysis,
        1.35,
    )
    assert count == 1
    assert abs(factors[1] - 1.35) < 1.0e-12
    assert abs(factors[2] - 1.0) < 1.0e-12
    small_bounds = VUV._uv_bounds(weighted[1].values())
    large_bounds = VUV._uv_bounds(weighted[2].values())
    small_width = small_bounds[2] - small_bounds[0]
    large_width = large_bounds[2] - large_bounds[0]
    assert abs(small_width - 0.27) < 1.0e-6
    assert abs(large_width - 1.0) < 1.0e-6
    _, capped, _ = VUV._boost_small_island_coordinates(
        oriented,
        analysis,
        9.0,
    )
    assert capped[1] == 3.0


def _test_square_pack_bias_improves_tile_utilization():
    # This mix exposes a shelf discontinuity: the compactness-only score picks
    # a 1.18:1 strip, while the bounded square bias finds a nearly square
    # candidate without increasing the longest dimension by more than 12%.
    pairs = (
        (0.0980, 0.7053),
        (0.8336, 1.1555),
        (0.9616, 0.0931),
        (0.1579, 1.0298),
    )
    rectangles = tuple(
        VUV._Rect(index, width, height, index)
        for index, (width, height) in enumerate(pairs)
    )
    compact = VUV._best_shelf_pack(
        rectangles,
        gap=0.06227,
        allow_rotate=True,
        square_pack_bias=0.0,
    )
    balanced = VUV._best_shelf_pack(
        rectangles,
        gap=0.06227,
        allow_rotate=True,
        square_pack_bias=VUV.GroupLayoutOptions().square_pack_bias,
    )
    compact_aspect = max(compact[1], compact[2]) / min(compact[1], compact[2])
    balanced_aspect = max(balanced[1], balanced[2]) / min(balanced[1], balanced[2])
    assert balanced_aspect + 0.1 < compact_aspect, (
        compact[1:],
        balanced[1:],
    )
    assert max(balanced[1], balanced[2]) <= max(compact[1], compact[2]) * 1.12


def _test_shelf_order_ensemble_closes_blank_space_without_scale_loss():
    # The legacy longest-side order leaves a shallow strip even though the
    # same rectangles fit a smaller, nearly square shelf layout.  The bounded
    # order ensemble improves both fitted UV scale and tile coverage, but it
    # may never increase the longest packed dimension because that would
    # shrink every UV island.
    pairs = (
        (0.29548, 0.62368),
        (0.59685, 0.57603),
        (0.30032, 0.58139),
        (0.06773, 0.15107),
    )
    gap = 0.05493
    rectangles = tuple(
        VUV._Rect(index, width, height, index)
        for index, (width, height) in enumerate(pairs)
    )
    legacy_candidates = []
    for target_width in VUV._candidate_shelf_widths(rectangles, gap):
        placements, width, height = VUV._shelf_pack_once(
            rectangles,
            target_width,
            gap,
            allow_rotate=True,
            preserve_order=False,
        )
        maximum = max(width, height)
        minimum = max(min(width, height), VUV._EPSILON)
        aspect = maximum / minimum
        score = maximum * (
            1.0
            + VUV.GroupLayoutOptions().square_pack_bias
            * min(max(aspect - 1.0, 0.0), 2.0)
        )
        legacy_candidates.append((
            score,
            maximum,
            width * height,
            aspect,
            abs(width - height),
            target_width,
            placements,
            width,
            height,
        ))
    legacy = min(legacy_candidates, key=lambda item: item[:6])
    improved = VUV._best_shelf_pack(
        rectangles,
        gap=gap,
        allow_rotate=True,
        square_pack_bias=VUV.GroupLayoutOptions().square_pack_bias,
    )
    improved_maximum = max(improved[1], improved[2])
    improved_aspect = improved_maximum / min(improved[1], improved[2])
    assert improved_maximum <= legacy[1] + 1.0e-12, (
        legacy[7:],
        improved[1:],
    )
    assert improved_maximum < legacy[1] * 0.95, (
        legacy[7:],
        improved[1:],
    )
    assert improved_aspect + 0.05 < legacy[3], (
        legacy[7:],
        improved[1:],
    )
    improved_placements = tuple(improved[0].values())
    for left_index, left in enumerate(improved_placements):
        for right in improved_placements[left_index + 1:]:
            assert VUV._placements_clear(left, right, gap), improved[0]


def _test_rigid_maxrects_is_deterministic_and_never_rotates():
    rectangles = tuple(
        VUV._Rect(index, width, height, index)
        for index, (width, height) in enumerate((
            (0.62, 0.38),
            (0.38, 0.62),
            (0.36, 0.24),
            (0.24, 0.36),
            (0.17, 0.11),
        ))
    )
    gap = 0.013
    first = VUV._best_rigid_maxrects_pack(
        rectangles, gap, baseline_width=1.0
    )
    second = VUV._best_rigid_maxrects_pack(
        rectangles, gap, baseline_width=1.0
    )
    assert first is not None
    assert first == second, (first, second)
    placements, width, height = first
    assert VUV._rigid_pack_is_valid(
        rectangles, placements, width, height, gap
    )
    by_id = {rect.key: rect for rect in rectangles}
    for key, placement in placements.items():
        assert not placement.quarter_turn
        assert abs(placement.width - by_id[key].width) < 1.0e-12
        assert abs(placement.height - by_id[key].height) < 1.0e-12


def _test_rigid_maxrects_longest_edge_gate_falls_back_to_shelf():
    rectangles = (
        VUV._Rect(0, 0.40, 0.20, 0),
        VUV._Rect(1, 0.30, 0.20, 1),
    )
    gap = 0.01
    shelf = VUV._best_shelf_pack(
        rectangles,
        gap,
        allow_rotate=False,
        preserve_order=False,
    )
    deliberately_worse = (
        {
            0: VUV._Placement(0.0, 0.0, 0.40, 0.20, False),
            1: VUV._Placement(2.0, 0.0, 0.30, 0.20, False),
        },
        2.30,
        0.20,
    )
    assert VUV._rigid_pack_is_valid(
        rectangles, deliberately_worse[0], deliberately_worse[1],
        deliberately_worse[2], gap,
    )
    original = VUV._best_rigid_maxrects_pack
    VUV._best_rigid_maxrects_pack = lambda *_args, **_kwargs: deliberately_worse
    try:
        selected = VUV._prefer_rigid_maxrects_pack(rectangles, gap, shelf)
    finally:
        VUV._best_rigid_maxrects_pack = original
    assert selected is shelf


def _test_low_anisotropy_boundary_edge_snaps_cardinal():
    angle = math.radians(30.0)
    base = (
        (-0.2, -0.2),
        (0.2, -0.2),
        (0.2, 0.2),
        (-0.2, 0.2),
    )
    rotated = []
    for point in base:
        rotated.append(
            VUV._rotate_point(Vector(point), Vector((0.5, 0.5)), angle)
        )
    model = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    obj, mesh = _polygon_object("VUV_Low_Anisotropy_Cardinal", (model,), (rotated,))
    try:
        analysis = VUV.analyze_active_uv(obj)
        island = analysis.islands[0]
        assert island.anisotropy < VUV.GroupLayoutOptions().min_pca_anisotropy
        assert island.dominant_edge_angle is not None
        assert island.dominant_edge_confidence >= 0.15
        angles = VUV._orientation_angles(
            analysis,
            VUV.GroupLayoutOptions(align_geometry_direction=False),
        )
        # The long boundary edge is at 30 degrees, so cardinal snapping uses
        # the negative 30-degree correction even though PCA is isotropic.
        assert abs(angles[island.island_id] + angle) < 1.0e-6, angles
        disabled = VUV._orientation_angles(
            analysis,
            VUV.GroupLayoutOptions(
                align_non_repeat_cardinal=False,
                align_geometry_direction=False,
            ),
        )
        assert abs(disabled[island.island_id]) < 1.0e-12, disabled
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_geometry_direction_removes_standalone_180_flip():
    model = (
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        ),
        (
            (3.0, 0.0, 0.0),
            (4.0, 0.0, 0.0),
            (4.0, 0.0, 2.0),
            (3.0, 0.0, 2.0),
        ),
    )
    upright = (
        (0.05, 0.05),
        (0.25, 0.05),
        (0.25, 0.45),
        (0.05, 0.45),
    )
    upside_down = (
        (0.75, 0.45),
        (0.55, 0.45),
        (0.55, 0.05),
        (0.75, 0.05),
    )
    obj, mesh = _polygon_object(
        "VUV_Directed_180", model, (upright, upside_down)
    )
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_space="OBJECT",
        direction_axis="AUTO",
        allow_group_quarter_turn=True,
        small_island_scale_boost=1.0,
    )
    try:
        analysis = VUV.analyze_active_uv(obj, settings)
        assert len(analysis.islands) == 2
        assert {
            island.geometry_axis_name for island in analysis.islands
        } == {"Z"}
        source_rotations = sorted(
            abs(VUV._angle_wrap(island.geometry_rotation_angle))
            for island in analysis.islands
        )
        assert source_rotations[0] < 1.0e-6, source_rotations
        assert abs(source_rotations[1] - math.pi) < 1.0e-6, source_rotations

        result = VUV.layout_active_uv(obj, settings)
        directed = result.quality_metrics["directed_geometry"]
        assert result.group_quarter_turn is False
        assert directed["resolved_islands"] == 2, directed
        assert directed["misaligned_islands"] == 0, directed
        assert directed["opposite_islands"] == 0, directed
        assert directed["quarter_turn_islands"] == 0, directed
        assert directed["residual_max_degrees"] < 1.0e-3, directed
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_geometry_direction_auto_axis_falls_back_on_horizontal_cap():
    model = ((
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ),)
    uv = (((0.1, 0.1), (0.9, 0.1), (0.9, 0.5), (0.1, 0.5)),)
    obj, mesh = _polygon_object("VUV_Directed_Cap", model, uv)
    try:
        automatic = VUV.analyze_active_uv(
            obj,
            VUV.GroupLayoutOptions(
                align_geometry_direction=True,
                direction_axis="AUTO",
            ),
        ).islands[0]
        assert automatic.geometry_axis_name == "X", automatic.to_dict()
        assert abs(
            VUV._angle_wrap(automatic.geometry_rotation_angle - math.pi * 0.5)
        ) < 1.0e-6, automatic.to_dict()

        explicit_normal = VUV.analyze_active_uv(
            obj,
            VUV.GroupLayoutOptions(
                align_geometry_direction=True,
                direction_axis="Z",
            ),
        ).islands[0]
        assert explicit_normal.geometry_axis_name is None
        assert explicit_normal.geometry_direction_confidence < 0.35
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_geometry_direction_auto_rejects_bimodal_preferred_axis():
    large = (
        (0.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    small = (
        (4.0, 0.0, 0.0),
        (6.0, 0.0, 0.0),
        (6.0, 0.0, 1.0),
        (4.0, 0.0, 1.0),
    )
    large_uv = tuple((point[0], point[2]) for point in large)
    small_uv = tuple((point[0], -point[2]) for point in small)
    obj, mesh = _polygon_object(
        "VUV_Directed_Bimodal_Auto",
        (large, small),
        (large_uv, small_uv),
    )
    try:
        uv_layer = mesh.uv_layers.active
        automatic = VUV._geometry_direction_record(
            obj,
            mesh,
            uv_layer,
            (0, 1),
            axis="AUTO",
            min_projection=VUV._GEOMETRY_AXIS_RELATIVE_EPSILON,
        )
        explicit = VUV._geometry_direction_record(
            obj,
            mesh,
            uv_layer,
            (0, 1),
            axis="Z",
            min_projection=VUV._GEOMETRY_AXIS_RELATIVE_EPSILON,
        )
        replay_fixed = VUV._geometry_direction_record(
            obj,
            mesh,
            uv_layer,
            (0, 1),
            axis="AUTO",
            min_projection=VUV._GEOMETRY_AXIS_RELATIVE_EPSILON,
            fixed_axis_name="Z",
        )
        assert automatic[0] == "X", automatic
        assert explicit[0] == "Z", explicit
        assert replay_fixed[0] == "Z", replay_fixed
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_structure_group_binds_one_signed_geometry_axis():
    vertices = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
        (0.0, 1.0, 0.0),
        (2.0, 1.0, 0.0),
    )
    faces = (
        (0, 1, 2, 3),
        (0, 4, 5, 1),
    )
    uv_polygons = (
        ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),
        ((3.0, 0.0), (5.0, 0.0), (5.0, 1.0), (3.0, 1.0)),
    )
    obj, mesh = _topology_object(
        "VUV_Structure_Common_Axis", vertices, faces, uv_polygons
    )
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_axis="AUTO",
        structure_group_enabled=True,
        structure_group_max_diameter_ratio=1.0,
        small_island_scale_boost=1.0,
    )
    try:
        uv_layer = mesh.uv_layers.active
        independent = [
            VUV._geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                (face_index,),
                axis="AUTO",
                min_projection=settings.direction_axis_min_projection,
            )[0]
            for face_index in (0, 1)
        ]
        assert independent == ["Z", "X"], independent

        analysis = VUV.analyze_active_uv(obj, settings)
        assert len(analysis.islands) == 2, analysis.to_dict()
        assert {
            island.geometry_axis_name for island in analysis.islands
        } == {"X"}, analysis.to_dict()
        assert any(
            item["source"] == "STRUCTURE_AFFINITY"
            and item["selected_axis"] == "X"
            and item["previous_axes"] == ["Z", "X"]
            for item in analysis.geometry_axis_groups
        ), analysis.geometry_axis_groups

        result = VUV.layout_active_uv(obj, settings)
        assert result.group_quarter_turn is False
        committed = VUV.analyze_active_uv(obj, settings)
        assert {
            island.geometry_axis_name for island in committed.islands
        } == {"X"}, committed.to_dict()
        for island in committed.islands:
            direction = island.geometry_direction_vector
            assert abs(float(direction.x)) < 1.0e-5, island.to_dict()
            assert float(direction.y) > 1.0 - 1.0e-5, island.to_dict()
    finally:
        _remove_object_and_mesh(obj, mesh)


def _owner_child_direction_fixture(name, include_second_child=False):
    vertices = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
        (2.005, 0.0, 0.50),
        (2.015, 0.0, 0.50),
        (2.015, 0.01, 0.50),
        (2.005, 0.01, 0.50),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    uv_polygons = [
        ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),
        ((3.0, 0.0), (3.01, 0.0), (3.01, 0.01), (3.0, 0.01)),
    ]
    if include_second_child:
        vertices.extend((
            (1.90, 0.005, 0.50),
            (1.90, 0.015, 0.50),
            (1.90, 0.015, 0.51),
            (1.90, 0.005, 0.51),
        ))
        faces.append((8, 9, 10, 11))
        uv_polygons.append((
            (3.02, 0.0),
            (3.03, 0.0),
            (3.03, 0.01),
            (3.02, 0.01),
        ))
    obj, mesh = _topology_object(name, vertices, faces, uv_polygons)
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_axis="AUTO",
        structure_group_enabled=False,
        small_area_ratio=0.01,
        small_uv_area_ratio=0.01,
        proximity_radius_ratio=1.0,
        small_island_scale_boost=1.0,
    )
    return obj, mesh, settings


def _test_owner_child_uses_one_shared_signed_geometry_axis():
    obj, mesh, settings = _owner_child_direction_fixture(
        "VUV_Owner_Child_Common_Axis"
    )
    try:
        uv_layer = mesh.uv_layers.active
        independent = [
            VUV._geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                (face_index,),
                axis="AUTO",
                min_projection=settings.direction_axis_min_projection,
            )[0]
            for face_index in (0, 1)
        ]
        assert independent == ["Z", "X"], independent

        analysis = VUV.analyze_active_uv(obj, settings)
        by_id = {island.island_id: island for island in analysis.islands}
        assert not by_id[0].is_small and by_id[1].is_small
        owner_group = next(
            group for group in analysis.layout_groups if 0 in group.anchor_ids
        )
        assert dict(zip(
            owner_group.small_member_ids,
            owner_group.small_anchor_ids,
        )) == {1: 0}, owner_group.to_dict()
        assert {by_id[index].geometry_axis_name for index in (0, 1)} == {"X"}
        assert any(
            item["source"] == "OWNER_ATTACHMENT"
            and item["selected_axis"] == "X"
            and set(item["members"]) == {0, 1}
            and item["resolved"]
            and not item["compatibility_split"]
            for item in analysis.geometry_axis_groups
        ), analysis.geometry_axis_groups

        result = VUV.layout_active_uv(obj, settings)
        assert result.candidate_valid, result.to_dict()
        committed = VUV.analyze_active_uv(obj, settings)
        for island in committed.islands:
            assert island.geometry_axis_name == "X", island.to_dict()
            assert abs(float(island.geometry_direction_vector.x)) < 1.0e-5
            assert float(island.geometry_direction_vector.y) > 1.0 - 1.0e-5
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_owner_axis_seeds_incompatible_child_partition():
    obj, mesh, settings = _owner_child_direction_fixture(
        "VUV_Owner_Child_Split_Axes", include_second_child=True
    )
    try:
        uv_layer = mesh.uv_layers.active
        independent = [
            VUV._geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                (face_index,),
                axis="AUTO",
                min_projection=settings.direction_axis_min_projection,
            )[0]
            for face_index in (0, 1, 2)
        ]
        assert independent == ["Z", "X", "Z"], independent

        analysis = VUV.analyze_active_uv(obj, settings)
        by_id = {island.island_id: island for island in analysis.islands}
        assert by_id[0].geometry_axis_name == "Z", analysis.to_dict()
        assert by_id[2].geometry_axis_name == "Z", analysis.to_dict()
        records = [
            item for item in analysis.geometry_axis_groups
            if item["source"] == "OWNER_ATTACHMENT"
        ]
        assert len(records) >= 2, analysis.geometry_axis_groups
        assert all(item["compatibility_split"] for item in records), records
        owner_record = next(item for item in records if 0 in item["members"])
        assert owner_record["selected_axis"] == "Z", owner_record
        assert {0, 2}.issubset(set(owner_record["members"])), owner_record
        assert any(
            1 in item["members"] and item["selected_axis"] != "Z"
            for item in records
        ), records

        result = VUV.layout_active_uv(obj, settings)
        assert result.candidate_valid, result.to_dict()
        committed = VUV.analyze_active_uv(obj, settings)
        committed_by_id = {
            island.island_id: island for island in committed.islands
        }
        assert committed_by_id[0].geometry_axis_name == "Z"
        assert committed_by_id[2].geometry_axis_name == "Z"
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_common_axis_uses_weakest_member_stability_before_priority():
    # This models the diagnosed Body cohort: Z is technically coherent for
    # every member, but one member sits at the 0.50 effective-fraction gate.
    # X has a robust margin on all members and must remain selected if float32
    # replay nudges that weak Z member below the gate.
    candidates = (
        {
            "Z": {"coherent": True, "stability": 0.6667, "confidence": 0.3414},
            "X": {"coherent": True, "stability": 0.7631, "confidence": 0.4843},
        },
        {
            "Z": {"coherent": True, "stability": 0.4994, "confidence": 0.0195},
            "X": {"coherent": True, "stability": 1.0, "confidence": 0.7068},
        },
        {
            "Z": {"coherent": True, "stability": 0.9563, "confidence": 0.6784},
            "X": {"coherent": True, "stability": 0.9574, "confidence": 0.7237},
        },
    )
    selected = VUV._select_common_geometry_axis(
        candidates, ("Z", "X", "Y")
    )
    assert selected == "X", selected

    replay = tuple(dict(values) for values in candidates)
    replay[1]["Z"] = dict(replay[1]["Z"], coherent=False)
    replay_selected = VUV._select_common_geometry_axis(
        replay, ("Z", "X", "Y")
    )
    assert replay_selected == selected == "X", (selected, replay_selected)
    assert VUV._select_common_geometry_axis(
        candidates, ("Z", "X", "Y"), preferred_axis="Z"
    ) == "Z"

    symmetric = (
        {
            "Z": {"coherent": True, "stability": 0.99995, "confidence": 0.70709},
            "X": {"coherent": True, "stability": 1.0, "confidence": 0.70713},
        },
        {
            "Z": {"coherent": True, "stability": 1.0, "confidence": 0.70713},
            "X": {"coherent": True, "stability": 0.99995, "confidence": 0.70709},
        },
    )
    assert VUV._select_common_geometry_axis(
        symmetric, ("Z", "X", "Y")
    ) == "Z"


def _test_structure_group_splits_incompatible_auto_axes_deterministically():
    vertices = (
        (-1.0, -1.0, -1.0),
        (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, -1.0, 1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
    )
    faces = (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    uv_polygons = tuple(
        tuple(
            (column * 0.30 + x * 0.20, row * 0.40 + y * 0.20)
            for x, y in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        )
        for row in range(2)
        for column in range(3)
    )
    obj, mesh = _topology_object(
        "VUV_Structure_Split_Axes", vertices, faces, uv_polygons
    )
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_axis="AUTO",
        structure_group_enabled=True,
        structure_group_max_members=12,
        structure_group_max_degree=4,
        structure_group_min_contact_ratio=0.06,
        structure_group_max_diameter_ratio=1.0,
        structure_group_max_normal_angle=math.radians(100.0),
        small_island_scale_boost=1.0,
    )
    try:
        analysis = VUV.analyze_active_uv(obj, settings)
        assert len(analysis.islands) == 6, analysis.to_dict()
        records = [
            item for item in analysis.geometry_axis_groups
            if item["source"] == "STRUCTURE_AFFINITY"
        ]
        assert len(records) >= 2, analysis.geometry_axis_groups
        assert all(item["resolved"] for item in records), records
        assert all(item["compatibility_split"] for item in records), records
        assert {
            island_id for item in records for island_id in item["members"]
        } == set(range(6)), records
        assert len({item["selected_axis"] for item in records}) >= 2, records
        assert max(len(item["members"]) for item in records) >= 2, records

        by_id = {island.island_id: island for island in analysis.islands}
        for item in records:
            assert {
                by_id[island_id].geometry_axis_name
                for island_id in item["members"]
            } == {item["selected_axis"]}, (item, analysis.to_dict())

        result = VUV.layout_active_uv(obj, settings)
        directed = result.quality_metrics["directed_geometry"]
        assert result.candidate_valid, result.to_dict()
        assert result.quality_metrics["directed_geometry_valid"], directed
        assert directed["resolved_islands"] == 6, directed
        assert directed["unresolved_islands"] == 0, directed
        assert directed["misaligned_islands"] == 0, directed

        committed = VUV.analyze_active_uv(obj, settings)
        committed_by_id = {
            island.island_id: island for island in committed.islands
        }
        committed_records = [
            item for item in committed.geometry_axis_groups
            if item["source"] == "STRUCTURE_AFFINITY" and item["resolved"]
        ]
        assert len(committed_records) >= 2, committed.geometry_axis_groups
        for item in committed_records:
            for island_id in item["members"]:
                island = committed_by_id[island_id]
                assert island.geometry_axis_name == item["selected_axis"]
                assert abs(float(island.geometry_direction_vector.x)) < 1.0e-5
                assert float(island.geometry_direction_vector.y) > 1.0 - 1.0e-5
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_geometry_direction_is_scale_invariant_for_tiny_islands():
    model = ((
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 2.0),
        (0.0, 0.0, 2.0),
    ),)
    records = []
    cases = (
        (1.0, 0.0),
        (1.0e-4, 0.3),
        (1.0e-6, 0.7),
        (1.0e-7, 0.0),
    )
    for index, (scale, offset) in enumerate(cases):
        uv = (((
            (offset, offset),
            (offset + scale, offset),
            (offset + scale, offset + 2.0 * scale),
            (offset, offset + 2.0 * scale),
        )),)
        obj, mesh = _polygon_object(
            "VUV_Directed_Tiny_{}".format(index), model, uv
        )
        try:
            island = VUV.analyze_active_uv(
                obj,
                VUV.GroupLayoutOptions(
                    align_geometry_direction=True,
                    direction_axis="AUTO",
                ),
            ).islands[0]
            records.append((
                island.geometry_axis_name,
                island.geometry_rotation_angle,
                island.geometry_direction_confidence,
            ))
        finally:
            _remove_object_and_mesh(obj, mesh)
    assert all(axis == "Z" for axis, _angle, _confidence in records), records
    assert max(abs(angle) for _axis, angle, _confidence in records) < 1.0e-4, records
    confidences = [confidence for _axis, _angle, confidence in records]
    assert max(confidences) - min(confidences) < 0.03, records


def _test_direction_resolution_gate_distinguishes_auto_and_explicit_axis():
    cap_model = ((
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ),)
    vertical_model = ((
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 2.0),
        (0.0, 0.0, 2.0),
    ),)
    uv = (((0.1, 0.1), (0.8, 0.1), (0.8, 0.6), (0.1, 0.6)),)
    cap_obj, cap_mesh = _polygon_object("VUV_Direction_Unresolved_Cap", cap_model, uv)
    vertical_obj, vertical_mesh = _polygon_object(
        "VUV_Direction_Required_Axis", vertical_model, uv
    )
    explicit = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_axis="Z",
    )
    automatic = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_axis="AUTO",
    )
    try:
        cap_analysis = VUV.analyze_active_uv(cap_obj, explicit)
        assert cap_analysis.islands[0].geometry_axis_name is None
        vertical_analysis = VUV.analyze_active_uv(vertical_obj, explicit)
        assert vertical_analysis.islands[0].geometry_axis_name == "Z"

        original_record = VUV._geometry_direction_record
        replay_analysis = VUV.analyze_active_uv(vertical_obj, explicit)
        replay_analysis.islands[0].geometry_axis_name = "X"
        captured_axes = []

        def traced_record(*args, **kwargs):
            captured_axes.append(kwargs.get("fixed_axis_name"))
            return original_record(*args, **kwargs)

        VUV._geometry_direction_record = traced_record
        try:
            rebound = VUV._rebind_geometry_axis_contract(
                vertical_obj,
                vertical_mesh,
                vertical_mesh.uv_layers.active,
                replay_analysis,
                vertical_analysis,
                explicit,
            )
        finally:
            VUV._geometry_direction_record = original_record
        assert captured_axes == ["Z"], captured_axes
        assert rebound.islands[0].geometry_axis_name == "Z"

        def unresolved(*args, **kwargs):
            return None, Vector((0.0, 0.0)), 0.0, 0.0

        VUV._geometry_direction_record = unresolved
        try:
            auto_metrics = VUV.evaluate_layout_quality(
                cap_obj,
                analysis=cap_analysis,
                face_to_island=cap_analysis.face_to_island,
                options=automatic,
            )
            explicit_cap_metrics = VUV.evaluate_layout_quality(
                cap_obj,
                analysis=cap_analysis,
                face_to_island=cap_analysis.face_to_island,
                options=explicit,
            )
            explicit_lost_metrics = VUV.evaluate_layout_quality(
                vertical_obj,
                analysis=vertical_analysis,
                face_to_island=vertical_analysis.face_to_island,
                options=explicit,
            )
        finally:
            VUV._geometry_direction_record = original_record

        assert auto_metrics["directed_geometry_unresolved_islands"] == 1
        assert not auto_metrics["directed_geometry_valid"], auto_metrics
        assert explicit_cap_metrics["directed_geometry_unresolved_islands"] == 1
        assert explicit_cap_metrics["directed_geometry_required_unresolved_islands"] == 0
        assert explicit_cap_metrics["directed_geometry_valid"], explicit_cap_metrics
        assert explicit_lost_metrics[
            "directed_geometry_required_unresolved_islands"
        ] == 1
        assert not explicit_lost_metrics["directed_geometry_valid"], (
            explicit_lost_metrics
        )
    finally:
        _remove_object_and_mesh(cap_obj, cap_mesh)
        _remove_object_and_mesh(vertical_obj, vertical_mesh)


def _test_replay_identity_includes_unresolved_explicit_axis_islands():
    bounds = (0.0, 0.0, 0.0, 1.0, 1.0, 0.1)
    expected_resolved = _island(1, bounds)
    expected_resolved.geometry_axis_name = "Z"
    expected_unresolved = _island(2, bounds)
    expected = _analysis((expected_resolved, expected_unresolved))

    replay_resolved = _island(1, bounds)
    replay_resolved.geometry_axis_name = "Z"
    replay_changed_unresolved = _island(3, bounds)
    replay = _analysis((replay_resolved, replay_changed_unresolved))
    try:
        VUV._rebind_geometry_axis_contract(
            None,
            None,
            None,
            replay,
            expected,
            VUV.GroupLayoutOptions(direction_axis="Z"),
        )
    except RuntimeError as exc:
        assert "changed island identity" in str(exc), str(exc)
    else:
        raise AssertionError("Unresolved replay island identity drift passed")


def _test_margin_correction_with_one_iteration():
    oriented = {
        0: _rectangle(0, 0.8, 0.2),
        1: _rectangle(10, 0.8, 0.2),
    }
    groups = (
        VUV.LayoutGroup(0, (0,), "SINGLE_ANCHOR", (0,)),
        VUV.LayoutGroup(1, (1,), "SINGLE_ANCHOR", (1,)),
    )
    options = VUV.GroupLayoutOptions(
        margin=0.02,
        packing_iterations=1,
    )
    plan, fitted, _scale = VUV._plan_with_margin(
        oriented,
        groups,
        options,
    )
    assert plan.width > 0.0 and plan.height > 0.0
    assert VUV._minimum_aabb_gap(fitted) + 1.0e-6 >= options.margin, (
        plan.width,
        plan.height,
        VUV._minimum_aabb_gap(fitted),
    )


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


def _test_area_aware_quality_metrics_use_polygon_area():
    # Concave UV outline: polygon area is 0.48 while its AABB is 0.64.  A
    # rectangle-based estimate would incorrectly report a full island.
    shape = (
        (0.0, 0.0),
        (0.8, 0.0),
        (0.8, 0.8),
        (0.4, 0.4),
        (0.0, 0.8),
    )
    model = tuple((x, y, 0.0) for x, y in shape)
    uv = tuple((x + 0.1, y + 0.1) for x, y in shape)
    obj, mesh = _polygon_object("VUV_QualityConcave", (model,), (uv,))
    try:
        analysis = VUV.analyze_active_uv(obj)
        analysis.islands[0].is_small = True
        metrics = VUV.evaluate_layout_quality(
            obj,
            analysis=analysis,
            face_to_island=analysis.face_to_island,
            options=VUV.GroupLayoutOptions(align_geometry_direction=False),
        )
        assert abs(metrics["polygon_area"] - 0.48) < 1.0e-6, metrics
        assert abs(metrics["tile_aabb_area"] - 0.64) < 1.0e-6, metrics
        assert abs(metrics["aabb_fill"] - 0.75) < 1.0e-6, metrics
        assert abs(metrics["tile_polygon_coverage"] - 0.48) < 1.0e-6
        assert abs(metrics["island_area_p05"] - 0.48) < 1.0e-6
        assert abs(metrics["small_island_area_p10"] - 0.48) < 1.0e-6
        assert metrics["short_edge_p05"] > 0.0
        assert metrics["small_short_edge_p10"] > 0.0
        assert metrics["valid"]
        assert VUV.score_layout_candidate(
            metrics, metrics, VUV.GroupLayoutOptions()
        ) > 0.0
        assert math.isinf(
            VUV.score_layout_candidate({"valid": False}, metrics)
        )
    finally:
        _remove_object_and_mesh(obj, mesh)


def _test_area_score_prioritizes_small_island_tail():
    options = VUV.GroupLayoutOptions(
        area_score_weight=1.0,
        polygon_coverage_score_weight=0.0,
        aabb_fill_score_weight=0.0,
        short_edge_score_weight=0.0,
    )
    reference = {
        "valid": True,
        "small_islands": 20,
        "small_island_area_p05": 1.0,
        "small_island_area_p10": 1.0,
        "island_area_p05": 1.0,
        "island_area_p10": 1.0,
    }
    candidate_a = dict(
        reference,
        island_area_p05=2.0,
        island_area_p10=2.0,
        small_island_area_p05=0.8,
        small_island_area_p10=0.8,
    )
    candidate_b = dict(
        reference,
        island_area_p05=1.2,
        island_area_p10=1.2,
        small_island_area_p05=1.5,
        small_island_area_p10=1.5,
    )
    score_a = VUV.score_layout_candidate(candidate_a, reference, options)
    score_b = VUV.score_layout_candidate(candidate_b, reference, options)
    assert score_b > score_a, (score_a, score_b)


def _test_area_score_falls_back_without_small_islands():
    options = VUV.GroupLayoutOptions(
        area_score_weight=1.0,
        polygon_coverage_score_weight=0.0,
        aabb_fill_score_weight=0.0,
        short_edge_score_weight=0.0,
    )
    reference = {
        "valid": True,
        "small_islands": 0,
        "small_island_area_p05": 0.0,
        "small_island_area_p10": 0.0,
        "island_area_p05": 1.0,
        "island_area_p10": 1.0,
    }
    candidate = dict(reference, island_area_p05=1.5, island_area_p10=1.5)
    assert VUV.score_layout_candidate(candidate, reference, options) > 1.0


def _test_area_score_balances_aabb_fill_and_coverage():
    """A tight strip must not beat a fuller tile solely on AABB fill."""

    options = VUV.GroupLayoutOptions(
        area_score_weight=0.0,
        polygon_coverage_score_weight=0.0,
        aabb_fill_score_weight=1.0,
        short_edge_score_weight=0.0,
    )
    fuller = {
        "valid": True,
        "small_islands": 0,
        "aabb_fill": 0.5316403872,
        "tile_aabb_coverage": 0.7622686939,
    }
    tighter_strip = {
        "valid": True,
        "small_islands": 0,
        "aabb_fill": 0.6076863685,
        "tile_aabb_coverage": 0.6601791671,
    }
    fuller_score = VUV.score_layout_candidate(fuller, fuller, options)
    strip_score = VUV.score_layout_candidate(tighter_strip, fuller, options)
    assert fuller_score > strip_score, (fuller_score, strip_score)


def _geometry_stub(
    island_id,
    rotation,
    *,
    axis="Z",
    area=1.0,
    confidence=0.9,
):
    island = _island(
        island_id,
        (float(island_id) * 2.0, 0.0, 0.0,
         float(island_id) * 2.0 + 1.0, 1.0, 1.0),
    )
    island.area_3d = area
    island.geometry_axis_name = axis
    island.geometry_direction_space = "OBJECT"
    island.geometry_direction_vector = Vector((0.0, 1.0))
    island.geometry_direction_confidence = confidence
    island.geometry_rotation_angle = rotation
    return island


def _test_same_axis_geometry_cohort_uses_stable_common_angle():
    first = _geometry_stub(0, math.radians(10.0), area=4.0)
    second = _geometry_stub(1, math.radians(11.5), area=1.0)
    group = VUV.LayoutGroup(
        group_id=0,
        member_ids=(0, 1),
        reason="TOPOLOGY_BLOCK",
        anchor_ids=(0, 1),
        affinity_pairs=((0, 1),),
    )
    analysis = _analysis((first, second), layout_groups=(group,))
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        direction_residual_tolerance=math.radians(3.0),
        cohere_geometry_angle_groups=True,
    )
    angles = VUV._orientation_angles(analysis, settings)
    # The larger panel is the deterministic weighted anchor.  Sharing its
    # correction removes the visible 1.5-degree checker drift while remaining
    # inside the strict three-degree geometry residual contract.
    assert abs(VUV._angle_wrap(angles[0] - first.geometry_rotation_angle)) < 1.0e-12
    assert abs(VUV._angle_wrap(angles[1] - angles[0])) < 1.0e-12
    assert abs(VUV._angle_wrap(angles[1] - second.geometry_rotation_angle)) < math.radians(3.0)
    assert VUV._geometry_angle_cohorts(analysis, settings)


def _test_geometry_cohort_rejects_real_turn_or_axis_mix():
    first = _geometry_stub(0, 0.0, axis="Z")
    quarter = _geometry_stub(1, math.radians(90.0), axis="Z")
    cross_axis = _geometry_stub(2, math.radians(0.5), axis="X")
    group = VUV.LayoutGroup(
        group_id=0,
        member_ids=(0, 1, 2),
        reason="ROTATIONAL",
        anchor_ids=(0, 1, 2),
        affinity_pairs=((0, 1), (1, 2)),
    )
    analysis = _analysis((first, quarter, cross_axis), layout_groups=(group,))
    settings = VUV.GroupLayoutOptions(align_geometry_direction=True)
    angles = VUV._orientation_angles(analysis, settings)
    # A true quarter-turn and an incompatible tangent axis are kept as
    # separate contracts; neither is silently flattened into the Z anchor.
    assert abs(VUV._angle_wrap(angles[0])) < 1.0e-12
    assert abs(VUV._angle_wrap(angles[1] - quarter.geometry_rotation_angle)) < 1.0e-12
    assert abs(VUV._angle_wrap(angles[2] - cross_axis.geometry_rotation_angle)) < 1.0e-12
    assert not VUV._geometry_angle_cohorts(analysis, settings)


def _repeat_local_stub(island_id, local_angle, global_rotation):
    island = _island(
        island_id,
        (float(island_id) * 2.0, 0.0, 0.0,
         float(island_id) * 2.0 + 1.0, 1.0, 0.1),
        principal_angle=local_angle,
        direction_angle=local_angle,
        direction_confidence=0.95,
    )
    island.geometry_signature = "repeat-local-shape"
    island.geometry_axis_name = "Y"
    island.geometry_direction_vector = Vector((0.0, 1.0))
    island.geometry_direction_confidence = 0.95
    island.geometry_rotation_angle = global_rotation
    return island


def _assert_repeat_local_signed_frame(relation):
    source_angles = (math.radians(25.0), math.radians(-55.0))
    islands = tuple(
        _repeat_local_stub(index, angle, 0.0)
        for index, angle in enumerate(source_angles)
    )
    repeat = VUV.RepeatGroup(
        0, (0, 1), relation, 0.99, "repeat-local-shape"
    )
    analysis = _analysis(islands, (repeat,))
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        align_repeat_local_frame=True,
    )
    angles = VUV._orientation_angles(analysis, settings)
    for island in islands:
        # Intrinsic +V lands on UV +V for every repeated instance.  Its
        # clockwise perpendicular lands on +U, so the local frame retains
        # positive parity without a UV reflection.
        source_angle = _direction_angle(island)
        final_v = VUV._angle_wrap(source_angle + angles[island.island_id])
        final_u = VUV._angle_wrap(
            source_angle - math.pi * 0.5 + angles[island.island_id]
        )
        assert abs(VUV._angle_wrap(final_v - math.pi * 0.5)) < 1.0e-7
        assert abs(final_u) < 1.0e-7
        assert island.face_indices == (island.island_id,)
        source = Vector((0.37, -0.22))
        rotated = VUV._rotate_point(
            source, Vector((0.0, 0.0)), angles[island.island_id]
        )
        assert abs(rotated.length - source.length) < 1.0e-7
    report = analysis.repeat_local_frame_metrics
    assert report["applied_groups"] == 1
    assert report["applied_member_ids"] == [0, 1]


def _test_mirror_repeat_uses_local_signed_frame():
    _assert_repeat_local_signed_frame("MIRROR")


def _test_rotational_repeat_uses_local_signed_frame():
    _assert_repeat_local_signed_frame("ROTATIONAL")


def _test_generic_repeat_uses_local_signed_frame():
    _assert_repeat_local_signed_frame("REPEATED")


def _test_repeat_local_priority_and_global_fallback_are_explicit():
    local = _repeat_local_stub(0, math.pi * 0.5, 0.0)
    peer = _repeat_local_stub(1, math.pi * 0.5, 0.0)
    repeat = VUV.RepeatGroup(
        0, (0, 1), "MIRROR", 0.99, "repeat-local-shape"
    )
    settings = VUV.GroupLayoutOptions(
        align_geometry_direction=True,
        align_repeat_local_frame=True,
    )
    analysis = _analysis((local, peer), (repeat,))
    local_metrics = VUV._repeat_local_frame_metrics(analysis, settings)
    effective = VUV._effective_direction_contract({
        "misaligned_ids": [0, 1],
        "required_unresolved_ids": [],
        "unresolved_ids": [],
    }, local_metrics)
    assert local_metrics["valid"]
    assert local_metrics["priority"] == (
        "repeat_local_then_global_axis_fallback"
    )
    assert effective["global_axis_overridden_ids"] == [0, 1]
    assert effective["effective_misaligned_ids"] == []

    peer.direction_confidence = 0.0
    fallback_analysis = _analysis((local, peer), (repeat,))
    angles = VUV._orientation_angles(fallback_analysis, settings)
    assert angles[0] == local.geometry_rotation_angle
    assert angles[1] == peer.geometry_rotation_angle
    assert fallback_analysis.repeat_local_frame_metrics[
        "applied_member_ids"
    ] == []


def _test_common_axis_cardinal_preference_is_bounded():
    """A straighter shared long edge may win only inside explicit gates."""

    candidates = (
        {
            "Z": {
                "coherent": True,
                "stability": 0.80,
                "confidence": 0.80,
                "cardinal_error": math.radians(30.0),
                "cardinal_weight": 4.0,
            },
            "X": {
                "coherent": True,
                "stability": 0.70,
                "confidence": 0.90,
                "cardinal_error": math.radians(0.0),
                "cardinal_weight": 4.0,
            },
        },
        {
            "Z": {
                "coherent": True,
                "stability": 0.90,
                "confidence": 0.80,
                "cardinal_error": math.radians(30.0),
                "cardinal_weight": 1.0,
            },
            "X": {
                "coherent": True,
                "stability": 0.80,
                "confidence": 0.90,
                "cardinal_error": math.radians(0.0),
                "cardinal_weight": 1.0,
            },
        },
    )
    # Legacy/default behavior remains stability-first.
    assert VUV._select_common_geometry_axis(candidates, ("Z", "X", "Y")) == "Z"
    # A 30-degree weighted gain is worth a 0.10 weakest-stability loss.
    assert VUV._select_common_geometry_axis(
        candidates,
        ("Z", "X", "Y"),
        prefer_geometry_axis_cardinal=True,
        geometry_axis_cardinal_min_gain=math.radians(5.0),
        geometry_axis_cardinal_max_quality_loss=0.15,
    ) == "X"
    # Tightening either gate must retain the stable baseline.
    assert VUV._select_common_geometry_axis(
        candidates,
        ("Z", "X", "Y"),
        prefer_geometry_axis_cardinal=True,
        geometry_axis_cardinal_min_gain=math.radians(35.0),
        geometry_axis_cardinal_max_quality_loss=0.15,
    ) == "Z"
    assert VUV._select_common_geometry_axis(
        candidates,
        ("Z", "X", "Y"),
        prefer_geometry_axis_cardinal=True,
        geometry_axis_cardinal_min_gain=math.radians(5.0),
        geometry_axis_cardinal_max_quality_loss=0.05,
    ) == "Z"
    # An inherited/explicit axis is never overridden by the presentation pass.
    assert VUV._select_common_geometry_axis(
        candidates,
        ("Z", "X", "Y"),
        preferred_axis="Z",
        prefer_geometry_axis_cardinal=True,
    ) == "Z"


_test_repeat_anchor_order_and_packing()
_test_structure_adjacency_builds_bounded_macro_group()
_test_repeat_micro_chain_does_not_merge_whole_asset()
_test_small_topology_chain_records_owner_affinity()
_test_topology_continuity_cross_group_merge_is_bounded()
_test_geometry_continuity_blocks_are_bounded_and_packed_nearby()
_test_continuity_block_uses_coarse_diameter_ratio()
_test_soft_topology_pairs_are_bounded_and_deterministic()
_test_cross_component_peer_uses_local_endpoint_bounds()
_test_continuity_component_uses_centroid_ordered_rigid_grid()
_test_small_island_requires_small_model_and_uv_area()
_test_directed_u_repeats_use_360_orientation()
_test_directed_repeats_resolve_positive_negative_angle()
_test_near_180_directed_conflict_uses_line_equivalence()
_test_mixed_direction_confidence_uses_group_line_fallback()
_test_owner_cohort_uses_one_directed_orientation()
_test_cross_layout_repeat_keeps_quarter_turn_parity()
_test_cross_layout_repeat_stays_near_with_unrelated_groups()
_test_model_distance_precedes_material_for_small_owner()
_test_topology_owner_is_not_rejected_by_fallback_capacity()
_test_fallback_capacity_is_independent_per_owner()
_test_owner_cells_keep_many_fragments_local()
_test_owner_attachment_rack_follows_model_centroids_without_rotation()
_test_regular_owner_attachment_grid_preserves_order_and_density()
_test_repeat_owner_cohorts_stay_near_in_large_component()
_test_split_weapon_repeat_owner_graph_is_solved()
_test_split_weapon_dual_domain_owner_graph_is_solved()
_test_affinity_search_budget_is_shared_by_component()
_test_centrosymmetric_repeats_remain_180_equivalent()
_test_tiny_float32_similarity_tolerance()
_test_quantized_winding_stabilization_and_audit_fields()
_test_welded_quantized_repair_preserves_real_chart_forms()
_test_welded_repair_boundaries_and_full_rollback()
_test_writeback_snap_preserves_source_island_count()
_test_writeback_snap_skips_seams_and_non_manifold_edges()
_test_rotated_shared_boundary_uses_semantic_uv_epsilon()
_test_adaptive_replay_reaudits_committed_coordinates()
_test_adaptive_replay_exception_restores_source()
_test_non_repeat_cardinal_switch()
_test_long_rectangles_prefer_vertical_cardinal_axis()
_test_vertical_preference_can_be_disabled()
_test_incompatible_directed_repeat_downgrades_to_cardinal_geometry()
_test_directed_repeat_can_disable_cardinal_compatibility_gate()
_test_post_layout_owner_partition_reapplies_direction_policy()
_test_small_island_boost_is_uniform_and_bounded()
_test_square_pack_bias_improves_tile_utilization()
_test_shelf_order_ensemble_closes_blank_space_without_scale_loss()
_test_rigid_maxrects_is_deterministic_and_never_rotates()
_test_rigid_maxrects_longest_edge_gate_falls_back_to_shelf()
_test_low_anisotropy_boundary_edge_snaps_cardinal()
_test_geometry_direction_removes_standalone_180_flip()
_test_geometry_direction_auto_axis_falls_back_on_horizontal_cap()
_test_geometry_direction_auto_rejects_bimodal_preferred_axis()
_test_structure_group_binds_one_signed_geometry_axis()
_test_owner_child_uses_one_shared_signed_geometry_axis()
_test_owner_axis_seeds_incompatible_child_partition()
_test_common_axis_uses_weakest_member_stability_before_priority()
_test_structure_group_splits_incompatible_auto_axes_deterministically()
_test_geometry_direction_is_scale_invariant_for_tiny_islands()
_test_direction_resolution_gate_distinguishes_auto_and_explicit_axis()
_test_replay_identity_includes_unresolved_explicit_axis_islands()
_test_source_cells_use_compact_soft_ordering()
_test_source_cell_variable_shelf_preserves_order_and_density()
_test_source_cell_variable_shelf_respects_density_floor()
_test_margin_correction_with_one_iteration()
_test_loose_vertex_does_not_change_radius()
_test_area_aware_quality_metrics_use_polygon_area()
_test_area_score_prioritizes_small_island_tail()
_test_area_score_falls_back_without_small_islands()
_test_area_score_balances_aabb_fill_and_coverage()
_test_same_axis_geometry_cohort_uses_stable_common_angle()
_test_geometry_cohort_rejects_real_turn_or_axis_mix()
_test_mirror_repeat_uses_local_signed_frame()
_test_rotational_repeat_uses_local_signed_frame()
_test_generic_repeat_uses_local_signed_frame()
_test_repeat_local_priority_and_global_fallback_are_explicit()
_test_common_axis_cardinal_preference_is_bounded()
print("VUV_GROUP_LAYOUT_REGRESSION_OK")
