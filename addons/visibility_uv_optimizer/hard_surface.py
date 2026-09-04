# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometry helpers for readable hard-surface UV charts.

The 0.5.3 optimizer intentionally keeps its merge loop conservative and
generic.  This module provides an independent geometry layer that can be
used by a future operator (or by an external audit) without changing that
loop.  It has four deliberately small responsibilities:

* classify edges as locked constraints or preferred cut locations;
* grow material/normal-coherent region seeds from those constraints;
* generate a deterministic planar projection for one region; and
* rotate an existing UV island to a cardinal or directed geometry orientation.

All functions are BMesh-oriented and work in Blender 3.3 through 5.2.  No
operator, mode switch, seam write, or UV write is implicit.  Callers must
pass ``write=True`` to UV report helpers when they want to mutate loops.
Angles are radians, matching Blender's API and ``VUVSettings``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from mathutils import Vector


EPSILON = 1.0e-10
DEFAULT_PLANAR_ANGLE = math.radians(5.0)
DEFAULT_BEVEL_MIN_ANGLE = math.radians(25.0)
DEFAULT_BEVEL_MAX_ANGLE = math.radians(80.0)
DEFAULT_HARD_ANGLE = math.radians(80.0)
DEFAULT_REGION_ANGLE = math.radians(15.0)
DEFAULT_CARDINAL_ANGLES = (0.0, math.pi * 0.5)
DEFAULT_GEOMETRY_AXIS_PRIORITY = ("Z", "X", "Y")
# Blender stores mesh and UV coordinates as float32.  Below this relative
# tangent-plane signal, an axis direction is not stable after UV writeback.
GEOMETRY_AXIS_RELATIVE_EPSILON = 1.0e-4
# AUTO axis resolution uses per-triangle direction statistics in addition to
# the aggregate Jacobian.  These gates deliberately only affect multi-axis
# (AUTO) resolution; an explicit X/Y/Z request keeps the historical behavior.
DEFAULT_GEOMETRY_AXIS_MIN_COHERENCE = 0.70
DEFAULT_GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION = 0.50
# Per-face direction audits intentionally use stricter defaults than the
# alignment transform itself.  A chart can have a perfectly valid aggregate
# direction while individual triangles point in opposite directions; these
# thresholds expose that case without changing the existing alignment API.
DEFAULT_DIRECTION_AUDIT_UNSTABLE_ANGLE = math.radians(45.0)
DEFAULT_DIRECTION_AUDIT_MIN_CONCENTRATION = 0.85
DEFAULT_DIRECTION_AUDIT_MIN_EFFECTIVE_FRACTION = 0.50
# A neighbouring side face on a low-poly cylinder can turn more than the
# default region normal gate.  This is only used after the stricter geometric
# cylinder detector has identified a closed radial face component.
DEFAULT_CYLINDER_SIDE_STEP = math.radians(60.0)
DEFAULT_CYLINDER_SIDE_MIN_FACES = 6


def _safe_normalize(value: Vector, fallback: Optional[Vector] = None) -> Vector:
    """Return a copy with unit length, or a deterministic fallback."""

    result = value.copy()
    if result.length_squared > EPSILON:
        result.normalize()
        return result
    if fallback is not None:
        result = fallback.copy()
        if result.length_squared > EPSILON:
            result.normalize()
            return result
    return Vector((0.0, 0.0, 1.0))


def _angle_between(first: Vector, second: Vector) -> float:
    if first.length_squared <= EPSILON or second.length_squared <= EPSILON:
        return 0.0
    dot = max(-1.0, min(1.0, first.normalized().dot(second.normalized())))
    return math.acos(dot)


def _edge_angle(edge: Any) -> Optional[float]:
    """Read a two-face edge angle without raising on boundary/non-manifold edges."""

    if len(getattr(edge, "link_faces", ())) != 2:
        return None
    try:
        return float(edge.calc_face_angle(0.0))
    except (AttributeError, RuntimeError, ValueError):
        faces = list(edge.link_faces)
        return _angle_between(faces[0].normal, faces[1].normal)


def _face_area(face: Any) -> float:
    try:
        return max(float(face.calc_area()), 0.0)
    except (AttributeError, RuntimeError, ValueError):
        return 0.0


def _face_center(face: Any) -> Vector:
    try:
        return face.calc_center_median().copy()
    except (AttributeError, RuntimeError, ValueError):
        vertices = [loop.vert.co for loop in face.loops]
        if not vertices:
            return Vector((0.0, 0.0, 0.0))
        return sum(
            (vertex.copy() for vertex in vertices),
            Vector((0.0, 0.0, 0.0)),
        ) / len(vertices)


def _face_normal(face: Any) -> Vector:
    return _safe_normalize(getattr(face, "normal", Vector((0.0, 0.0, 1.0))))


def _material_index(face: Any) -> int:
    return int(getattr(face, "material_index", 0))


def _is_sharp(edge: Any) -> bool:
    """Handle both BMesh's ``smooth`` flag and Mesh edge sharp naming."""

    if hasattr(edge, "smooth"):
        return not bool(edge.smooth)
    if hasattr(edge, "use_edge_sharp"):
        return bool(edge.use_edge_sharp)
    return False


def _is_seam(edge: Any) -> bool:
    if hasattr(edge, "seam"):
        return bool(edge.seam)
    if hasattr(edge, "use_seam"):
        return bool(edge.use_seam)
    return False


def _is_convex(edge: Any) -> Optional[bool]:
    try:
        return bool(edge.is_convex)
    except (AttributeError, RuntimeError, ValueError):
        return None


def _ensure_index_tables(bm: Any) -> None:
    """Make index-based reports deterministic without assuming an edit mode."""

    for collection_name in ("verts", "edges", "faces"):
        collection = getattr(bm, collection_name, None)
        if collection is None:
            continue
        ensure = getattr(collection, "ensure_lookup_table", None)
        if ensure is not None:
            ensure()
        update = getattr(collection, "index_update", None)
        if update is not None:
            update()


def _vector_tuple(value: Vector) -> Tuple[float, float, float]:
    return (float(value.x), float(value.y), float(value.z))


def _uv_tuple(value: Vector) -> Tuple[float, float]:
    return (float(value.x), float(value.y))


def _bounds_2d(values: Iterable[Vector]) -> Tuple[float, float, float, float]:
    values = list(values)
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(value.x for value in values),
        min(value.y for value in values),
        max(value.x for value in values),
        max(value.y for value in values),
    )


def _weighted_average_normal(faces: Sequence[Any]) -> Vector:
    total = Vector((0.0, 0.0, 0.0))
    for face in faces:
        total += _face_normal(face) * max(_face_area(face), EPSILON)
    if total.length_squared <= EPSILON:
        total = sum((_face_normal(face) for face in faces), Vector())
    return _safe_normalize(total)


def _weighted_centroid(faces: Sequence[Any]) -> Vector:
    total = Vector((0.0, 0.0, 0.0))
    weight = 0.0
    for face in faces:
        area = max(_face_area(face), EPSILON)
        total += _face_center(face) * area
        weight += area
    return total / max(weight, EPSILON)


def _projected_tangent(
    faces: Sequence[Any],
    normal: Vector,
    preferred: Optional[Vector] = None,
) -> Vector:
    """Choose a stable in-plane axis from the longest projected mesh edge."""

    if preferred is not None:
        candidate = preferred - normal * preferred.dot(normal)
        if candidate.length_squared > EPSILON:
            return _safe_normalize(candidate)

    longest = None
    longest_length = 0.0
    seen_edges: Set[int] = set()
    for face in faces:
        for edge in getattr(face, "edges", ()):
            edge_index = getattr(edge, "index", id(edge))
            if edge_index in seen_edges:
                continue
            seen_edges.add(edge_index)
            vertices = list(getattr(edge, "verts", ()))
            if len(vertices) != 2:
                continue
            direction = vertices[1].co - vertices[0].co
            projected = direction - normal * direction.dot(normal)
            length = projected.length_squared
            if length > longest_length:
                longest = projected
                longest_length = length
    if longest is not None:
        candidate = _safe_normalize(longest)
    else:
        # Pick the world axis least parallel to the normal, then project it.
        axes = (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)))
        axis = min(axes, key=lambda value: abs(value.dot(normal)))
        candidate = _safe_normalize(axis - normal * axis.dot(normal))

    # Make the sign deterministic.  The dominant world component is positive.
    dominant = max(range(3), key=lambda index: abs(candidate[index]))
    if candidate[dominant] < 0.0:
        candidate.negate()
    return candidate


def _basis_from_normal(
    faces: Sequence[Any],
    normal: Optional[Vector] = None,
    tangent: Optional[Vector] = None,
) -> Tuple[Vector, Vector, Vector]:
    plane_normal = _safe_normalize(
        Vector(normal) if normal is not None else _weighted_average_normal(faces)
    )
    plane_tangent = _projected_tangent(
        faces,
        plane_normal,
        Vector(tangent) if tangent is not None else None,
    )
    plane_bitangent = _safe_normalize(plane_normal.cross(plane_tangent))
    # Re-orthogonalize in case the caller supplied a nearly parallel tangent.
    plane_tangent = _safe_normalize(plane_bitangent.cross(plane_normal), plane_tangent)
    return plane_tangent, plane_bitangent, plane_normal


def _coerce_faces(region: Any) -> List[Any]:
    if isinstance(region, RegionSeed):
        return list(region.faces)
    faces = getattr(region, "faces", None)
    if faces is not None and not isinstance(region, (list, tuple, set)):
        return list(faces)
    return list(region)


def _visibility_value(visibility: Any, face: Any) -> float:
    if visibility is None:
        return 1.0
    if callable(visibility):
        return float(visibility(face))
    index = int(getattr(face, "index", -1))
    if isinstance(visibility, Mapping):
        return float(visibility.get(index, 1.0))
    try:
        return float(visibility[index]) if index >= 0 else 1.0
    except (IndexError, TypeError, KeyError):
        return 1.0


@dataclass
class EdgeConstraintOptions:
    """Policy knobs for :func:`classify_edge_constraints`.

    Hard constraints prevent two faces from entering the same seed.  Bevel
    bands are reported as preferred cuts by default, allowing a caller to
    choose between a readable ribbon and a lower island count.
    """

    planar_angle: float = DEFAULT_PLANAR_ANGLE
    bevel_min_angle: float = DEFAULT_BEVEL_MIN_ANGLE
    bevel_max_angle: float = DEFAULT_BEVEL_MAX_ANGLE
    hard_angle: float = DEFAULT_HARD_ANGLE
    preserve_boundaries: bool = True
    preserve_nonmanifold: bool = True
    preserve_seams: bool = True
    preserve_materials: bool = True
    preserve_sharp: bool = True
    prefer_bevel_cuts: bool = True
    prefer_concave_cuts: bool = False


@dataclass
class EdgeConstraint:
    edge_index: int
    face_indices: Tuple[int, ...]
    angle: Optional[float]
    length: float
    hard_lock: bool
    preferred_cut: bool
    penalty: float
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def merge_allowed(self) -> bool:
        return not self.hard_lock

    @property
    def kind(self) -> str:
        if self.hard_lock:
            return self.reasons[0] if self.reasons else "locked"
        if self.preferred_cut:
            return self.reasons[0] if self.reasons else "preferred_cut"
        return "planar" if not self.reasons else self.reasons[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge": self.edge_index,
            "faces": list(self.face_indices),
            "angle": None if self.angle is None else round(self.angle, 6),
            "length": round(self.length, 6),
            "hard_lock": self.hard_lock,
            "preferred_cut": self.preferred_cut,
            "penalty": round(self.penalty, 6),
            "reasons": list(self.reasons),
        }


class EdgeConstraints(Mapping):
    """Mapping-like report returned by :func:`classify_edge_constraints`.

    The explicit policy dictionaries are intentionally kept alongside the
    richer :class:`EdgeConstraint` records.  They form the small integration
    contract used by the 0.5.4 hard-surface pass.
    """

    def __init__(
        self,
        by_edge: Mapping[int, EdgeConstraint],
        *,
        edge_policy: Optional[Mapping[int, str]] = None,
        edge_reasons: Optional[Mapping[int, Any]] = None,
        face_classes: Optional[Mapping[int, str]] = None,
        forced_cuts: Optional[Iterable[int]] = None,
        locked_cuts: Optional[Iterable[int]] = None,
        protected_cuts: Optional[Iterable[int]] = None,
        mode: str = "HARD_SURFACE",
        options: Optional[EdgeConstraintOptions] = None,
    ):
        self.by_edge = dict(by_edge)
        self.locked = frozenset(
            index for index, value in self.by_edge.items() if value.hard_lock
        )
        self.preferred = frozenset(
            index for index, value in self.by_edge.items() if value.preferred_cut
        )
        self.edge_policy = dict(edge_policy or {
            index: ("LOCKED_CUT" if value.hard_lock else
                    "PROTECTED_CUT" if value.preferred_cut else "NEUTRAL")
            for index, value in self.by_edge.items()
        })
        self.edge_reasons = dict(edge_reasons or {
            index: list(value.reasons) for index, value in self.by_edge.items()
        })
        self.face_classes = dict(face_classes or {})
        self.forced_cuts = set(() if forced_cuts is None else forced_cuts)
        self.locked_cuts = set(self.locked if locked_cuts is None else locked_cuts)
        self.protected_cuts = set(self.preferred if protected_cuts is None else protected_cuts)
        self.mode = str(mode or "HARD_SURFACE")
        self.options = options

    def __getitem__(self, key: int) -> EdgeConstraint:
        return self.by_edge[key]

    def __iter__(self):
        return iter(self.by_edge)

    def __len__(self) -> int:
        return len(self.by_edge)

    def items(self):
        return self.by_edge.items()

    def values(self):
        return self.by_edge.values()

    def get(self, key: int, default: Optional[EdgeConstraint] = None):
        return self.by_edge.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edges": [value.to_dict() for value in self.by_edge.values()],
            "locked_edges": sorted(self.locked),
            "preferred_cut_edges": sorted(self.preferred),
            "edge_policy": dict(self.edge_policy),
            "edge_reasons": dict(self.edge_reasons),
            "face_classes": dict(self.face_classes),
            "forced_cuts": sorted(self.forced_cuts),
            "locked_cuts": sorted(self.locked_cuts),
            "protected_cuts": sorted(self.protected_cuts),
            "mode": self.mode,
        }


def _setting(settings: Any, name: str, default: Any) -> Any:
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _explicit_setting(settings: Any, name: str, default: Any) -> Any:
    """Use an RNA value only when the user or a preset explicitly set it."""
    if settings is None or isinstance(settings, Mapping):
        return _setting(settings, name, default)
    checker = getattr(settings, "is_property_set", None)
    if checker is not None:
        try:
            if not checker(name):
                return default
        except (AttributeError, RuntimeError, TypeError):
            pass
    return getattr(settings, name, default)


def _options_from_settings(settings: Any, mode: str) -> EdgeConstraintOptions:
    if isinstance(settings, EdgeConstraintOptions):
        return settings
    hard_surface = str(mode or "HARD_SURFACE").upper() in {
        "HARD_SURFACE", "MARKED_SEAMS"
    }
    profile = str(_setting(settings, "hard_surface_profile", "DEFAULT")).upper()
    profile_hard_angle = {
        "WEAPON": math.radians(70.0),
        "GENERAL": math.radians(80.0),
        "DEFAULT": DEFAULT_HARD_ANGLE,
    }.get(profile, DEFAULT_HARD_ANGLE)
    profile_bevel_min = {
        "WEAPON": math.radians(20.0),
        "GENERAL": math.radians(25.0),
        "DEFAULT": DEFAULT_BEVEL_MIN_ANGLE,
    }.get(profile, DEFAULT_BEVEL_MIN_ANGLE)
    return EdgeConstraintOptions(
        planar_angle=float(_setting(
            settings,
            "hard_surface_planar_angle",
            _setting(settings, "planar_angle", DEFAULT_PLANAR_ANGLE),
        )),
        bevel_min_angle=float(_setting(settings, "bevel_min_angle", profile_bevel_min)),
        bevel_max_angle=float(_setting(settings, "bevel_max_angle", DEFAULT_BEVEL_MAX_ANGLE)),
        hard_angle=float(_explicit_setting(
            settings,
            "hard_surface_sharp_angle",
            _setting(settings, "hard_angle", profile_hard_angle),
        )),
        preserve_boundaries=bool(_setting(settings, "preserve_boundaries", True)),
        preserve_nonmanifold=bool(_setting(settings, "preserve_nonmanifold", True)),
        preserve_seams=bool(_setting(settings, "preserve_seams", True)),
        preserve_materials=bool(_setting(settings, "respect_materials", _setting(settings, "preserve_materials", True))),
        preserve_sharp=bool(_setting(
            settings,
            "hard_surface_respect_sharp",
            True,
        )) if hard_surface else bool(_setting(settings, "respect_sharp", False)),
        prefer_bevel_cuts=bool(_setting(settings, "prefer_bevel_cuts", hard_surface)),
        prefer_concave_cuts=bool(_setting(settings, "prefer_concave_cuts", False)),
    )


def _classify_face_classes(
    bm: Any,
    options: EdgeConstraintOptions,
    mode: str,
    cylinder_side_faces: Optional[Iterable[int]] = None,
) -> Dict[int, str]:
    """Assign conservative semantic labels used only for merge compatibility."""

    faces = list(bm.faces)
    if str(mode or "HARD_SURFACE").upper() not in {"HARD_SURFACE", "MARKED_SEAMS"}:
        return {int(face.index): "GENERAL" for face in faces}
    areas = sorted(_face_area(face) for face in faces)
    median_area = areas[len(areas) // 2] if areas else 0.0
    classes: Dict[int, str] = {}
    cylinder_side_faces = set(
        _detect_cylinder_side_faces(bm, options)
        if cylinder_side_faces is None else cylinder_side_faces
    )
    for face in faces:
        try:
            angles = [
                value for value in (_edge_angle(edge) for edge in face.edges)
                if value is not None
            ]
            bevel_edges = sum(
                options.bevel_min_angle <= value < options.bevel_max_angle
                for value in angles
            )
            vertex_count = len(face.verts)
            area = _face_area(face)
            if int(face.index) in cylinder_side_faces:
                # Keep the side wall distinct from planar panels and caps.  A
                # side component is allowed to flow around its radial ring;
                # cap/side boundaries remain hard cuts in the edge policy.
                label = "CYLINDER_SIDE"
            elif vertex_count >= 6 and any(
                any(
                    int(neighbor.index) in cylinder_side_faces
                    for neighbor in edge.link_faces
                    if neighbor != face
                )
                for edge in face.edges
            ):
                label = "RADIAL_CAP"
            elif bevel_edges >= 2 and area <= max(median_area * 1.5, EPSILON):
                label = "BEVEL"
            elif vertex_count >= 6 and bevel_edges >= 1:
                label = "CYLINDER"
            elif vertex_count <= 4 and max(angles, default=0.0) <= options.bevel_max_angle:
                label = "PANEL"
            else:
                label = "GENERAL"
            classes[int(face.index)] = label
        except Exception:
            classes[int(face.index)] = "GENERAL"
    return classes


def _detect_cylinder_side_components(
    bm: Any,
    options: EdgeConstraintOptions,
    *,
    min_faces: int = DEFAULT_CYLINDER_SIDE_MIN_FACES,
) -> List[Dict[str, Any]]:
    """Find closed radial rings and a deterministic opening edge for each.

    This deliberately conservative detector targets the topology produced by
    capped cylinders and low-poly tubular weapon parts.  A face must be a
    quad with at least two quad neighbours whose normals turn by a small,
    non-zero amount.  Components are accepted only when those normals share a
    common axis and cover most of a circle; planar grids and short bevel bands
    therefore remain on the generic path.

    The helper is read-only.  Each result contains the ring's face indices,
    its turning (axis-direction) edges, and the edge best aligned with a
    stable world-space radial reference.  Adjacent rings on a subdivided tube
    consequently choose a continuous longitudinal seam.
    """

    try:
        _ensure_index_tables(bm)
        faces = list(bm.faces)
        if not faces:
            return []
        max_step = min(
            math.radians(65.0),
            max(float(options.hard_angle) - math.radians(0.5), math.radians(5.0)),
        )
        candidate = {
            int(face.index)
            for face in faces
            if len(getattr(face, "verts", ())) == 4
        }
        graph: Dict[int, Set[int]] = {index: set() for index in candidate}
        flow_edges: Dict[int, Tuple[int, int]] = {}
        for edge in bm.edges:
            linked = [
                int(face.index) for face in getattr(edge, "link_faces", ())
                if int(face.index) in candidate
            ]
            if len(linked) != 2:
                continue
            angle = _edge_angle(edge)
            # Zero-angle axial neighbours connect cylinder levels but do not
            # identify the radial ring.  Keep only turning neighbours.
            if angle is None or angle <= math.radians(0.25) or angle > max_step:
                continue
            left, right = linked
            graph[left].add(right)
            graph[right].add(left)
            flow_edges[int(edge.index)] = (left, right)

        visited: Set[int] = set()
        detected: List[Dict[str, Any]] = []
        by_index = {int(face.index): face for face in faces}
        for start in sorted(candidate):
            if start in visited or len(graph.get(start, ())) < 2:
                continue
            pending = [start]
            visited.add(start)
            component: Set[int] = set()
            while pending:
                current = pending.pop()
                component.add(current)
                for neighbor in sorted(graph.get(current, ())):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        pending.append(neighbor)
            if len(component) < max(int(min_faces), 3):
                continue
            if any(len(graph.get(index, ())) < 2 for index in component):
                continue

            normals = [_face_normal(by_index[index]) for index in sorted(component)]
            cross_vectors: List[Vector] = []
            component_flow_edges = {
                edge_index
                for edge_index, (left, right) in flow_edges.items()
                if left in component and right in component
            }
            for edge_index in sorted(component_flow_edges):
                left, right = flow_edges[edge_index]
                if left not in component or right not in component:
                    continue
                cross = _face_normal(by_index[left]).cross(
                    _face_normal(by_index[right])
                )
                if cross.length_squared > EPSILON:
                    cross_vectors.append(cross.normalized())
            if len(cross_vectors) < 2:
                continue
            axis = cross_vectors[0].copy()
            aligned = Vector((0.0, 0.0, 0.0))
            for vector in cross_vectors:
                aligned += vector if vector.dot(axis) >= 0.0 else -vector
            if aligned.length_squared <= EPSILON:
                continue
            axis = aligned.normalized()
            dominant = max(range(3), key=lambda index: abs(axis[index]))
            if axis[dominant] < 0.0:
                axis.negate()
            # Every radial normal should lie close to the plane perpendicular
            # to the axis.  This rejects box corners and arbitrary bent strips.
            if max(abs(normal.dot(axis)) for normal in normals) > 0.35:
                continue
            tangent = normals[0] - axis * normals[0].dot(axis)
            if tangent.length_squared <= EPSILON:
                continue
            tangent.normalize()
            bitangent = axis.cross(tangent)
            if bitangent.length_squared <= EPSILON:
                continue
            bitangent.normalize()
            angles = sorted(
                math.atan2(
                    normal.dot(bitangent),
                    normal.dot(tangent),
                ) % (math.pi * 2.0)
                for normal in normals
            )
            if len(angles) < 3:
                continue
            gaps = [
                angles[index + 1] - angles[index]
                for index in range(len(angles) - 1)
            ]
            gaps.append(angles[0] + math.pi * 2.0 - angles[-1])
            coverage = math.pi * 2.0 - max(gaps)
            if coverage < math.radians(180.0):
                continue
            center = _weighted_centroid([by_index[index] for index in component])
            world_axes = (
                Vector((1.0, 0.0, 0.0)),
                Vector((0.0, 1.0, 0.0)),
                Vector((0.0, 0.0, 1.0)),
            )
            reference_axis = min(world_axes, key=lambda value: abs(value.dot(axis)))
            reference = reference_axis - axis * reference_axis.dot(axis)
            if reference.length_squared <= EPSILON:
                continue
            reference.normalize()

            edge_by_index = {int(edge.index): edge for edge in bm.edges}

            def opening_score(edge_index: int) -> Tuple[float, int]:
                edge = edge_by_index[edge_index]
                vertices = list(edge.verts)
                midpoint = (vertices[0].co + vertices[1].co) * 0.5
                radial = midpoint - center
                radial -= axis * radial.dot(axis)
                if radial.length_squared <= EPSILON:
                    return (-math.inf, -edge_index)
                radial.normalize()
                return (radial.dot(reference), -edge_index)

            opening_edge = max(component_flow_edges, key=opening_score)
            detected.append({
                "faces": frozenset(component),
                "flow_edges": frozenset(component_flow_edges),
                "opening_edge": int(opening_edge),
                "axis": _vector_tuple(axis),
            })
        return detected
    except Exception:
        # Classification is advisory; malformed/custom BMeshes use the
        # generic face labels rather than aborting UV optimization.
        return []


def _detect_cylinder_side_faces(
    bm: Any,
    options: EdgeConstraintOptions,
) -> Set[int]:
    return {
        face_index
        for component in _detect_cylinder_side_components(bm, options)
        for face_index in component["faces"]
    }


def can_merge_classes(first: Any, second: Any) -> bool:
    """Return whether two semantic face classes may share a region.

    ``GENERAL`` and ``NEUTRAL`` are deliberately permissive fallbacks: a
    classification failure must not make the generic optimizer unusable.
    Distinct hard-surface semantic families remain separated so a bevel ribbon
    is not silently absorbed into a broad panel.
    """

    left = str(first or "GENERAL").upper()
    right = str(second or "GENERAL").upper()
    if left in {"GENERAL", "NEUTRAL"} or right in {"GENERAL", "NEUTRAL"}:
        return True
    if left == right:
        return True
    if {left, right} <= {"BEVEL", "BAND"}:
        return True
    if {left, right} <= {"CYLINDER", "ROUND"}:
        return True
    return False


def classify_edge_constraints(
    bm: Any,
    settings: Any = None,
    mode: str = "HARD_SURFACE",
    original_seams: Optional[Iterable[int]] = None,
    preferred_cut_edges: Optional[Iterable[int]] = None,
    preferred_cut_penalty: float = 0.35,
    **overrides: Any,
) -> EdgeConstraints:
    """Return hard-surface edge policies without mutating the BMesh.

    Integration contract:

    ``edge_policy`` maps edge indices to ``FORCE_CUT``, ``LOCKED_CUT``,
    ``PROTECTED_CUT``, ``MERGE`` or ``NEUTRAL``; ``edge_reasons`` records why;
    ``face_classes`` maps faces to ``PANEL``, ``BEVEL``, ``CYLINDER`` or
    ``GENERAL``; and the three cut sets separate structural, artist-locked and
    soft protected boundaries.  Missing/invalid settings never abort a run:
    the affected item falls back to ``GENERAL``/``NEUTRAL``.
    """

    try:
        if isinstance(settings, EdgeConstraintOptions):
            options = settings
            if overrides:
                values = dict(options.__dict__)
                values.update(overrides)
                options = EdgeConstraintOptions(**values)
        else:
            values = dict(overrides)
            if values:
                # Only known dataclass fields are accepted; unknown panel
                # properties are deliberately ignored for forward safety.
                allowed = set(EdgeConstraintOptions.__dataclass_fields__)
                values = {key: value for key, value in values.items() if key in allowed}
            options = _options_from_settings(
                dict(settings, **values) if isinstance(settings, Mapping) else settings,
                mode,
            )
        _ensure_index_tables(bm)
        cylinder_components = _detect_cylinder_side_components(bm, options)
        cylinder_side_faces = {
            face_index
            for component in cylinder_components
            for face_index in component["faces"]
        }
        face_classes = _classify_face_classes(
            bm,
            options,
            mode,
            cylinder_side_faces=cylinder_side_faces,
        )
        seam_set = {
            int(getattr(value, "index", value))
            for value in (original_seams or ())
        }
        reference_set = {
            int(getattr(value, "index", value))
            for value in (preferred_cut_edges or ())
        }
        cylinder_opening_edges = {
            int(component["opening_edge"])
            for component in cylinder_components
            if not seam_set.intersection(component["flow_edges"])
        }
        result: Dict[int, EdgeConstraint] = {}
        edge_policy: Dict[int, str] = {}
        edge_reasons: Dict[int, List[str]] = {}
        forced_cuts: Set[int] = set()
        locked_cuts: Set[int] = set()
        protected_cuts: Set[int] = set()
        for edge in bm.edges:
            index = int(edge.index)
            faces = tuple(sorted(int(face.index) for face in edge.link_faces))
            reasons: List[str] = []
            hard = False
            forced = False
            locked = index in seam_set
            preferred = False
            penalty = 0.0
            angle = _edge_angle(edge)
            if len(faces) == 0:
                reasons.append("orphan")
                forced = options.preserve_nonmanifold
            elif len(faces) != 2:
                reasons.append("boundary" if len(faces) == 1 else "nonmanifold")
                forced = options.preserve_boundaries if len(faces) == 1 else options.preserve_nonmanifold
            if len(faces) == 2:
                left, right = edge.link_faces
                if options.preserve_materials and _material_index(left) != _material_index(right):
                    reasons.append("material")
                    forced = True
                class_left = face_classes.get(faces[0], "GENERAL")
                class_right = face_classes.get(faces[1], "GENERAL")
                if (
                    "CYLINDER_SIDE" in {class_left, class_right}
                    and class_left != class_right
                ):
                    reasons.append("cylinder_cap_boundary")
                    forced = True
                if not can_merge_classes(
                    class_left,
                    class_right,
                ):
                    reasons.append("class_boundary")
                    preferred = True
            if options.preserve_seams and _is_seam(edge):
                reasons.append("seam")
                locked = True
            if options.preserve_sharp and _is_sharp(edge):
                reasons.append("sharp")
                forced = True
            if index in reference_set:
                reasons.append("reference_uv")
                # Reference boundaries are deliberately soft.  They seed the
                # unwrap and bias later merge ordering, but can be stitched
                # back when strict Unique validation requires it.
                preferred = True
                penalty = max(penalty, float(preferred_cut_penalty))
            if index in cylinder_opening_edges:
                reasons.append("cylinder_opening")
                forced = True
            if angle is not None:
                if angle >= options.hard_angle:
                    reasons.append("hard_angle")
                    # A geometric corner is a strong seed cut, but unlike an
                    # artist seam, material boundary, or explicit Sharp flag,
                    # it may be merged into a readable net after distortion
                    # and overlap validation succeeds.
                    preferred = True
                    penalty = max(penalty, 0.75)
                elif options.bevel_min_angle <= angle < options.bevel_max_angle:
                    cylinder_flow = (
                        len(faces) == 2
                        and face_classes.get(faces[0], "GENERAL") == "CYLINDER_SIDE"
                        and face_classes.get(faces[1], "GENERAL") == "CYLINDER_SIDE"
                    )
                    if cylinder_flow:
                        reasons.append("cylinder_side_flow")
                    else:
                        reasons.append("bevel_band")
                        preferred = preferred or options.prefer_bevel_cuts
                        penalty = max(penalty, (angle - options.bevel_min_angle) / max(
                            options.bevel_max_angle - options.bevel_min_angle, EPSILON
                        ))
                elif options.prefer_concave_cuts and _is_convex(edge) is False:
                    reasons.append("concave")
                    preferred = True
                    penalty = max(penalty, 0.15)
            hard = forced or locked
            if hard:
                penalty = math.inf
            elif preferred:
                penalty = max(penalty, 0.1)
            if forced:
                forced_cuts.add(index)
            if locked:
                locked_cuts.add(index)
            if preferred and not hard:
                protected_cuts.add(index)
            if locked:
                policy = "LOCKED_CUT"
            elif forced:
                policy = "FORCE_CUT"
            elif preferred:
                policy = "PROTECTED_CUT"
            elif len(faces) == 2 and can_merge_classes(
                face_classes.get(faces[0], "GENERAL"),
                face_classes.get(faces[1], "GENERAL"),
            ):
                policy = "MERGE"
            else:
                policy = "NEUTRAL"
            edge_policy[index] = policy
            edge_reasons[index] = list(dict.fromkeys(reasons)) or ["neutral"]
            result[index] = EdgeConstraint(
                edge_index=index,
                face_indices=faces,
                angle=angle,
                length=max(float(edge.calc_length()), 0.0),
                hard_lock=hard,
                preferred_cut=preferred and not hard,
                penalty=penalty,
                reasons=tuple(edge_reasons[index]),
            )
        return EdgeConstraints(
            result,
            edge_policy=edge_policy,
            edge_reasons=edge_reasons,
            face_classes=face_classes,
            forced_cuts=forced_cuts,
            locked_cuts=locked_cuts,
            protected_cuts=protected_cuts,
            mode=mode,
            options=options,
        )
    except Exception:
        # A malformed/custom BMesh should remain usable by the generic pass.
        faces = list(getattr(bm, "faces", ()))
        edges = list(getattr(bm, "edges", ()))
        fallback = {
            int(getattr(edge, "index", index)): EdgeConstraint(
                edge_index=int(getattr(edge, "index", index)),
                face_indices=tuple(),
                angle=None,
                length=0.0,
                hard_lock=False,
                preferred_cut=False,
                penalty=0.0,
                reasons=("general",),
            )
            for index, edge in enumerate(edges)
        }
        return EdgeConstraints(
            fallback,
            edge_policy={index: "NEUTRAL" for index in fallback},
            edge_reasons={index: ["general"] for index in fallback},
            face_classes={int(getattr(face, "index", index)): "GENERAL" for index, face in enumerate(faces)},
            mode=mode,
            options=locals().get('options'),
        )


@dataclass
class RegionSeedOptions:
    """Controls normal/material coherence when growing region seeds."""

    max_normal_angle: float = DEFAULT_REGION_ANGLE
    planar_spread_angle: float = DEFAULT_PLANAR_ANGLE
    split_preferred_cuts: bool = True
    respect_materials: bool = True
    minimum_faces: int = 1
    band_aspect_ratio: float = 6.0
    cylinder_side_step_angle: float = DEFAULT_CYLINDER_SIDE_STEP


@dataclass
class RegionSeed:
    seed_face_index: int
    face_indices: Tuple[int, ...]
    faces: Tuple[Any, ...]
    kind: str
    area: float
    centroid: Vector
    normal: Vector
    normal_spread: float
    priority: float
    boundary_edges: Tuple[int, ...]
    preferred_cut_edges: Tuple[int, ...]
    material_indices: Tuple[int, ...]

    @property
    def face_count(self) -> int:
        return len(self.face_indices)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed_face": self.seed_face_index,
            "faces": list(self.face_indices),
            "kind": self.kind,
            "area": round(self.area, 6),
            "centroid": [round(value, 6) for value in self.centroid],
            "normal": [round(value, 6) for value in self.normal],
            "normal_spread": round(self.normal_spread, 6),
            "priority": round(self.priority, 6),
            "boundary_edges": list(self.boundary_edges),
            "preferred_cut_edges": list(self.preferred_cut_edges),
            "materials": list(self.material_indices),
        }


def _region_aspect(faces: Sequence[Any], normal: Vector) -> float:
    tangent, bitangent, _ = _basis_from_normal(faces, normal)
    points = [loop.vert.co for face in faces for loop in face.loops]
    if not points:
        return 1.0
    projected_x = [point.dot(tangent) for point in points]
    projected_y = [point.dot(bitangent) for point in points]
    width = max(projected_x) - min(projected_x)
    height = max(projected_y) - min(projected_y)
    return max(width, height) / max(min(width, height), EPSILON)


def _region_normal_spread(faces: Sequence[Any], normal: Vector) -> float:
    total = 0.0
    weight = 0.0
    for face in faces:
        area = max(_face_area(face), EPSILON)
        total += _angle_between(_face_normal(face), normal) * area
        weight += area
    return total / max(weight, EPSILON)


def _build_region_seed_records(
    bm: Any,
    constraints: Optional[EdgeConstraints] = None,
    options: Optional[RegionSeedOptions] = None,
    visibility: Any = None,
) -> List[RegionSeed]:
    """Grow deterministic planar/band seeds across admissible BMesh edges.

    The function only reads BMesh state.  A preferred bevel edge is treated as
    a barrier when ``split_preferred_cuts`` is true; a caller can disable that
    for a lower-island exploratory pass.
    """

    _ensure_index_tables(bm)
    if constraints is None:
        constraints = classify_edge_constraints(bm)
    if not isinstance(constraints, EdgeConstraints):
        constraints = EdgeConstraints(constraints)
    if options is None:
        constraint_options = getattr(constraints, 'options', None)
        options = RegionSeedOptions(
            planar_spread_angle=float(constraint_options.planar_angle)
        ) if constraint_options is not None else RegionSeedOptions()
    if not isinstance(options, RegionSeedOptions):
        raise TypeError("options must be RegionSeedOptions or None")

    faces = sorted(list(bm.faces), key=lambda face: int(face.index))
    by_index = {int(face.index): face for face in faces}
    visited: Set[int] = set()
    seeds: List[RegionSeed] = []

    for start in faces:
        start_index = int(start.index)
        if start_index in visited:
            continue
        pending = [start]
        visited.add(start_index)
        region: List[Any] = []
        while pending:
            face = pending.pop()
            region.append(face)
            for edge in sorted(face.edges, key=lambda value: int(value.index)):
                constraint = constraints.get(int(edge.index))
                if constraint is not None and constraint.hard_lock:
                    continue
                if constraint is not None and constraint.preferred_cut and options.split_preferred_cuts:
                    continue
                for neighbor in sorted(edge.link_faces, key=lambda value: int(value.index)):
                    neighbor_index = int(neighbor.index)
                    if neighbor_index == int(face.index) or neighbor_index in visited:
                        continue
                    if options.respect_materials and _material_index(face) != _material_index(neighbor):
                        continue
                    class_left = constraints.face_classes.get(
                        int(face.index), "GENERAL"
                    )
                    class_right = constraints.face_classes.get(
                        neighbor_index, "GENERAL"
                    )
                    normal_limit = options.max_normal_angle
                    if class_left == class_right == "CYLINDER_SIDE":
                        normal_limit = max(
                            normal_limit,
                            options.cylinder_side_step_angle,
                        )
                    if _angle_between(
                        _face_normal(face), _face_normal(neighbor)
                    ) > normal_limit:
                        continue
                    visited.add(neighbor_index)
                    pending.append(neighbor)

        region.sort(key=lambda value: int(value.index))
        if len(region) < max(int(options.minimum_faces), 1):
            # Keep tiny regions rather than silently dropping geometry; callers
            # can identify them by ``face_count`` and decide how to merge them.
            pass
        normal = _weighted_average_normal(region)
        area = sum(_face_area(face) for face in region)
        centroid = _weighted_centroid(region)
        spread = _region_normal_spread(region, normal)
        aspect = _region_aspect(region, normal)
        if spread <= options.planar_spread_angle:
            kind = "panel"
        elif aspect >= options.band_aspect_ratio:
            kind = "band"
        else:
            kind = "mixed"

        region_indices = {int(face.index) for face in region}
        boundary_edges: Set[int] = set()
        preferred_edges: Set[int] = set()
        materials = {_material_index(face) for face in region}
        for face in region:
            for edge in face.edges:
                linked = [neighbor for neighbor in edge.link_faces if int(neighbor.index) in region_indices]
                if len(linked) != len(edge.link_faces) or len(edge.link_faces) != 2:
                    boundary_edges.add(int(edge.index))
                constraint = constraints.get(int(edge.index))
                if constraint is not None and constraint.preferred_cut:
                    preferred_edges.add(int(edge.index))

        priority = area * max(
            sum(_visibility_value(visibility, face) * max(_face_area(face), EPSILON) for face in region)
            / max(area, EPSILON),
            0.0,
        )
        seed = max(region, key=lambda face: (_face_area(face), -int(face.index)))
        seeds.append(RegionSeed(
            seed_face_index=int(seed.index),
            face_indices=tuple(int(face.index) for face in region),
            faces=tuple(region),
            kind=kind,
            area=area,
            centroid=centroid,
            normal=normal,
            normal_spread=spread,
            priority=priority,
            boundary_edges=tuple(sorted(boundary_edges)),
            preferred_cut_edges=tuple(sorted(preferred_edges)),
            material_indices=tuple(sorted(materials)),
        ))
    seeds.sort(key=lambda seed: (-seed.priority, seed.seed_face_index))
    return seeds


def build_region_seed_records(
    bm: Any,
    constraints: Optional[EdgeConstraints] = None,
    options: Optional[RegionSeedOptions] = None,
    visibility: Any = None,
) -> List[RegionSeed]:
    """Return rich seed records for diagnostics and future UI panels."""

    return _build_region_seed_records(bm, constraints, options, visibility)


def build_region_seeds(
    bm: Any,
    constraints: EdgeConstraints,
) -> List[List[Any]]:
    """Return region face lists for the 0.5.4 integration contract.

    The public contract intentionally returns raw ``BMFace`` lists so the
    caller can feed them straight into Blender UV operators.  Rich metadata is
    available through :func:`build_region_seed_records`.
    """

    try:
        records = _build_region_seed_records(bm, constraints)
        return [list(record.faces) for record in records]
    except Exception:
        # A generic connected-face fallback is safer than dropping geometry.
        try:
            _ensure_index_tables(bm)
            return [[face] for face in sorted(bm.faces, key=lambda value: int(value.index))]
        except Exception:
            return []


@dataclass
class ProjectionResult:
    face_indices: Tuple[int, ...]
    loop_uvs: Dict[Tuple[int, int], Vector]
    origin: Vector
    tangent: Vector
    bitangent: Vector
    normal: Vector
    scale: float
    planarity_rms: float
    planarity_max: float
    projected_area: float
    bounds: Tuple[float, float, float, float]
    written: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "faces": list(self.face_indices),
            "origin": [round(value, 6) for value in self.origin],
            "tangent": [round(value, 6) for value in self.tangent],
            "bitangent": [round(value, 6) for value in self.bitangent],
            "normal": [round(value, 6) for value in self.normal],
            "scale": round(self.scale, 6),
            "planarity_rms": round(self.planarity_rms, 6),
            "planarity_max": round(self.planarity_max, 6),
            "projected_area": round(self.projected_area, 6),
            "bounds": [round(value, 6) for value in self.bounds],
            "loop_count": len(self.loop_uvs),
            "written": self.written,
        }


def _write_uv_mapping(
    faces: Sequence[Any],
    uv_layer: Any,
    coordinates: Mapping[Tuple[int, int], Vector],
) -> int:
    if uv_layer is None:
        raise ValueError("uv_layer is required when write=True")
    count = 0
    for face in faces:
        face_index = int(face.index)
        for loop in face.loops:
            key = (face_index, int(loop.vert.index))
            value = coordinates.get(key)
            if value is None:
                continue
            loop[uv_layer].uv = value
            count += 1
    return count


def project_planar_region_report(
    region: Any,
    uv_layer: Any = None,
    *,
    normal: Optional[Vector] = None,
    tangent: Optional[Vector] = None,
    origin: Optional[Vector] = None,
    scale: float = 1.0,
    offset: Optional[Vector] = None,
    write: bool = False,
) -> ProjectionResult:
    """Project region vertices onto a deterministic tangent/bitangent plane.

    The returned mapping is keyed by ``(face.index, vertex.index)`` rather
    than only vertex index, so discontinuous UV corners remain representable.
    ``scale`` is intentionally explicit: callers can match texel density or
    pack later without this helper guessing a global texture size.
    """

    faces = _coerce_faces(region)
    if not faces:
        raise ValueError("region must contain at least one face")
    if not math.isfinite(float(scale)) or scale <= 0.0:
        raise ValueError("scale must be finite and greater than zero")
    plane_tangent, plane_bitangent, plane_normal = _basis_from_normal(
        faces, normal, tangent
    )
    plane_origin = origin.copy() if origin is not None else _weighted_centroid(faces)
    plane_offset = offset.copy() if offset is not None else Vector((0.0, 0.0))

    coordinates: Dict[Tuple[int, int], Vector] = {}
    distances = []
    for face in faces:
        for loop in face.loops:
            point = loop.vert.co
            relative = point - plane_origin
            distances.append(abs(relative.dot(plane_normal)))
            key = (int(face.index), int(loop.vert.index))
            coordinates[key] = Vector((
                relative.dot(plane_tangent) * scale + plane_offset.x,
                relative.dot(plane_bitangent) * scale + plane_offset.y,
            ))

    projected_area = 0.0
    for face in faces:
        points = [coordinates[(int(face.index), int(loop.vert.index))] for loop in face.loops]
        if len(points) >= 3:
            projected_area += abs(sum(
                points[index].x * points[(index + 1) % len(points)].y
                - points[(index + 1) % len(points)].x * points[index].y
                for index in range(len(points))
            ) * 0.5)
    written = False
    if write:
        _write_uv_mapping(faces, uv_layer, coordinates)
        written = True
    return ProjectionResult(
        face_indices=tuple(sorted(int(face.index) for face in faces)),
        loop_uvs=coordinates,
        origin=plane_origin,
        tangent=plane_tangent,
        bitangent=plane_bitangent,
        normal=plane_normal,
        scale=float(scale),
        planarity_rms=math.sqrt(sum(value * value for value in distances) / max(len(distances), 1)),
        planarity_max=max(distances, default=0.0),
        projected_area=projected_area,
        bounds=_bounds_2d(coordinates.values()),
        written=written,
    )


def _axis_hint_normal(axis_hint: Any) -> Optional[Vector]:
    if axis_hint is None:
        return None
    if isinstance(axis_hint, str):
        lookup = {
            "X": Vector((1.0, 0.0, 0.0)),
            "Y": Vector((0.0, 1.0, 0.0)),
            "Z": Vector((0.0, 0.0, 1.0)),
            "-X": Vector((-1.0, 0.0, 0.0)),
            "-Y": Vector((0.0, -1.0, 0.0)),
            "-Z": Vector((0.0, 0.0, -1.0)),
        }
        return lookup.get(axis_hint.upper())
    try:
        value = Vector(axis_hint)
    except (TypeError, ValueError):
        return None
    return value if value.length_squared > EPSILON else None


def project_planar_region(
    faces: Any,
    uv_layer: Any,
    axis_hint: Any = None,
) -> bool:
    """Write a planar projection and return whether it succeeded.

    This is the compact integration API.  ``axis_hint`` may be a world-axis
    string (``"X"``, ``"Y"``, ``"Z"``) or a normal-like 3-vector.  Detailed
    metrics are available from :func:`project_planar_region_report`.
    """

    try:
        face_list = _coerce_faces(faces)
        if not face_list or uv_layer is None:
            return False
        report = project_planar_region_report(
            face_list,
            uv_layer,
            normal=_axis_hint_normal(axis_hint),
            write=True,
        )
        return bool(report.written and report.loop_uvs)
    except Exception:
        return False


@dataclass
class AlignmentResult:
    face_indices: Tuple[int, ...]
    centroid: Vector
    angle_before: float
    angle_delta: float
    angle_after: float
    anisotropy: float
    confidence: float
    bounds_before: Tuple[float, float, float, float]
    bounds_after: Tuple[float, float, float, float]
    loop_uvs: Dict[Tuple[int, int], Vector]
    written: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "faces": list(self.face_indices),
            "centroid": [round(value, 6) for value in self.centroid],
            "angle_before": round(self.angle_before, 6),
            "angle_delta": round(self.angle_delta, 6),
            "angle_after": round(self.angle_after, 6),
            "anisotropy": round(self.anisotropy, 6),
            "confidence": round(self.confidence, 6),
            "bounds_before": [round(value, 6) for value in self.bounds_before],
            "bounds_after": [round(value, 6) for value in self.bounds_after],
            "loop_count": len(self.loop_uvs),
            "written": self.written,
        }


@dataclass
class GeometryAlignmentResult:
    """Diagnostics for directed, geometry-derived UV island alignment.

    ``loop_uvs`` uses ``(face.index, vertex.index)`` when both indices are
    valid.  Dirty BMesh elements use deterministic negative ordinal keys.
    """

    face_indices: Tuple[int, ...]
    centroid: Vector
    axis_priority: Tuple[str, ...]
    selected_axis: Optional[str]
    angle_delta: float
    derivative_u: Vector
    derivative_v: Vector
    axis_strength: float
    bounds_before: Tuple[float, float, float, float]
    bounds_after: Tuple[float, float, float, float]
    loop_uvs: Dict[Tuple[int, int], Vector]
    axis_strengths: Dict[str, float] = field(default_factory=dict)
    triangle_count: int = 0
    skipped_triangle_count: int = 0
    written: bool = False
    degenerate: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "faces": list(self.face_indices),
            "centroid": [round(value, 6) for value in self.centroid],
            "axis_priority": list(self.axis_priority),
            "selected_axis": self.selected_axis,
            "angle_delta": round(self.angle_delta, 6),
            "derivative_u": [round(value, 6) for value in self.derivative_u],
            "derivative_v": [round(value, 6) for value in self.derivative_v],
            "axis_strength": round(self.axis_strength, 6),
            "axis_strengths": {
                axis: round(strength, 6)
                for axis, strength in self.axis_strengths.items()
            },
            "bounds_before": [round(value, 6) for value in self.bounds_before],
            "bounds_after": [round(value, 6) for value in self.bounds_after],
            "loop_count": len(self.loop_uvs),
            "triangle_count": self.triangle_count,
            "skipped_triangle_count": self.skipped_triangle_count,
            "written": self.written,
            "degenerate": self.degenerate,
        }


@dataclass
class FaceDirectionAuditResult:
    """Read-only per-triangle audit of a directed geometry contract.

    ``selected_axis`` follows the same ordered, signed axis contract as
    :func:`align_island_geometry_report`.  The aggregate projection used to
    select that axis is deliberately kept separate from the per-triangle
    angle distribution.  This distinction catches a folded chart whose
    opposite triangles cancel in neither the old island-level average nor its
    final modulo-360 residual.

    Angles and residuals are stored in radians.  ``records`` is a tuple of
    plain dictionaries so callers can inspect the individual fan triangles
    without retaining BMesh references or mutating the source mesh.
    """

    face_indices: Tuple[int, ...]
    axis_priority: Tuple[str, ...]
    selected_axis: Optional[str]
    axis_projection: Dict[str, Dict[str, float]]
    triangle_count: int
    valid_triangle_count: int
    skipped_triangle_count: int
    low_signal_triangle_count: int
    negative_triangle_count: int
    unstable_triangle_count: int
    angle_mean: Optional[float]
    angle_p50: Optional[float]
    angle_p95: Optional[float]
    angle_max_residual: Optional[float]
    circular_concentration: float
    effective_triangle_fraction: float
    concentration_stable: bool
    resolved: bool
    min_projection: float
    unstable_angle: float
    min_concentration: float
    min_effective_fraction: float
    records: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @staticmethod
    def _degrees(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(math.degrees(float(value)), 6)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe diagnostic mapping."""

        projection = {}
        for axis, values in self.axis_projection.items():
            projection[str(axis)] = {
                str(key): round(float(value), 9)
                for key, value in values.items()
            }
        records = []
        for record in self.records:
            item = {}
            for key, value in record.items():
                if isinstance(value, Vector):
                    item[str(key)] = [
                        round(float(component), 9) for component in value
                    ]
                elif isinstance(value, float):
                    item[str(key)] = round(float(value), 9)
                else:
                    item[str(key)] = value
            if "angle" in item and item["angle"] is not None:
                item["angle_degrees"] = round(
                    math.degrees(float(record["angle"])), 6
                )
            if "residual" in item and item["residual"] is not None:
                item["residual_degrees"] = round(
                    math.degrees(float(record["residual"])), 6
                )
            records.append(item)
        return {
            "faces": list(self.face_indices),
            "axis_priority": list(self.axis_priority),
            "selected_axis": self.selected_axis,
            "axis_projection": projection,
            "triangle_count": int(self.triangle_count),
            "valid_triangle_count": int(self.valid_triangle_count),
            "skipped_triangle_count": int(self.skipped_triangle_count),
            "low_signal_triangle_count": int(self.low_signal_triangle_count),
            "negative_triangle_count": int(self.negative_triangle_count),
            "unstable_triangle_count": int(self.unstable_triangle_count),
            "angle_mean_degrees": self._degrees(self.angle_mean),
            "angle_p50_degrees": self._degrees(self.angle_p50),
            "angle_p95_degrees": self._degrees(self.angle_p95),
            "angle_max_residual_degrees": self._degrees(
                self.angle_max_residual
            ),
            "circular_concentration": round(
                float(self.circular_concentration), 6
            ),
            "effective_triangle_fraction": round(
                float(self.effective_triangle_fraction), 6
            ),
            "concentration_stable": bool(self.concentration_stable),
            "resolved": bool(self.resolved),
            "min_projection": round(float(self.min_projection), 9),
            "unstable_angle_degrees": round(
                math.degrees(float(self.unstable_angle)), 6
            ),
            "min_concentration": round(float(self.min_concentration), 6),
            "min_effective_fraction": round(
                float(self.min_effective_fraction), 6
            ),
            "records": records,
        }


def _wrap_line_angle(angle: float) -> float:
    """Wrap an unoriented line angle to [-pi/2, pi/2)."""

    while angle >= math.pi * 0.5:
        angle -= math.pi
    while angle < -math.pi * 0.5:
        angle += math.pi
    return angle


def _cardinal_delta(angle: float, allowed_angles: Sequence[float]) -> float:
    candidates = []
    for target in allowed_angles:
        for offset in (0.0, math.pi, -math.pi):
            candidates.append(_wrap_line_angle(target + offset - angle))
    return min(candidates, key=abs) if candidates else _wrap_line_angle(-angle)


def align_island_cardinal_report(
    faces: Any,
    uv_layer: Any,
    *,
    allowed_angles: Sequence[float] = DEFAULT_CARDINAL_ANGLES,
    min_anisotropy: float = 0.05,
    write: bool = False,
) -> AlignmentResult:
    """Rotate an existing UV island toward its nearest cardinal direction.

    The orientation comes from the 2D covariance of UV loop positions.  Near
    circular islands are left untouched (low confidence), which avoids noisy
    rotations of round caps and tiny detail cards.
    """

    face_list = _coerce_faces(faces)
    if not face_list:
        raise ValueError("faces must contain at least one face")
    if uv_layer is None:
        raise ValueError("uv_layer is required")
    points: List[Vector] = []
    keys: List[Tuple[int, int]] = []
    for face in face_list:
        for loop in face.loops:
            points.append(loop[uv_layer].uv.copy())
            keys.append((int(face.index), int(loop.vert.index)))
    if not points:
        raise ValueError("faces contain no loops")
    centroid = sum(points, Vector((0.0, 0.0))) / len(points)
    xx = sum((point.x - centroid.x) ** 2 for point in points) / len(points)
    yy = sum((point.y - centroid.y) ** 2 for point in points) / len(points)
    xy = sum((point.x - centroid.x) * (point.y - centroid.y) for point in points) / len(points)
    principal = 0.5 * math.atan2(2.0 * xy, xx - yy)
    trace = xx + yy
    discriminant = math.sqrt(max((xx - yy) ** 2 + 4.0 * xy * xy, 0.0))
    major = max((trace + discriminant) * 0.5, 0.0)
    minor = max((trace - discriminant) * 0.5, 0.0)
    anisotropy = (major - minor) / max(major, EPSILON)
    confidence = max(0.0, min(1.0, anisotropy))
    before = {key: point.copy() for key, point in zip(keys, points)}
    bounds_before = _bounds_2d(points)
    if anisotropy < max(float(min_anisotropy), 0.0):
        delta = 0.0
    else:
        delta = _cardinal_delta(principal, allowed_angles)
    cosine = math.cos(delta)
    sine = math.sin(delta)
    transformed: Dict[Tuple[int, int], Vector] = {}
    for key, point in before.items():
        relative = point - centroid
        transformed[key] = Vector((
            centroid.x + relative.x * cosine - relative.y * sine,
            centroid.y + relative.x * sine + relative.y * cosine,
        ))
    after_points = list(transformed.values())
    angle_after = _wrap_line_angle(principal + delta)
    written = False
    if write:
        _write_uv_mapping(face_list, uv_layer, transformed)
        written = True
    return AlignmentResult(
        face_indices=tuple(sorted(int(face.index) for face in face_list)),
        centroid=centroid,
        angle_before=_wrap_line_angle(principal),
        angle_delta=delta,
        angle_after=angle_after,
        anisotropy=anisotropy,
        confidence=confidence,
        bounds_before=bounds_before,
        bounds_after=_bounds_2d(after_points),
        loop_uvs=transformed,
        written=written,
    )


def align_island_cardinal(
    faces: Any,
    uv_layer: Any,
) -> bool:
    """Rotate an existing island in-place and return whether it succeeded."""

    try:
        face_list = _coerce_faces(faces)
        if not face_list or uv_layer is None:
            return False
        report = align_island_cardinal_report(face_list, uv_layer, write=True)
        return bool(report.written and report.loop_uvs)
    except Exception:
        return False


def _geometry_axis_priority(axis_priority: Sequence[str]) -> Tuple[str, ...]:
    if axis_priority is None:
        return DEFAULT_GEOMETRY_AXIS_PRIORITY
    if isinstance(axis_priority, str):
        # Configuration properties commonly store a compact order such as
        # ``"YZX"``.  Treat it as three axis tokens while retaining support
        # for a single explicit axis string.
        compact = axis_priority.replace(",", "").replace(" ", "")
        values = tuple(compact) if len(compact) > 1 else (compact,)
    else:
        values = tuple(axis_priority)
    normalized: List[str] = []
    for value in values:
        axis = str(value).upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("axis_priority may only contain X, Y, and Z")
        if axis not in normalized:
            normalized.append(axis)
    if not normalized:
        raise ValueError("axis_priority must contain at least one axis")
    return tuple(normalized)


def _geometry_uv_derivatives(
    faces: Sequence[Any],
    uv_layer: Any,
    geometry_matrix: Any = None,
) -> Tuple[Vector, Vector, int, int]:
    """Return Blender-style area-weighted dP/du and dP/dv accumulators.

    ``geometry_matrix`` transforms edge differences, not points.  Passing an
    object's world 3x3 matrix therefore supports the WORLD direction contract
    without introducing translation into the UV Jacobian.
    """

    derivative_u = Vector((0.0, 0.0, 0.0))
    derivative_v = Vector((0.0, 0.0, 0.0))
    triangle_count = 0
    skipped_triangle_count = 0
    for face in faces:
        loops = list(face.loops)
        for fan in range(2, len(loops)):
            root = loops[0]
            first = loops[fan - 1]
            second = loops[fan]
            delta_uv0 = first[uv_layer].uv - root[uv_layer].uv
            delta_uv1 = second[uv_layer].uv - root[uv_layer].uv
            determinant = (
                float(delta_uv0.x) * float(delta_uv1.y)
                - float(delta_uv0.y) * float(delta_uv1.x)
            )
            uv_product = math.sqrt(
                max(float(delta_uv0.length_squared), 0.0)
                * max(float(delta_uv1.length_squared), 0.0)
            )
            delta_co0 = first.vert.co - root.vert.co
            delta_co1 = second.vert.co - root.vert.co
            if geometry_matrix is not None:
                delta_co0 = geometry_matrix @ delta_co0
                delta_co1 = geometry_matrix @ delta_co1
            area_weight = float(delta_co0.cross(delta_co1).length)
            geometry_product = math.sqrt(
                max(float(delta_co0.length_squared), 0.0)
                * max(float(delta_co1.length_squared), 0.0)
            )
            if (
                not math.isfinite(determinant)
                or not math.isfinite(uv_product)
                or uv_product <= 0.0
                or abs(determinant) <= EPSILON * uv_product
                or not math.isfinite(area_weight)
                or not math.isfinite(geometry_product)
                or geometry_product <= 0.0
                or area_weight <= EPSILON * geometry_product
            ):
                skipped_triangle_count += 1
                continue

            inverse_00 = float(delta_uv1.y) / determinant
            inverse_01 = -float(delta_uv0.y) / determinant
            inverse_10 = -float(delta_uv1.x) / determinant
            inverse_11 = float(delta_uv0.x) / determinant
            triangle_u = delta_co0 * inverse_00 + delta_co1 * inverse_01
            triangle_v = delta_co0 * inverse_10 + delta_co1 * inverse_11
            weighted_u = triangle_u * area_weight
            weighted_v = triangle_v * area_weight
            if not all(
                math.isfinite(float(component))
                for component in tuple(weighted_u) + tuple(weighted_v)
            ):
                skipped_triangle_count += 1
                continue
            derivative_u += weighted_u
            derivative_v += weighted_v
            triangle_count += 1
    return derivative_u, derivative_v, triangle_count, skipped_triangle_count


def _wrap_directed_angle(angle: float) -> float:
    """Wrap a signed angle to ``[-pi, pi)`` without relying on bpy helpers."""

    while angle >= math.pi:
        angle -= math.pi * 2.0
    while angle < -math.pi:
        angle += math.pi * 2.0
    return angle


def _finite_quantile(values: Sequence[float], fraction: float) -> Optional[float]:
    """Return a deterministic nearest-rank quantile for finite values."""

    finite = sorted(
        float(value)
        for value in values
        if math.isfinite(float(value))
    )
    if not finite:
        return None
    fraction = max(0.0, min(1.0, float(fraction)))
    index = int(round((len(finite) - 1) * fraction))
    return finite[max(0, min(len(finite) - 1, index))]


def _geometry_uv_triangle_records(
    faces: Sequence[Any],
    uv_layer: Any,
    geometry_matrix: Any = None,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Collect one immutable-ish Jacobian record per triangle fan.

    The returned records contain no BMesh objects.  ``triangle_count`` counts
    all fan triangles encountered, while ``skipped_count`` counts triangles
    rejected for a non-finite, zero-area, or near-singular UV/geometry basis.
    Keeping this collector separate from the existing aggregate derivative
    function lets the alignment transform remain byte-for-byte compatible
    while consumers opt into a more detailed audit.
    """

    records: List[Dict[str, Any]] = []
    triangle_count = 0
    skipped_count = 0
    for face_position, face in enumerate(faces):
        loops = list(face.loops)
        for fan in range(2, len(loops)):
            triangle_count += 1
            root = loops[0]
            first = loops[fan - 1]
            second = loops[fan]
            delta_uv0 = first[uv_layer].uv - root[uv_layer].uv
            delta_uv1 = second[uv_layer].uv - root[uv_layer].uv
            determinant = (
                float(delta_uv0.x) * float(delta_uv1.y)
                - float(delta_uv0.y) * float(delta_uv1.x)
            )
            uv_product = math.sqrt(
                max(float(delta_uv0.length_squared), 0.0)
                * max(float(delta_uv1.length_squared), 0.0)
            )
            delta_co0 = first.vert.co - root.vert.co
            delta_co1 = second.vert.co - root.vert.co
            if geometry_matrix is not None:
                delta_co0 = geometry_matrix @ delta_co0
                delta_co1 = geometry_matrix @ delta_co1
            area_weight = float(delta_co0.cross(delta_co1).length)
            geometry_product = math.sqrt(
                max(float(delta_co0.length_squared), 0.0)
                * max(float(delta_co1.length_squared), 0.0)
            )
            if (
                not math.isfinite(determinant)
                or not math.isfinite(uv_product)
                or uv_product <= 0.0
                or abs(determinant) <= EPSILON * uv_product
                or not math.isfinite(area_weight)
                or not math.isfinite(geometry_product)
                or geometry_product <= 0.0
                or area_weight <= EPSILON * geometry_product
            ):
                skipped_count += 1
                continue

            inverse_00 = float(delta_uv1.y) / determinant
            inverse_01 = -float(delta_uv0.y) / determinant
            inverse_10 = -float(delta_uv1.x) / determinant
            inverse_11 = float(delta_uv0.x) / determinant
            derivative_u = (
                delta_co0 * inverse_00 + delta_co1 * inverse_01
            )
            derivative_v = (
                delta_co0 * inverse_10 + delta_co1 * inverse_11
            )
            values = tuple(derivative_u) + tuple(derivative_v)
            if not all(math.isfinite(float(value)) for value in values):
                skipped_count += 1
                continue
            basis = math.hypot(
                float(derivative_u.length), float(derivative_v.length)
            )
            if not math.isfinite(basis) or basis <= EPSILON:
                skipped_count += 1
                continue
            records.append({
                "face_index": int(getattr(face, "index", face_position)),
                "fan_index": int(fan),
                "area_weight": area_weight,
                "derivative_u": derivative_u.copy(),
                "derivative_v": derivative_v.copy(),
                "basis": basis,
                "determinant": determinant,
            })
    return records, triangle_count, skipped_count


def _geometry_axis_candidate_statistics(
    triangle_records: Sequence[Mapping[str, Any]],
    axis_vectors: Mapping[str, Vector],
    aggregate_basis: float,
    min_projection: float,
) -> Dict[str, Dict[str, float]]:
    """Compute aggregate and per-triangle stability for each model axis.

    The aggregate Jacobian is useful for finding a tangent signal, but it can
    hide a bimodal chart when opposite triangles cancel.  This companion
    statistic keeps the same signed projection while measuring circular
    concentration and the fraction of triangles with a usable projection.
    Values are plain floats so callers can persist them in diagnostics.
    """

    statistics: Dict[str, Dict[str, float]] = {}
    total_area = sum(
        max(float(record.get("area_weight", 0.0)), 0.0)
        for record in triangle_records
    )
    total_count = len(triangle_records)
    for axis, vector in axis_vectors.items():
        sum_u = 0.0
        sum_v = 0.0
        signal_sum = 0.0
        stable_count = 0
        circular_sin = 0.0
        circular_cos = 0.0
        circular_weight = 0.0
        for record in triangle_records:
            derivative_u = record["derivative_u"]
            derivative_v = record["derivative_v"]
            component_u = float(derivative_u.dot(vector))
            component_v = float(derivative_v.dot(vector))
            signal = math.hypot(component_u, component_v)
            basis = max(float(record.get("basis", 0.0)), EPSILON)
            local_signal = max(0.0, min(1.0, signal / basis))
            weight = max(float(record.get("area_weight", 0.0)), 0.0)
            sum_u += weight * component_u
            sum_v += weight * component_v
            signal_sum += weight * local_signal
            if local_signal < min_projection:
                continue
            stable_count += 1
            angle = math.atan2(component_u, component_v)
            contribution = weight * max(local_signal, min_projection)
            circular_weight += contribution
            circular_sin += contribution * math.sin(angle)
            circular_cos += contribution * math.cos(angle)
        strength = math.hypot(sum_u, sum_v)
        normalized_strength = strength / max(float(aggregate_basis), EPSILON)
        if circular_weight > EPSILON:
            concentration = min(
                1.0,
                max(0.0, math.hypot(circular_sin, circular_cos) / circular_weight),
            )
        else:
            concentration = 0.0
        effective_fraction = stable_count / max(total_count, 1)
        mean_local_signal = signal_sum / max(total_area, EPSILON)
        statistics[axis] = {
            "sum_u": sum_u,
            "sum_v": sum_v,
            "strength": strength,
            "normalized_strength": normalized_strength,
            "mean_local_signal": mean_local_signal,
            "concentration": concentration,
            "effective_fraction": effective_fraction,
            "stability_score": concentration * effective_fraction,
        }
    return statistics


def _select_geometry_axis(
    priority: Sequence[str],
    statistics: Mapping[str, Mapping[str, float]],
    strength_threshold: float,
    *,
    min_coherence: float = DEFAULT_GEOMETRY_AXIS_MIN_COHERENCE,
    min_effective_fraction: float = DEFAULT_GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION,
) -> Optional[str]:
    """Resolve a directed axis, using coherence only for AUTO priorities.

    A single-axis priority is an explicit user choice and therefore follows
    the legacy aggregate-strength rule.  For AUTO (two or more candidates),
    a candidate must have a usable tangent signal; the first coherent axis
    wins, otherwise the candidate with the strongest stable signal wins.  The
    ordered priority remains the final tie-breaker, making the result fully
    deterministic.
    """

    candidates = [
        axis for axis in priority
        if axis in statistics
        and math.isfinite(float(statistics[axis].get("strength", 0.0)))
        and float(statistics[axis].get("strength", 0.0)) > strength_threshold
    ]
    if not candidates:
        return None
    if len(tuple(priority)) == 1:
        return candidates[0]

    first = candidates[0]
    first_stats = statistics[first]
    first_coherent = (
        float(first_stats.get("concentration", 0.0)) + EPSILON >= min_coherence
        and float(first_stats.get("effective_fraction", 0.0)) + EPSILON
        >= min_effective_fraction
    )
    if first_coherent:
        return first

    coherent = [
        axis for axis in candidates
        if float(statistics[axis].get("concentration", 0.0)) + EPSILON
        >= min_coherence
        and float(statistics[axis].get("effective_fraction", 0.0)) + EPSILON
        >= min_effective_fraction
    ]
    pool = coherent or candidates
    priority_index = {axis: index for index, axis in enumerate(priority)}
    return max(
        pool,
        key=lambda axis: (
            float(statistics[axis].get("stability_score", 0.0))
            * float(statistics[axis].get("normalized_strength", 0.0)),
            float(statistics[axis].get("normalized_strength", 0.0)),
            -priority_index.get(axis, len(priority)),
        ),
    )


def audit_island_geometry_direction(
    faces: Any,
    uv_layer: Any,
    *,
    axis_priority: Sequence[str] = DEFAULT_GEOMETRY_AXIS_PRIORITY,
    geometry_matrix: Any = None,
    min_projection: float = GEOMETRY_AXIS_RELATIVE_EPSILON,
    unstable_angle: float = DEFAULT_DIRECTION_AUDIT_UNSTABLE_ANGLE,
    min_concentration: float = DEFAULT_DIRECTION_AUDIT_MIN_CONCENTRATION,
    min_effective_fraction: float = DEFAULT_DIRECTION_AUDIT_MIN_EFFECTIVE_FRACTION,
) -> FaceDirectionAuditResult:
    """Audit signed direction coherence for every triangle in an island.

    ``min_projection`` is relative to each triangle's Jacobian basis, so a
    uniformly scaled chart produces the same result.  Axis resolution still
    follows the explicit priority order; only the first axis whose aggregate
    tangent signal clears the threshold is selected.  Per-triangle angles are
    then weighted by 3D area and local axis signal.  A negative triangle has a
    projection opposite the circular mean (dot product < 0); an unstable
    triangle exceeds ``unstable_angle`` from that mean.  Low-signal triangles
    are reported separately and do not contaminate the circular statistics.

    The function is strictly read-only: it copies UV/geometry derivatives and
    never writes to loops, seams, mesh attributes, or selection state.
    """

    face_list = _coerce_faces(faces)
    if not face_list:
        raise ValueError("faces must contain at least one face")
    if uv_layer is None:
        raise ValueError("uv_layer is required")
    priority = _geometry_axis_priority(axis_priority)
    min_projection = float(min_projection)
    unstable_angle = float(unstable_angle)
    min_concentration = float(min_concentration)
    min_effective_fraction = float(min_effective_fraction)
    if (
        not math.isfinite(min_projection)
        or not 0.0 <= min_projection <= 1.0
    ):
        raise ValueError("min_projection must be finite and in [0, 1]")
    if (
        not math.isfinite(unstable_angle)
        or not 0.0 <= unstable_angle <= math.pi
    ):
        raise ValueError("unstable_angle must be finite and in [0, pi]")
    if (
        not math.isfinite(min_concentration)
        or not 0.0 <= min_concentration <= 1.0
    ):
        raise ValueError("min_concentration must be finite and in [0, 1]")
    if (
        not math.isfinite(min_effective_fraction)
        or not 0.0 <= min_effective_fraction <= 1.0
    ):
        raise ValueError(
            "min_effective_fraction must be finite and in [0, 1]"
        )

    triangle_records, triangle_count, skipped_count = (
        _geometry_uv_triangle_records(
            face_list,
            uv_layer,
            geometry_matrix=geometry_matrix,
        )
    )
    axis_vectors = {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }
    aggregate_basis = sum(
        float(record["area_weight"]) * float(record["basis"])
        for record in triangle_records
    )
    axis_projection = _geometry_axis_candidate_statistics(
        triangle_records,
        axis_vectors,
        aggregate_basis,
        min_projection,
    )
    # Keep the audit's historical resolver (first aggregate signal) so stored
    # diagnostics remain comparable across plugin versions.  The mutating
    # alignment path below uses the coherence-aware AUTO resolver.
    selected_axis = next(
        (
            axis for axis in priority
            if math.isfinite(float(axis_projection[axis]["normalized_strength"]))
            and float(axis_projection[axis]["normalized_strength"]) >= min_projection
        ),
        None,
    )

    low_signal_count = 0
    negative_count = 0
    unstable_count = 0
    selected_records: List[Dict[str, Any]] = []
    if selected_axis is not None:
        axis_vector = axis_vectors[selected_axis]
        for record in triangle_records:
            derivative_u = record["derivative_u"]
            derivative_v = record["derivative_v"]
            component_u = float(derivative_u.dot(axis_vector))
            component_v = float(derivative_v.dot(axis_vector))
            signal = math.hypot(component_u, component_v)
            local_signal = signal / max(float(record["basis"]), EPSILON)
            local_signal = max(0.0, min(1.0, local_signal))
            record["axis_projection_u"] = component_u
            record["axis_projection_v"] = component_v
            record["axis_signal"] = local_signal
            if local_signal < min_projection:
                record["low_signal"] = True
                low_signal_count += 1
                continue
            angle = math.atan2(component_u, component_v)
            record["angle"] = angle
            # The circular pass below adds the residual and classifications.
            selected_records.append(record)

    sum_sin = 0.0
    sum_cos = 0.0
    for record in selected_records:
        weight = float(record["area_weight"]) * max(
            float(record["axis_signal"]), min_projection
        )
        record["circular_weight"] = weight
        sum_sin += weight * math.sin(float(record["angle"]))
        sum_cos += weight * math.cos(float(record["angle"]))
    circular_weight = sum(
        float(record.get("circular_weight", 0.0))
        for record in selected_records
    )
    if circular_weight > EPSILON:
        angle_mean = math.atan2(sum_sin, sum_cos)
        concentration = min(
            1.0,
            max(0.0, math.hypot(sum_sin, sum_cos) / circular_weight),
        )
    else:
        angle_mean = None
        concentration = 0.0

    residuals: List[float] = []
    if angle_mean is not None:
        for record in selected_records:
            residual = abs(_wrap_directed_angle(
                float(record["angle"]) - angle_mean
            ))
            negative = math.cos(
                float(record["angle"]) - angle_mean
            ) < 0.0
            unstable = residual > unstable_angle
            record["residual"] = residual
            record["negative"] = bool(negative)
            record["unstable"] = bool(unstable)
            residuals.append(residual)
            negative_count += int(negative)
            unstable_count += int(unstable)

    valid_count = len(triangle_records)
    effective_fraction = len(selected_records) / max(valid_count, 1)
    concentration_stable = bool(
        selected_records and concentration + EPSILON >= min_concentration
    )
    resolved = bool(
        selected_axis is not None
        and selected_records
        and effective_fraction + EPSILON >= min_effective_fraction
        and concentration_stable
    )
    return FaceDirectionAuditResult(
        face_indices=tuple(sorted(
            int(getattr(face, "index", index))
            for index, face in enumerate(face_list)
        )),
        axis_priority=priority,
        selected_axis=selected_axis,
        axis_projection=axis_projection,
        triangle_count=triangle_count,
        valid_triangle_count=valid_count,
        skipped_triangle_count=skipped_count,
        low_signal_triangle_count=low_signal_count,
        negative_triangle_count=negative_count,
        unstable_triangle_count=unstable_count,
        angle_mean=angle_mean,
        angle_p50=_finite_quantile(residuals, 0.50),
        angle_p95=_finite_quantile(residuals, 0.95),
        angle_max_residual=max(residuals, default=None),
        circular_concentration=concentration,
        effective_triangle_fraction=effective_fraction,
        concentration_stable=concentration_stable,
        resolved=resolved,
        min_projection=min_projection,
        unstable_angle=unstable_angle,
        min_concentration=min_concentration,
        min_effective_fraction=min_effective_fraction,
        records=tuple(dict(record) for record in triangle_records),
    )


def align_island_geometry_report(
    faces: Any,
    uv_layer: Any,
    *,
    axis_priority: Sequence[str] = DEFAULT_GEOMETRY_AXIS_PRIORITY,
    geometry_matrix: Any = None,
    write: bool = False,
) -> GeometryAlignmentResult:
    """Align a model-space positive axis to UV +V without a 180-degree ambiguity.

    This follows Blender's Geometry alignment method: each polygon is split
    into a triangle fan, its UV Jacobian is inverted, and the resulting
    ``dP/du`` and ``dP/dv`` vectors are accumulated by 3D triangle area.
    Explicit axes keep priority semantics.  AUTO also rejects a preferred axis
    whose per-triangle directions are incoherent, then chooses the strongest
    stable fallback axis.
    """

    face_list = _coerce_faces(faces)
    if not face_list:
        raise ValueError("faces must contain at least one face")
    if uv_layer is None:
        raise ValueError("uv_layer is required")
    priority = _geometry_axis_priority(axis_priority)
    before: Dict[Tuple[int, int], Vector] = {}
    loop_keys: List[Tuple[Any, Tuple[int, int]]] = []
    used_keys: Set[Tuple[int, int]] = set()
    for face_position, face in enumerate(face_list):
        face_index = int(face.index)
        for loop_position, loop in enumerate(face.loops):
            uv = loop[uv_layer].uv.copy()
            if not all(math.isfinite(float(component)) for component in uv):
                raise ValueError("faces contain non-finite UV coordinates")
            key = (face_index, int(loop.vert.index))
            if key[0] < 0 or key[1] < 0 or key in used_keys:
                key = (-(face_position + 1), -(loop_position + 1))
            used_keys.add(key)
            before[key] = uv
            loop_keys.append((loop, key))
    if not before:
        raise ValueError("faces contain no loops")

    bounds_before = _bounds_2d(before.values())
    centroid = Vector((
        (bounds_before[0] + bounds_before[2]) * 0.5,
        (bounds_before[1] + bounds_before[3]) * 0.5,
    ))
    triangle_records, triangle_count, skipped_count = (
        _geometry_uv_triangle_records(
            face_list,
            uv_layer,
            geometry_matrix=geometry_matrix,
        )
    )
    derivative_u = sum(
        (record["derivative_u"] * float(record["area_weight"])
         for record in triangle_records),
        Vector((0.0, 0.0, 0.0)),
    )
    derivative_v = sum(
        (record["derivative_v"] * float(record["area_weight"])
         for record in triangle_records),
        Vector((0.0, 0.0, 0.0)),
    )
    axis_vectors = {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }
    derivative_scale = math.hypot(
        float(derivative_u.length),
        float(derivative_v.length),
    )
    # BMesh coordinates and mathutils vectors are float32.  A merely non-zero
    # projection can therefore be round-off from an axis that is actually
    # normal to the island; use the module's length-scale tolerance here.
    strength_threshold = GEOMETRY_AXIS_RELATIVE_EPSILON * derivative_scale
    aggregate_basis = sum(
        float(record["area_weight"]) * float(record["basis"])
        for record in triangle_records
    )
    axis_projection = _geometry_axis_candidate_statistics(
        triangle_records,
        axis_vectors,
        aggregate_basis,
        GEOMETRY_AXIS_RELATIVE_EPSILON,
    )
    axis_strengths = {
        axis: float(values["strength"])
        for axis, values in axis_projection.items()
        if axis in priority
    }
    selected_axis = _select_geometry_axis(
        priority,
        axis_projection,
        strength_threshold,
    )
    selected_u = 0.0
    selected_v = 0.0
    if selected_axis is not None:
        selected_u = float(axis_projection[selected_axis]["sum_u"])
        selected_v = float(axis_projection[selected_axis]["sum_v"])

    degenerate = triangle_count == 0 or selected_axis is None
    if degenerate:
        return GeometryAlignmentResult(
            face_indices=tuple(sorted(int(face.index) for face in face_list)),
            centroid=centroid,
            axis_priority=priority,
            selected_axis=None,
            angle_delta=0.0,
            derivative_u=derivative_u,
            derivative_v=derivative_v,
            axis_strength=0.0,
            bounds_before=bounds_before,
            bounds_after=bounds_before,
            loop_uvs={key: value.copy() for key, value in before.items()},
            axis_strengths=axis_strengths,
            triangle_count=triangle_count,
            skipped_triangle_count=skipped_count,
            written=False,
            degenerate=True,
        )

    delta = math.atan2(selected_u, selected_v)
    cosine = math.cos(delta)
    sine = math.sin(delta)
    transformed: Dict[Tuple[int, int], Vector] = {}
    for key, point in before.items():
        relative = point - centroid
        transformed[key] = Vector((
            centroid.x + relative.x * cosine - relative.y * sine,
            centroid.y + relative.x * sine + relative.y * cosine,
        ))
    written = False
    if write:
        for loop, key in loop_keys:
            loop[uv_layer].uv = transformed[key]
        written = bool(loop_keys)
    return GeometryAlignmentResult(
        face_indices=tuple(sorted(int(face.index) for face in face_list)),
        centroid=centroid,
        axis_priority=priority,
        selected_axis=selected_axis,
        angle_delta=delta,
        derivative_u=derivative_u,
        derivative_v=derivative_v,
        axis_strength=axis_strengths[selected_axis],
        bounds_before=bounds_before,
        bounds_after=_bounds_2d(transformed.values()),
        loop_uvs=transformed,
        axis_strengths=axis_strengths,
        triangle_count=triangle_count,
        skipped_triangle_count=skipped_count,
        written=written,
        degenerate=False,
    )


def align_island_geometry(
    faces: Any,
    uv_layer: Any,
    *,
    axis_priority: Sequence[str] = DEFAULT_GEOMETRY_AXIS_PRIORITY,
    geometry_matrix: Any = None,
) -> bool:
    """Write directed Geometry alignment and return whether it succeeded."""

    try:
        face_list = _coerce_faces(faces)
        if not face_list or uv_layer is None:
            return False
        report = align_island_geometry_report(
            face_list,
            uv_layer,
            axis_priority=axis_priority,
            geometry_matrix=geometry_matrix,
            write=True,
        )
        return bool(report.written and report.loop_uvs and not report.degenerate)
    except Exception:
        return False


# Explicit aliases for callers that prefer the longer name in reports.
align_uv_island_cardinal = align_island_cardinal
align_uv_island_cardinal_report = align_island_cardinal_report
align_uv_island_geometry = align_island_geometry
align_uv_island_geometry_report = align_island_geometry_report


__all__ = [
    "AlignmentResult",
    "DEFAULT_GEOMETRY_AXIS_PRIORITY",
    "DEFAULT_GEOMETRY_AXIS_MIN_COHERENCE",
    "DEFAULT_GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION",
    "DEFAULT_DIRECTION_AUDIT_MIN_CONCENTRATION",
    "DEFAULT_DIRECTION_AUDIT_MIN_EFFECTIVE_FRACTION",
    "DEFAULT_DIRECTION_AUDIT_UNSTABLE_ANGLE",
    "EdgeConstraint",
    "EdgeConstraintOptions",
    "EdgeConstraints",
    "FaceDirectionAuditResult",
    "GeometryAlignmentResult",
    "GEOMETRY_AXIS_RELATIVE_EPSILON",
    "ProjectionResult",
    "RegionSeed",
    "RegionSeedOptions",
    "align_island_cardinal",
    "align_island_cardinal_report",
    "align_island_geometry",
    "align_island_geometry_report",
    "align_uv_island_cardinal",
    "align_uv_island_cardinal_report",
    "align_uv_island_geometry",
    "align_uv_island_geometry_report",
    "audit_island_geometry_direction",
    "build_region_seed_records",
    "build_region_seeds",
    "can_merge_classes",
    "classify_edge_constraints",
    "project_planar_region",
    "project_planar_region_report",
]
