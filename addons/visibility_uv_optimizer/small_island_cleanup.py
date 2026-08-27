# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative topology-adjacent cleanup for fragmented hard-surface UVs."""

from __future__ import annotations

from collections import defaultdict
import math

import bmesh


CAP_CLASSES = {"RADIAL_CAP", "CAP"}
CYLINDER_CLASSES = {"CYLINDER_SIDE", "CYLINDER"}
PANEL_CLASSES = {"PLANAR_PANEL", "PANEL"}
BAND_CLASSES = {"BEVEL_STRIP", "BEVEL", "QUAD_STRIP", "STRIP"}
GENERAL_CLASSES = {"GENERAL"}


def _setting(settings, name, default):
    return getattr(settings, name, default)


def _perimeter_length(chart):
    selected = {face.index for face in chart}
    perimeter = 0.0
    for face in chart:
        for edge in face.edges:
            linked_inside = sum(
                neighbor.index in selected for neighbor in edge.link_faces
            )
            if linked_inside == 1:
                perimeter += max(edge.calc_length(), 1.0e-12)
    return perimeter


def _polygon_uv_area(face, uv_layer):
    points = [loop[uv_layer].uv for loop in face.loops]
    if len(points) < 3:
        return 0.0
    return abs(sum(
        points[index].x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * points[index].y
        for index in range(len(points))
    )) * 0.5


def _chart_uv_area(chart, uv_layer):
    return sum(_polygon_uv_area(face, uv_layer) for face in chart)


def is_small_chart(chart, uv_layer, total_mesh_area, total_uv_area, settings):
    """Return whether a chart belongs to the strict small-island cleanup lane."""

    face_limit = max(int(_setting(settings, "small_island_faces", 12)), 1)
    if len(chart) > face_limit:
        return False
    mesh_ratio_limit = max(
        float(_setting(settings, "small_island_area_ratio", 0.001)), 0.0
    )
    uv_ratio_limit = max(
        float(_setting(settings, "small_uv_area_ratio", 0.001)), 0.0
    )
    mesh_area = sum(face.calc_area() for face in chart)
    uv_area = _chart_uv_area(chart, uv_layer)
    return (
        mesh_area <= max(float(total_mesh_area), 0.0) * mesh_ratio_limit
        and uv_area <= max(float(total_uv_area), 0.0) * uv_ratio_limit
    )


def _class_family(classes):
    classes = set(classes)
    if not classes or classes & CAP_CLASSES or classes & CYLINDER_CLASSES:
        return None
    if classes <= PANEL_CLASSES:
        return "PANEL"
    if classes <= BAND_CLASSES:
        return "BAND"
    if classes <= GENERAL_CLASSES:
        return "GENERAL"
    return None


def _compatible_family(small_classes, target_classes):
    small_family = _class_family(small_classes)
    target_family = _class_family(target_classes)
    if small_family is not None and small_family == target_family:
        return small_family
    combined = set(small_classes) | set(target_classes)
    if not combined or combined & CAP_CLASSES or combined & CYLINDER_CLASSES:
        return None
    if combined <= PANEL_CLASSES | GENERAL_CLASSES:
        return "PANEL_GENERAL"
    if combined <= BAND_CLASSES | GENERAL_CLASSES:
        return "BAND_GENERAL"
    return None


def _angle_limit(settings, family):
    names = {
        "PANEL": ("small_panel_angle", math.radians(5.0)),
        "BAND": ("small_band_angle", math.radians(55.0)),
        "GENERAL": ("small_general_angle", math.radians(45.0)),
        "PANEL_GENERAL": ("small_panel_general_angle", math.radians(10.0)),
        "BAND_GENERAL": ("small_band_general_angle", math.radians(35.0)),
    }
    name, default = names[family]
    return float(_setting(settings, name, default))


def _candidate_records(
    bm,
    uv_layer,
    charts,
    chart_ids,
    total_area,
    total_uv_area,
    face_classes,
    locked_cuts,
    forced_cuts,
    blocked,
    settings,
    helpers,
    funnel,
):
    boundaries = defaultdict(list)
    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        left, right = edge.link_faces
        chart_left = chart_ids[left.index]
        chart_right = chart_ids[right.index]
        if chart_left == chart_right:
            continue
        boundaries[tuple(sorted((chart_left, chart_right)))].append(edge)

    target_multiplier = max(
        float(_setting(settings, "small_target_area_multiplier", 1.0)), 1.0
    )
    boundary_ratio_limit = max(
        float(_setting(settings, "small_boundary_ratio", 0.25)), 0.0
    )
    candidates = []
    for (left_id, right_id), boundary in boundaries.items():
        funnel["uv_adjacent_pairs"] += 1
        signature = frozenset(edge.index for edge in boundary)
        if signature in blocked:
            funnel["blocked"] += 1
            continue
        if any(
            edge.index in locked_cuts or edge.index in forced_cuts
            for edge in boundary
        ):
            funnel["hard_cut"] += 1
            continue
        if (
            bool(_setting(settings, "respect_sharp", False))
            and any(not edge.smooth for edge in boundary)
        ):
            funnel["sharp_edge"] += 1
            continue
        if any(
            edge.link_faces[0].material_index
            != edge.link_faces[1].material_index
            for edge in boundary
        ):
            funnel["boundary_material_mismatch"] += 1
            continue

        left_chart = charts[left_id]
        right_chart = charts[right_id]
        left_area = helpers._chart_area(left_chart)
        right_area = helpers._chart_area(right_chart)
        if left_area <= right_area:
            small_id, target_id = left_id, right_id
            small_chart, target_chart = left_chart, right_chart
            small_area, target_area = left_area, right_area
        else:
            small_id, target_id = right_id, left_id
            small_chart, target_chart = right_chart, left_chart
            small_area, target_area = right_area, left_area

        small_uv_area = _chart_uv_area(small_chart, uv_layer)
        if len(small_chart) > max(
            int(_setting(settings, "small_island_faces", 12)), 1
        ):
            funnel["too_many_faces"] += 1
            continue
        if not is_small_chart(
            small_chart,
            uv_layer,
            total_area,
            total_uv_area,
            settings,
        ):
            funnel["not_small"] += 1
            continue
        if target_area < small_area * target_multiplier:
            funnel["target_too_small"] += 1
            continue
        small_materials = {face.material_index for face in small_chart}
        target_materials = {face.material_index for face in target_chart}
        if len(small_materials) != 1 or small_materials != target_materials:
            funnel["island_material_mismatch"] += 1
            continue

        small_classes = {
            face_classes.get(face.index, "GENERAL") for face in small_chart
        }
        target_classes = {
            face_classes.get(face.index, "GENERAL") for face in target_chart
        }
        family = _compatible_family(small_classes, target_classes)
        if family is None:
            funnel["class_mismatch"] += 1
            continue

        lengths = [max(edge.calc_length(), 1.0e-12) for edge in boundary]
        shared_length = sum(lengths)
        shared_ratio = shared_length / max(
            _perimeter_length(small_chart), 1.0e-12
        )
        if shared_ratio < boundary_ratio_limit:
            funnel["shared_ratio"] += 1
            continue
        weighted_angle = sum(
            edge.calc_face_angle(0.0) * length
            for edge, length in zip(boundary, lengths)
        ) / shared_length
        if weighted_angle > _angle_limit(settings, family):
            funnel["angle"] += 1
            continue

        combined = small_chart + target_chart
        if not helpers._is_disk(combined):
            funnel["topology"] += 1
            continue
        funnel["eligible"] += 1
        candidates.append({
            "small_id": small_id,
            "target_id": target_id,
            "faces": combined,
            "boundary": boundary,
            "signature": signature,
            "shared_ratio": shared_ratio,
            "angle": weighted_angle,
            "target_area": target_area,
            "small_mesh_area_ratio": small_area / max(total_area, 1.0e-12),
            "small_uv_area_ratio": small_uv_area / max(total_uv_area, 1.0e-12),
            "family": family,
        })

    candidates.sort(key=lambda item: (
        -item["shared_ratio"],
        item["angle"],
        -item["target_area"],
        min(item["signature"], default=-1),
    ))
    return candidates


def _restore_candidate(
    bm,
    uv_layer,
    face_indices,
    boundary_indices,
    saved_uv,
    saved_seams,
    helpers,
):
    faces = [bm.faces[index] for index in face_indices]
    boundary = [bm.edges[index] for index in boundary_indices]
    helpers._restore_uv(faces, uv_layer, saved_uv)
    for edge in boundary:
        edge.seam = saved_seams[edge.index]


def stitch_small_islands(
    obj,
    bm,
    uv_layer,
    settings,
    constraints,
    helpers,
):
    """Join only high-confidence topology-adjacent fragments.

    ``helpers`` is the active ``uv_optimize`` module. Passing it explicitly
    avoids a circular module import while keeping all distortion and overlap
    tests identical to the main optimizer.
    """

    if not bool(_setting(settings, "small_cleanup_enabled", True)):
        _, charts = helpers._uv_charts(bm, uv_layer)
        return {
            "enabled": False,
            "initial_charts": len(charts),
            "final_charts": len(charts),
            "tests": 0,
            "accepted": 0,
        }

    locked_cuts = set(getattr(constraints, "locked_cuts", set()))
    forced_cuts = set(getattr(constraints, "forced_cuts", set()))
    face_classes = dict(getattr(constraints, "face_classes", {}))
    total_area = sum(face.calc_area() for face in bm.faces)
    _, initial_charts = helpers._uv_charts(bm, uv_layer)
    result = {
        "enabled": True,
        "initial_charts": len(initial_charts),
        "final_charts": len(initial_charts),
        "tests": 0,
        "accepted": 0,
        "rejected_operator": 0,
        "rejected_chart_delta": 0,
        "locked_cuts": len(locked_cuts),
        "forced_cuts": len(forced_cuts),
    }
    max_tests = max(int(_setting(settings, "small_cleanup_tests", 300)), 0)
    max_accepted = max(int(_setting(settings, "small_cleanup_max_merges", 120)), 0)
    blocked = set()
    funnel = defaultdict(int)

    old_p95 = settings.max_p95_stretch
    old_max = settings.max_stretch
    settings.max_p95_stretch = min(
        float(old_p95),
        float(_setting(settings, "small_cleanup_p95", 1.35)),
    )
    settings.max_stretch = min(
        float(old_max),
        float(_setting(settings, "small_cleanup_max_stretch", 2.0)),
    )
    try:
        while result["tests"] < max_tests and result["accepted"] < max_accepted:
            chart_ids, charts = helpers._uv_charts(bm, uv_layer)
            # Each accepted unwrap can change total UV coverage.  Recompute the
            # denominator so later candidates use the configured ratio against
            # the layout they are actually being considered in.
            total_uv_area = sum(
                _chart_uv_area(chart, uv_layer) for chart in charts
            )
            candidates = _candidate_records(
                bm,
                uv_layer,
                charts,
                chart_ids,
                total_area,
                total_uv_area,
                face_classes,
                locked_cuts,
                forced_cuts,
                blocked,
                settings,
                helpers,
                funnel,
            )
            if not candidates:
                break
            candidate = candidates[0]
            result["tests"] += 1
            before_count = len(charts)
            face_indices = tuple(
                sorted(face.index for face in candidate["faces"])
            )
            boundary_indices = tuple(
                sorted(edge.index for edge in candidate["boundary"])
            )
            saved_uv = helpers._save_uv(candidate["faces"], uv_layer)
            saved_seams = {
                edge.index: edge.seam for edge in candidate["boundary"]
            }
            accepted, reason = helpers._try_merge(
                obj,
                bm,
                uv_layer,
                candidate["faces"],
                candidate["boundary"],
                settings,
                reject_overlap=True,
            )
            bm, uv_layer = helpers._refresh_edit_bmesh(obj.data)
            if not accepted:
                result["rejected_operator"] += 1
                result["rejected_" + str(reason).lower()] = (
                    result.get("rejected_" + str(reason).lower(), 0) + 1
                )
                blocked.add(candidate["signature"])
                continue
            _, after_charts = helpers._uv_charts(bm, uv_layer)
            if len(after_charts) != before_count - 1:
                _restore_candidate(
                    bm,
                    uv_layer,
                    face_indices,
                    boundary_indices,
                    saved_uv,
                    saved_seams,
                    helpers,
                )
                bmesh.update_edit_mesh(
                    obj.data,
                    loop_triangles=False,
                    destructive=False,
                )
                result["rejected_chart_delta"] += 1
                blocked.add(candidate["signature"])
                continue
            result["accepted"] += 1
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=False,
            )
    finally:
        settings.max_p95_stretch = old_p95
        settings.max_stretch = old_max

    _, final_charts = helpers._uv_charts(bm, uv_layer)
    result["final_charts"] = len(final_charts)
    result["filter_funnel"] = dict(funnel)
    return result
