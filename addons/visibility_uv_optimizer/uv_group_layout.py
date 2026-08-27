# SPDX-License-Identifier: GPL-2.0-or-later
"""Safe semantic post-layout for an existing unique UV map.

This module does not unwrap, stitch, split, mirror, or edit seams.  It treats
each UV island as a rigid 2D shape, detects conservative repeated mechanical
parts, keeps those parts together with a common direction, and places tiny
detached charts near a model-space neighbor.  The final pack uses disjoint
axis-aligned rectangles and one positive global scale, so relative texel
density and every island's winding are preserved.

The public entry points intentionally work in Object Mode.  This keeps the
module independent from UV editor selection state and makes rollback reliable
in Blender 3.3 as well as newer versions.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mathutils import Vector


_EPSILON = 1.0e-12
_TAU = math.pi * 2.0
_AFFINITY_SEARCH_POLICIES = (
    (4, 96, 25000),
    (8, 256, 50000),
    (None, None, 100000),
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _relative_error(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), _EPSILON)


def _angle_wrap(angle: float) -> float:
    while angle >= math.pi:
        angle -= _TAU
    while angle < -math.pi:
        angle += _TAU
    return angle


def _line_angle_wrap(angle: float) -> float:
    while angle >= math.pi * 0.5:
        angle -= math.pi
    while angle < -math.pi * 0.5:
        angle += math.pi
    return angle


def _vector_list(value: Vector, digits: int = 6) -> List[float]:
    return [round(float(component), digits) for component in value]


def _bounds_to_list(bounds: Sequence[float], digits: int = 6) -> List[float]:
    return [round(float(value), digits) for value in bounds]


@dataclass(frozen=True)
class GroupLayoutOptions:
    """Conservative thresholds for analysis and rigid post-packing."""

    margin: float = 0.003
    uv_epsilon: float = 1.0e-6
    small_face_count: int = 12
    small_area_ratio: float = 1.0e-3
    small_uv_area_ratio: float = 1.0e-3
    proximity_radius_ratio: float = 0.025
    repeat_area_tolerance: float = 0.02
    repeat_min_confidence: float = 0.93
    repeat_min_faces: int = 2
    repeat_single_face_min_vertices: int = 4
    repeat_min_area_ratio: float = 2.0e-4
    max_repeat_group_size: int = 24
    max_small_members_per_group: int = 16
    min_direction_confidence: float = 0.20
    min_pca_anisotropy: float = 0.06
    allow_group_quarter_turn: bool = True
    strict_positive_winding: bool = True
    strict_source_overlap: bool = True
    packing_iterations: int = 10
    align_non_repeat_cardinal: bool = True

    def validated(self) -> "GroupLayoutOptions":
        if not math.isfinite(float(self.margin)) or not 0.0 <= self.margin < 0.25:
            raise ValueError("margin must be finite and in [0, 0.25)")
        if not math.isfinite(float(self.uv_epsilon)) or self.uv_epsilon <= 0.0:
            raise ValueError("uv_epsilon must be finite and greater than zero")
        if not 0.0 <= self.small_area_ratio <= 1.0:
            raise ValueError("small_area_ratio must be in [0, 1]")
        if not 0.0 <= self.small_uv_area_ratio <= 1.0:
            raise ValueError("small_uv_area_ratio must be in [0, 1]")
        if self.small_face_count < 1:
            raise ValueError("small_face_count must be at least one")
        if not 0.0 <= self.proximity_radius_ratio <= 1.0:
            raise ValueError("proximity_radius_ratio must be in [0, 1]")
        if not 0.0 <= self.repeat_area_tolerance < 1.0:
            raise ValueError("repeat_area_tolerance must be in [0, 1)")
        if not 0.0 <= self.repeat_min_confidence <= 1.0:
            raise ValueError("repeat_min_confidence must be in [0, 1]")
        if self.max_repeat_group_size < 2:
            raise ValueError("max_repeat_group_size must be at least two")
        if self.max_small_members_per_group < 1:
            raise ValueError("max_small_members_per_group must be positive")
        if self.packing_iterations < 1:
            raise ValueError("packing_iterations must be positive")
        return self


@dataclass
class IslandRecord:
    island_id: int
    face_indices: Tuple[int, ...]
    loop_indices: Tuple[int, ...]
    vertex_indices: Tuple[int, ...]
    edge_indices: Tuple[int, ...]
    material_indices: Tuple[int, ...]
    area_3d: float
    area_3d_ratio: float
    uv_area: float
    uv_area_ratio: float
    uv_centroid: Vector
    uv_bounds: Tuple[float, float, float, float]
    model_centroid: Vector
    model_bounds: Tuple[float, float, float, float, float, float]
    average_normal: Vector
    principal_angle: float
    anisotropy: float
    direction_vector: Vector
    direction_confidence: float
    landmark_vertex: Optional[int]
    geometry_signature: str
    geometry_payload: Mapping[str, Any] = field(repr=False)
    neighbor_ids: Tuple[int, ...] = ()
    is_small: bool = False

    @property
    def face_count(self) -> int:
        return len(self.face_indices)

    @property
    def uv_width(self) -> float:
        return self.uv_bounds[2] - self.uv_bounds[0]

    @property
    def uv_height(self) -> float:
        return self.uv_bounds[3] - self.uv_bounds[1]

    def to_dict(self) -> Dict[str, Any]:
        direction_angle = None
        if self.direction_vector.length_squared > _EPSILON:
            direction_angle = round(
                math.degrees(math.atan2(
                    self.direction_vector.y, self.direction_vector.x)),
                4,
            )
        return {
            "id": self.island_id,
            "faces": list(self.face_indices),
            "loops": len(self.loop_indices),
            "vertices": len(self.vertex_indices),
            "materials": list(self.material_indices),
            "area_3d": round(self.area_3d, 9),
            "area_3d_ratio": round(self.area_3d_ratio, 9),
            "uv_area": round(self.uv_area, 9),
            "uv_area_ratio": round(self.uv_area_ratio, 9),
            "uv_centroid": _vector_list(self.uv_centroid),
            "uv_bounds": _bounds_to_list(self.uv_bounds),
            "model_centroid": _vector_list(self.model_centroid),
            "model_bounds": _bounds_to_list(self.model_bounds),
            "principal_angle_degrees": round(math.degrees(self.principal_angle), 4),
            "anisotropy": round(self.anisotropy, 6),
            "direction_confidence": round(self.direction_confidence, 6),
            "direction_angle_degrees": direction_angle,
            "landmark_vertex": self.landmark_vertex,
            "signature": self.geometry_signature,
            "neighbors": list(self.neighbor_ids),
            "small": self.is_small,
        }


@dataclass(frozen=True)
class IslandAdjacency:
    left_id: int
    right_id: int
    edge_count: int
    shared_length: float
    seam_edge_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left": self.left_id,
            "right": self.right_id,
            "edge_count": self.edge_count,
            "shared_length": round(self.shared_length, 8),
            "seam_edge_count": self.seam_edge_count,
        }


@dataclass(frozen=True)
class RepeatCandidate:
    left_id: int
    right_id: int
    relation: str
    axis: Optional[str]
    confidence: float
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left": self.left_id,
            "right": self.right_id,
            "relation": self.relation,
            "axis": self.axis,
            "confidence": round(self.confidence, 6),
            "signature": self.signature,
        }


@dataclass
class RepeatGroup:
    group_id: int
    member_ids: Tuple[int, ...]
    relation: str
    confidence: float
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.group_id,
            "members": list(self.member_ids),
            "relation": self.relation,
            "confidence": round(self.confidence, 6),
            "signature": self.signature,
        }


@dataclass
class LayoutGroup:
    group_id: int
    member_ids: Tuple[int, ...]
    reason: str
    anchor_ids: Tuple[int, ...]
    small_member_ids: Tuple[int, ...] = ()
    small_anchor_ids: Tuple[int, ...] = ()
    owner_cohorts: Tuple[Tuple[int, ...], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.group_id,
            "members": list(self.member_ids),
            "reason": self.reason,
            "anchors": list(self.anchor_ids),
            "small_members": list(self.small_member_ids),
            "small_anchors": list(self.small_anchor_ids),
            "owner_cohorts": [list(cohort) for cohort in self.owner_cohorts],
        }


@dataclass
class UVLayoutAnalysis:
    object_name: str
    mesh_name: str
    uv_layer_name: str
    object_diagonal: float
    islands: List[IslandRecord]
    adjacency: List[IslandAdjacency]
    repeat_candidates: List[RepeatCandidate]
    repeat_groups: List[RepeatGroup]
    layout_groups: List[LayoutGroup]
    face_to_island: Tuple[int, ...] = field(repr=False)

    def to_dict(self, include_islands: bool = True) -> Dict[str, Any]:
        payload = {
            "object": self.object_name,
            "mesh": self.mesh_name,
            "uv_layer": self.uv_layer_name,
            "object_diagonal": round(self.object_diagonal, 8),
            "counts": {
                "islands": len(self.islands),
                "adjacent_pairs": len(self.adjacency),
                "repeat_candidates": len(self.repeat_candidates),
                "repeat_groups": len(self.repeat_groups),
                "repeat_members": sum(len(group.member_ids) for group in self.repeat_groups),
                "small_islands": sum(island.is_small for island in self.islands),
                "layout_groups": len(self.layout_groups),
            },
            "repeat_groups": [group.to_dict() for group in self.repeat_groups],
            "layout_groups": [group.to_dict() for group in self.layout_groups],
        }
        if include_islands:
            payload["islands"] = [island.to_dict() for island in self.islands]
            payload["adjacency"] = [item.to_dict() for item in self.adjacency]
            payload["repeat_candidates"] = [
                item.to_dict() for item in self.repeat_candidates
            ]
        return payload


@dataclass
class GroupLayoutResult:
    analysis: UVLayoutAnalysis
    uniform_scale: float
    requested_margin: float
    achieved_aabb_gap: float
    rotated_islands: int
    grouped_small_islands: int
    before_audit: Mapping[str, Any]
    after_audit: Mapping[str, Any]
    group_quarter_turn: bool = True
    fallback_used: bool = False
    fallback_errors: Tuple[str, ...] = ()
    quantization_adjusted_islands: int = 0
    max_quantization_rotation_degrees: float = 0.0

    def to_dict(self, include_islands: bool = False) -> Dict[str, Any]:
        return {
            "analysis": self.analysis.to_dict(include_islands=include_islands),
            "uniform_scale": round(self.uniform_scale, 9),
            "requested_margin": round(self.requested_margin, 9),
            "achieved_aabb_gap": round(self.achieved_aabb_gap, 9),
            "rotated_islands": self.rotated_islands,
            "grouped_small_islands": self.grouped_small_islands,
            "group_quarter_turn": self.group_quarter_turn,
            "fallback_used": self.fallback_used,
            "fallback_errors": list(self.fallback_errors),
            "quantization_adjusted_islands": self.quantization_adjusted_islands,
            "max_quantization_rotation_degrees": round(
                self.max_quantization_rotation_degrees, 9
            ),
            "topology_stitches": 0,
            "reflection_applied": False,
            "relative_texel_density_preserved": True,
            "before_audit": dict(self.before_audit),
            "after_audit": dict(self.after_audit),
        }


@dataclass(frozen=True)
class _EdgeFaceRecord:
    face_index: int
    vertices: Tuple[int, int]
    vertex_uvs: Mapping[int, Vector]


@dataclass(frozen=True)
class _Rect:
    key: int
    width: float
    height: float
    sort_rank: int = 0


@dataclass(frozen=True)
class _Placement:
    x: float
    y: float
    width: float
    height: float
    quarter_turn: bool = False


@dataclass
class _PackedPlan:
    coordinates: Dict[int, Dict[int, Vector]]
    width: float
    height: float
    source_gap: float
    group_placements: Dict[int, _Placement]


def _require_object_mode_mesh(obj: Any) -> Tuple[Any, Any]:
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise ValueError("A Mesh object is required")
    if getattr(obj, "mode", "OBJECT") != "OBJECT":
        raise RuntimeError("UV group layout requires the object to be in Object Mode")
    mesh = getattr(obj, "data", None)
    if mesh is None or not mesh.polygons:
        raise ValueError("The Mesh object has no faces")
    mesh.update()
    uv_layer = mesh.uv_layers.active
    if uv_layer is None or len(uv_layer.data) != len(mesh.loops):
        raise ValueError("The active UV map is unavailable or incomplete")
    return mesh, uv_layer


def _polygon_signed_uv_area(mesh: Any, uv_layer: Any, face_index: int) -> float:
    loop_indices = mesh.polygons[face_index].loop_indices
    points = [uv_layer.data[index].uv for index in loop_indices]
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        points[index].x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * points[index].y
        for index in range(len(points))
    )


def _uv_bounds(points: Iterable[Vector]) -> Tuple[float, float, float, float]:
    values = list(points)
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(point.x for point in values),
        min(point.y for point in values),
        max(point.x for point in values),
        max(point.y for point in values),
    )


def _model_bounds(points: Iterable[Vector]) -> Tuple[float, float, float, float, float, float]:
    values = list(points)
    if not values:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return (
        min(point.x for point in values),
        min(point.y for point in values),
        min(point.z for point in values),
        max(point.x for point in values),
        max(point.y for point in values),
        max(point.z for point in values),
    )


def _edge_face_records(mesh: Any, uv_layer: Any) -> Dict[int, List[_EdgeFaceRecord]]:
    records: Dict[int, List[_EdgeFaceRecord]] = defaultdict(list)
    for polygon in mesh.polygons:
        loop_indices = list(polygon.loop_indices)
        for offset, loop_index in enumerate(loop_indices):
            next_loop_index = loop_indices[(offset + 1) % len(loop_indices)]
            vertex_a = int(mesh.loops[loop_index].vertex_index)
            vertex_b = int(mesh.loops[next_loop_index].vertex_index)
            edge_index = int(mesh.loops[loop_index].edge_index)
            records[edge_index].append(_EdgeFaceRecord(
                face_index=int(polygon.index),
                vertices=(vertex_a, vertex_b),
                vertex_uvs={
                    vertex_a: uv_layer.data[loop_index].uv.copy(),
                    vertex_b: uv_layer.data[next_loop_index].uv.copy(),
                },
            ))
    return records


def _continuous_across_edge(
    left: _EdgeFaceRecord,
    right: _EdgeFaceRecord,
    epsilon_squared: float,
) -> bool:
    shared = set(left.vertices) & set(right.vertices)
    return len(shared) == 2 and all(
        (left.vertex_uvs[vertex] - right.vertex_uvs[vertex]).length_squared
        <= epsilon_squared
        for vertex in shared
    )


def _reconstruct_islands(
    mesh: Any,
    uv_layer: Any,
    uv_epsilon: float,
) -> Tuple[List[List[int]], List[int], Dict[int, List[_EdgeFaceRecord]]]:
    records = _edge_face_records(mesh, uv_layer)
    face_adjacency = [set() for _ in mesh.polygons]
    epsilon_squared = uv_epsilon * uv_epsilon
    for edge_records in records.values():
        for left_index, left in enumerate(edge_records):
            for right in edge_records[left_index + 1:]:
                if _continuous_across_edge(left, right, epsilon_squared):
                    face_adjacency[left.face_index].add(right.face_index)
                    face_adjacency[right.face_index].add(left.face_index)

    face_to_island = [-1] * len(mesh.polygons)
    islands: List[List[int]] = []
    for face_index in range(len(mesh.polygons)):
        if face_to_island[face_index] >= 0:
            continue
        island_id = len(islands)
        face_to_island[face_index] = island_id
        pending = deque([face_index])
        faces = []
        while pending:
            current = pending.popleft()
            faces.append(current)
            for neighbor in sorted(face_adjacency[current]):
                if face_to_island[neighbor] < 0:
                    face_to_island[neighbor] = island_id
                    pending.append(neighbor)
        islands.append(sorted(faces))
    return islands, face_to_island, records


def _principal_axis(points: Sequence[Vector]) -> Tuple[float, float]:
    if len(points) < 2:
        return 0.0, 0.0
    center = sum(points, Vector((0.0, 0.0))) / len(points)
    xx = sum((point.x - center.x) ** 2 for point in points) / len(points)
    yy = sum((point.y - center.y) ** 2 for point in points) / len(points)
    xy = sum(
        (point.x - center.x) * (point.y - center.y) for point in points
    ) / len(points)
    angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
    trace = xx + yy
    discriminant = math.sqrt(max((xx - yy) ** 2 + 4.0 * xy * xy, 0.0))
    major = max((trace + discriminant) * 0.5, 0.0)
    minor = max((trace - discriminant) * 0.5, 0.0)
    anisotropy = (major - minor) / max(major, _EPSILON)
    return _line_angle_wrap(angle), _clamp(anisotropy, 0.0, 1.0)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _geometry_payload(
    mesh: Any,
    face_indices: Sequence[int],
    vertex_indices: Sequence[int],
    edge_indices: Sequence[int],
    area_3d: float,
) -> Dict[str, Any]:
    scale = max(math.sqrt(max(area_3d, _EPSILON)), _EPSILON)
    vertex_set = set(vertex_indices)
    internal_valence = defaultdict(int)
    edge_lengths = []
    boundary_edge_lengths = []
    face_membership = set(face_indices)
    edge_faces: Dict[int, List[int]] = defaultdict(list)
    for face_index in face_indices:
        for loop_index in mesh.polygons[face_index].loop_indices:
            edge_faces[int(mesh.loops[loop_index].edge_index)].append(face_index)
    for edge_index in edge_indices:
        edge = mesh.edges[edge_index]
        vertex_a, vertex_b = map(int, edge.vertices)
        length = (mesh.vertices[vertex_a].co - mesh.vertices[vertex_b].co).length
        edge_lengths.append(round(length / scale, 5))
        if vertex_a in vertex_set and vertex_b in vertex_set:
            internal_valence[vertex_a] += 1
            internal_valence[vertex_b] += 1
        linked_inside = sum(face in face_membership for face in edge_faces.get(edge_index, ()))
        if linked_inside == 1:
            boundary_edge_lengths.append(round(length / scale, 5))

    center = sum(
        (mesh.vertices[index].co for index in vertex_indices),
        Vector((0.0, 0.0, 0.0)),
    ) / max(len(vertex_indices), 1)
    radial_distances = sorted(
        round((mesh.vertices[index].co - center).length / scale, 5)
        for index in vertex_indices
    )
    pairwise_distances: List[float] = []
    if len(vertex_indices) <= 40:
        for left_position, left_index in enumerate(vertex_indices):
            for right_index in vertex_indices[left_position + 1:]:
                distance = (
                    mesh.vertices[left_index].co - mesh.vertices[right_index].co
                ).length
                pairwise_distances.append(round(distance / scale, 5))

    face_areas = sorted(
        round(float(mesh.polygons[index].area) / max(area_3d, _EPSILON), 6)
        for index in face_indices
    )
    face_sides = sorted(len(mesh.polygons[index].vertices) for index in face_indices)
    material_indices = sorted({
        int(mesh.polygons[index].material_index) for index in face_indices
    })
    return {
        "faces": len(face_indices),
        "vertices": len(vertex_indices),
        "edges": len(edge_indices),
        "face_sides": face_sides,
        "face_areas": face_areas,
        "edge_lengths": sorted(edge_lengths),
        "boundary_edge_lengths": sorted(boundary_edge_lengths),
        "vertex_valence": sorted(internal_valence[index] for index in vertex_indices),
        "radial_distances": radial_distances,
        "pairwise_distances": sorted(pairwise_distances),
        "materials": material_indices,
    }


def _landmark_direction(
    mesh: Any,
    uv_layer: Any,
    loop_indices: Sequence[int],
    vertex_indices: Sequence[int],
    edge_indices: Sequence[int],
    model_centroid_local: Vector,
    uv_centroid: Vector,
    area_3d: float,
    uv_extent: float,
) -> Tuple[Vector, float, Optional[int]]:
    scale = max(math.sqrt(max(area_3d, _EPSILON)), _EPSILON)
    incident_lengths: Dict[int, List[float]] = defaultdict(list)
    incident_count = defaultdict(int)
    for edge_index in edge_indices:
        edge = mesh.edges[edge_index]
        vertex_a, vertex_b = map(int, edge.vertices)
        length = (mesh.vertices[vertex_a].co - mesh.vertices[vertex_b].co).length / scale
        incident_lengths[vertex_a].append(length)
        incident_lengths[vertex_b].append(length)
        incident_count[vertex_a] += 1
        incident_count[vertex_b] += 1

    salience: List[Tuple[Tuple[Any, ...], int]] = []
    for vertex_index in vertex_indices:
        radius = (mesh.vertices[vertex_index].co - model_centroid_local).length / scale
        lengths = sorted(round(value, 4) for value in incident_lengths[vertex_index])
        key = (
            round(radius, 4),
            incident_count[vertex_index],
            tuple(lengths),
        )
        salience.append((key, vertex_index))
    salience.sort(reverse=True)
    if not salience:
        return Vector((0.0, 0.0)), 0.0, None
    uv_by_vertex: Dict[int, Vector] = {}
    for vertex_index in vertex_indices:
        values = [
            uv_layer.data[index].uv.copy()
            for index in loop_indices
            if int(mesh.loops[index].vertex_index) == vertex_index
        ]
        if values:
            uv_by_vertex[vertex_index] = (
                sum(values, Vector((0.0, 0.0))) / len(values)
            )

    # A unique most-salient vertex gives a directed landmark. When several
    # vertices are intrinsically equivalent, their centroid can still resolve
    # a meaningful direction (for example the open side of a U-shaped part).
    # Fully centre-symmetric sets remain unresolved instead of inventing an
    # unstable 180-degree choice from vertex indices.
    start = 0
    while start < len(salience):
        key = salience[start][0]
        end = start + 1
        while end < len(salience) and salience[end][0] == key:
            end += 1
        equivalent = [
            vertex_index
            for _item_key, vertex_index in salience[start:end]
            if vertex_index in uv_by_vertex
        ]
        if equivalent:
            landmark_uv = sum(
                (uv_by_vertex[index] for index in equivalent),
                Vector((0.0, 0.0)),
            ) / len(equivalent)
            direction = landmark_uv - uv_centroid
            if direction.length_squared > _EPSILON:
                length_ratio = direction.length / max(uv_extent, _EPSILON)
                confidence = _clamp(length_ratio * 2.5, 0.0, 1.0)
                landmark = equivalent[0] if len(equivalent) == 1 else None
                return direction, confidence, landmark
        start = end
    return Vector((0.0, 0.0)), 0.0, None


def _build_adjacency(
    mesh: Any,
    face_to_island: Sequence[int],
    edge_records: Mapping[int, Sequence[_EdgeFaceRecord]],
) -> List[IslandAdjacency]:
    accum: Dict[Tuple[int, int], Dict[str, float]] = defaultdict(
        lambda: {"edges": 0.0, "length": 0.0, "seams": 0.0}
    )
    for edge_index, records in edge_records.items():
        if len(records) < 2:
            continue
        for left_index, left in enumerate(records):
            for right in records[left_index + 1:]:
                left_island = face_to_island[left.face_index]
                right_island = face_to_island[right.face_index]
                if left_island == right_island:
                    continue
                key = tuple(sorted((left_island, right_island)))
                edge = mesh.edges[edge_index]
                vertex_a, vertex_b = map(int, edge.vertices)
                length = (
                    mesh.vertices[vertex_a].co - mesh.vertices[vertex_b].co
                ).length
                accum[key]["edges"] += 1.0
                accum[key]["length"] += length
                accum[key]["seams"] += float(bool(edge.use_seam))
    return [
        IslandAdjacency(
            left_id=key[0],
            right_id=key[1],
            edge_count=int(values["edges"]),
            shared_length=float(values["length"]),
            seam_edge_count=int(values["seams"]),
        )
        for key, values in sorted(accum.items())
    ]


def _normal_matrix(obj: Any) -> Any:
    try:
        return obj.matrix_world.to_3x3().inverted().transposed()
    except (AttributeError, RuntimeError, ValueError, ZeroDivisionError):
        return None


def _world_point(obj: Any, point: Vector) -> Vector:
    try:
        return obj.matrix_world @ point
    except (AttributeError, TypeError, ValueError):
        return point.copy()


def compute_active_uv_islands(
    obj: Any,
    options: Optional[GroupLayoutOptions] = None,
) -> Tuple[List[IslandRecord], Tuple[int, ...], List[IslandAdjacency], float]:
    """Return records reconstructed from actual continuity in the active UV."""

    settings = (options or GroupLayoutOptions()).validated()
    mesh, uv_layer = _require_object_mode_mesh(obj)
    island_faces, face_to_island, edge_records = _reconstruct_islands(
        mesh, uv_layer, settings.uv_epsilon
    )
    total_area = sum(max(float(face.area), 0.0) for face in mesh.polygons)
    total_uv_area = sum(
        abs(_polygon_signed_uv_area(mesh, uv_layer, int(face.index)))
        for face in mesh.polygons
    )
    used_vertex_indices = sorted({
        int(vertex_index)
        for polygon in mesh.polygons
        for vertex_index in polygon.vertices
    })
    world_vertices = [
        _world_point(obj, mesh.vertices[index].co)
        for index in used_vertex_indices
    ]
    object_bounds = _model_bounds(world_vertices)
    object_diagonal = Vector((
        object_bounds[3] - object_bounds[0],
        object_bounds[4] - object_bounds[1],
        object_bounds[5] - object_bounds[2],
    )).length
    object_diagonal = max(object_diagonal, _EPSILON)
    normals = _normal_matrix(obj)

    records: List[IslandRecord] = []
    for island_id, face_indices in enumerate(island_faces):
        loop_indices = tuple(sorted(
            int(loop_index)
            for face_index in face_indices
            for loop_index in mesh.polygons[face_index].loop_indices
        ))
        vertex_indices = tuple(sorted({
            int(mesh.loops[index].vertex_index) for index in loop_indices
        }))
        edge_indices = tuple(sorted({
            int(mesh.loops[index].edge_index) for index in loop_indices
        }))
        uv_points = [uv_layer.data[index].uv.copy() for index in loop_indices]
        bounds_uv = _uv_bounds(uv_points)
        uv_centroid = sum(uv_points, Vector((0.0, 0.0))) / max(len(uv_points), 1)
        principal_angle, anisotropy = _principal_axis(uv_points)
        area_3d = sum(float(mesh.polygons[index].area) for index in face_indices)
        uv_area = sum(
            abs(_polygon_signed_uv_area(mesh, uv_layer, index))
            for index in face_indices
        )

        local_weighted = Vector((0.0, 0.0, 0.0))
        world_weighted = Vector((0.0, 0.0, 0.0))
        normal_weighted = Vector((0.0, 0.0, 0.0))
        for face_index in face_indices:
            polygon = mesh.polygons[face_index]
            weight = max(float(polygon.area), _EPSILON)
            local_weighted += polygon.center * weight
            world_weighted += _world_point(obj, polygon.center) * weight
            transformed_normal = (
                normals @ polygon.normal if normals is not None else polygon.normal.copy()
            )
            if transformed_normal.length_squared > _EPSILON:
                transformed_normal.normalize()
            normal_weighted += transformed_normal * weight
        area_weight = max(area_3d, _EPSILON)
        local_centroid = local_weighted / area_weight
        model_centroid = world_weighted / area_weight
        if normal_weighted.length_squared > _EPSILON:
            normal_weighted.normalize()

        model_points = [_world_point(obj, mesh.vertices[index].co) for index in vertex_indices]
        payload = _geometry_payload(
            mesh, face_indices, vertex_indices, edge_indices, area_3d
        )
        uv_extent = max(
            bounds_uv[2] - bounds_uv[0], bounds_uv[3] - bounds_uv[1], _EPSILON
        )
        direction, direction_confidence, landmark = _landmark_direction(
            mesh,
            uv_layer,
            loop_indices,
            vertex_indices,
            edge_indices,
            local_centroid,
            uv_centroid,
            area_3d,
            uv_extent,
        )
        records.append(IslandRecord(
            island_id=island_id,
            face_indices=tuple(face_indices),
            loop_indices=loop_indices,
            vertex_indices=vertex_indices,
            edge_indices=edge_indices,
            material_indices=tuple(payload["materials"]),
            area_3d=area_3d,
            area_3d_ratio=area_3d / max(total_area, _EPSILON),
            uv_area=uv_area,
            uv_area_ratio=uv_area / max(total_uv_area, _EPSILON),
            uv_centroid=uv_centroid,
            uv_bounds=bounds_uv,
            model_centroid=model_centroid,
            model_bounds=_model_bounds(model_points),
            average_normal=normal_weighted,
            principal_angle=principal_angle,
            anisotropy=anisotropy,
            direction_vector=direction,
            direction_confidence=direction_confidence,
            landmark_vertex=landmark,
            geometry_signature=_stable_hash(payload),
            geometry_payload=payload,
        ))

    adjacency = _build_adjacency(mesh, face_to_island, edge_records)
    neighbor_map: Dict[int, set] = defaultdict(set)
    for item in adjacency:
        neighbor_map[item.left_id].add(item.right_id)
        neighbor_map[item.right_id].add(item.left_id)
    for island in records:
        island.neighbor_ids = tuple(sorted(neighbor_map[island.island_id]))
        island.is_small = bool(
            island.face_count <= settings.small_face_count
            and island.area_3d_ratio <= settings.small_area_ratio
            and island.uv_area_ratio <= settings.small_uv_area_ratio
        )
    return records, tuple(face_to_island), adjacency, object_diagonal


# More explicit alias for callers producing reports.
calculate_island_records = compute_active_uv_islands


def _object_center(islands: Sequence[IslandRecord]) -> Vector:
    if not islands:
        return Vector((0.0, 0.0, 0.0))
    bounds = (
        min(island.model_bounds[0] for island in islands),
        min(island.model_bounds[1] for island in islands),
        min(island.model_bounds[2] for island in islands),
        max(island.model_bounds[3] for island in islands),
        max(island.model_bounds[4] for island in islands),
        max(island.model_bounds[5] for island in islands),
    )
    return Vector((
        (bounds[0] + bounds[3]) * 0.5,
        (bounds[1] + bounds[4]) * 0.5,
        (bounds[2] + bounds[5]) * 0.5,
    ))


def _relation_for_pair(
    left: IslandRecord,
    right: IslandRecord,
    center: Vector,
    object_diagonal: float,
) -> Tuple[str, Optional[str], float]:
    tolerance = max(object_diagonal * 0.025, _EPSILON)
    best_mirror: Optional[Tuple[float, str]] = None
    axis_names = ("X", "Y", "Z")
    for axis, name in enumerate(axis_names):
        mirror_error = abs(
            left.model_centroid[axis] + right.model_centroid[axis] - 2.0 * center[axis]
        )
        other_error = sum(
            abs(left.model_centroid[index] - right.model_centroid[index])
            for index in range(3) if index != axis
        )
        error = mirror_error + other_error
        if error <= tolerance * 2.0 and (
            best_mirror is None or error < best_mirror[0]
        ):
            best_mirror = (error, name)
    if best_mirror is not None:
        spatial = 1.0 - _clamp(best_mirror[0] / (tolerance * 2.0), 0.0, 1.0)
        return "MIRROR", best_mirror[1], 0.975 + 0.02 * spatial

    best_rotation: Optional[Tuple[float, str]] = None
    for axis, name in enumerate(axis_names):
        other_axes = [index for index in range(3) if index != axis]
        left_relative = left.model_centroid - center
        right_relative = right.model_centroid - center
        axial_error = abs(left_relative[axis] - right_relative[axis])
        left_radius = math.sqrt(sum(left_relative[index] ** 2 for index in other_axes))
        right_radius = math.sqrt(sum(right_relative[index] ** 2 for index in other_axes))
        radius_error = abs(left_radius - right_radius)
        separation = (left.model_centroid - right.model_centroid).length
        error = axial_error + radius_error
        if (
            min(left_radius, right_radius) > tolerance * 0.25
            and separation > tolerance
            and error <= tolerance * 1.5
            and (best_rotation is None or error < best_rotation[0])
        ):
            best_rotation = (error, name)
    if best_rotation is not None:
        spatial = 1.0 - _clamp(best_rotation[0] / (tolerance * 1.5), 0.0, 1.0)
        return "ROTATIONAL", best_rotation[1], 0.965 + 0.025 * spatial
    return "REPEATED", None, 0.945


def _meaningful_repeat_candidate(
    island: IslandRecord,
    settings: GroupLayoutOptions,
) -> bool:
    return bool(
        island.face_count >= settings.repeat_min_faces
        or len(island.vertex_indices) >= settings.repeat_single_face_min_vertices
        or island.area_3d_ratio >= settings.repeat_min_area_ratio
    )


def _repeat_spatial_key(island: IslandRecord, center: Vector) -> Tuple[float, ...]:
    relative = island.model_centroid - center
    angle = math.atan2(relative.y, relative.x) % _TAU
    return (
        round(relative.z, 8),
        round(math.sqrt(relative.x * relative.x + relative.y * relative.y), 8),
        round(angle, 8),
        island.island_id,
    )


def detect_repeat_groups(
    islands: Sequence[IslandRecord],
    object_diagonal: float,
    options: Optional[GroupLayoutOptions] = None,
) -> Tuple[List[RepeatCandidate], List[RepeatGroup]]:
    """Detect strict geometry duplicates, then label mirror/rotation evidence.

    Geometry signatures are invariant to rigid 3D transforms and reflection.
    Mirror and rotational labels require an additional centroid relation around
    the object's bounds center.  Generic exact duplicates are retained with a
    slightly lower, but still high, confidence.
    """

    settings = (options or GroupLayoutOptions()).validated()
    center = _object_center(islands)
    buckets: Dict[Tuple[str, Tuple[int, ...]], List[IslandRecord]] = defaultdict(list)
    for island in islands:
        if _meaningful_repeat_candidate(island, settings):
            buckets[(island.geometry_signature, island.material_indices)].append(island)

    candidates: List[RepeatCandidate] = []
    groups: List[RepeatGroup] = []
    for (signature, _materials), bucket in sorted(buckets.items()):
        if len(bucket) < 2:
            continue
        ordered = sorted(bucket, key=lambda item: _repeat_spatial_key(item, center))
        clusters: List[List[IslandRecord]] = []
        for island in ordered:
            placed = False
            for cluster in clusters:
                reference = cluster[0]
                if (
                    len(cluster) < settings.max_repeat_group_size
                    and _relative_error(island.area_3d, reference.area_3d)
                    <= settings.repeat_area_tolerance
                ):
                    cluster.append(island)
                    placed = True
                    break
            if not placed:
                clusters.append([island])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            reference = cluster[0]
            local_candidates = []
            relation_counts = defaultdict(int)
            confidences = []
            for island in cluster[1:]:
                relation, axis, confidence = _relation_for_pair(
                    reference, island, center, object_diagonal
                )
                area_similarity = 1.0 - _clamp(
                    _relative_error(reference.area_3d, island.area_3d)
                    / max(settings.repeat_area_tolerance, _EPSILON),
                    0.0,
                    1.0,
                )
                confidence = min(0.999, confidence + area_similarity * 0.001)
                if confidence < settings.repeat_min_confidence:
                    continue
                candidate = RepeatCandidate(
                    left_id=reference.island_id,
                    right_id=island.island_id,
                    relation=relation,
                    axis=axis,
                    confidence=confidence,
                    signature=signature,
                )
                local_candidates.append(candidate)
                relation_counts[relation] += 1
                confidences.append(confidence)
            if not local_candidates:
                continue
            members = (reference.island_id,) + tuple(
                candidate.right_id for candidate in local_candidates
            )
            relation = sorted(
                relation_counts, key=lambda value: (-relation_counts[value], value)
            )[0]
            candidates.extend(local_candidates)
            groups.append(RepeatGroup(
                group_id=len(groups),
                member_ids=members,
                relation=relation,
                confidence=min(confidences),
                signature=signature,
            ))
    return candidates, groups


def _bounds_distance_3d(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    squared = 0.0
    for axis in range(3):
        gap = max(left[axis] - right[axis + 3], right[axis] - left[axis + 3], 0.0)
        squared += gap * gap
    return math.sqrt(squared)


def _merge_small_repeat_owner_groups(
    groups: Sequence[Dict[str, Any]],
    repeat_groups: Sequence[RepeatGroup],
    by_id: Mapping[int, IslandRecord],
    settings: GroupLayoutOptions,
) -> List[Dict[str, Any]]:
    """Keep repeated small parts together without detaching local context.

    Small islands first select their concrete model-space owner in
    ``build_layout_groups``. This pass then joins the owner cells for a
    high-confidence all-small repeat cohort. The UV block therefore keeps each
    fragment beside its local structure while also placing corresponding
    structures together.
    """

    groups = [dict(group) for group in groups]
    if len(groups) < 2:
        return groups
    cohorts = [
        repeat
        for repeat in repeat_groups
        if (
            len(repeat.member_ids) >= 2
            and all(
                member in by_id and by_id[member].is_small
                for member in repeat.member_ids
            )
        )
    ]
    if not cohorts:
        return groups

    member_group = {
        member: group_index
        for group_index, group in enumerate(groups)
        for member in group["members"]
    }
    parent = list(range(len(groups)))
    anchor_count = [max(len(group["anchors"]), 1) for group in groups]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for repeat in cohorts:
        roots = sorted({
            find(member_group[member])
            for member in repeat.member_ids
            if member in member_group
        })
        if len(roots) < 2:
            continue
        combined_anchors = sum(anchor_count[root] for root in roots)
        if combined_anchors > settings.max_repeat_group_size:
            continue
        target = roots[0]
        for root in roots[1:]:
            parent[root] = target
            anchor_count[target] += anchor_count[root]

    components: Dict[int, List[int]] = defaultdict(list)
    for group_index in range(len(groups)):
        components[find(group_index)].append(group_index)
    if all(len(indices) == 1 for indices in components.values()):
        return groups

    relations: Dict[int, set] = defaultdict(set)
    for repeat in cohorts:
        owners = {
            find(member_group[member])
            for member in repeat.member_ids
            if member in member_group
        }
        if len(owners) == 1:
            relations[min(owners)].add(repeat.relation)

    merged_groups: List[Dict[str, Any]] = []
    for root, indices in sorted(
        components.items(), key=lambda item: min(item[1])
    ):
        if len(indices) == 1:
            merged_groups.append(groups[indices[0]])
            continue

        members: List[int] = []
        anchors: List[int] = []
        small: List[int] = []
        small_anchors: List[int] = []
        for group_index in sorted(indices):
            group = groups[group_index]
            for member in group["members"]:
                if member not in members:
                    members.append(member)
            for anchor in group["anchors"]:
                if anchor not in anchors:
                    anchors.append(anchor)
            for member, anchor in zip(group["small"], group["small_anchors"]):
                if member not in small:
                    small.append(member)
                    small_anchors.append(anchor)
        relation_set = relations.get(find(root), set())
        reason = (
            min(relation_set)
            if len(relation_set) == 1
            else "REPEATED"
        )
        merged_groups.append({
            "members": members,
            "anchors": anchors,
            "small": small,
            "small_anchors": small_anchors,
            "reason": reason,
        })
    return merged_groups


def build_layout_groups(
    islands: Sequence[IslandRecord],
    repeat_groups: Sequence[RepeatGroup],
    object_diagonal: float,
    options: Optional[GroupLayoutOptions] = None,
) -> List[LayoutGroup]:
    """Attach small islands to a concrete nearby model-space anchor."""

    settings = (options or GroupLayoutOptions()).validated()
    by_id = {island.island_id: island for island in islands}
    assigned = set()
    mutable_groups: List[Dict[str, Any]] = []
    for repeat in repeat_groups:
        # Repeated tiny fragments still share the orientation resolved from
        # ``repeat_groups``, but they must not become global layout anchors.
        # Keeping them unassigned here lets the model-space pass below attach
        # each fragment to the nearest meaningful structure instead of pulling
        # distant, identical slivers into one UV block.
        members = [
            member
            for member in repeat.member_ids
            if member not in assigned and not by_id[member].is_small
        ]
        if len(members) < 2:
            continue
        mutable_groups.append({
            "members": list(members),
            "anchors": list(members),
            "small": [],
            "small_anchors": [],
            "reason": repeat.relation,
        })
        assigned.update(members)

    for island in islands:
        if island.island_id in assigned or island.is_small:
            continue
        mutable_groups.append({
            "members": [island.island_id],
            "anchors": [island.island_id],
            "small": [],
            "small_anchors": [],
            "reason": "SINGLE_ANCHOR",
        })
        assigned.add(island.island_id)

    unassigned_small = [
        island for island in islands
        if island.is_small and island.island_id not in assigned
    ]
    if not mutable_groups and unassigned_small:
        anchor = max(unassigned_small, key=lambda item: (item.area_3d, -item.island_id))
        mutable_groups.append({
            "members": [anchor.island_id],
            "anchors": [anchor.island_id],
            "small": [],
            "small_anchors": [],
            "reason": "SMALL_CLUSTER",
        })
        assigned.add(anchor.island_id)
        unassigned_small.remove(anchor)

    adjacency_sets = {island.island_id: set(island.neighbor_ids) for island in islands}
    maximum_distance = object_diagonal * settings.proximity_radius_ratio
    fallback_small_counts: Dict[int, int] = defaultdict(int)
    remaining = []
    for small in sorted(unassigned_small, key=lambda item: (-item.area_3d, item.island_id)):
        scored = []
        for group_index, group in enumerate(mutable_groups):
            for anchor_id in group["anchors"]:
                anchor = by_id[anchor_id]
                topology_neighbor = anchor_id in adjacency_sets[small.island_id]
                if (
                    not topology_neighbor
                    and fallback_small_counts[anchor_id]
                    >= settings.max_small_members_per_group
                ):
                    continue
                same_material = bool(
                    set(small.material_indices) & set(anchor.material_indices)
                )
                distance = _bounds_distance_3d(
                    small.model_bounds, anchor.model_bounds
                )
                scored.append((
                    not topology_neighbor,
                    distance,
                    not same_material,
                    group_index,
                    anchor_id,
                ))
        if not scored:
            remaining.append(small)
            continue
        best = min(scored)
        topology_neighbor = not best[0]
        if not topology_neighbor and best[1] > maximum_distance:
            remaining.append(small)
            continue
        group = mutable_groups[best[3]]
        group["members"].append(small.island_id)
        group["small"].append(small.island_id)
        group["small_anchors"].append(best[4])
        if not topology_neighbor:
            fallback_small_counts[best[4]] += 1
        if group["reason"] == "SINGLE_ANCHOR":
            group["reason"] = "ANCHOR_WITH_SMALL"
        assigned.add(small.island_id)

    # Detached tiny pieces with no eligible large anchor are still clustered
    # with their nearest small peers, but never welded or overlapped.
    while remaining:
        anchor = remaining.pop(0)
        cluster = [anchor]
        near = sorted(
            remaining,
            key=lambda item: (
                _bounds_distance_3d(anchor.model_bounds, item.model_bounds),
                item.island_id,
            ),
        )
        for candidate in near:
            if len(cluster) >= settings.max_small_members_per_group + 1:
                break
            distance = _bounds_distance_3d(anchor.model_bounds, candidate.model_bounds)
            if distance <= maximum_distance:
                cluster.append(candidate)
        selected = {item.island_id for item in cluster}
        remaining = [item for item in remaining if item.island_id not in selected]
        mutable_groups.append({
            "members": [item.island_id for item in cluster],
            "anchors": [anchor.island_id],
            "small": [item.island_id for item in cluster[1:]],
            "small_anchors": [anchor.island_id] * max(len(cluster) - 1, 0),
            "reason": "SMALL_CLUSTER",
        })
        assigned.update(selected)

    mutable_groups = _merge_small_repeat_owner_groups(
        mutable_groups, repeat_groups, by_id, settings
    )

    # A defensive fallback makes the partition total even if thresholds or
    # future caller-provided records are unusual.
    for island in islands:
        if island.island_id not in assigned:
            mutable_groups.append({
                "members": [island.island_id],
                "anchors": [island.island_id],
                "small": [],
                "small_anchors": [],
                "reason": "SINGLETON",
            })
            assigned.add(island.island_id)

    member_group_index = {
        member_id: group_index
        for group_index, group in enumerate(mutable_groups)
        for member_id in group["members"]
    }
    member_owner = {}
    for group in mutable_groups:
        for anchor_id in group["anchors"]:
            member_owner[anchor_id] = anchor_id
        for small_id, anchor_id in zip(group["small"], group["small_anchors"]):
            member_owner[small_id] = anchor_id
        for member_id in group["members"]:
            member_owner.setdefault(member_id, member_id)

    owner_cohorts_by_group: Dict[int, List[Tuple[int, ...]]] = defaultdict(list)
    for repeat in repeat_groups:
        group_indices = {
            member_group_index[member_id]
            for member_id in repeat.member_ids
            if member_id in member_group_index
        }
        if len(group_indices) != 1:
            continue
        owners = []
        for member_id in repeat.member_ids:
            owner_id = member_owner.get(member_id)
            if owner_id is not None and owner_id not in owners:
                owners.append(owner_id)
        if len(owners) < 2:
            continue
        group_index = next(iter(group_indices))
        cohort = tuple(owners)
        if cohort not in owner_cohorts_by_group[group_index]:
            owner_cohorts_by_group[group_index].append(cohort)

    layout_groups = []
    for index, group in enumerate(mutable_groups):
        small_by_anchor: Dict[int, List[int]] = defaultdict(list)
        for small_id, anchor_id in zip(group["small"], group["small_anchors"]):
            small_by_anchor[anchor_id].append(small_id)
        ordered_members = []
        ordered_small = []
        ordered_small_anchors = []
        for anchor_id in group["anchors"]:
            ordered_members.append(anchor_id)
            attached = small_by_anchor.get(anchor_id, ())
            ordered_members.extend(attached)
            ordered_small.extend(attached)
            ordered_small_anchors.extend([anchor_id] * len(attached))
        ordered_members.extend(
            member_id
            for member_id in group["members"]
            if member_id not in ordered_members
        )
        layout_groups.append(LayoutGroup(
            group_id=index,
            member_ids=tuple(ordered_members),
            reason=str(group["reason"]),
            anchor_ids=tuple(group["anchors"]),
            small_member_ids=tuple(ordered_small),
            small_anchor_ids=tuple(ordered_small_anchors),
            owner_cohorts=tuple(owner_cohorts_by_group.get(index, ())),
        ))
    return layout_groups


def analyze_active_uv(
    obj: Any,
    options: Optional[GroupLayoutOptions] = None,
) -> UVLayoutAnalysis:
    """Analyze the active UV without changing coordinates or mesh state."""

    settings = (options or GroupLayoutOptions()).validated()
    mesh, uv_layer = _require_object_mode_mesh(obj)
    islands, face_to_island, adjacency, object_diagonal = compute_active_uv_islands(
        obj, settings
    )
    repeat_candidates, repeat_groups = detect_repeat_groups(
        islands, object_diagonal, settings
    )
    layout_groups = build_layout_groups(
        islands, repeat_groups, object_diagonal, settings
    )
    return UVLayoutAnalysis(
        object_name=str(obj.name),
        mesh_name=str(mesh.name),
        uv_layer_name=str(uv_layer.name),
        object_diagonal=object_diagonal,
        islands=islands,
        adjacency=adjacency,
        repeat_candidates=repeat_candidates,
        repeat_groups=repeat_groups,
        layout_groups=layout_groups,
        face_to_island=face_to_island,
    )


def _rotate_point(point: Vector, center: Vector, angle: float) -> Vector:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    relative = point - center
    return Vector((
        center.x + relative.x * cosine - relative.y * sine,
        center.y + relative.x * sine + relative.y * cosine,
    ))


def _nearest_cardinal_delta(angle: float) -> float:
    targets = (-math.pi * 0.5, 0.0, math.pi * 0.5)
    return min((_line_angle_wrap(target - angle) for target in targets), key=abs)


def _repeat_target_angle(
    group: RepeatGroup,
    by_id: Mapping[int, IslandRecord],
) -> float:
    return _member_target_angle(group.member_ids, by_id)


def _member_target_angle(
    member_ids: Sequence[int],
    by_id: Mapping[int, IslandRecord],
) -> float:
    scores = []
    for target in (0.0, math.pi * 0.5):
        score = sum(
            abs(_line_angle_wrap(target - by_id[index].principal_angle))
            * max(by_id[index].anisotropy, 0.05)
            for index in member_ids
        )
        scores.append((score, target))
    return min(scores)[1]


def _orientation_components(
    analysis: UVLayoutAnalysis,
) -> Tuple[Tuple[int, ...], ...]:
    """Join every repeat and owner-cohort direction constraint transitively."""

    valid_ids = {island.island_id for island in analysis.islands}
    constraints = [
        tuple(island_id for island_id in group.member_ids if island_id in valid_ids)
        for group in analysis.repeat_groups
    ]
    constraints.extend(
        tuple(island_id for island_id in cohort if island_id in valid_ids)
        for layout_group in analysis.layout_groups
        for cohort in layout_group.owner_cohorts
    )
    constraints = [
        tuple(dict.fromkeys(members))
        for members in constraints
        if len(set(members)) >= 2
    ]
    if not constraints:
        return ()

    parent: Dict[int, int] = {}

    def find(island_id: int) -> int:
        parent.setdefault(island_id, island_id)
        while parent[island_id] != island_id:
            parent[island_id] = parent[parent[island_id]]
            island_id = parent[island_id]
        return island_id

    for members in constraints:
        root = find(members[0])
        for island_id in members[1:]:
            other = find(island_id)
            if other != root:
                parent[other] = root

    components: Dict[int, List[int]] = defaultdict(list)
    for island_id in sorted(parent):
        components[find(island_id)].append(island_id)
    return tuple(
        tuple(members)
        for _root, members in sorted(
            components.items(), key=lambda item: (min(item[1]), tuple(item[1]))
        )
        if len(members) >= 2
    )


def _orientation_angles(
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> Dict[int, float]:
    by_id = {island.island_id: island for island in analysis.islands}
    angles: Dict[int, float] = {}
    constrained = set()
    for member_ids in _orientation_components(analysis):
        target = _member_target_angle(member_ids, by_id)
        directed_group = all(
            by_id[island_id].direction_confidence
            >= settings.min_direction_confidence
            and by_id[island_id].direction_vector.length_squared > _EPSILON
            and all(
                math.isfinite(float(component))
                for component in by_id[island_id].direction_vector
            )
            for island_id in member_ids
        )
        for island_id in member_ids:
            island = by_id[island_id]
            constrained.add(island_id)
            if directed_group:
                direction_angle = math.atan2(
                    island.direction_vector.y, island.direction_vector.x
                )
                # PCA is an undirected line and cannot distinguish +theta from
                # -theta. A stable landmark can, so align it directly to the
                # shared group target under a full 360-degree policy.
                angle = _angle_wrap(target - direction_angle)
            elif island.anisotropy >= settings.min_pca_anisotropy:
                angle = _line_angle_wrap(target - island.principal_angle)
            else:
                angle = 0.0
            angles[island_id] = angle

    for island in analysis.islands:
        if island.island_id in constrained:
            continue
        if (
            settings.align_non_repeat_cardinal
            and island.anisotropy >= settings.min_pca_anisotropy
        ):
            angles[island.island_id] = _nearest_cardinal_delta(island.principal_angle)
        else:
            angles[island.island_id] = 0.0
    return angles


def _oriented_coordinates(
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    angles: Mapping[int, float],
) -> Dict[int, Dict[int, Vector]]:
    result: Dict[int, Dict[int, Vector]] = {}
    for island in analysis.islands:
        angle = angles.get(island.island_id, 0.0)
        result[island.island_id] = {
            loop_index: _rotate_point(
                uv_layer.data[loop_index].uv.copy(), island.uv_centroid, angle
            )
            for loop_index in island.loop_indices
        }
    return result


def _candidate_shelf_widths(rectangles: Sequence[_Rect], gap: float) -> List[float]:
    if not rectangles:
        return [1.0]
    maximum = max(max(rect.width, rect.height) for rect in rectangles)
    total_area = sum(rect.width * rect.height for rect in rectangles)
    root = math.sqrt(max(total_area, _EPSILON))
    total_width = sum(rect.width for rect in rectangles) + gap * max(len(rectangles) - 1, 0)
    values = [
        maximum,
        root * 0.80,
        root,
        root * 1.20,
        root * 1.50,
        root * 2.00,
        total_width,
    ]
    return sorted({max(float(value), maximum, _EPSILON) for value in values})


def _shelf_pack_once(
    rectangles: Sequence[_Rect],
    target_width: float,
    gap: float,
    allow_rotate: bool,
    preserve_order: bool,
    fixed_quarter_turns: Optional[Mapping[int, bool]] = None,
) -> Tuple[Dict[int, _Placement], float, float]:
    if preserve_order:
        ordered = list(rectangles)
    else:
        ordered = sorted(
            rectangles,
            key=lambda rect: (
                -max(rect.width, rect.height),
                -(rect.width * rect.height),
                rect.sort_rank,
                rect.key,
            ),
        )
    placements: Dict[int, _Placement] = {}
    x = 0.0
    y = 0.0
    row_height = 0.0
    maximum_x = 0.0
    fixed_quarter_turns = fixed_quarter_turns or {}
    for rect in ordered:
        required_quarter_turn = fixed_quarter_turns.get(rect.key)
        if required_quarter_turn is True:
            if not allow_rotate:
                raise RuntimeError(
                    "Rectangle {} requires a disabled quarter-turn".format(rect.key)
                )
            orientations = [(rect.height, rect.width, True)]
        elif required_quarter_turn is False:
            orientations = [(rect.width, rect.height, False)]
        else:
            orientations = [(rect.width, rect.height, False)]
            if allow_rotate and abs(rect.width - rect.height) > _EPSILON:
                orientations.append((rect.height, rect.width, True))

        fitting = [
            item for item in orientations
            if x <= _EPSILON or x + item[0] <= target_width + _EPSILON
        ]
        if not fitting:
            y += row_height + gap
            x = 0.0
            row_height = 0.0
            fitting = orientations
        width, height, rotated = min(
            fitting,
            key=lambda item: (
                max(row_height, item[1]),
                max(target_width, x + item[0]),
                item[1],
                item[0],
                item[2],
            ),
        )
        placements[rect.key] = _Placement(
            x=x,
            y=y,
            width=width,
            height=height,
            quarter_turn=rotated,
        )
        x += width + gap
        row_height = max(row_height, height)
        maximum_x = max(maximum_x, x - gap)
    return placements, maximum_x, y + row_height


def _best_shelf_pack(
    rectangles: Sequence[_Rect],
    gap: float,
    allow_rotate: bool,
    preserve_order: bool = False,
    fixed_quarter_turns: Optional[Mapping[int, bool]] = None,
) -> Tuple[Dict[int, _Placement], float, float]:
    if not rectangles:
        return {}, 0.0, 0.0
    candidates = []
    for target_width in _candidate_shelf_widths(rectangles, gap):
        placements, width, height = _shelf_pack_once(
            rectangles,
            target_width,
            gap,
            allow_rotate,
            preserve_order,
            fixed_quarter_turns,
        )
        candidates.append((
            max(width, height),
            width * height,
            abs(width - height),
            target_width,
            placements,
            width,
            height,
        ))
    best = min(candidates, key=lambda item: item[:4])
    return best[4], best[5], best[6]


def _translate_to_origin(coordinates: Mapping[int, Vector]) -> Tuple[Dict[int, Vector], float, float]:
    bounds = _uv_bounds(coordinates.values())
    translated = {
        key: Vector((value.x - bounds[0], value.y - bounds[1]))
        for key, value in coordinates.items()
    }
    return translated, bounds[2] - bounds[0], bounds[3] - bounds[1]


def _placement_distance(left: _Placement, right: _Placement) -> float:
    horizontal = max(
        left.x - (right.x + right.width),
        right.x - (left.x + left.width),
        0.0,
    )
    vertical = max(
        left.y - (right.y + right.height),
        right.y - (left.y + left.height),
        0.0,
    )
    return math.hypot(horizontal, vertical)


def _placements_clear(
    left: _Placement,
    right: _Placement,
    gap: float,
) -> bool:
    return bool(
        left.x + left.width + gap <= right.x + _EPSILON
        or right.x + right.width + gap <= left.x + _EPSILON
        or left.y + left.height + gap <= right.y + _EPSILON
        or right.y + right.height + gap <= left.y + _EPSILON
    )


def _placements_near(
    left: _Placement,
    right: _Placement,
    gap: float,
) -> bool:
    near_limit = max(
        math.hypot(left.width, left.height),
        math.hypot(right.width, right.height),
    ) + 2.0 * gap
    return bool(
        _placement_distance(left, right) <= near_limit + _EPSILON
    )


def _affinity_candidate_placements(
    cell_id: int,
    rectangles: Mapping[int, _Rect],
    adjacency: Mapping[int, set],
    placements: Mapping[int, _Placement],
    gap: float,
) -> List[_Placement]:
    """Return deterministic non-overlapping positions near a placed peer."""

    rect = rectangles[cell_id]
    related_ids = tuple(sorted(
        set(adjacency.get(cell_id, ())) & set(placements)
    ))
    if not related_ids:
        return []

    current = tuple(placements.values())
    minimum_x = min(item.x for item in current)
    minimum_y = min(item.y for item in current)
    maximum_x = max(item.x + item.width for item in current)
    maximum_y = max(item.y + item.height for item in current)
    x_values = {
        minimum_x - rect.width - gap,
        maximum_x + gap,
    }
    y_values = {
        minimum_y - rect.height - gap,
        maximum_y + gap,
    }

    # Obstacle edges let a candidate slide past a blocking owner while the
    # related-owner coordinates keep the search focused on affinity peers.
    for placed in current:
        x_values.update((
            placed.x - rect.width - gap,
            placed.x + placed.width + gap,
            placed.x,
            placed.x + placed.width - rect.width,
            placed.x + (placed.width - rect.width) * 0.5,
        ))
        y_values.update((
            placed.y - rect.height - gap,
            placed.y + placed.height + gap,
            placed.y,
            placed.y + placed.height - rect.height,
            placed.y + (placed.height - rect.height) * 0.5,
        ))

    scored = []
    seen = set()
    for x in sorted(x_values):
        for y in sorted(y_values):
            key = (round(float(x), 12), round(float(y), 12))
            if key in seen:
                continue
            seen.add(key)
            candidate = _Placement(x, y, rect.width, rect.height)
            if any(
                not _placements_clear(candidate, item, gap)
                for item in current
            ):
                continue
            related = [placements[related_id] for related_id in related_ids]
            near_count = sum(
                _placements_near(candidate, item, gap) for item in related
            )
            if near_count == 0:
                continue
            related_distances = [
                _placement_distance(candidate, item) for item in related
            ]
            candidate_minimum_x = min(minimum_x, x)
            candidate_minimum_y = min(minimum_y, y)
            candidate_maximum_x = max(maximum_x, x + rect.width)
            candidate_maximum_y = max(maximum_y, y + rect.height)
            component_width = candidate_maximum_x - candidate_minimum_x
            component_height = candidate_maximum_y - candidate_minimum_y
            scored.append((
                -near_count,
                min(related_distances),
                max(component_width, component_height),
                component_width * component_height,
                abs(x) + abs(y),
                y,
                x,
                candidate,
            ))
    scored.sort(key=lambda item: item[:-1])
    return [item[-1] for item in scored]


def _affinity_cohorts_valid(
    placements: Mapping[int, _Placement],
    cohorts: Sequence[Sequence[int]],
    gap: float,
    require_complete: bool,
) -> bool:
    """Check placed cohort members without rejecting satisfiable partials."""

    placed_ids = set(placements)
    for cohort in cohorts:
        members = tuple(member_id for member_id in cohort if member_id in placed_ids)
        if len(members) < 2:
            if require_complete and members:
                return False
            continue
        complete = len(members) == len(cohort)
        if not complete and not require_complete:
            continue
        for member_id in members:
            if not any(
                _placements_near(
                    placements[member_id], placements[peer_id], gap
                )
                for peer_id in members
                if peer_id != member_id
            ):
                return False
    return True


def _affinity_state_key(
    placements: Mapping[int, _Placement],
) -> Tuple[Tuple[int, float, float], ...]:
    minimum_x = min(item.x for item in placements.values())
    minimum_y = min(item.y for item in placements.values())
    return tuple(
        (
            cell_id,
            round(float(placement.x - minimum_x), 10),
            round(float(placement.y - minimum_y), 10),
        )
        for cell_id, placement in sorted(placements.items())
    )


def _pack_affinity_component(
    rectangles: Mapping[int, _Rect],
    adjacency: Mapping[int, set],
    cohorts: Sequence[Sequence[int]],
    gap: float,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack one owner-cell graph with deterministic bounded backtracking."""

    ids = tuple(sorted(rectangles))
    if not ids:
        return {}, 0.0, 0.0
    normalized_cohorts = tuple(
        tuple(member_id for member_id in cohort if member_id in rectangles)
        for cohort in cohorts
        if sum(member_id in rectangles for member_id in cohort) >= 2
    )
    root_order = sorted(
        ids,
        key=lambda cell_id: (
            -len(adjacency.get(cell_id, ())),
            -max(rectangles[cell_id].width, rectangles[cell_id].height),
            -(rectangles[cell_id].width * rectangles[cell_id].height),
            cell_id,
        ),
    )
    solution = None
    shared_states = [0]
    for maximum_cell_choices, maximum_candidates, policy_budget in (
        _AFFINITY_SEARCH_POLICIES
    ):
        maximum_cell_choices = (
            len(ids) if maximum_cell_choices is None else maximum_cell_choices
        )
        policy_ceiling = shared_states[0] + max(int(policy_budget), 0)
        for root_index, root_id in enumerate(root_order):
            remaining_roots = len(root_order) - root_index
            remaining_budget = policy_ceiling - shared_states[0]
            if remaining_budget <= 0:
                break
            root_ceiling = shared_states[0] + max(
                remaining_budget // remaining_roots,
                1,
            )
            root = rectangles[root_id]
            placements: Dict[int, _Placement] = {
                root_id: _Placement(0.0, 0.0, root.width, root.height)
            }
            visited = set()
            root_exhausted = [False]

            def search() -> Optional[Dict[int, _Placement]]:
                if shared_states[0] >= root_ceiling:
                    root_exhausted[0] = True
                    return None
                shared_states[0] += 1
                state_key = _affinity_state_key(placements)
                if state_key in visited:
                    return None
                visited.add(state_key)
                if len(placements) == len(ids):
                    if _affinity_cohorts_valid(
                        placements, normalized_cohorts, gap, True
                    ):
                        return dict(placements)
                    return None
                if shared_states[0] >= root_ceiling:
                    root_exhausted[0] = True
                    return None

                placed_ids = set(placements)
                frontier = []
                for cell_id in ids:
                    if root_exhausted[0]:
                        return None
                    if cell_id in placed_ids:
                        continue
                    placed_neighbors = (
                        set(adjacency.get(cell_id, ())) & placed_ids
                    )
                    if not placed_neighbors:
                        continue
                    candidates = _affinity_candidate_placements(
                        cell_id, rectangles, adjacency, placements, gap
                    )
                    if not candidates:
                        continue
                    frontier.append((
                        -len(placed_neighbors),
                        -len(adjacency.get(cell_id, ())),
                        len(candidates),
                        -max(
                            rectangles[cell_id].width,
                            rectangles[cell_id].height,
                        ),
                        cell_id,
                        candidates,
                    ))
                if not frontier:
                    return None
                frontier.sort(key=lambda item: item[:-1])
                for record in frontier[:maximum_cell_choices]:
                    if root_exhausted[0]:
                        return None
                    cell_id = record[-2]
                    candidates = record[-1]
                    if maximum_candidates is not None:
                        candidates = candidates[:maximum_candidates]
                    for candidate in candidates:
                        if root_exhausted[0]:
                            return None
                        placements[cell_id] = candidate
                        result = None
                        if _affinity_cohorts_valid(
                            placements, normalized_cohorts, gap, False
                        ):
                            result = search()
                        placements.pop(cell_id, None)
                        if result is not None:
                            return result
                        if root_exhausted[0]:
                            return None
                return None

            solution = search()
            if solution is not None:
                break
        if solution is not None:
            break

    if solution is None:
        raise RuntimeError(
            "Could not solve repeat-owner affinity component {}".format(ids)
        )
    placements = solution

    minimum_x = min(item.x for item in placements.values())
    minimum_y = min(item.y for item in placements.values())
    maximum_x = max(item.x + item.width for item in placements.values())
    maximum_y = max(item.y + item.height for item in placements.values())
    normalized = {
        cell_id: _Placement(
            placement.x - minimum_x,
            placement.y - minimum_y,
            placement.width,
            placement.height,
        )
        for cell_id, placement in placements.items()
    }
    return normalized, maximum_x - minimum_x, maximum_y - minimum_y


def _pack_owner_cells(
    owner_cells: Mapping[int, Tuple[Mapping[int, Mapping[int, Vector]], float, float]],
    owner_order: Sequence[int],
    owner_cohorts: Sequence[Sequence[int]],
    gap: float,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack owner cells while keeping every repeat owner near a cohort peer."""

    rectangles = {
        cell_id: _Rect(
            key=cell_id,
            width=float(owner_cells[cell_id][1]),
            height=float(owner_cells[cell_id][2]),
            sort_rank=position,
        )
        for position, cell_id in enumerate(owner_order)
    }
    adjacency: Dict[int, set] = {
        cell_id: set() for cell_id in owner_order
    }
    normalized_cohorts = []
    for cohort in owner_cohorts:
        members = tuple(
            cell_id for cell_id in cohort
            if cell_id in rectangles
        )
        members = tuple(dict.fromkeys(members))
        if len(members) < 2:
            continue
        normalized_cohorts.append(members)
        for left_id in members:
            adjacency[left_id].update(
                right_id for right_id in members if right_id != left_id
            )

    components = []
    remaining = set(owner_order)
    order_rank = {cell_id: index for index, cell_id in enumerate(owner_order)}
    while remaining:
        seed = min(remaining, key=lambda cell_id: order_rank[cell_id])
        component = []
        stack = [seed]
        while stack:
            cell_id = stack.pop()
            if cell_id not in remaining:
                continue
            remaining.remove(cell_id)
            component.append(cell_id)
            neighbors = sorted(
                adjacency.get(cell_id, ()) & remaining,
                key=lambda value: order_rank[value],
                reverse=True,
            )
            stack.extend(neighbors)
        components.append(tuple(component))

    component_placements = {}
    component_sizes = {}
    for component_id, component in enumerate(components):
        if len(component) == 1:
            cell_id = component[0]
            rect = rectangles[cell_id]
            placements = {
                cell_id: _Placement(0.0, 0.0, rect.width, rect.height)
            }
            width, height = rect.width, rect.height
        else:
            placements, width, height = _pack_affinity_component(
                {cell_id: rectangles[cell_id] for cell_id in component},
                adjacency,
                [
                    cohort for cohort in normalized_cohorts
                    if any(cell_id in component for cell_id in cohort)
                ],
                gap,
            )
        component_placements[component_id] = placements
        component_sizes[component_id] = (width, height)

    component_rectangles = [
        _Rect(
            key=component_id,
            width=component_sizes[component_id][0],
            height=component_sizes[component_id][1],
            sort_rank=component_id,
        )
        for component_id in range(len(components))
    ]
    block_placements, packed_width, packed_height = _best_shelf_pack(
        component_rectangles,
        gap,
        allow_rotate=False,
        preserve_order=False,
    )
    placements = {}
    for component_id, local_placements in component_placements.items():
        block = block_placements[component_id]
        for cell_id, local in local_placements.items():
            placements[cell_id] = _Placement(
                local.x + block.x,
                local.y + block.y,
                local.width,
                local.height,
            )

    for cohort in normalized_cohorts:
        for cell_id in cohort:
            placement = placements[cell_id]
            peer_distances = []
            for peer_id in cohort:
                if peer_id == cell_id:
                    continue
                peer = placements[peer_id]
                near_limit = max(
                    math.hypot(placement.width, placement.height),
                    math.hypot(peer.width, peer.height),
                ) + 2.0 * gap
                peer_distances.append(
                    (_placement_distance(placement, peer), near_limit)
                )
            if not any(
                distance <= near_limit + _EPSILON
                for distance, near_limit in peer_distances
            ):
                raise RuntimeError(
                    "Owner cell {} is not near a repeat-cohort peer".format(
                        cell_id
                    )
                )
    return placements, packed_width, packed_height


def _pack_owner_cell(
    anchor_id: int,
    member_ids: Sequence[int],
    island_local: Mapping[int, Mapping[int, Vector]],
    island_sizes: Mapping[int, Tuple[float, float]],
    gap: float,
) -> Tuple[Dict[int, Dict[int, Vector]], float, float]:
    """Pack attached fragments around their model-space owner."""

    anchor_width, anchor_height = island_sizes[anchor_id]
    placements: Dict[int, _Placement] = {
        anchor_id: _Placement(0.0, 0.0, anchor_width, anchor_height)
    }
    attachments = [
        member_id for member_id in member_ids if member_id != anchor_id
    ]
    attachment_rank = {
        member_id: position for position, member_id in enumerate(attachments)
    }
    attachments.sort(key=lambda member_id: (
        -max(island_sizes[member_id]),
        -(island_sizes[member_id][0] * island_sizes[member_id][1]),
        attachment_rank[member_id],
        member_id,
    ))
    ordered_members = [anchor_id] + attachments

    for member_id in ordered_members[1:]:
        width, height = island_sizes[member_id]
        current = tuple(placements.values())
        minimum_x = min(item.x for item in current)
        minimum_y = min(item.y for item in current)
        maximum_x = max(item.x + item.width for item in current)
        maximum_y = max(item.y + item.height for item in current)

        x_values = {
            minimum_x - width - gap,
            maximum_x + gap,
            (anchor_width - width) * 0.5,
        }
        y_values = {
            minimum_y - height - gap,
            maximum_y + gap,
            (anchor_height - height) * 0.5,
        }
        for item in current:
            x_values.update((
                item.x - width - gap,
                item.x + item.width + gap,
                item.x,
                item.x + item.width - width,
                item.x + (item.width - width) * 0.5,
            ))
            y_values.update((
                item.y - height - gap,
                item.y + item.height + gap,
                item.y,
                item.y + item.height - height,
                item.y + (item.height - height) * 0.5,
            ))

        candidates = []
        anchor = placements[anchor_id]
        for x in sorted(x_values):
            for y in sorted(y_values):
                candidate = _Placement(x, y, width, height)
                if any(
                    not _placements_clear(candidate, item, gap)
                    for item in current
                ):
                    continue
                owner_distance = _placement_distance(candidate, anchor)
                member_diagonal = math.hypot(width, height)
                anchor_diagonal = math.hypot(anchor_width, anchor_height)
                near_limit = max(member_diagonal, anchor_diagonal) + 2.0 * gap
                if owner_distance > near_limit + _EPSILON:
                    continue
                cell_minimum_x = min(minimum_x, x)
                cell_minimum_y = min(minimum_y, y)
                cell_maximum_x = max(maximum_x, x + width)
                cell_maximum_y = max(maximum_y, y + height)
                cell_width = cell_maximum_x - cell_minimum_x
                cell_height = cell_maximum_y - cell_minimum_y
                center_distance = math.hypot(
                    x + width * 0.5 - anchor_width * 0.5,
                    y + height * 0.5 - anchor_height * 0.5,
                )
                candidates.append((
                    owner_distance,
                    max(cell_width, cell_height),
                    cell_width * cell_height,
                    center_distance,
                    abs(x) + abs(y),
                    y,
                    x,
                    candidate,
                ))
        if not candidates:
            raise RuntimeError(
                "Could not place UV island {} near owner {}".format(
                    member_id, anchor_id
                )
            )
        placements[member_id] = min(candidates, key=lambda item: item[:-1])[-1]

    minimum_x = min(item.x for item in placements.values())
    minimum_y = min(item.y for item in placements.values())
    maximum_x = max(item.x + item.width for item in placements.values())
    maximum_y = max(item.y + item.height for item in placements.values())
    packed = {}
    for member_id in ordered_members:
        placement = placements[member_id]
        packed[member_id] = {
            loop_index: Vector((
                point.x + placement.x - minimum_x,
                point.y + placement.y - minimum_y,
            ))
            for loop_index, point in island_local[member_id].items()
        }
    return packed, maximum_x - minimum_x, maximum_y - minimum_y


def _cross_group_repeat_cohorts(
    groups: Sequence[LayoutGroup],
    repeat_groups: Sequence[RepeatGroup],
) -> Tuple[Tuple[int, ...], ...]:
    """Return deterministic layout-group cohorts for cross-block repeats."""

    member_group: Dict[int, int] = {}
    for group in groups:
        for island_id in group.member_ids:
            previous = member_group.setdefault(island_id, group.group_id)
            if previous != group.group_id:
                raise RuntimeError(
                    "UV island {} belongs to multiple layout groups".format(island_id)
                )

    cohorts = []
    seen = set()
    for repeat in repeat_groups:
        group_ids = tuple(dict.fromkeys(
            member_group[island_id]
            for island_id in repeat.member_ids
            if island_id in member_group
        ))
        if len(group_ids) >= 2 and group_ids not in seen:
            cohorts.append(group_ids)
            seen.add(group_ids)
    return tuple(cohorts)


def _validate_repeat_group_rotation_parity(
    groups: Sequence[LayoutGroup],
    repeat_groups: Sequence[RepeatGroup],
    placements: Mapping[int, _Placement],
) -> None:
    member_group = {
        island_id: group.group_id
        for group in groups
        for island_id in group.member_ids
    }
    for repeat in repeat_groups:
        group_ids = {
            member_group[island_id]
            for island_id in repeat.member_ids
            if island_id in member_group
        }
        parities = {
            placements[group_id].quarter_turn
            for group_id in group_ids
            if group_id in placements
        }
        if len(parities) > 1:
            raise RuntimeError(
                "Repeat group {} was quarter-turned inconsistently across layout "
                "groups".format(repeat.group_id)
            )


def _pack_layout_group_rectangles(
    rectangles: Sequence[_Rect],
    repeat_cohorts: Sequence[Sequence[int]],
    gap: float,
    allow_rotate: bool,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack cross-group repeats as nearby blocks without merging their owners."""

    rectangle_by_id = {rectangle.key: rectangle for rectangle in rectangles}
    if len(rectangle_by_id) != len(rectangles):
        raise RuntimeError("Layout group rectangles contain duplicate ids")
    if not rectangles:
        return {}, 0.0, 0.0

    group_order = tuple(rectangle.key for rectangle in rectangles)
    order_rank = {
        group_id: index for index, group_id in enumerate(group_order)
    }
    adjacency: Dict[int, set] = {
        group_id: set() for group_id in group_order
    }
    normalized_cohorts = []
    for cohort in repeat_cohorts:
        members = tuple(dict.fromkeys(
            group_id for group_id in cohort if group_id in rectangle_by_id
        ))
        if len(members) < 2:
            continue
        normalized_cohorts.append(members)
        for group_id in members:
            adjacency[group_id].update(
                peer_id for peer_id in members if peer_id != group_id
            )

    components = []
    remaining = set(group_order)
    while remaining:
        seed = min(remaining, key=lambda group_id: order_rank[group_id])
        component = []
        stack = [seed]
        while stack:
            group_id = stack.pop()
            if group_id not in remaining:
                continue
            remaining.remove(group_id)
            component.append(group_id)
            neighbors = sorted(
                adjacency[group_id] & remaining,
                key=lambda value: order_rank[value],
                reverse=True,
            )
            stack.extend(neighbors)
        components.append(tuple(component))

    component_placements = {}
    component_sizes = {}
    for component_id, component in enumerate(components):
        if len(component) == 1:
            group_id = component[0]
            rectangle = rectangle_by_id[group_id]
            placements = {
                group_id: _Placement(
                    0.0, 0.0, rectangle.width, rectangle.height
                )
            }
            width, height = rectangle.width, rectangle.height
        else:
            component_set = set(component)
            placements, width, height = _pack_affinity_component(
                {
                    group_id: rectangle_by_id[group_id]
                    for group_id in component
                },
                adjacency,
                [
                    cohort for cohort in normalized_cohorts
                    if set(cohort) <= component_set
                ],
                gap,
            )
        component_placements[component_id] = placements
        component_sizes[component_id] = (width, height)

    component_rectangles = [
        _Rect(
            key=component_id,
            width=component_sizes[component_id][0],
            height=component_sizes[component_id][1],
            sort_rank=component_id,
        )
        for component_id in range(len(components))
    ]
    # A cross-repeat component already contains one shared direction. Keeping
    # the component unrotated preserves the previous parity lock, while
    # unrelated singleton components retain the normal quarter-turn choice.
    component_rotation_locks = {
        component_id: False
        for component_id, component in enumerate(components)
        if len(component) >= 2
    }
    block_placements, packed_width, packed_height = _best_shelf_pack(
        component_rectangles,
        gap,
        allow_rotate=allow_rotate,
        preserve_order=False,
        fixed_quarter_turns=component_rotation_locks,
    )

    placements = {}
    for component_id, local_placements in component_placements.items():
        block = block_placements[component_id]
        _component_width, component_height = component_sizes[component_id]
        for group_id, local in local_placements.items():
            if block.quarter_turn:
                placements[group_id] = _Placement(
                    x=block.x + component_height - local.y - local.height,
                    y=block.y + local.x,
                    width=local.height,
                    height=local.width,
                    quarter_turn=True,
                )
            else:
                placements[group_id] = _Placement(
                    x=block.x + local.x,
                    y=block.y + local.y,
                    width=local.width,
                    height=local.height,
                    quarter_turn=False,
                )

    if not _affinity_cohorts_valid(
        placements, normalized_cohorts, gap, require_complete=True
    ):
        raise RuntimeError("Cross-layout repeat affinity was not preserved")
    return placements, packed_width, packed_height


def _pack_plan(
    oriented: Mapping[int, Mapping[int, Vector]],
    groups: Sequence[LayoutGroup],
    gap: float,
    allow_group_quarter_turn: bool,
    repeat_groups: Sequence[RepeatGroup] = (),
) -> _PackedPlan:
    island_local: Dict[int, Dict[int, Vector]] = {}
    island_sizes: Dict[int, Tuple[float, float]] = {}
    for island_id, coordinates in oriented.items():
        local, width, height = _translate_to_origin(coordinates)
        if width <= _EPSILON or height <= _EPSILON:
            raise RuntimeError("UV island {} has degenerate bounds".format(island_id))
        island_local[island_id] = local
        island_sizes[island_id] = (width, height)

    group_coordinates: Dict[int, Dict[int, Dict[int, Vector]]] = {}
    group_sizes: Dict[int, Tuple[float, float]] = {}
    for group in groups:
        if len(group.small_member_ids) != len(group.small_anchor_ids):
            raise RuntimeError(
                "Layout group {} has inconsistent small-owner metadata".format(
                    group.group_id
                )
            )
        member_set = set(group.member_ids)
        anchor_set = set(group.anchor_ids)
        if len(member_set) != len(group.member_ids):
            raise RuntimeError(
                "Layout group {} contains duplicate members".format(group.group_id)
            )
        if len(set(group.small_member_ids)) != len(group.small_member_ids):
            raise RuntimeError(
                "Layout group {} assigns a small island more than once".format(
                    group.group_id
                )
            )
        if any(
            small_id not in member_set
            or anchor_id not in member_set
            or anchor_id not in anchor_set
            or small_id == anchor_id
            for small_id, anchor_id in zip(
                group.small_member_ids, group.small_anchor_ids
            )
        ):
            raise RuntimeError(
                "Layout group {} has invalid small-owner metadata".format(
                    group.group_id
                )
            )

        small_by_anchor: Dict[int, List[int]] = defaultdict(list)
        for small_id, anchor_id in zip(
            group.small_member_ids, group.small_anchor_ids
        ):
            if small_id in group.member_ids and anchor_id in group.member_ids:
                small_by_anchor[anchor_id].append(small_id)

        owner_cells = {}
        owner_order = []
        assigned = set()
        for anchor_id in group.anchor_ids:
            if anchor_id in assigned or anchor_id not in group.member_ids:
                continue
            members = [anchor_id]
            members.extend(
                member_id
                for member_id in small_by_anchor.get(anchor_id, ())
                if member_id not in assigned and member_id != anchor_id
            )
            cell_coordinates, cell_width, cell_height = _pack_owner_cell(
                anchor_id, members, island_local, island_sizes, gap
            )
            owner_cells[anchor_id] = (
                cell_coordinates, cell_width, cell_height
            )
            owner_order.append(anchor_id)
            assigned.update(members)

        # Defensive singleton cells preserve a total partition if a caller
        # supplies members without owner metadata.
        for island_id in group.member_ids:
            if island_id in assigned:
                continue
            cell_coordinates, cell_width, cell_height = _pack_owner_cell(
                island_id, (island_id,), island_local, island_sizes, gap
            )
            owner_cells[island_id] = (
                cell_coordinates, cell_width, cell_height
            )
            owner_order.append(island_id)
            assigned.add(island_id)

        placements, group_width, group_height = _pack_owner_cells(
            owner_cells,
            owner_order,
            group.owner_cohorts,
            gap,
        )
        packed_members: Dict[int, Dict[int, Vector]] = {}
        for cell_id in owner_order:
            cell_coordinates = owner_cells[cell_id][0]
            placement = placements[cell_id]
            for island_id, coordinates in cell_coordinates.items():
                packed_members[island_id] = {
                    loop_index: Vector((
                        point.x + placement.x,
                        point.y + placement.y,
                    ))
                    for loop_index, point in coordinates.items()
                }
        group_coordinates[group.group_id] = packed_members
        group_sizes[group.group_id] = (group_width, group_height)

    group_rectangles = [
        _Rect(
            key=group.group_id,
            width=group_sizes[group.group_id][0],
            height=group_sizes[group.group_id][1],
            sort_rank=group.group_id,
        )
        for group in groups
    ]
    repeat_cohorts = _cross_group_repeat_cohorts(groups, repeat_groups)
    placements, packed_width, packed_height = _pack_layout_group_rectangles(
        group_rectangles,
        repeat_cohorts,
        gap,
        allow_rotate=allow_group_quarter_turn,
    )
    _validate_repeat_group_rotation_parity(groups, repeat_groups, placements)
    final_coordinates: Dict[int, Dict[int, Vector]] = {}
    for group in groups:
        placement = placements[group.group_id]
        original_width, original_height = group_sizes[group.group_id]
        for island_id, coordinates in group_coordinates[group.group_id].items():
            transformed = {}
            for loop_index, point in coordinates.items():
                if placement.quarter_turn:
                    local = Vector((original_height - point.y, point.x))
                else:
                    local = point
                transformed[loop_index] = Vector((
                    local.x + placement.x,
                    local.y + placement.y,
                ))
            final_coordinates[island_id] = transformed
    return _PackedPlan(
        coordinates=final_coordinates,
        width=packed_width,
        height=packed_height,
        source_gap=gap,
        group_placements=placements,
    )


def _fit_plan_to_tile(
    plan: _PackedPlan,
    margin: float,
) -> Tuple[Dict[int, Dict[int, Vector]], float]:
    usable = max(1.0 - 2.0 * margin, _EPSILON)
    scale = min(
        usable / max(plan.width, _EPSILON),
        usable / max(plan.height, _EPSILON),
    )
    extra_x = (usable - plan.width * scale) * 0.5
    extra_y = (usable - plan.height * scale) * 0.5
    offset = Vector((margin + extra_x, margin + extra_y))
    return {
        island_id: {
            loop_index: point * scale + offset
            for loop_index, point in coordinates.items()
        }
        for island_id, coordinates in plan.coordinates.items()
    }, scale


def _plan_with_margin(
    oriented: Mapping[int, Mapping[int, Vector]],
    groups: Sequence[LayoutGroup],
    settings: GroupLayoutOptions,
    repeat_groups: Sequence[RepeatGroup] = (),
) -> Tuple[_PackedPlan, Dict[int, Dict[int, Vector]], float]:
    gap = 0.0
    plan = _pack_plan(
        oriented,
        groups,
        gap,
        settings.allow_group_quarter_turn,
        repeat_groups,
    )
    fitted, scale = _fit_plan_to_tile(plan, settings.margin)
    for _iteration in range(settings.packing_iterations):
        requested_source_gap = settings.margin / max(scale, _EPSILON)
        if abs(requested_source_gap - gap) <= max(requested_source_gap, 1.0) * 1.0e-6:
            break
        gap = requested_source_gap
        plan = _pack_plan(
            oriented,
            groups,
            gap,
            settings.allow_group_quarter_turn,
            repeat_groups,
        )
        fitted, scale = _fit_plan_to_tile(plan, settings.margin)

    # One conservative correction avoids ending below the requested gap when
    # the fixed-point iteration stops on a shelf-layout discontinuity.
    achieved = gap * scale
    if achieved + 1.0e-9 < settings.margin:
        gap *= settings.margin / max(achieved, _EPSILON) * 1.002
        plan = _pack_plan(
            oriented,
            groups,
            gap,
            settings.allow_group_quarter_turn,
            repeat_groups,
        )
        fitted, scale = _fit_plan_to_tile(plan, settings.margin)
    return plan, fitted, scale


def _triangle_records(
    mesh: Any,
    uv_layer: Any,
    face_to_island: Sequence[int],
) -> List[Tuple[float, float, float, float, int, int, Tuple[Vector, Vector, Vector]]]:
    mesh.calc_loop_triangles()
    records = []
    for triangle in mesh.loop_triangles:
        loop_indices = tuple(int(index) for index in triangle.loops)
        uvs = tuple(uv_layer.data[index].uv.copy() for index in loop_indices)
        face_index = int(triangle.polygon_index)
        records.append((
            min(point.x for point in uvs),
            max(point.x for point in uvs),
            min(point.y for point in uvs),
            max(point.y for point in uvs),
            face_index,
            face_to_island[face_index],
            uvs,
        ))
    return records


def _positive_triangle_overlap(
    left: Sequence[Vector],
    right: Sequence[Vector],
    epsilon: float,
) -> bool:
    for triangle in (left, right):
        for index in range(3):
            edge = triangle[(index + 1) % 3] - triangle[index]
            axis = Vector((-edge.y, edge.x))
            if axis.length_squared <= epsilon * epsilon:
                continue
            left_values = [point.dot(axis) for point in left]
            right_values = [point.dot(axis) for point in right]
            overlap = (
                min(max(left_values), max(right_values))
                - max(min(left_values), min(right_values))
            )
            if overlap <= epsilon * axis.length:
                return False
    return True


def _has_triangle_overlap(
    records: Sequence[Tuple[float, float, float, float, int, int, Sequence[Vector]]],
    epsilon: float,
) -> bool:
    active = []
    for record in sorted(records, key=lambda item: item[0]):
        minimum_x, maximum_x, minimum_y, maximum_y = record[:4]
        active = [item for item in active if item[1] > minimum_x + epsilon]
        for other in active:
            if min(maximum_y, other[3]) - max(minimum_y, other[2]) <= epsilon:
                continue
            if _positive_triangle_overlap(record[6], other[6], epsilon):
                return True
        active.append(record)
    return False


def audit_active_uv(
    obj: Any,
    face_to_island: Optional[Sequence[int]] = None,
    epsilon: float = 1.0e-7,
) -> Dict[str, Any]:
    """Audit finite bounds, triangle winding, and positive-area overlap."""

    mesh, uv_layer = _require_object_mode_mesh(obj)
    if face_to_island is None:
        _islands, face_to_island, _adjacency, _diagonal = compute_active_uv_islands(
            obj, GroupLayoutOptions(uv_epsilon=max(epsilon, 1.0e-9))
        )
    points = [item.uv for item in uv_layer.data]
    finite = bool(points) and all(
        math.isfinite(point.x) and math.isfinite(point.y) for point in points
    )
    inside_tile = finite and all(
        -epsilon <= value <= 1.0 + epsilon
        for point in points for value in (point.x, point.y)
    )
    triangle_records = (
        _triangle_records(mesh, uv_layer, face_to_island) if finite else []
    )
    positive = 0
    negative = 0
    degenerate = 0
    for record in triangle_records:
        points_uv = record[6]
        edge_a = points_uv[1] - points_uv[0]
        edge_b = points_uv[2] - points_uv[0]
        determinant = edge_a.x * edge_b.y - edge_a.y * edge_b.x
        local_scale = max(
            edge_a.length_squared,
            edge_b.length_squared,
            (points_uv[2] - points_uv[1]).length_squared,
            _EPSILON,
        )
        threshold = max(local_scale * 1.0e-12, 1.0e-24)
        if determinant > threshold:
            positive += 1
        elif determinant < -threshold:
            negative += 1
        else:
            degenerate += 1
    return {
        "triangles": len(triangle_records),
        "positive": positive,
        "negative": negative,
        "degenerate": degenerate,
        "finite": finite,
        "inside_tile": inside_tile,
        "overlap": finite and _has_triangle_overlap(triangle_records, epsilon),
    }


def _validate_source_audit(
    audit: Mapping[str, Any],
    settings: GroupLayoutOptions,
) -> None:
    if not audit["finite"]:
        raise RuntimeError("Source UV contains a non-finite coordinate")
    if settings.strict_positive_winding and (
        audit["negative"] or audit["degenerate"] or not audit["positive"]
    ):
        raise RuntimeError(
            "Source UV must have positive, non-degenerate winding before group layout"
        )
    if settings.strict_source_overlap and audit["overlap"]:
        raise RuntimeError("Source UV contains positive-area overlap")


def _validate_result_audit(audit: Mapping[str, Any]) -> None:
    if not audit["finite"]:
        raise RuntimeError("Group layout produced a non-finite coordinate")
    if not audit["inside_tile"]:
        raise RuntimeError("Group layout produced coordinates outside the 0-1 tile")
    if audit["negative"] or audit["degenerate"] or not audit["positive"]:
        raise RuntimeError(
            "Group layout changed positive UV winding: positive {}, negative {}, "
            "degenerate {}".format(
                audit["positive"], audit["negative"], audit["degenerate"]
            )
        )
    if audit["overlap"]:
        raise RuntimeError("Group layout produced positive-area overlap")


def _nonpositive_winding_islands(
    mesh: Any,
    uv_layer: Any,
    face_to_island: Sequence[int],
) -> List[int]:
    mesh.calc_loop_triangles()
    problem = set()
    for triangle in mesh.loop_triangles:
        loop_indices = tuple(int(index) for index in triangle.loops)
        points = tuple(uv_layer.data[index].uv.copy() for index in loop_indices)
        edge_a = points[1] - points[0]
        edge_b = points[2] - points[0]
        determinant = edge_a.x * edge_b.y - edge_a.y * edge_b.x
        local_scale = max(
            edge_a.length_squared,
            edge_b.length_squared,
            (points[2] - points[1]).length_squared,
            _EPSILON,
        )
        threshold = max(local_scale * 1.0e-12, 1.0e-24)
        if determinant <= threshold:
            face_index = int(triangle.polygon_index)
            problem.add(int(face_to_island[face_index]))
    return sorted(problem)


def _stabilize_quantized_winding(
    mesh: Any,
    uv_layer: Any,
    fitted: Dict[int, Dict[int, Vector]],
    face_to_island: Sequence[int],
) -> Dict[int, float]:
    """Resolve float32 winding flips with a bounded rigid micro-transform.

    Near-collinear source triangles can have positive double-precision area
    that changes sign only when the packed coordinates are stored by Blender
    as float32. Rotating or translating the complete island changes the
    quantization phase without changing shape, scale, or topology. Rotation
    stays below 0.001 radians and translation preserves direction exactly.
    The ordinary result audit still rejects any unresolved or newly
    overlapping layout.
    """

    problem_ids = _nonpositive_winding_islands(
        mesh, uv_layer, face_to_island
    )
    if not problem_ids:
        return {}
    magnitudes = (
        1.0e-7,
        2.0e-7,
        5.0e-7,
        1.0e-6,
        2.0e-6,
        5.0e-6,
        1.0e-5,
        2.0e-5,
        5.0e-5,
        1.0e-4,
        1.5e-4,
        2.0e-4,
        3.0e-4,
        5.0e-4,
        7.5e-4,
        1.0e-3,
    )
    resolved: Dict[int, float] = {}
    for island_id in problem_ids:
        base = {
            loop_index: point.copy()
            for loop_index, point in fitted[island_id].items()
        }
        center = sum(base.values(), Vector((0.0, 0.0))) / len(base)
        accepted = False
        for magnitude in magnitudes:
            for angle in (magnitude, -magnitude):
                candidate = {
                    loop_index: _rotate_point(point, center, angle)
                    for loop_index, point in base.items()
                }
                for loop_index, point in candidate.items():
                    uv_layer.data[loop_index].uv = point
                mesh.update()
                remaining = _nonpositive_winding_islands(
                    mesh, uv_layer, face_to_island
                )
                if island_id not in remaining:
                    fitted[island_id] = candidate
                    resolved[island_id] = angle
                    accepted = True
                    break
            if accepted:
                break
        if not accepted:
            translation_magnitudes = (
                1.0e-9,
                2.0e-9,
                5.0e-9,
                1.0e-8,
                2.0e-8,
                5.0e-8,
                1.0e-7,
                2.0e-7,
                5.0e-7,
                1.0e-6,
                2.0e-6,
                5.0e-6,
            )
            for magnitude in translation_magnitudes:
                offsets = (
                    Vector((magnitude, 0.0)),
                    Vector((-magnitude, 0.0)),
                    Vector((0.0, magnitude)),
                    Vector((0.0, -magnitude)),
                    Vector((magnitude, magnitude)),
                    Vector((magnitude, -magnitude)),
                    Vector((-magnitude, magnitude)),
                    Vector((-magnitude, -magnitude)),
                )
                for offset in offsets:
                    candidate = {
                        loop_index: point + offset
                        for loop_index, point in base.items()
                    }
                    for loop_index, point in candidate.items():
                        uv_layer.data[loop_index].uv = point
                    mesh.update()
                    remaining = _nonpositive_winding_islands(
                        mesh, uv_layer, face_to_island
                    )
                    if island_id not in remaining:
                        fitted[island_id] = candidate
                        resolved[island_id] = 0.0
                        accepted = True
                        break
                if accepted:
                    break
        if accepted:
            continue
        for loop_index, point in base.items():
            uv_layer.data[loop_index].uv = point
        mesh.update()
    return resolved


def _validate_island_similarity(
    before: Sequence[Vector],
    uv_layer: Any,
    islands: Sequence[IslandRecord],
    uniform_scale: float,
) -> None:
    for island in islands:
        if not island.loop_indices:
            continue
        anchor_index = island.loop_indices[0]
        before_anchor = before[anchor_index]
        after_anchor = uv_layer.data[anchor_index].uv
        reference = None
        reference_length = 0.0
        for loop_index in island.loop_indices[1:]:
            source = before[loop_index] - before_anchor
            if source.length_squared > reference_length:
                target = uv_layer.data[loop_index].uv - after_anchor
                reference = (source, target)
                reference_length = source.length_squared
        if reference is None:
            raise RuntimeError("UV island {} is degenerate".format(island.island_id))
        source, target = reference
        denominator = source.length_squared
        real = source.dot(target) / denominator
        imaginary = (source.x * target.y - source.y * target.x) / denominator
        measured_scale = math.sqrt(real * real + imaginary * imaginary)
        tolerance = max(uniform_scale, 1.0) * 5.0e-7
        # UV coordinates are stored as float32 by Blender.  For a very small
        # island, a sub-pixel absolute writeback error can exceed a fixed
        # relative-scale threshold even though every loop remains within the
        # rigid-transform error budget checked below.  Convert that same
        # absolute budget to a size-aware relative tolerance instead of
        # rejecting otherwise rigid tiny mechanical details.
        scale_tolerance = max(
            5.0e-5,
            tolerance / max(source.length, _EPSILON),
        )
        if _relative_error(measured_scale, uniform_scale) > scale_tolerance:
            raise RuntimeError(
                "UV island {} changed relative scale".format(island.island_id)
            )
        for loop_index in island.loop_indices:
            source_delta = before[loop_index] - before_anchor
            expected = Vector((
                source_delta.x * real - source_delta.y * imaginary,
                source_delta.x * imaginary + source_delta.y * real,
            ))
            actual = uv_layer.data[loop_index].uv - after_anchor
            if (expected - actual).length > tolerance:
                raise RuntimeError(
                    "UV island {} was not transformed rigidly".format(
                        island.island_id
                    )
                )


def _minimum_aabb_gap(
    coordinates: Mapping[int, Mapping[int, Vector]],
) -> float:
    bounds = {
        island_id: _uv_bounds(points.values())
        for island_id, points in coordinates.items()
    }
    ids = sorted(bounds)
    minimum = math.inf
    for left_position, left_id in enumerate(ids):
        left = bounds[left_id]
        for right_id in ids[left_position + 1:]:
            right = bounds[right_id]
            gap_x = max(left[0] - right[2], right[0] - left[2], 0.0)
            gap_y = max(left[1] - right[3], right[1] - left[3], 0.0)
            if gap_x <= _EPSILON and gap_y <= _EPSILON:
                return 0.0
            minimum = min(minimum, max(gap_x, gap_y))
    return 0.0 if not math.isfinite(minimum) else minimum


def layout_active_uv(
    obj: Any,
    options: Optional[GroupLayoutOptions] = None,
) -> GroupLayoutResult:
    """Analyze, semantically group, rigidly pack, audit, and commit active UVs.

    On any failure every UV coordinate is restored.  The function never edits
    topology, seam flags, materials, selection, pins, or UV layer identity.
    """

    settings = (options or GroupLayoutOptions()).validated()
    mesh, uv_layer = _require_object_mode_mesh(obj)
    snapshot = [item.uv.copy() for item in uv_layer.data]
    analysis = analyze_active_uv(obj, settings)
    before_audit = audit_active_uv(
        obj, analysis.face_to_island, epsilon=settings.uv_epsilon * 0.1
    )
    _validate_source_audit(before_audit, settings)

    angles = _orientation_angles(analysis, settings)
    oriented = _oriented_coordinates(uv_layer, analysis, angles)
    plan, fitted, uniform_scale = _plan_with_margin(
        oriented,
        analysis.layout_groups,
        settings,
        analysis.repeat_groups,
    )
    try:
        for island_coordinates in fitted.values():
            for loop_index, coordinate in island_coordinates.items():
                uv_layer.data[loop_index].uv = coordinate
        mesh.update()
        after_audit = audit_active_uv(
            obj, analysis.face_to_island, epsilon=settings.uv_epsilon * 0.1
        )
        quantization_rotations = {}
        if after_audit["negative"] or after_audit["degenerate"]:
            quantization_rotations = _stabilize_quantized_winding(
                mesh,
                uv_layer,
                fitted,
                analysis.face_to_island,
            )
            after_audit = audit_active_uv(
                obj,
                analysis.face_to_island,
                epsilon=settings.uv_epsilon * 0.1,
            )
        _validate_result_audit(after_audit)
        _validate_island_similarity(
            snapshot, uv_layer, analysis.islands, uniform_scale
        )
    except Exception:
        for item, coordinate in zip(uv_layer.data, snapshot):
            item.uv = coordinate
        mesh.update()
        raise

    fitted_gap = _minimum_aabb_gap(fitted)
    grouped_small = len({
        island_id
        for group in analysis.layout_groups
        for island_id in group.small_member_ids
    })
    rotated_islands = sum(
        abs(_angle_wrap(angle)) > 1.0e-7 for angle in angles.values()
    )
    return GroupLayoutResult(
        analysis=analysis,
        uniform_scale=uniform_scale,
        requested_margin=settings.margin,
        achieved_aabb_gap=fitted_gap,
        rotated_islands=rotated_islands,
        grouped_small_islands=grouped_small,
        before_audit=before_audit,
        after_audit=after_audit,
        group_quarter_turn=settings.allow_group_quarter_turn,
        quantization_adjusted_islands=len(quantization_rotations),
        max_quantization_rotation_degrees=max(
            (abs(math.degrees(value)) for value in quantization_rotations.values()),
            default=0.0,
        ),
    )


def layout_active_uv_adaptive(
    obj: Any,
    options: Optional[GroupLayoutOptions] = None,
) -> GroupLayoutResult:
    """Try both group-block rotation policies, committing only a valid result."""

    primary = (options or GroupLayoutOptions()).validated()
    attempts = (
        primary,
        replace(
            primary,
            allow_group_quarter_turn=not primary.allow_group_quarter_turn,
        ),
    )
    errors = []
    for index, settings in enumerate(attempts):
        try:
            result = layout_active_uv(obj, settings)
            result.fallback_used = index > 0
            result.fallback_errors = tuple(errors)
            return result
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError(
        "Adaptive UV group layout failed: " + " | ".join(errors)
    )


# A descriptive alias for integration code in the optimizer transaction.
post_layout_active_uv = layout_active_uv_adaptive


__all__ = [
    "GroupLayoutOptions",
    "GroupLayoutResult",
    "IslandAdjacency",
    "IslandRecord",
    "LayoutGroup",
    "RepeatCandidate",
    "RepeatGroup",
    "UVLayoutAnalysis",
    "analyze_active_uv",
    "audit_active_uv",
    "build_layout_groups",
    "calculate_island_records",
    "compute_active_uv_islands",
    "detect_repeat_groups",
    "layout_active_uv",
    "layout_active_uv_adaptive",
    "post_layout_active_uv",
]
