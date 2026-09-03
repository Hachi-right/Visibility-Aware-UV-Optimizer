"""Read-only hard-surface UV benchmark for a real Blender scene.

Run with either supported Blender version, for example::

    blender -b asset.blend --factory-startup \
      --python tests/benchmark_hard_surface_scene.py -- \
      --layer VUV_StructuredLocalGrid_v42 --output report.json

The script never saves the blend file.  It measures fragmentation, packing,
the signed +V direction contract, and the complete U/V tangent-frame signal.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Vector


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "addons"
    / "visibility_uv_optimizer"
    / "uv_group_layout.py"
)
DEFAULT_OBJECT_SUFFIXES = (
    "RIFLE_SM_Model.Body",
    "RIFLE_SM_Model_Bullet",
    "RIFLE_SM_Model_Gun_Head.001",
)
STRICT_PLANAR_NORMAL_TOLERANCE_DEGREES = 0.1
STRICT_PLANAR_DISTANCE_RATIO = 1.0e-5


def _arguments():
    raw = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--object-suffix",
        action="append",
        dest="object_suffixes",
        help="Repeat to override the three weapon object suffixes.",
    )
    parser.add_argument("--direction-space", choices=("OBJECT", "WORLD"), default="WORLD")
    parser.add_argument("--direction-axis", choices=("AUTO", "X", "Y", "Z"), default="AUTO")
    parser.add_argument("--auto-priority", default="YZX")
    parser.add_argument(
        "--ignore-persisted-contract",
        action="store_true",
        help="Re-resolve AUTO axes instead of replaying the saved face-set contract.",
    )
    parser.add_argument("--small-face-count", type=int, default=32)
    parser.add_argument("--small-area-ratio", type=float, default=0.0025)
    parser.add_argument("--small-uv-area-ratio", type=float, default=0.0025)
    parser.add_argument("--tolerance-degrees", type=float, default=3.0)
    parser.add_argument(
        "--planar-angle-degrees",
        type=float,
        default=STRICT_PLANAR_NORMAL_TOLERANCE_DEGREES,
        help="Strict triangle-normal spread tolerance (default: 0.1 degrees).",
    )
    parser.add_argument(
        "--planar-distance-ratio",
        type=float,
        default=STRICT_PLANAR_DISTANCE_RATIO,
        help=(
            "Maximum point-to-plane error divided by the chart bounding-box "
            "diagonal (default: 1e-5)."
        ),
    )
    return parser.parse_args(raw)


def _load_layout_module():
    name = "vuv_scene_benchmark_layout"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _quantile(values, fraction):
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return 0.0
    position = int(round((len(finite) - 1) * max(0.0, min(1.0, fraction))))
    return finite[position]


def _uv_hash(layer):
    digest = hashlib.sha256()
    for item in layer.data:
        digest.update(struct.pack("<dd", round(float(item.uv.x), 9), round(float(item.uv.y), 9)))
    return digest.hexdigest()


def _line_delta(left, right):
    delta = float(left) - float(right)
    while delta >= math.pi * 0.5:
        delta -= math.pi
    while delta < -math.pi * 0.5:
        delta += math.pi
    return abs(delta)


def _frame_space_point(obj, point, space):
    if str(space).upper() == "WORLD":
        return obj.matrix_world @ point
    return point.copy()


def _strict_planarity_report(
    obj,
    mesh,
    island,
    direction_space,
    normal_tolerance_degrees=STRICT_PLANAR_NORMAL_TOLERANCE_DEGREES,
    distance_ratio=STRICT_PLANAR_DISTANCE_RATIO,
):
    """Mirror the optimizer's strict triangle-normal and plane-error gates."""

    selected = {int(index) for index in island.face_indices}
    mesh.calc_loop_triangles()
    weighted_normal = Vector((0.0, 0.0, 0.0))
    triangle_normals = []
    for triangle in mesh.loop_triangles:
        if int(triangle.polygon_index) not in selected:
            continue
        points = tuple(
            _frame_space_point(
                obj,
                mesh.vertices[mesh.loops[int(loop_index)].vertex_index].co,
                direction_space,
            )
            for loop_index in triangle.loops
        )
        cross = (points[1] - points[0]).cross(points[2] - points[0])
        if (
            cross.length_squared <= 1.0e-20
            or not all(math.isfinite(float(value)) for value in cross)
        ):
            continue
        triangle_normals.append(cross.normalized())
        weighted_normal += cross
    if not triangle_normals or weighted_normal.length_squared <= 1.0e-20:
        return {
            "is_planar": False,
            "reason": "degenerate_geometry",
            "normal_spread_degrees": None,
            "plane_error_ratio": None,
        }

    normal = weighted_normal.normalized()
    normal_spread = math.degrees(max(
        math.acos(max(-1.0, min(1.0, float(value.dot(normal)))))
        for value in triangle_normals
    ))
    tolerance = math.radians(float(normal_tolerance_degrees))
    cosine_limit = math.cos(max(min(tolerance, math.pi), 0.0))
    if any(
        float(value.dot(normal)) + 1.0e-9 < cosine_limit
        for value in triangle_normals
    ):
        return {
            "is_planar": False,
            "reason": "normal_spread",
            "normal_spread_degrees": normal_spread,
            "plane_error_ratio": None,
        }

    vertex_indices = sorted({
        int(vertex_index)
        for face_index in selected
        for vertex_index in mesh.polygons[face_index].vertices
    })
    points = [
        _frame_space_point(
            obj, mesh.vertices[index].co, direction_space
        )
        for index in vertex_indices
    ]
    if len(points) < 3:
        return {
            "is_planar": False,
            "reason": "degenerate_geometry",
            "normal_spread_degrees": normal_spread,
            "plane_error_ratio": None,
        }
    origin = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
    minimum = Vector(tuple(
        min(point[axis] for point in points) for axis in range(3)
    ))
    maximum = Vector(tuple(
        max(point[axis] for point in points) for axis in range(3)
    ))
    diagonal = (maximum - minimum).length
    if not math.isfinite(diagonal) or diagonal <= 1.0e-10:
        return {
            "is_planar": False,
            "reason": "degenerate_geometry",
            "normal_spread_degrees": normal_spread,
            "plane_error_ratio": None,
        }
    plane_error = max(
        abs(float((point - origin).dot(normal))) for point in points
    )
    relative_error = plane_error / diagonal
    if (
        not math.isfinite(relative_error)
        or relative_error > float(distance_ratio) + 1.0e-12
    ):
        return {
            "is_planar": False,
            "reason": "point_plane_error",
            "normal_spread_degrees": normal_spread,
            "plane_error_ratio": relative_error,
        }
    return {
        "is_planar": True,
        "reason": "strict_planar",
        "normal_spread_degrees": normal_spread,
        "plane_error_ratio": relative_error,
    }


def _adjacency_components(analysis):
    graph = defaultdict(set)
    island_ids = {int(island.island_id) for island in analysis.islands}
    for island_id in island_ids:
        graph[island_id]
    for link in analysis.adjacency:
        left = int(link.left_id)
        right = int(link.right_id)
        graph[left].add(right)
        graph[right].add(left)
    components = []
    pending = set(island_ids)
    while pending:
        start = min(pending)
        pending.remove(start)
        queue = deque((start,))
        members = []
        while queue:
            current = queue.popleft()
            members.append(current)
            for neighbor in sorted(graph[current]):
                if neighbor in pending:
                    pending.remove(neighbor)
                    queue.append(neighbor)
        components.append(tuple(sorted(members)))
    return components


def _repeat_metrics(layout, analysis, direction):
    by_id = {int(island.island_id): island for island in analysis.islands}
    global_misaligned = set(
        int(value) for value in direction.get("misaligned_ids", ())
    )
    signed_checker_errors = set(
        int(value)
        for value in direction.get(
            "effective_misaligned_ids", global_misaligned
        )
    )
    fallback = set(int(value) for value in direction.get("frame_fallback_ids", ()))
    mixed_axis = 0
    global_axis_difference_groups = 0
    signed_checker_error_groups = 0
    fallback_groups = 0
    non_cardinal_groups = 0
    spreads = []
    details = []
    for group in analysis.repeat_groups:
        member_ids = [int(value) for value in group.member_ids if int(value) in by_id]
        if len(member_ids) < 2:
            continue
        axes = sorted({
            by_id[value].geometry_axis_name or "UNRESOLVED"
            for value in member_ids
        })
        references = []
        for member_id in member_ids:
            reference = layout._orientation_reference_angle(by_id[member_id])
            if reference is not None and math.isfinite(float(reference)):
                references.append(float(reference))
        spread = 0.0
        if references:
            spread = max(
                _line_delta(left, right)
                for left in references
                for right in references
            )
        spread_degrees = math.degrees(spread)
        spreads.append(spread_degrees)
        group_global_difference = bool(
            set(member_ids) & global_misaligned
        )
        group_checker_error = bool(
            set(member_ids) & signed_checker_errors
        )
        group_fallback = bool(set(member_ids) & fallback)
        if len(axes) > 1:
            mixed_axis += 1
        if group_global_difference:
            global_axis_difference_groups += 1
        if group_checker_error:
            signed_checker_error_groups += 1
        if group_fallback:
            fallback_groups += 1
        if spread_degrees > 3.0 + 1.0e-7:
            non_cardinal_groups += 1
        details.append({
            "id": int(group.group_id),
            "members": member_ids,
            "axes": axes,
            "presentation_shape_heading_spread_degrees": round(
                spread_degrees, 6
            ),
            "has_global_axis_difference": group_global_difference,
            "has_signed_checker_error": group_checker_error,
            "has_frame_fallback": group_fallback,
        })
    return {
        "shape_heading_metric_role": "presentation_only_not_signed_direction",
        "groups": len(details),
        "mixed_geometry_axis_groups": mixed_axis,
        "signed_checker_error_groups": signed_checker_error_groups,
        "global_axis_difference_groups": global_axis_difference_groups,
        "frame_fallback_groups": fallback_groups,
        "presentation_shape_heading_nonuniform_groups": non_cardinal_groups,
        "presentation_shape_heading_spread_p95_degrees": round(
            _quantile(spreads, 0.95), 6
        ),
        "presentation_shape_heading_spread_max_degrees": round(
            max(spreads, default=0.0), 6
        ),
        "details": details,
    }


def _measure_object(
    layout,
    obj,
    layer_name,
    settings,
    planar_angle_degrees,
    planar_distance_ratio,
):
    mesh = obj.data
    layer = mesh.uv_layers.get(layer_name)
    if layer is None:
        return None
    previous = mesh.uv_layers.active
    mesh.uv_layers.active = layer
    try:
        analysis = layout.analyze_active_uv(obj, settings)
        audit = layout.audit_active_uv(
            obj, analysis.face_to_island, epsilon=settings.uv_epsilon
        )
        quality = layout.evaluate_layout_quality(
            obj,
            analysis=analysis,
            face_to_island=analysis.face_to_island,
            audit=audit,
            epsilon=settings.uv_epsilon,
            options=settings,
        )
    finally:
        mesh.uv_layers.active = previous

    direction = quality.get("directed_geometry", {}) or {}
    face_counts = [len(island.face_indices) for island in analysis.islands]
    islands = len(face_counts)
    faces = len(mesh.polygons)
    micro_ids = {
        int(island.island_id)
        for island in analysis.islands
        if len(island.face_indices) <= 4
    }
    micro_faces = sum(
        len(island.face_indices)
        for island in analysis.islands
        if int(island.island_id) in micro_ids
    )
    layout_sizes = [len(group.member_ids) for group in analysis.layout_groups]
    components = _adjacency_components(analysis)
    component_sizes = [len(value) for value in components]
    record_map = direction.get("records", {}) or {}
    records = list(record_map.values())
    frame_axis_residuals = [
        record.get("frame_axis_residual_degrees")
        for record in records
        if record.get("frame_axis_residual_degrees") is not None
    ]
    planarity_by_id = {
        int(island.island_id): _strict_planarity_report(
            obj,
            mesh,
            island,
            settings.direction_space,
            normal_tolerance_degrees=planar_angle_degrees,
            distance_ratio=planar_distance_ratio,
        )
        for island in analysis.islands
    }
    planar_ids = {
        island_id
        for island_id, report in planarity_by_id.items()
        if bool(report["is_planar"])
    }
    frame_fallback_ids = set(
        int(value) for value in direction.get("frame_fallback_ids", ())
    )
    frame_unresolved_ids = set(
        int(value) for value in direction.get("frame_unresolved_ids", ())
    )
    frame_misaligned_ids = set(
        int(value) for value in direction.get("frame_misaligned_ids", ())
    )
    frame_negative_ids = set(
        int(value) for value in direction.get("frame_negative_parity_ids", ())
    )
    strict_frame_failure_ids = (
        frame_fallback_ids
        | frame_unresolved_ids
        | frame_misaligned_ids
        | frame_negative_ids
    )
    planar_frame_failure_ids = planar_ids & strict_frame_failure_ids
    curved_ids = set(planarity_by_id) - planar_ids
    planarity_rejections = Counter(
        str(report["reason"])
        for report in planarity_by_id.values()
        if not bool(report["is_planar"])
    )
    contract_key = layout._geometry_axis_contract_key(layer.name)
    try:
        contract_present = contract_key in mesh.keys()
    except (AttributeError, RuntimeError, TypeError):
        contract_present = False

    return {
        "object": obj.name,
        "mesh": mesh.name,
        "faces": faces,
        "edges": len(mesh.edges),
        "seam_edges": sum(bool(edge.use_seam) for edge in mesh.edges),
        "uv_hash_sha256": _uv_hash(layer),
        "persisted_direction_contract": bool(contract_present),
        "fragmentation": {
            "islands": islands,
            "islands_per_1000_faces": round(1000.0 * islands / max(faces, 1), 6),
            "single_face_islands": sum(value == 1 for value in face_counts),
            "single_face_island_ratio": round(
                sum(value == 1 for value in face_counts) / max(islands, 1), 6
            ),
            "micro_islands_le_4_faces": len(micro_ids),
            "micro_island_ratio": round(len(micro_ids) / max(islands, 1), 6),
            "faces_in_micro_islands": micro_faces,
            "faces_in_micro_island_ratio": round(micro_faces / max(faces, 1), 6),
            "small_islands": sum(bool(island.is_small) for island in analysis.islands),
            "face_count_histogram": {
                str(key): int(value) for key, value in sorted(Counter(face_counts).items())
            },
            "topology_components": len(components),
            "multi_island_topology_components": sum(value > 1 for value in component_sizes),
            "islands_per_topology_component_p95": _quantile(component_sizes, 0.95),
            "islands_per_topology_component_max": max(component_sizes, default=0),
        },
        "structure_layout": {
            "layout_groups": len(layout_sizes),
            "singleton_layout_groups": sum(value == 1 for value in layout_sizes),
            "members_per_group_p50": _quantile(layout_sizes, 0.50),
            "members_per_group_p95": _quantile(layout_sizes, 0.95),
            "members_per_group_max": max(layout_sizes, default=0),
        },
        "packing": {
            "tile_polygon_coverage": round(float(quality.get("tile_polygon_coverage", 0.0)), 9),
            "tile_aabb_coverage": round(float(quality.get("tile_aabb_coverage", 0.0)), 9),
            "aabb_fill": round(float(quality.get("aabb_fill", 0.0)), 9),
            "tile_bounds": quality.get("tile_bounds", ()),
            "inside_tile": bool(audit.get("inside_tile", False)),
            "overlap": bool(audit.get("overlap", False)),
            "degenerate_triangles": int(audit.get("degenerate", 0)),
            "negative_triangles": int(audit.get("negative", 0)),
            "valid": bool(audit.get("valid", False)),
        },
        "direction": {
            "valid": bool(quality.get("directed_geometry_valid", False)),
            "resolved": int(direction.get("resolved_islands", 0)),
            "unresolved": int(direction.get("unresolved_islands", 0)),
            "misaligned": int(direction.get("misaligned_islands", 0)),
            "effective_misaligned": int(
                direction.get(
                    "effective_misaligned_islands",
                    direction.get("misaligned_islands", 0),
                )
            ),
            "global_axis_overridden_ids": list(
                direction.get("global_axis_overridden_ids", ())
            ),
            "opposite_180": int(direction.get("opposite_islands", 0)),
            "quarter_turn_90": int(direction.get("quarter_turn_islands", 0)),
            "axis_residual_p95_degrees": round(float(direction.get("residual_p95_degrees", 0.0)), 6),
            "axis_residual_max_degrees": round(float(direction.get("residual_max_degrees", 0.0)), 6),
            "frame_resolved": int(direction.get("frame_resolved_islands", 0)),
            "frame_unresolved": int(direction.get("frame_unresolved_islands", 0)),
            "frame_misaligned": int(direction.get("frame_misaligned_islands", 0)),
            "frame_fallback": int(direction.get("frame_fallback_islands", 0)),
            "frame_negative_parity": int(direction.get("frame_negative_parity_islands", 0)),
            "frame_residual_p95_degrees": round(float(direction.get("frame_residual_p95_degrees", 0.0)), 6),
            "frame_residual_max_degrees": round(float(direction.get("frame_residual_max_degrees", 0.0)), 6),
            "frame_axis_residual_p95_degrees": round(_quantile(frame_axis_residuals, 0.95), 6),
            "frame_axis_residual_max_degrees": round(max(frame_axis_residuals, default=0.0), 6),
            "planar_angle_degrees": round(float(planar_angle_degrees), 6),
            "planar_distance_ratio": float(planar_distance_ratio),
            "planar_islands": len(planar_ids),
            "curved_or_mixed_normal_islands": len(curved_ids),
            "strict_planarity_rejection_reasons": {
                key: int(value)
                for key, value in sorted(planarity_rejections.items())
            },
            "planar_frame_failures": len(planar_frame_failure_ids),
            "planar_frame_failure_ratio": round(
                len(planar_frame_failure_ids) / max(len(planar_ids), 1), 6
            ),
            "planar_frame_fallback": len(planar_ids & frame_fallback_ids),
            "planar_frame_unresolved": len(planar_ids & frame_unresolved_ids),
            "planar_frame_misaligned": len(planar_ids & frame_misaligned_ids),
            "planar_frame_negative_parity": len(planar_ids & frame_negative_ids),
            "curved_frame_fallback": len(curved_ids & frame_fallback_ids),
            "curved_frame_unresolved": len(curved_ids & frame_unresolved_ids),
            "frame_fallback_ids": sorted(frame_fallback_ids),
            "frame_unresolved_ids": sorted(frame_unresolved_ids),
            "planar_frame_failure_ids": sorted(planar_frame_failure_ids),
        },
        "repeat_local_frame": dict(
            quality.get("repeat_local_frame", {}) or {}
        ),
        "repeat_consistency": _repeat_metrics(layout, analysis, direction),
    }


def main():
    args = _arguments()
    layout = _load_layout_module()
    settings = layout.GroupLayoutOptions(
        small_face_count=max(int(args.small_face_count), 1),
        small_area_ratio=max(float(args.small_area_ratio), 0.0),
        small_uv_area_ratio=max(float(args.small_uv_area_ratio), 0.0),
        align_geometry_direction=True,
        align_repeat_local_frame=True,
        direction_space=args.direction_space,
        direction_axis=args.direction_axis,
        direction_auto_priority=args.auto_priority,
        restore_persisted_direction_contract=not args.ignore_persisted_contract,
        direction_residual_tolerance=math.radians(args.tolerance_degrees),
        align_geometry_frame=True,
        use_geometry_frame_rotation=True,
        cohere_geometry_angle_groups=True,
        cohere_geometry_long_edge=True,
        preserve_source_layout=True,
        source_layout_affinity_weight=0.65,
        allow_group_quarter_turn=False,
    ).validated()
    suffixes = tuple(args.object_suffixes or DEFAULT_OBJECT_SUFFIXES)
    objects = []
    for obj in sorted(bpy.data.objects, key=lambda value: value.name):
        if obj.type != "MESH" or not any(obj.name.endswith(value) for value in suffixes):
            continue
        measured = _measure_object(
            layout,
            obj,
            args.layer,
            settings,
            args.planar_angle_degrees,
            args.planar_distance_ratio,
        )
        if measured is not None:
            objects.append(measured)
    if not objects:
        raise RuntimeError("No matching mesh contains UV layer {!r}".format(args.layer))

    output = {
        "schema": "vuv-hard-surface-benchmark-v1",
        "blender_version": bpy.app.version_string,
        "blend_file": bpy.data.filepath,
        "module_path": str(MODULE_PATH),
        "uv_layer": args.layer,
        "settings": {
            "direction_space": settings.direction_space,
            "direction_axis": settings.direction_axis,
            "direction_auto_priority": settings.direction_auto_priority,
            "restore_persisted_direction_contract": (
                settings.restore_persisted_direction_contract
            ),
            "direction_tolerance_degrees": args.tolerance_degrees,
            "planar_angle_degrees": args.planar_angle_degrees,
            "planar_distance_ratio": args.planar_distance_ratio,
            "small_face_count": settings.small_face_count,
            "small_area_ratio": settings.small_area_ratio,
            "small_uv_area_ratio": settings.small_uv_area_ratio,
        },
        "objects": objects,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    print("VUV_HARD_SURFACE_BENCHMARK_OK {}".format(output_path))


main()
