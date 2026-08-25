# SPDX-License-Identifier: GPL-2.0-or-later

import math
from collections import defaultdict
from dataclasses import dataclass

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import probe_analysis
from .visibility import effective_face_visibility
from .visibility import write_face_float


_LOW_VISIBILITY_SCALE = 0.01


@dataclass
class OptimizeResult:
    initial_islands: int
    final_islands: int
    merge_tests: int
    accepted_merges: int
    rejected_stretch: int
    rejected_overlap: int
    rejected_topology: int
    detail_values: list
    rejected_probe: int = 0
    probe_guided_merges: int = 0


def _set_uv_selection(bm, uv_layer, selected_faces):
    selected = set(selected_faces)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        state = face.index in selected
        face.select_set(state)
        if state:
            for vertex in face.verts:
                vertex.select_set(True)
            for edge in face.edges:
                edge.select_set(True)
        for loop in face.loops:
            loop[uv_layer].select = state
            loop[uv_layer].select_edge = state


def _uv_at_vertex(face, vertex, uv_layer):
    for loop in face.loops:
        if loop.vert == vertex:
            return loop[uv_layer].uv
    raise RuntimeError("Face does not contain edge vertex")


def _derive_seams_from_uv(bm, uv_layer, locked_seams):
    for edge in bm.edges:
        if edge.index in locked_seams:
            edge.seam = True
            continue
        if len(edge.link_faces) != 2:
            edge.seam = False
            continue
        face_a, face_b = edge.link_faces
        discontinuous = False
        for vertex in edge.verts:
            uv_a = _uv_at_vertex(face_a, vertex, uv_layer)
            uv_b = _uv_at_vertex(face_b, vertex, uv_layer)
            if (uv_a - uv_b).length_squared > 1e-12:
                discontinuous = True
                break
        edge.seam = discontinuous


def _charts(bm):
    chart_ids = [-1] * len(bm.faces)
    charts = []
    for face in bm.faces:
        if chart_ids[face.index] >= 0:
            continue
        chart_index = len(charts)
        pending = [face]
        chart = []
        chart_ids[face.index] = chart_index
        while pending:
            current = pending.pop()
            chart.append(current)
            for edge in current.edges:
                if edge.seam:
                    continue
                for neighbor in edge.link_faces:
                    if chart_ids[neighbor.index] < 0:
                        chart_ids[neighbor.index] = chart_index
                        pending.append(neighbor)
        charts.append(chart)
    return chart_ids, charts


def _chart_area(chart):
    return sum(face.calc_area() for face in chart)


def _compute_face_detail(bm):
    """Estimate local signal/detail complexity from weighted normal variation."""
    values = [0.0] * len(bm.faces)
    bm.faces.index_update()
    for face in bm.faces:
        total_length = 0.0
        variation = 0.0
        for edge in face.edges:
            length = max(edge.calc_length(), 1e-12)
            neighbors = [neighbor for neighbor in edge.link_faces if neighbor != face]
            if not neighbors:
                continue
            angle = edge.calc_face_angle(0.0)
            variation += angle * length
            total_length += length
        if total_length > 1e-12:
            values[face.index] = min(variation / total_length / math.pi, 1.0)
    maximum = max(values, default=0.0)
    if maximum > 1e-12:
        values = [value / maximum for value in values]
    return values


def _boundary_alignment(boundary):
    directions = []
    for edge in boundary:
        direction = edge.verts[1].co - edge.verts[0].co
        if direction.length_squared > 1e-12:
            directions.append(direction.normalized())
    if len(directions) < 2:
        return 0.0
    reference = directions[0]
    return sum(abs(reference.dot(direction)) for direction in directions[1:]) / (len(directions) - 1)


def _developable_band_bonus(chart_left, chart_right, boundary, settings):
    if not settings.developable_enabled:
        return 0.0
    total_length = sum(max(edge.calc_length(), 1e-12) for edge in boundary)
    if total_length <= 1e-12:
        return 0.0
    average_angle = sum(
        edge.calc_face_angle(0.0) * max(edge.calc_length(), 1e-12)
        for edge in boundary
    ) / total_length
    if average_angle > settings.developable_angle:
        return 0.0
    alignment = _boundary_alignment(boundary)
    if alignment < 0.72:
        return 0.0
    combined = chart_left + chart_right
    normal_sum = sum((face.normal for face in combined), Vector((0.0, 0.0, 0.0)))
    if normal_sum.length_squared <= 1e-12:
        return 0.0
    average_normal = normal_sum.normalized()
    spread = sum(
        face.normal.angle(average_normal) * max(face.calc_area(), 1e-12)
        for face in combined
    ) / max(sum(face.calc_area() for face in combined), 1e-12)
    # A long, directionally coherent band with moderate normal spread is
    # characteristic of a cylinder, cone, or bevel strip.
    spread_factor = max(0.0, 1.0 - spread / math.pi)
    angle_factor = max(0.0, 1.0 - average_angle / max(settings.developable_angle, 1e-8))
    return settings.developable_bonus * alignment * spread_factor * angle_factor


def _boundary_seam_cost(boundary, visibility, settings):
    """Estimate how objectionable it is to leave this boundary as a seam."""
    total_length = sum(max(edge.calc_length(), 1e-12) for edge in boundary)
    if total_length <= 1e-12:
        return 0.0
    cost = 0.0
    for edge in boundary:
        length = max(edge.calc_length(), 1e-12)
        faces = list(edge.link_faces)
        face_visibility = max(
            (visibility[face.index] for face in faces if face.index < len(visibility)),
            default=0.0,
        )
        edge_cost = face_visibility * settings.seam_visibility_weight
        if not edge.smooth:
            edge_cost -= settings.seam_hard_edge_bonus
        if len(faces) == 2 and not edge.is_convex:
            edge_cost -= settings.seam_concave_bonus
        cost += edge_cost * length
    return cost / total_length


def _is_disk(faces):
    vertices = {vertex.index for face in faces for vertex in face.verts}
    edges = {edge.index for face in faces for edge in face.edges}
    return len(vertices) - len(edges) + len(faces) == 1


def _triangle_data(faces, uv_layer):
    triangles = []
    for face in faces:
        loops = list(face.loops)
        if len(loops) < 3:
            continue
        coordinates = [loop.vert.co.copy() for loop in loops]
        tessellated = tessellate_polygon([coordinates])
        if not tessellated:
            tessellated = [
                (0, index, index + 1)
                for index in range(1, len(loops) - 1)
            ]
        for triangle in tessellated:
            if isinstance(triangle[0], int):
                indices = triangle
            else:
                indices = tuple(min(
                    range(len(coordinates)),
                    key=lambda index: (coordinates[index] - point).length_squared,
                ) for point in triangle)
            tri_loops = tuple(loops[index] for index in indices)
            points = tuple(loop.vert.co.copy() for loop in tri_loops)
            uvs = tuple(loop[uv_layer].uv.copy() for loop in tri_loops)
            triangles.append((face.index, points, uvs))
    return triangles


def _triangle_stretch(points, uvs):
    p0, p1, p2 = points
    e1 = p1 - p0
    e2 = p2 - p0
    x = e1.length
    if x <= 1e-12:
        return math.inf, 0.0, 0.0
    axis = e1 / x
    sx = e2.dot(axis)
    sy_squared = max(e2.length_squared - sx * sx, 0.0)
    sy = math.sqrt(sy_squared)
    if sy <= 1e-12:
        return math.inf, 0.0, 0.0

    du1 = uvs[1] - uvs[0]
    du2 = uvs[2] - uvs[0]
    j00 = du1.x / x
    j01 = (du2.x - du1.x * sx / x) / sy
    j10 = du1.y / x
    j11 = (du2.y - du1.y * sx / x) / sy
    det = j00 * j11 - j01 * j10

    a = j00 * j00 + j10 * j10
    b = j00 * j01 + j10 * j11
    d = j01 * j01 + j11 * j11
    trace = a + d
    root = math.sqrt(max((a - d) * (a - d) + 4.0 * b * b, 0.0))
    lambda_max = max((trace + root) * 0.5, 0.0)
    lambda_min = max((trace - root) * 0.5, 0.0)
    if lambda_min <= 1e-18:
        stretch = math.inf
    else:
        stretch = math.sqrt(lambda_max / lambda_min)
    area = e1.cross(e2).length * 0.5
    return stretch, area, det


def _weighted_percentile(values, percentile):
    if not values:
        return math.inf
    ordered = sorted(values, key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    if total <= 1e-18:
        return math.inf
    target = total * percentile
    accumulated = 0.0
    for value, weight in ordered:
        accumulated += weight
        if accumulated >= target:
            return value
    return ordered[-1][0]


def _measure_distortion(triangles):
    values = []
    maximum = 1.0
    has_positive = False
    has_negative = False
    degenerate = False
    for _, points, uvs in triangles:
        stretch, area, determinant = _triangle_stretch(points, uvs)
        values.append((stretch, max(area, 1e-18)))
        maximum = max(maximum, stretch)
        if determinant > 1e-12:
            has_positive = True
        elif determinant < -1e-12:
            has_negative = True
        else:
            degenerate = True
    invalid_winding = degenerate or has_negative or not has_positive
    return _weighted_percentile(values, 0.95), maximum, invalid_winding


def _positive_triangle_overlap(uv_a, uv_b, epsilon=1e-7):
    for triangle in (uv_a, uv_b):
        for index in range(3):
            edge = triangle[(index + 1) % 3] - triangle[index]
            axis = Vector((-edge.y, edge.x))
            if axis.length_squared <= epsilon * epsilon:
                continue
            values_a = [point.dot(axis) for point in uv_a]
            values_b = [point.dot(axis) for point in uv_b]
            overlap = min(max(values_a), max(values_b)) - max(min(values_a), min(values_b))
            if overlap <= epsilon * axis.length:
                return False
    return True


def _has_overlap(triangles):
    epsilon = 1e-7
    records = []
    for face_index, _, uvs in triangles:
        minimum_x = min(uv.x for uv in uvs)
        maximum_x = max(uv.x for uv in uvs)
        minimum_y = min(uv.y for uv in uvs)
        maximum_y = max(uv.y for uv in uvs)
        records.append((minimum_x, maximum_x, minimum_y, maximum_y, face_index, uvs))
    records.sort(key=lambda item: item[0])
    active = []
    for record in records:
        minimum_x, maximum_x, minimum_y, maximum_y, face_index, uvs = record
        active = [item for item in active if item[1] > minimum_x + epsilon]
        for other in active:
            if face_index == other[4]:
                continue
            if min(maximum_y, other[3]) - max(minimum_y, other[2]) <= epsilon:
                continue
            if _positive_triangle_overlap(uvs, other[5]):
                return True
        active.append(record)
    return False


def _save_uv(faces, uv_layer):
    return {
        face.index: [loop[uv_layer].uv.copy() for loop in face.loops]
        for face in faces
    }


def _restore_uv(faces, uv_layer, saved):
    for face in faces:
        for loop, uv in zip(face.loops, saved[face.index]):
            loop[uv_layer].uv = uv


def _candidate_groups(bm, chart_ids, charts, locked_seams, blocked,
                      visibility, settings, probe_evidence=None):
    groups = defaultdict(list)
    for edge in bm.edges:
        if not edge.seam or len(edge.link_faces) != 2 or edge.index in locked_seams:
            continue
        face_a, face_b = edge.link_faces
        chart_a = chart_ids[face_a.index]
        chart_b = chart_ids[face_b.index]
        if chart_a == chart_b:
            continue
        if settings.respect_materials and face_a.material_index != face_b.material_index:
            continue
        if settings.respect_sharp and not edge.smooth:
            continue
        groups[tuple(sorted((chart_a, chart_b)))].append(edge)

    total_area = sum(_chart_area(chart) for chart in charts)
    candidates = []
    for (chart_a, chart_b), boundary in groups.items():
        signature = frozenset(edge.index for edge in boundary)
        if signature in blocked:
            continue
        chart_left = charts[chart_a]
        chart_right = charts[chart_b]
        lengths = [max(edge.calc_length(), 1e-12) for edge in boundary]
        total_length = sum(lengths)
        average_angle = sum(
            edge.calc_face_angle(0.0) * length for edge, length in zip(boundary, lengths)
        ) / total_length
        area_left = _chart_area(chart_left)
        area_right = _chart_area(chart_right)
        small = (
            min(len(chart_left), len(chart_right)) <= settings.small_island_faces
            or min(area_left, area_right) <= total_area * settings.small_island_area_ratio
        )
        if average_angle > settings.merge_angle and not small:
            continue
        seam_cost = _boundary_seam_cost(boundary, visibility, settings)
        score = average_angle / max(settings.merge_angle, 1e-8)
        # Removing a seam is most valuable where that seam would be visible.
        score -= seam_cost * 0.25
        score -= _developable_band_bonus(
            chart_left, chart_right, boundary, settings)
        if small:
            score -= 0.9
        score -= min(total_length / max(math.sqrt(total_area), 1e-8), 1.0) * 0.1
        probe = probe_analysis.summarize_boundary(
            boundary,
            probe_evidence,
            min_confidence=settings.probe_min_confidence,
            reject_flipped=settings.probe_reject_flipped,
        )
        if (
            settings.probe_enabled
            and probe["supported"]
            and not probe["hard_reject"]
        ):
            score -= (
                settings.probe_merge_bonus
                * probe["score"]
                * max(probe["confidence"], 0.0)
            )
        elif settings.probe_enabled and probe["hard_reject"]:
            # Keep rejected candidates in reports, but sort them after viable
            # candidates so an optimizer pass can explicitly count the reason.
            score += 1000.0
        candidates.append((score, chart_a, chart_b, boundary, signature, probe))
    candidates.sort(key=lambda item: item[0])
    return candidates


def build_probe_candidate_report(obj, settings):
    """Report current UV boundary candidates and their probe evidence.

    This is intentionally read-only. It does not run Smart UV, unwrap, or
    change the object's seams, UVs, selection, or mode.
    """
    evidence = probe_analysis.load_stored_evidence(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            raise ValueError("Active mesh has no UV layer")
        _derive_seams_from_uv(bm, uv_layer, set())
        chart_ids, charts = _charts(bm)
        visibility = effective_face_visibility(obj.data, default=1.0)
        candidates = _candidate_groups(
            bm, chart_ids, charts, set(), set(), visibility, settings, evidence)
        report = {
            "schema": probe_analysis.SCHEMA,
            "object": obj.name,
            "charts": len(charts),
            "evidence_edges": len(evidence["edges"]) if evidence else 0,
            "candidates": [],
        }
        for score, chart_a, chart_b, boundary, signature, probe in candidates:
            report["candidates"].append({
                "chart_a": chart_a,
                "chart_b": chart_b,
                "score": round(float(score), 6),
                "boundary_edges": sorted(int(value) for value in signature),
                "boundary_vertices": [
                    sorted(int(vertex.index) for vertex in edge.verts)
                    for edge in boundary
                ],
                "probe": probe,
            })
        return report
    finally:
        bm.free()


def _try_merge(obj, bm, uv_layer, faces, boundary, settings):
    if not _is_disk(faces):
        return False, 'TOPOLOGY'

    saved_uv = _save_uv(faces, uv_layer)
    old_seams = [edge.seam for edge in boundary]
    for edge in boundary:
        edge.seam = False
    _set_uv_selection(bm, uv_layer, (face.index for face in faces))
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    result = bpy.ops.uv.unwrap(
        method='ANGLE_BASED',
        fill_holes=True,
        correct_aspect=True,
        use_subsurf_data=False,
        margin=settings.island_margin,
    )
    if 'FINISHED' not in result:
        _restore_uv(faces, uv_layer, saved_uv)
        for edge, old_value in zip(boundary, old_seams):
            edge.seam = old_value
        return False, 'STRETCH'

    triangles = _triangle_data(faces, uv_layer)
    p95, maximum, flipped = _measure_distortion(triangles)
    if flipped or p95 > settings.max_p95_stretch or maximum > settings.max_stretch:
        _restore_uv(faces, uv_layer, saved_uv)
        for edge, old_value in zip(boundary, old_seams):
            edge.seam = old_value
        return False, 'STRETCH'
    if settings.reject_overlap and _has_overlap(triangles):
        _restore_uv(faces, uv_layer, saved_uv)
        for edge, old_value in zip(boundary, old_seams):
            edge.seam = old_value
        return False, 'OVERLAP'
    return True, 'ACCEPT'


def _scale_charts_by_visibility(bm, uv_layer, charts, visibility,
                                detail_values, settings):
    for chart in charts:
        all_hidden = all(
            face.index < len(visibility) and visibility[face.index] <= 0.0
            for face in chart
        )
        if all_hidden:
            # Red/hidden charts are kept small while packing, then collapsed
            # to the UV origin so they do not need unique texture coverage.
            scale = _LOW_VISIBILITY_SCALE
        else:
            # A chart containing any visible face must not be reduced merely
            # because a neighboring face is hidden. Hidden faces are split
            # from mixed charts during the final origin-collapse pass.
            importance = max((visibility[face.index] for face in chart), default=0.0)
            normalized = min(importance / max(settings.visibility_high, 1e-8), 1.0)
            scale = (
                _LOW_VISIBILITY_SCALE
                + (1.0 - _LOW_VISIBILITY_SCALE) * normalized
            )
            detail = sum(
                detail_values[face.index] * max(face.calc_area(), 1e-12)
                for face in chart
            ) / max(_chart_area(chart), 1e-12)
            scale *= 1.0 + settings.detail_strength * detail
            scale = min(scale, settings.detail_scale_cap)
        if scale >= 0.999999:
            continue
        loops = [loop for face in chart for loop in face.loops]
        if not loops:
            continue
        center = sum((loop[uv_layer].uv for loop in loops), Vector((0.0, 0.0))) / len(loops)
        for loop in loops:
            loop[uv_layer].uv = center + (loop[uv_layer].uv - center) * scale


def _collapse_hidden_faces_to_origin(bm, uv_layer, visibility):
    """Collapse every red/hidden face loop to the lower-left UV origin."""
    origin = Vector((0.0, 0.0))
    for face in bm.faces:
        if face.index >= len(visibility) or visibility[face.index] > 0.0:
            continue
        for loop in face.loops:
            loop[uv_layer].uv = origin


def optimize_active_object(context, obj, settings):
    mesh = obj.data
    mesh.update()
    if not mesh.polygons:
        raise ValueError("Active mesh contains no faces")
    # Without analysis, preserve normal texel density instead of treating the
    # entire object as red/hidden. Manual overrides still apply.
    visibility = effective_face_visibility(mesh, default=1.0)
    probe_evidence = probe_analysis.load_stored_evidence(obj)
    original_seams = {
        edge.index for edge in mesh.edges if edge.use_seam
    } if settings.preserve_seams else set()

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    smart_result = bpy.ops.uv.smart_project(
        angle_limit=settings.smart_angle,
        island_margin=settings.island_margin,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
    )
    if 'FINISHED' not in smart_result:
        raise RuntimeError("Smart UV Project did not finish")

    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.verify()
    detail_values = _compute_face_detail(bm)
    _derive_seams_from_uv(bm, uv_layer, original_seams)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

    _, initial_charts = _charts(bm)
    initial_count = len(initial_charts)
    blocked = set()
    merge_tests = 0
    accepted = 0
    rejected_stretch = 0
    rejected_overlap = 0
    rejected_topology = 0
    rejected_probe = 0
    probe_guided_merges = 0

    while merge_tests < settings.max_merge_tests:
        chart_ids, charts = _charts(bm)
        candidates = _candidate_groups(
            bm, chart_ids, charts, original_seams, blocked, visibility,
            settings, probe_evidence)
        if not candidates:
            break

        merged_this_round = False
        for _, chart_a, chart_b, boundary, signature, probe in candidates:
            if merge_tests >= settings.max_merge_tests:
                break
            merge_tests += 1
            if settings.probe_enabled and probe["hard_reject"]:
                rejected_probe += 1
                blocked.add(signature)
                continue
            faces = charts[chart_a] + charts[chart_b]
            accepted_merge, reason = _try_merge(
                obj, bm, uv_layer, faces, boundary, settings)
            if accepted_merge:
                accepted += 1
                if (
                    settings.probe_enabled
                    and probe["supported"]
                    and probe["score"] >= 0.5
                ):
                    probe_guided_merges += 1
                merged_this_round = True
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
                break
            blocked.add(signature)
            if reason == 'OVERLAP':
                rejected_overlap += 1
            elif reason == 'TOPOLOGY':
                rejected_topology += 1
            else:
                rejected_stretch += 1
        if not merged_this_round:
            break

    _, final_charts = _charts(bm)
    _set_uv_selection(bm, uv_layer, (face.index for face in bm.faces))
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    bpy.ops.uv.average_islands_scale()
    _scale_charts_by_visibility(
        bm, uv_layer, final_charts, visibility, detail_values, settings)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    bpy.ops.uv.pack_islands(rotate=True, margin=settings.island_margin)
    _collapse_hidden_faces_to_origin(bm, uv_layer, visibility)
    _derive_seams_from_uv(bm, uv_layer, original_seams)
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)

    return OptimizeResult(
        initial_islands=initial_count,
        final_islands=len(final_charts),
        merge_tests=merge_tests,
        accepted_merges=accepted,
        rejected_stretch=rejected_stretch,
        rejected_overlap=rejected_overlap,
        rejected_topology=rejected_topology,
        detail_values=detail_values,
        rejected_probe=rejected_probe,
        probe_guided_merges=probe_guided_merges,
    )
