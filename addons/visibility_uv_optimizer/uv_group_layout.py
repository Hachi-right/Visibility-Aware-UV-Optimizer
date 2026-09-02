# SPDX-License-Identifier: GPL-2.0-or-later
"""Safe semantic post-layout for an existing unique UV map.

This module does not unwrap, stitch, split, mirror, or edit seams.  It treats
each UV island as a rigid 2D shape, detects conservative repeated mechanical
parts, keeps those parts together with a common direction, and places tiny
detached charts near a model-space neighbor.  The final pack uses disjoint
axis-aligned rectangles and one positive global scale.  An optional bounded
uniform boost can improve the readability of tiny mechanical charts; all
other islands retain their relative texel density and every island retains its
winding.

The public entry points intentionally work in Object Mode.  This keeps the
module independent from UV editor selection state and makes rollback reliable
in Blender 3.3 as well as newer versions.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
import hashlib
import itertools
import json
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from mathutils import Vector


_EPSILON = 1.0e-12
# Soft topology links are deliberately weaker than structure-group links.
# They are only used to keep two already-separated layout blocks nearby; they
# never authorize UV welding, seam removal, or a topology edit.
_CONTINUITY_SOFT_MIN_CONTACT_RATIO = 0.04
_CONTINUITY_SOFT_MAX_NORMAL_ANGLE = math.radians(150.0)
_GEOMETRY_JACOBIAN_RELATIVE_EPSILON = 1.0e-10
_GEOMETRY_AXIS_RELATIVE_EPSILON = 1.0e-4
# Keep AUTO selection in lockstep with hard_surface.py.  These gates apply
# only when several semantic axes are candidates; explicit and replay-fixed
# axes retain the aggregate projection contract.
_GEOMETRY_AXIS_MIN_COHERENCE = 0.70
_GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION = 0.50
# Group-axis scores are derived from float32 geometry/UV data.  Treat smaller
# differences as a tie so symmetric repeat cohorts cannot switch X/Z merely
# because packing changed the final rounding phase.
_GEOMETRY_AXIS_GROUP_TIE_EPSILON = 1.0e-4
# Detached hard-surface charts that share an (unsigned) model normal are
# treated as one presentation domain.  Keep this angular bucket deliberately
# narrow: it catches coplanar/repeated panels without merging adjacent faces
# on a curved shell into one axis contract.
_GEOMETRY_AXIS_CONSENSUS_NORMAL_ANGLE = math.radians(12.0)
# A common axis is useful only when it covers a meaningful part of a normal
# domain.  The selected axis may still be inherited by a smaller member when
# its own Jacobian is numerically valid; that member is reported as a bounded
# consensus downgrade rather than silently making a second cohort.
_GEOMETRY_AXIS_CONSENSUS_MIN_COVERAGE = 0.60
# A repeated/owner cohort may share one writeback angle only when all of its
# members already agree within this small signed window.  Larger differences
# usually mean a real quarter-turn, a mirrored tangent, or a different model
# axis; forcing those cases would violate the strict +V contract.
_GEOMETRY_GROUP_ANGLE_TOLERANCE = math.radians(3.0)
# A long-edge cohort is a visual-heading aid, not a new geometry contract.
# Keep its accepted members on one cardinal line and never bridge a genuine
# diagonal/quarter-turn split.  The strict +V residual gate below is tighter
# (normally 3 degrees); this wider bound is retained as an auditable safety
# check for callers that inspect the cohort metrics directly.
_GEOMETRY_LONG_EDGE_MAX_SPREAD = math.radians(45.0)
# A complete signed tangent frame is only enforced when both tangent axes are
# numerically coherent.  Curved/round charts can have no single canonical U
# direction; those charts remain on the existing +V contract and are reported
# as frame-downgraded instead of being rotated unpredictably.
_GEOMETRY_FRAME_MIN_CONCENTRATION = 0.60
_GEOMETRY_FRAME_MIN_EFFECTIVE_FRACTION = 0.50
_GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE = 0.75
# Robust frame aggregation rejects a small bevel/UV-shear mode only when the
# dominant local frame still carries most of the weighted chart area.  These
# gates are intentionally stricter than a visual heading heuristic: a frame
# that cannot explain the majority of a chart must fall back to the strict
# single-axis +V correction.
_GEOMETRY_FRAME_ROBUST_TRIM_ANGLE = math.radians(35.0)
_GEOMETRY_FRAME_MIN_INLIER_WEIGHT = 0.60
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


def _quantile(values: Iterable[float], fraction: float) -> float:
    """Return a deterministic nearest-rank quantile for finite values.

    Layout reports are generated in Blender's embedded Python where pulling in
    a statistics dependency is undesirable.  Nearest-rank is deliberately
    simple and stable across Python/Blender versions; it is also less
    surprising for the lower-tail island metrics than interpolation between
    two tiny float32 areas.
    """

    finite = sorted(
        float(value)
        for value in values
        if math.isfinite(float(value))
    )
    if not finite:
        return 0.0
    index = int(round((len(finite) - 1) * _clamp(float(fraction), 0.0, 1.0)))
    return finite[max(0, min(len(finite) - 1, index))]


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
    # Directed landmarks are useful for choosing a repeat's front/back sign,
    # but they can point diagonally on an otherwise axis-aligned hard-surface
    # panel.  A landmark is used for the strict 360-degree contract only when
    # its line agrees with the geometric reference within the tolerance below.
    align_directed_cardinal: bool = True
    # Maximum modulo-180 residual between a directed landmark and the PCA or
    # dominant boundary-edge reference before an entire repeat component is
    # treated as center-symmetric.  This keeps hard-surface repeats upright
    # while retaining 360-degree direction for genuinely collinear landmarks.
    directed_cardinal_tolerance: float = math.radians(3.0)
    # A bounded bias toward square packing keeps the rigid layout from
    # filling only one strip of the 0-1 tile.  It never changes island scale
    # independently, so texel density remains uniform.
    square_pack_bias: float = 0.35
    # PCA is deliberately conservative for round-ish pieces.  A clear long
    # boundary edge is a safer orientation cue for hard-surface panels.
    min_cardinal_edge_confidence: float = 0.15
    # A checker with arrows or letters needs a modulo-360 contract.  When
    # enabled, a positive geometry axis is mapped to UV +V and later packing
    # is translation/scale only.  This mirrors Blender's Geometry alignment
    # semantics without depending on the operator added after Blender 3.3.
    align_geometry_direction: bool = True
    direction_space: str = "OBJECT"
    direction_axis: str = "AUTO"
    # AUTO is a strict signed contract.  The default preserves the historical
    # Z -> X -> Y behavior; elongated weapon bodies can opt into Y -> Z -> X
    # without changing the explicit ``direction_axis`` contract.
    direction_auto_priority: str = "ZXY"
    # Optional hard-surface AUTO resolver bias.  A positive value lets a
    # candidate model axis win when its rigid +V correction also leaves the
    # chart's dominant long edge horizontal/vertical.  Zero preserves the
    # historical priority/stability-only resolver used by older callers.
    direction_auto_cardinal_bias: float = 0.0
    # Ignore weak/ambiguous long-edge cues when applying the AUTO bias.  The
    # cue is never allowed to replace a candidate below the strict geometric
    # projection threshold.
    direction_auto_cardinal_min_confidence: float = 0.15
    # Optional common-axis presentation preference for hard-surface cohorts.
    # This is intentionally separate from the per-island AUTO resolver: a
    # long-edge cue may influence a shared structural axis only after every
    # member has a valid candidate.  The default keeps legacy AUTO selection
    # byte-for-byte stable for callers that do not opt in.
    prefer_geometry_axis_cardinal: bool = False
    # Minimum reduction in the weighted long-edge cardinal error (radians)
    # required before a lower-stability common axis can replace the baseline.
    geometry_axis_cardinal_min_gain: float = math.radians(5.0)
    # Maximum loss in the weakest candidate stability accepted for that visual
    # improvement.  Stability is the same bounded metric used by the legacy
    # common-axis resolver, so this threshold is scale independent.
    geometry_axis_cardinal_max_quality_loss: float = 0.15
    # Resolve otherwise independent AUTO charts from a model-space normal
    # domain before falling back to the UV-Jacobian winner.  This is an
    # opt-in presentation contract: structure/repeat/owner cohorts are still
    # authoritative, while singleton panels use the configured axis priority
    # against their actual tangent plane instead of their arbitrary source-UV
    # rotation.  It prevents a checker pattern from changing heading merely
    # because an unwrap happened to rotate one detached chart.
    cohere_auto_geometry_axis: bool = False
    # Minimum geometric tangent projection required for a priority axis to be
    # considered in the normal-domain resolver.  A value near 0.70 accepts
    # ordinary bevels while rejecting axes that are effectively the face
    # normal.  Candidates must also pass the existing UV Jacobian confidence
    # gate, so this setting cannot invent a direction on a degenerate chart.
    geometry_axis_consensus_min_tangent: float = 0.70
    # Reporting threshold for UV-Jacobian confidence in the normal-domain
    # resolver.  It is intentionally separate from
    # ``direction_axis_min_projection``: a numerically valid low-confidence
    # member may inherit the domain axis and is reported as downgraded instead
    # of being split into a second orientation cohort.
    geometry_axis_consensus_min_confidence: float = 0.30
    # Only a numerically normal axis may trigger the next fallback.  A
    # perceptual threshold here can make successive pipeline stages choose
    # different axes and introduce a deterministic 180-degree flip.
    direction_axis_min_projection: float = _GEOMETRY_AXIS_RELATIVE_EPSILON
    direction_residual_tolerance: float = math.radians(3.0)
    # Complete U/V frame checking is deliberately opt-out for callers that
    # need the historical single-axis behavior.  It never authorizes a UV
    # reflection: a negative parity is recorded and downgraded.
    align_geometry_frame: bool = True
    # Use the complete signed tangent frame when it agrees with the strict
    # positive-axis contract.  This is opt-in for backwards compatibility:
    # older callers measured the frame only as a diagnostic and wrote the
    # single-axis correction for every chart.
    use_geometry_frame_rotation: bool = False
    # Sharing one correction across a near-aligned topology/repeat cohort can
    # leave every member a few degrees away from its signed Geometry contract.
    # Keep exact per-island direction by default; legacy callers may opt in to
    # the softer visual-heading behavior explicitly.
    cohere_geometry_angle_groups: bool = False
    # Opt-in hard-surface presentation pass.  Related charts that resolve to
    # the same model tangent axis may share a horizontal/vertical long-edge
    # heading, but only when the additional correction remains inside the
    # strict +V residual budget.  The default is deliberately off so existing
    # callers retain their exact per-island rotations.
    cohere_geometry_long_edge: bool = False
    # Preserve the artist/source atlas as the primary structural signal.
    # Islands remain independent rigid bodies; orientation may rotate them,
    # and a local collision resolver translates only the charts that would
    # overlap.  This avoids rebuilding a sparse hard-surface atlas from broad
    # semantic groups whose members can span several unrelated UV regions.
    preserve_source_layout: bool = False
    source_layout_row_quantum: float = 0.025
    source_layout_row_weight: float = 0.15
    # Placement order for the source-atlas repair pass.  ROW_MAJOR keeps the
    # artist's existing rows/columns as the primary scaffold; AREA is retained
    # for legacy callers that intentionally place the largest charts first.
    source_layout_order: str = "ROW_MAJOR"
    # Weight for keeping linked source-layout charts on a common translation.
    # This is a layout-only affinity and never changes island ownership or
    # seam data.  A zero default preserves the historical source-row behavior.
    source_layout_affinity_weight: float = 0.0
    # Source-cell packing keeps nearby structural charts in a small rigid
    # rack before the atlas-level collision pass.  The limits are expressed
    # in source-UV space so a semantic chain cannot pull distant regions into
    # one giant block (the failure mode of the old macro packer).
    source_layout_cell_enabled: bool = True
    source_layout_cell_max_members: int = 12
    source_layout_cell_diameter_ratio: float = 0.16
    source_layout_cell_link_radius_ratio: float = 0.12
    # Small mechanical details are easy to lose at bake resolution.  This is
    # a bounded *uniform* boost applied only to islands classified as small;
    # it does not shear or stretch an island and can be disabled with 1.0.
    small_island_scale_boost: float = 1.0
    # Permit a small longest-side trade-off when a more square shelf candidate
    # materially improves use of the 0-1 tile.  The bound is relative to the
    # compact baseline and keeps texel-density loss explicit and predictable.
    square_pack_max_edge_relaxation: float = 0.02
    # Select the best valid adaptive layout with an area-aware objective.  The
    # switch defaults on for new callers, while callers that need the exact
    # historical adaptive ordering can set it to False.
    area_aware_scoring: bool = True
    # A candidate may not trade away more than this fraction of the reference
    # uniform scale merely to obtain a fuller-looking tile.
    candidate_density_floor: float = 0.90
    # Relative weights for the adaptive candidate score.  They are normalized
    # at evaluation time, so old callers can omit all of them safely.
    area_score_weight: float = 0.45
    polygon_coverage_score_weight: float = 0.25
    aabb_fill_score_weight: float = 0.15
    short_edge_score_weight: float = 0.15
    # Keep topologically adjacent, non-micro charts in a bounded macro block.
    # This is deliberately a layout-only relationship: charts remain separate
    # UV islands and artist seams are never removed.
    structure_group_enabled: bool = True
    structure_group_max_members: int = 12
    structure_group_max_degree: int = 2
    structure_group_min_contact_ratio: float = 0.12
    structure_group_max_diameter_ratio: float = 0.20
    structure_group_max_normal_angle: float = math.radians(100.0)
    # Repeated micro parts may be attached to different local owners.  Do not
    # let a transitive repeat chain merge owners across the whole asset.
    repeat_group_max_members: int = 48
    repeat_group_max_diameter_ratio: float = 0.20
    # Geometry-connected layout blocks are a second, coarser continuity
    # layer.  Existing repeat/owner groups remain atomic; neighboring groups
    # may be packed into bounded blocks and linked at the top level.
    continuity_block_enabled: bool = True
    continuity_block_target_members: int = 32
    continuity_block_max_members: int = 48
    # A raw mesh connected component can span an entire weapon.  Continuity
    # components are therefore capped independently from the block merge
    # budget; this prevents a long chain of otherwise valid blocks from
    # becoming one atlas-wide rigid search component.
    continuity_component_max_groups: int = 6
    continuity_component_max_members: int = 96
    # The continuity envelope is intentionally less strict than the owner
    # merge envelope (0.20 by default).  ``_build_geometry_continuity_blocks``
    # uses the larger of the two values so existing callers that explicitly
    # raise ``structure_group_max_diameter_ratio`` retain their behavior.
    continuity_block_max_diameter_ratio: float = 0.35

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
        if not math.isfinite(float(self.square_pack_bias)) or not 0.0 <= float(
            self.square_pack_bias
        ) <= 1.0:
            raise ValueError("square_pack_bias must be finite and in [0, 1]")
        if (
            not math.isfinite(float(self.directed_cardinal_tolerance))
            or not 0.0 <= float(self.directed_cardinal_tolerance) <= math.pi * 0.5
        ):
            raise ValueError(
                "directed_cardinal_tolerance must be finite and in [0, pi/2]"
            )
        if not math.isfinite(float(self.min_cardinal_edge_confidence)) or not 0.0 <= float(
            self.min_cardinal_edge_confidence
        ) <= 1.0:
            raise ValueError(
                "min_cardinal_edge_confidence must be finite and in [0, 1]"
            )
        if str(self.direction_space).upper() not in {"OBJECT", "WORLD"}:
            raise ValueError("direction_space must be OBJECT or WORLD")
        if str(self.direction_axis).upper() not in {"AUTO", "X", "Y", "Z"}:
            raise ValueError("direction_axis must be AUTO, X, Y, or Z")
        _normalize_direction_auto_priority(self.direction_auto_priority)
        if not math.isfinite(float(self.direction_auto_cardinal_bias)) or not 0.0 <= float(
            self.direction_auto_cardinal_bias
        ) <= 1.0:
            raise ValueError(
                "direction_auto_cardinal_bias must be finite and in [0, 1]"
            )
        if not math.isfinite(float(self.direction_auto_cardinal_min_confidence)) or not 0.0 <= float(
            self.direction_auto_cardinal_min_confidence
        ) <= 1.0:
            raise ValueError(
                "direction_auto_cardinal_min_confidence must be finite and in [0, 1]"
            )
        if (
            not math.isfinite(float(self.geometry_axis_cardinal_min_gain))
            or not 0.0 <= float(self.geometry_axis_cardinal_min_gain) <= math.pi * 0.25
        ):
            raise ValueError(
                "geometry_axis_cardinal_min_gain must be finite and in [0, pi/4]"
            )
        if (
            not math.isfinite(float(self.geometry_axis_cardinal_max_quality_loss))
            or not 0.0 <= float(self.geometry_axis_cardinal_max_quality_loss) <= 1.0
        ):
            raise ValueError(
                "geometry_axis_cardinal_max_quality_loss must be finite and in [0, 1]"
            )
        if (
            not math.isfinite(float(self.geometry_axis_consensus_min_tangent))
            or not 0.0 <= float(self.geometry_axis_consensus_min_tangent) <= 1.0
        ):
            raise ValueError(
                "geometry_axis_consensus_min_tangent must be finite and in [0, 1]"
            )
        if (
            not math.isfinite(float(self.geometry_axis_consensus_min_confidence))
            or not 0.0 <= float(self.geometry_axis_consensus_min_confidence) <= 1.0
        ):
            raise ValueError(
                "geometry_axis_consensus_min_confidence must be finite and in [0, 1]"
            )
        if (
            not math.isfinite(float(self.direction_axis_min_projection))
            or not 0.0 <= float(self.direction_axis_min_projection) <= 1.0
        ):
            raise ValueError(
                "direction_axis_min_projection must be finite and in [0, 1]"
            )
        if (
            not math.isfinite(float(self.direction_residual_tolerance))
            or not 0.0 <= float(self.direction_residual_tolerance) <= math.pi
        ):
            raise ValueError(
                "direction_residual_tolerance must be finite and in [0, pi]"
            )
        if (
            not math.isfinite(float(self.source_layout_row_quantum))
            or not 1.0e-6 <= float(self.source_layout_row_quantum) <= 1.0
        ):
            raise ValueError(
                "source_layout_row_quantum must be finite and in [1e-6, 1]"
            )
        if (
            not math.isfinite(float(self.source_layout_row_weight))
            or not 0.0 <= float(self.source_layout_row_weight) <= 10.0
        ):
            raise ValueError(
                "source_layout_row_weight must be finite and in [0, 10]"
            )
        if str(self.source_layout_order).upper() not in {"ROW_MAJOR", "AREA"}:
            raise ValueError("source_layout_order must be ROW_MAJOR or AREA")
        if (
            not math.isfinite(float(self.source_layout_affinity_weight))
            or not 0.0 <= float(self.source_layout_affinity_weight) <= 10.0
        ):
            raise ValueError(
                "source_layout_affinity_weight must be finite and in [0, 10]"
            )
        if self.source_layout_cell_max_members < 2:
            raise ValueError(
                "source_layout_cell_max_members must be at least two"
            )
        for name in (
            "source_layout_cell_diameter_ratio",
            "source_layout_cell_link_radius_ratio",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 < value <= 1.0:
                raise ValueError(
                    "{} must be finite and in (0, 1]".format(name)
                )
        if not math.isfinite(float(self.small_island_scale_boost)) or not 1.0 <= float(
            self.small_island_scale_boost
        ) <= 3.0:
            raise ValueError(
                "small_island_scale_boost must be finite and in [1, 3]"
            )
        if not math.isfinite(float(self.square_pack_max_edge_relaxation)) or not 0.0 <= float(
            self.square_pack_max_edge_relaxation
        ) <= 0.25:
            raise ValueError(
                "square_pack_max_edge_relaxation must be finite and in [0, 0.25]"
            )
        if not math.isfinite(float(self.candidate_density_floor)) or not 0.0 < float(
            self.candidate_density_floor
        ) <= 1.0:
            raise ValueError("candidate_density_floor must be finite and in (0, 1]")
        for name in (
            "area_score_weight",
            "polygon_coverage_score_weight",
            "aabb_fill_score_weight",
            "short_edge_score_weight",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError("{} must be finite and non-negative".format(name))
        if self.structure_group_max_members < 2:
            raise ValueError("structure_group_max_members must be at least two")
        if self.structure_group_max_degree < 1:
            raise ValueError("structure_group_max_degree must be positive")
        if not math.isfinite(float(self.structure_group_min_contact_ratio)) or not 0.0 <= float(
            self.structure_group_min_contact_ratio
        ):
            raise ValueError(
                "structure_group_min_contact_ratio must be finite and non-negative"
            )
        if not math.isfinite(float(self.structure_group_max_diameter_ratio)) or not 0.0 < float(
            self.structure_group_max_diameter_ratio
        ) <= 1.0:
            raise ValueError(
                "structure_group_max_diameter_ratio must be finite and in (0, 1]"
            )
        if not math.isfinite(float(self.structure_group_max_normal_angle)) or not 0.0 <= float(
            self.structure_group_max_normal_angle
        ) <= math.pi:
            raise ValueError(
                "structure_group_max_normal_angle must be finite and in [0, pi]"
            )
        if self.repeat_group_max_members < 2:
            raise ValueError("repeat_group_max_members must be at least two")
        if not math.isfinite(float(self.repeat_group_max_diameter_ratio)) or not 0.0 < float(
            self.repeat_group_max_diameter_ratio
        ) <= 1.0:
            raise ValueError(
                "repeat_group_max_diameter_ratio must be finite and in (0, 1]"
            )
        if self.continuity_block_target_members < 2:
            raise ValueError(
                "continuity_block_target_members must be at least two"
            )
        if self.continuity_block_max_members < self.continuity_block_target_members:
            raise ValueError(
                "continuity_block_max_members must be >= target members"
            )
        if self.continuity_component_max_groups < 2:
            raise ValueError(
                "continuity_component_max_groups must be at least two"
            )
        if self.continuity_component_max_members < 2:
            raise ValueError(
                "continuity_component_max_members must be at least two"
            )
        if (
            not math.isfinite(float(self.continuity_block_max_diameter_ratio))
            or not 0.0 < float(self.continuity_block_max_diameter_ratio) <= 1.0
        ):
            raise ValueError(
                "continuity_block_max_diameter_ratio must be finite and in (0, 1]"
            )
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
    dominant_edge_angle: Optional[float] = None
    dominant_edge_confidence: float = 0.0
    # ``directed`` means a reliable landmark is eligible for modulo-360
    # repeat alignment.  ``center_symmetric`` records either an intrinsically
    # unresolved landmark or an explicit cardinal-compatibility downgrade.
    direction_mode: str = "center_symmetric"
    direction_downgrade_reason: Optional[str] = None
    # Shape diagnostics are kept on the island record so reports and adaptive
    # scoring use the same definitions.  Defaults preserve compatibility with
    # callers constructing IslandRecord positionally from older releases.
    uv_aabb_area: float = 0.0
    uv_polygon_fill: float = 0.0
    uv_short_edge: float = 0.0
    geometry_axis_name: Optional[str] = None
    geometry_direction_space: str = "OBJECT"
    geometry_direction_vector: Vector = field(
        default_factory=lambda: Vector((0.0, 0.0))
    )
    geometry_direction_confidence: float = 0.0
    geometry_rotation_angle: float = 0.0
    geometry_final_residual: Optional[float] = None
    geometry_frame_u_vector: Vector = field(
        default_factory=lambda: Vector((0.0, 0.0))
    )
    geometry_frame_v_vector: Vector = field(
        default_factory=lambda: Vector((0.0, 0.0))
    )
    geometry_frame_parity: int = 0
    geometry_frame_parity_confidence: float = 0.0
    geometry_frame_confidence: float = 0.0
    geometry_frame_rotation_angle: float = 0.0
    geometry_frame_residual: Optional[float] = None
    geometry_frame_downgrade_reason: Optional[str] = None
    # Optional visual-heading diagnostics populated by the opt-in long-edge
    # coherence pass.  Defaults keep older positional constructors valid.
    geometry_long_edge_angle: Optional[float] = None
    geometry_long_edge_target_angle: Optional[float] = None
    geometry_long_edge_correction: float = 0.0
    geometry_long_edge_aligned: bool = False
    geometry_long_edge_downgrade_reason: Optional[str] = None

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
            "direction_mode": str(self.direction_mode),
            "direction_downgrade_reason": self.direction_downgrade_reason,
            "landmark_vertex": self.landmark_vertex,
            "signature": self.geometry_signature,
            "neighbors": list(self.neighbor_ids),
            "small": self.is_small,
            "dominant_edge_angle_degrees": (
                None
                if self.dominant_edge_angle is None
                else round(math.degrees(self.dominant_edge_angle), 4)
            ),
            "dominant_edge_confidence": round(
                self.dominant_edge_confidence, 6
            ),
            "average_normal": _vector_list(self.average_normal),
            "geometry_axis": self.geometry_axis_name,
            "geometry_direction_space": str(self.geometry_direction_space),
            "geometry_direction_vector": _vector_list(
                self.geometry_direction_vector
            ),
            "geometry_direction_confidence": round(
                float(self.geometry_direction_confidence), 6
            ),
            "geometry_rotation_degrees": round(
                math.degrees(float(self.geometry_rotation_angle)), 6
            ),
            "geometry_final_residual_degrees": (
                None
                if self.geometry_final_residual is None
                else round(
                    math.degrees(float(self.geometry_final_residual)), 6
                )
            ),
            "geometry_frame_u_vector": _vector_list(
                self.geometry_frame_u_vector
            ),
            "geometry_frame_v_vector": _vector_list(
                self.geometry_frame_v_vector
            ),
            "geometry_frame_parity": int(self.geometry_frame_parity),
            "geometry_frame_parity_confidence": round(
                float(self.geometry_frame_parity_confidence), 6
            ),
            "geometry_frame_confidence": round(
                float(self.geometry_frame_confidence), 6
            ),
            "geometry_frame_rotation_degrees": round(
                math.degrees(float(self.geometry_frame_rotation_angle)), 6
            ),
            "geometry_frame_residual_degrees": (
                None
                if self.geometry_frame_residual is None
                else round(
                    math.degrees(float(self.geometry_frame_residual)), 6
                )
            ),
            "geometry_frame_downgrade_reason": (
                self.geometry_frame_downgrade_reason
            ),
            "geometry_long_edge_angle_degrees": (
                None
                if self.geometry_long_edge_angle is None
                else round(math.degrees(float(self.geometry_long_edge_angle)), 6)
            ),
            "geometry_long_edge_target_angle_degrees": (
                None
                if self.geometry_long_edge_target_angle is None
                else round(
                    math.degrees(float(self.geometry_long_edge_target_angle)),
                    6,
                )
            ),
            "geometry_long_edge_correction_degrees": round(
                math.degrees(float(self.geometry_long_edge_correction)), 6
            ),
            "geometry_long_edge_aligned": bool(
                self.geometry_long_edge_aligned
            ),
            "geometry_long_edge_downgrade_reason": (
                self.geometry_long_edge_downgrade_reason
            ),
            "uv_aabb_area": round(float(self.uv_aabb_area), 9),
            "uv_polygon_fill": round(float(self.uv_polygon_fill), 9),
            "uv_short_edge": round(float(self.uv_short_edge), 9),
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
    # Pairwise layout affinities are derived from real mesh adjacency.  They
    # constrain owner-cell placement only; the member islands remain separate
    # UV charts and no seam/topology data is changed.
    affinity_pairs: Tuple[Tuple[int, int], ...] = ()
    # Optional geometry-continuity metadata.  ``continuity_component`` is a
    # stable per-object token; ``continuity_peers`` is populated with final
    # layout-group ids after bounded component blocking.  Defaults keep the
    # pre-block API/constructor contract intact.
    continuity_component: Optional[int] = None
    continuity_peers: Tuple[int, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.group_id,
            "members": list(self.member_ids),
            "reason": self.reason,
            "anchors": list(self.anchor_ids),
            "small_members": list(self.small_member_ids),
            "small_anchors": list(self.small_anchor_ids),
            "owner_cohorts": [list(cohort) for cohort in self.owner_cohorts],
            "affinity_pairs": [list(pair) for pair in self.affinity_pairs],
            "continuity_component": self.continuity_component,
            "continuity_peers": list(self.continuity_peers),
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
    # Explicit records make the fallback auditable in manifests.  The list is
    # empty for layouts that never needed a directed-to-center-symmetric
    # downgrade.
    orientation_downgrades: List[Mapping[str, Any]] = field(default_factory=list)
    orientation_policy: Mapping[str, Any] = field(default_factory=dict)
    geometry_axis_groups: List[Mapping[str, Any]] = field(default_factory=list)
    geometry_long_edge_metrics: Mapping[str, Any] = field(
        default_factory=dict
    )

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
            "orientation_policy": dict(self.orientation_policy),
            "geometry_axis_groups": [
                dict(item) for item in self.geometry_axis_groups
            ],
            "geometry_long_edge_metrics": dict(
                self.geometry_long_edge_metrics
            ),
            "orientation_downgrades": [
                dict(item) for item in self.orientation_downgrades
            ],
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
    packed_width: float = 0.0
    packed_height: float = 0.0
    tile_occupancy: float = 0.0
    small_island_scale_boost: float = 1.0
    small_islands_scaled: int = 0
    square_pack_max_edge_relaxation: float = 0.02
    # Read-only post-pack diagnostics.  These fields are appended with
    # defaults so callers using the pre-0.5.5 positional constructor keep
    # working.  ``candidate_valid`` is the geometric validity gate; density
    # eligibility is intentionally kept separate in the adaptive selector.
    quality_metrics: Mapping[str, Any] = field(default_factory=dict)
    candidate_score: float = 0.0
    candidate_valid: bool = True
    layout_strategy: str = "global_pack"
    source_moved_islands: int = 0
    source_total_translation: float = 0.0
    source_max_translation: float = 0.0

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
            "packed_width": round(self.packed_width, 9),
            "packed_height": round(self.packed_height, 9),
            "tile_occupancy": round(self.tile_occupancy, 9),
            "small_island_scale_boost": round(
                float(self.small_island_scale_boost), 6
            ),
            "small_islands_scaled": int(self.small_islands_scaled),
            "square_pack_max_edge_relaxation": round(
                float(self.square_pack_max_edge_relaxation), 6
            ),
            "quality_metrics": dict(self.quality_metrics),
            "candidate_score": round(float(self.candidate_score), 9),
            "candidate_valid": bool(self.candidate_valid),
            "layout_strategy": str(self.layout_strategy),
            "source_moved_islands": int(self.source_moved_islands),
            "source_total_translation": round(
                float(self.source_total_translation), 9
            ),
            "source_max_translation": round(
                float(self.source_max_translation), 9
            ),
            "topology_stitches": 0,
            "reflection_applied": False,
            "relative_texel_density_preserved": bool(
                int(self.small_islands_scaled) == 0
                or float(self.small_island_scale_boost) <= 1.0 + 1.0e-9
            ),
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


@dataclass(frozen=True)
class _FreeRect:
    x: float
    y: float
    width: float
    height: float


@dataclass
class _PackedPlan:
    coordinates: Dict[int, Dict[int, Vector]]
    width: float
    height: float
    source_gap: float
    group_placements: Dict[int, _Placement]
    strategy: str = "global_pack"
    moved_islands: int = 0
    total_translation: float = 0.0
    max_translation: float = 0.0


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


def _dominant_uv_edge_angle(
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    uv_extent: float,
) -> Tuple[Optional[float], float]:
    """Return a stable long-edge direction for cardinal alignment.

    PCA becomes undefined for square or nearly circular charts.  Hard-surface
    charts still commonly have a meaningful straight boundary, so use the
    longest UV boundary edge as a fallback.  Internal triangulation diagonals
    are ignored whenever a boundary edge is available; this avoids snapping a
    panel to an arbitrary diagonal.  The confidence is based on edge length
    relative to the chart extent and is intentionally capped at one.
    """

    face_set = {int(index) for index in face_indices}
    edge_face_counts: Dict[int, int] = defaultdict(int)
    for face_index in face_set:
        for loop_index in mesh.polygons[face_index].loop_indices:
            edge_face_counts[int(mesh.loops[loop_index].edge_index)] += 1

    candidates: List[Tuple[float, float, int, int]] = []
    for face_index in sorted(face_set):
        loop_indices = list(mesh.polygons[face_index].loop_indices)
        for offset, loop_index in enumerate(loop_indices):
            next_loop_index = loop_indices[(offset + 1) % len(loop_indices)]
            first = uv_layer.data[loop_index].uv
            second = uv_layer.data[next_loop_index].uv
            delta = second - first
            length = float(delta.length)
            if not math.isfinite(length) or length <= _EPSILON:
                continue
            edge_index = int(mesh.loops[loop_index].edge_index)
            angle = _line_angle_wrap(math.atan2(delta.y, delta.x))
            candidates.append((
                length,
                angle,
                edge_index,
                int(loop_index),
            ))
    if not candidates:
        return None, 0.0

    boundary = [
        item for item in candidates
        if edge_face_counts.get(item[2], 0) <= 1
    ]
    pool = boundary or candidates
    # Stable tie-breaking by edge/loop index is important for mirrored meshes
    # whose equivalent edges have exactly the same length.
    longest = max(pool, key=lambda item: (item[0], -item[2], -item[3]))
    extent = max(float(uv_extent), _EPSILON)
    confidence = _clamp(longest[0] / extent, 0.0, 1.0)
    return longest[1], confidence


def _orientation_reference_angle(
    island: IslandRecord,
    min_pca_anisotropy: float = 0.06,
    min_edge_confidence: float = 0.15,
) -> Optional[float]:
    """Choose PCA or a recorded boundary-edge direction for an island."""

    if island.anisotropy >= min_pca_anisotropy:
        return island.principal_angle
    edge_angle = getattr(island, "dominant_edge_angle", None)
    edge_confidence = float(
        getattr(island, "dominant_edge_confidence", 0.0)
    )
    if (
        edge_angle is not None
        and math.isfinite(float(edge_angle))
        and edge_confidence >= min_edge_confidence
    ):
        return _line_angle_wrap(float(edge_angle))
    return None


def _uv_cardinal_reference(
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    min_pca_anisotropy: float = 0.06,
    min_edge_confidence: float = 0.15,
) -> Tuple[Optional[float], float]:
    """Return a line cue and confidence for AUTO axis scoring.

    AUTO axis resolution runs before an :class:`IslandRecord` is assembled,
    so it cannot reuse ``_orientation_reference_angle`` directly.  Keep this
    small read-only helper in the same module and use the exact PCA/boundary
    edge policy as the later cardinal alignment pass.
    """

    points = []
    for face_index in face_indices:
        try:
            points.extend(
                uv_layer.data[int(loop_index)].uv.copy()
                for loop_index in mesh.polygons[int(face_index)].loop_indices
            )
        except (AttributeError, IndexError, TypeError, RuntimeError):
            continue
    if len(points) < 2:
        return None, 0.0
    bounds = _uv_bounds(points)
    extent = max(
        float(bounds[2]) - float(bounds[0]),
        float(bounds[3]) - float(bounds[1]),
        _EPSILON,
    )
    principal, anisotropy = _principal_axis(points)
    if anisotropy >= float(min_pca_anisotropy):
        return _line_angle_wrap(float(principal)), _clamp(float(anisotropy), 0.0, 1.0)
    edge_angle, edge_confidence = _dominant_uv_edge_angle(
        mesh, uv_layer, face_indices, extent
    )
    if (
        edge_angle is not None
        and math.isfinite(float(edge_angle))
        and float(edge_confidence) >= float(min_edge_confidence)
    ):
        return _line_angle_wrap(float(edge_angle)), _clamp(
            float(edge_confidence), 0.0, 1.0
        )
    return None, 0.0


def _cardinal_axis_quality(
    reference: Optional[float],
    rotation: float,
    confidence: float,
    min_confidence: float,
) -> Optional[float]:
    """Map a candidate's rigidly corrected line to a bounded quality score."""

    if reference is None:
        return None
    try:
        reference = float(reference)
        rotation = float(rotation)
        confidence = float(confidence)
        min_confidence = float(min_confidence)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (
        reference, rotation, confidence, min_confidence
    )) or confidence + _EPSILON < min_confidence:
        return None
    error = abs(_nearest_cardinal_delta(
        _line_angle_wrap(reference + rotation)
    ))
    # A 45-degree line is the least useful orientation cue.  Keep the
    # confidence factor separate so a weak edge cannot dominate a strong PCA
    # candidate merely because it happens to be near a cardinal angle.
    normalized = 1.0 - _clamp(error / (math.pi * 0.25), 0.0, 1.0)
    return _clamp(normalized * _clamp(confidence, 0.0, 1.0), 0.0, 1.0)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


# AUTO geometry-axis selection is based on UV Jacobians.  A rigid UV rotation
# should not change the selected model axis, but float32 writeback can move two
# nearly tied candidates across the coherence threshold.  Persisting the
# face-set contract on the mesh makes a post-layout re-analysis deterministic
# across Blender callbacks and after the .blend is reopened.  The property is
# deliberately namespaced and versioned so older files are ignored safely.
_GEOMETRY_AXIS_CONTRACT_VERSION = 1
_GEOMETRY_AXIS_CONTRACT_PREFIX = "_vuv_geometry_axis_contract_v1_"


def _geometry_axis_contract_key(layer_name: str) -> str:
    digest = hashlib.sha1(str(layer_name).encode("utf-8")).hexdigest()[:16]
    return _GEOMETRY_AXIS_CONTRACT_PREFIX + digest


def _mesh_topology_contract(mesh: Any) -> str:
    """Return a compact topology fingerprint for an axis contract."""

    try:
        faces = [
            tuple(int(index) for index in polygon.vertices)
            for polygon in mesh.polygons
        ]
        vertex_count = len(mesh.vertices)
    except (AttributeError, TypeError, ValueError):
        return ""
    return _stable_hash({
        "vertices": int(vertex_count),
        "faces": faces,
    })


def _face_set_contract_key(face_indices: Sequence[int]) -> str:
    return ",".join(str(int(index)) for index in sorted(face_indices))


def _load_geometry_axis_contract(
    mesh: Any,
    uv_layer: Any,
    settings: GroupLayoutOptions,
) -> Dict[Tuple[int, ...], Tuple[Optional[str], str]]:
    """Read a validated per-face-set axis map from mesh ID properties.

    Missing, malformed, or stale contracts are ignored.  This keeps ordinary
    source UV analysis side-effect free and prevents a contract copied from a
    different mesh topology from changing axis resolution.
    """

    if (
        not settings.align_geometry_direction
        or str(settings.direction_axis).upper() != "AUTO"
    ):
        return {}
    layer_name = getattr(uv_layer, "name", None)
    if not layer_name:
        return {}
    try:
        raw = mesh.get(_geometry_axis_contract_key(str(layer_name)))
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return {}
    if raw in (None, ""):
        return {}
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(str(raw))
    except (TypeError, ValueError, UnicodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    try:
        payload_version = int(payload.get("version", -1))
    except (TypeError, ValueError, OverflowError):
        # A user-edited or legacy ID property must never make UV analysis
        # fail.  Treat malformed metadata as an absent contract.
        return {}
    if payload_version != _GEOMETRY_AXIS_CONTRACT_VERSION:
        return {}
    if str(payload.get("layer", "")) != str(layer_name):
        return {}
    if str(payload.get("topology", "")) != _mesh_topology_contract(mesh):
        return {}
    # Contracts capture the resolver order as well as the selected axis.  A
    # contract written with a different AUTO priority is not safe to replay:
    # the selected fallback may have been intentional for that layout.
    try:
        requested_priority = _normalize_direction_auto_priority(
            getattr(settings, "direction_auto_priority", None)
        )
        payload_priority = _normalize_direction_auto_priority(
            payload.get("auto_priority", "".join(_DEFAULT_DIRECTION_AUTO_PRIORITY))
        )
    except (TypeError, ValueError):
        return {}
    if payload_priority != requested_priority:
        return {}
    contract_space = str(payload.get("space", "")).upper()
    requested_space = str(settings.direction_space).upper()
    if (
        contract_space not in {"OBJECT", "WORLD"}
        or contract_space != requested_space
    ):
        return {}
    entries = payload.get("faces")
    if not isinstance(entries, Mapping):
        return {}
    result: Dict[Tuple[int, ...], Tuple[Optional[str], str]] = {}
    for raw_faces, raw_axis in entries.items():
        try:
            face_key = tuple(
                sorted(
                    int(value)
                    for value in str(raw_faces).split(",")
                    if str(value).strip() != ""
                )
            )
        except (TypeError, ValueError):
            continue
        if not face_key:
            continue
        axis_name: Optional[str]
        if raw_axis in (None, "", "NONE"):
            axis_name = None
        else:
            axis_name = str(raw_axis).upper()
            if axis_name not in _DIRECTION_AXES:
                continue
        result[face_key] = (axis_name, contract_space)
    return result


def _persist_geometry_axis_contract(
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> bool:
    """Persist the selected AUTO axis for every current UV face set."""

    if (
        not settings.align_geometry_direction
        or str(settings.direction_axis).upper() != "AUTO"
    ):
        return False
    layer_name = getattr(uv_layer, "name", None)
    topology = _mesh_topology_contract(mesh)
    if not layer_name or not topology:
        return False
    entries = {
        _face_set_contract_key(island.face_indices): (
            None
            if island.geometry_axis_name not in _DIRECTION_AXES
            else str(island.geometry_axis_name).upper()
        )
        for island in analysis.islands
        if island.face_indices
    }
    payload = {
        "version": _GEOMETRY_AXIS_CONTRACT_VERSION,
        "layer": str(layer_name),
        "space": str(settings.direction_space).upper(),
        "auto_priority": "".join(_normalize_direction_auto_priority(
            getattr(settings, "direction_auto_priority", None)
        )),
        "topology": topology,
        "faces": entries,
    }
    try:
        mesh[_geometry_axis_contract_key(str(layer_name))] = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        # ID properties are unavailable on a few light-weight test doubles and
        # can reject very old Blender data-blocks.  Axis rebind still protects
        # the in-memory pipeline; persistence is an enhancement, not a gate.
        return False
    return True


def _apply_persisted_geometry_axis_contract(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> int:
    """Restore previously selected AUTO axes for matching face sets.

    AUTO axis resolution is based on the current UV Jacobian.  Once an island
    has been rotated into its final atlas position, two tangent axes can be
    numerically equivalent and a fresh analysis may choose a different one.
    The persisted contract is keyed by the island's face set and topology
    fingerprint, so applying it here makes analysis deterministic after a
    save/reopen while still falling back safely for changed topology.
    """

    contract = _load_geometry_axis_contract(mesh, uv_layer, settings)
    if not contract:
        return 0
    by_faces = {
        tuple(sorted(int(index) for index in island.face_indices)): island
        for island in analysis.islands
    }
    # A contract is an all-island snapshot.  Applying only the intersection
    # after a seam/UV partition change would silently assign old axes to new
    # charts, so reject the whole snapshot when its face-set identity differs.
    if set(contract) != set(by_faces):
        return 0
    rebound = []
    unresolved = []
    for face_key, raw_value in sorted(contract.items()):
        island = by_faces.get(tuple(face_key))
        if island is None:
            continue
        try:
            expected_axis, expected_space = raw_value
            expected_axis = (
                None
                if expected_axis is None
                else str(expected_axis).upper()
            )
            expected_space = str(
                expected_space or settings.direction_space
            ).upper()
        except (TypeError, ValueError):
            continue
        previous_axis = island.geometry_axis_name
        if expected_axis is None:
            # ``None`` is an explicit unresolved result from the previous
            # successful layout.  Do not let a later AUTO pass opportunistically
            # choose a different axis for this face set.
            island.geometry_axis_name = None
            island.geometry_direction_space = expected_space
            island.geometry_direction_vector = Vector((0.0, 0.0))
            island.geometry_direction_confidence = 0.0
            island.geometry_rotation_angle = 0.0
            island.geometry_final_residual = None
            _clear_geometry_frame_metadata(
                island, "persisted_axis_unresolved"
            )
            unresolved.append((island, previous_axis))
            continue
        if expected_axis not in _DIRECTION_AXES:
            continue
        axis_name, direction, rotation, confidence = (
            _geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                island.face_indices,
                space=expected_space,
                axis=settings.direction_axis,
                min_projection=settings.direction_axis_min_projection,
                fixed_axis_name=expected_axis,
                auto_priority=settings.direction_auto_priority,
            )
        )
        if (
            axis_name != expected_axis
            or direction.length_squared <= _EPSILON
            or not math.isfinite(float(confidence))
            or float(confidence) + _EPSILON
            < float(settings.direction_axis_min_projection)
        ):
            # The selected axis is no longer usable for this face set.  Keep
            # the persisted contract explicit and unresolved instead of
            # silently switching to another AUTO axis.
            island.geometry_axis_name = None
            island.geometry_direction_space = expected_space
            island.geometry_direction_vector = Vector((0.0, 0.0))
            island.geometry_direction_confidence = 0.0
            island.geometry_rotation_angle = 0.0
            island.geometry_final_residual = None
            _clear_geometry_frame_metadata(
                island, "persisted_axis_unavailable"
            )
            unresolved.append((island, previous_axis))
            continue
        island.geometry_axis_name = expected_axis
        island.geometry_direction_space = expected_space
        island.geometry_direction_vector = direction.copy()
        island.geometry_rotation_angle = float(rotation)
        island.geometry_direction_confidence = float(confidence)
        island.geometry_final_residual = None
        _bind_geometry_frame_metadata(
            obj,
            mesh,
            uv_layer,
            island,
            settings,
            axis_name=expected_axis,
        )
        rebound.append((island, previous_axis))
    if rebound:
        rebound_ids = [
            int(island.island_id) for island, _previous in rebound
        ]
        rebound_axes = {
            island.geometry_axis_name for island, _previous in rebound
        }
        analysis.geometry_axis_groups.append({
            "source": "PERSISTED_CONTRACT",
            "members": rebound_ids,
            "component_members": rebound_ids,
            "selected_axis": (
                next(iter(rebound_axes))
                if len(rebound_axes) == 1
                else "MIXED"
            ),
            "previous_axes": [
                previous for _island, previous in rebound
            ],
            "resolved": True,
            "compatibility_split": False,
            "compatibility_rank": 0,
        })
    if unresolved:
        analysis.geometry_axis_groups.append({
            "source": "PERSISTED_CONTRACT",
            "members": [
                int(island.island_id) for island, _previous in unresolved
            ],
            "component_members": [
                int(island.island_id) for island in by_faces.values()
            ],
            "selected_axis": None,
            "previous_axes": [
                previous for _island, previous in unresolved
            ],
            "resolved": False,
            "compatibility_split": False,
            "compatibility_rank": 0,
        })
    return len(rebound)


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
        if len(records) != 2:
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


_DIRECTION_AXES = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}


_DEFAULT_DIRECTION_AUTO_PRIORITY = ("Z", "X", "Y")


def _normalize_direction_auto_priority(
    priority: Any,
) -> Tuple[str, ...]:
    """Normalize the AUTO axis priority to a complete X/Y/Z permutation.

    The public option is intentionally convenient for configuration files and
    Blender ID properties (``"YZX"``), while accepting a tuple/list keeps the
    helper interoperable with the geometry audit API.  AUTO needs all three
    axes: callers that want one fixed axis should use ``direction_axis``.
    """

    if priority is None:
        return _DEFAULT_DIRECTION_AUTO_PRIORITY
    if isinstance(priority, str):
        # Accept compact strings as well as the common ``Y,Z,X`` spelling.
        raw = priority.replace(",", "").replace(" ", "")
        values = tuple(raw)
    else:
        try:
            values = tuple(priority)
        except (TypeError, ValueError):
            raise ValueError(
                "direction_auto_priority must be a permutation of X, Y, Z"
            )
        # A one-element sequence containing a compact string is a natural
        # result when a generic sequence normalizer is used by a caller.
        if len(values) == 1 and isinstance(values[0], str) and len(values[0]) > 1:
            values = tuple(
                values[0].replace(",", "").replace(" ", "")
            )
    normalized: List[str] = []
    for value in values:
        axis_name = str(value).upper()
        if axis_name not in _DIRECTION_AXES:
            raise ValueError(
                "direction_auto_priority must be a permutation of X, Y, Z"
            )
        if axis_name in normalized:
            raise ValueError(
                "direction_auto_priority must not contain duplicate axes"
            )
        normalized.append(axis_name)
    if len(normalized) != len(_DIRECTION_AXES):
        raise ValueError(
            "direction_auto_priority must contain X, Y, and Z exactly once"
        )
    return tuple(normalized)


def _direction_axis_order(
    axis: str,
    auto_priority: Any = _DEFAULT_DIRECTION_AUTO_PRIORITY,
) -> Tuple[str, ...]:
    """Return the explicit axis or the configured automatic priority."""

    normalized = str(axis).upper()
    if normalized == "AUTO":
        return _normalize_direction_auto_priority(auto_priority)
    if normalized not in _DIRECTION_AXES:
        raise ValueError("Unsupported UV direction axis {}".format(axis))
    return (normalized,)


def _direction_point(obj: Any, point: Vector, space: str) -> Vector:
    if str(space).upper() == "WORLD":
        return _world_point(obj, point)
    return point.copy()


def _geometry_island_normal(
    obj: Any,
    mesh: Any,
    face_indices: Sequence[int],
    space: str,
) -> Vector:
    """Return an area-weighted island normal in the requested direction space.

    ``IslandRecord.average_normal`` is retained for legacy reports and is
    always evaluated with the object's world normal matrix.  AUTO direction
    coherence, however, must compare the normal to the same axis basis used
    by the derivative solver.  Recompute it here so OBJECT-space contracts do
    not accidentally inherit a rotated object's world orientation.
    """

    normal_matrix = _normal_matrix(obj) if str(space).upper() == "WORLD" else None
    weighted = Vector((0.0, 0.0, 0.0))
    total_weight = 0.0
    for face_index in face_indices:
        try:
            polygon = mesh.polygons[int(face_index)]
            normal = polygon.normal.copy()
            if normal_matrix is not None:
                normal = normal_matrix @ normal
            if normal.length_squared <= _EPSILON:
                continue
            normal.normalize()
            weight = max(float(polygon.area), _EPSILON)
        except (AttributeError, IndexError, TypeError, ValueError, RuntimeError):
            continue
        weighted += normal * weight
        total_weight += weight
    if weighted.length_squared <= _EPSILON:
        return Vector((0.0, 0.0, 0.0))
    weighted.normalize()
    return weighted


def _geometry_axis_tangent_projection(
    normal: Vector,
    axis_name: str,
) -> float:
    """Return the unit model-axis magnitude that lies in a tangent plane."""

    axis = _DIRECTION_AXES.get(str(axis_name).upper())
    if axis is None or normal.length_squared <= _EPSILON:
        return 0.0 if axis is not None else 0.0
    try:
        normalized = normal.normalized()
        parallel = float(normalized.dot(axis))
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return 0.0
    if not math.isfinite(parallel):
        return 0.0
    return _clamp(math.sqrt(max(0.0, 1.0 - parallel * parallel)), 0.0, 1.0)


def _unsigned_normal_alignment(left: Vector, right: Vector) -> float:
    """Return the orientation-invariant alignment of two normals."""

    if (
        left is None
        or right is None
        or left.length_squared <= _EPSILON
        or right.length_squared <= _EPSILON
    ):
        return -1.0
    try:
        return _clamp(abs(float(left.normalized().dot(right.normalized()))), 0.0, 1.0)
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return -1.0


def _geometry_normal_domains(
    normals: Mapping[int, Vector],
    *,
    max_angle: float = _GEOMETRY_AXIS_CONSENSUS_NORMAL_ANGLE,
) -> Tuple[Tuple[int, ...], ...]:
    """Cluster island normals into deterministic unsigned normal domains.

    Opposite-facing sides of a repeated hard-surface part belong to the same
    domain because the direction contract is expressed in the positive object
    axis, not in the face-normal sign.  A greedy representative update keeps
    this O(n * domain_count) for dense meshes and makes the result independent
    of hash/set iteration order.
    """

    try:
        angle = float(max_angle)
    except (TypeError, ValueError):
        angle = _GEOMETRY_AXIS_CONSENSUS_NORMAL_ANGLE
    if not math.isfinite(angle):
        angle = _GEOMETRY_AXIS_CONSENSUS_NORMAL_ANGLE
    angle = _clamp(angle, 0.0, math.pi * 0.5)
    cosine_threshold = math.cos(angle)
    domains: List[List[int]] = []
    representatives: List[Vector] = []
    for island_id in sorted(int(value) for value in normals):
        normal = normals[island_id]
        if normal is None or normal.length_squared <= _EPSILON:
            continue
        try:
            normalized = normal.normalized()
        except (AttributeError, ValueError, ZeroDivisionError):
            continue
        best_index = None
        best_alignment = cosine_threshold
        for index, representative in enumerate(representatives):
            alignment = _unsigned_normal_alignment(normalized, representative)
            if alignment + _EPSILON < best_alignment:
                continue
            if (
                best_index is None
                or alignment > best_alignment + _EPSILON
                or index < best_index
            ):
                best_index = index
                best_alignment = alignment
        if best_index is None:
            domains.append([island_id])
            representatives.append(normalized.copy())
            continue
        domains[best_index].append(island_id)
        representative = representatives[best_index]
        # Align opposite normals before averaging so the representative stays
        # on one unsigned hemisphere and does not collapse toward zero.
        aligned = normalized if representative.dot(normalized) >= 0.0 else -normalized
        updated = representative + aligned
        if updated.length_squared > _EPSILON:
            updated.normalize()
            representatives[best_index] = updated
    return tuple(tuple(domain) for domain in domains)


def _geometry_derivative_records(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    space: str,
) -> Tuple[Vector, Vector, List[Mapping[str, Any]]]:
    """Return aggregate and per-triangle Blender-style UV Jacobians.

    This is the Object Mode equivalent of Blender's
    ``find_rotation_geometry``.  Keeping the math here avoids relying on the
    Align Rotation operator, which is unavailable in Blender 3.3.
    """

    sum_u = Vector((0.0, 0.0, 0.0))
    sum_v = Vector((0.0, 0.0, 0.0))
    records: List[Mapping[str, Any]] = []
    for face_index in face_indices:
        loop_indices = list(mesh.polygons[int(face_index)].loop_indices)
        if len(loop_indices) < 3:
            continue
        root_loop = int(loop_indices[0])
        root_uv = uv_layer.data[root_loop].uv
        root_point = _direction_point(
            obj,
            mesh.vertices[int(mesh.loops[root_loop].vertex_index)].co,
            space,
        )
        for fan in range(2, len(loop_indices)):
            first_loop = int(loop_indices[fan - 1])
            second_loop = int(loop_indices[fan])
            delta_uv0 = uv_layer.data[first_loop].uv - root_uv
            delta_uv1 = uv_layer.data[second_loop].uv - root_uv
            determinant = (
                float(delta_uv0.x) * float(delta_uv1.y)
                - float(delta_uv0.y) * float(delta_uv1.x)
            )
            uv_product = math.sqrt(
                max(float(delta_uv0.length_squared), 0.0)
                * max(float(delta_uv1.length_squared), 0.0)
            )
            delta_co0 = _direction_point(
                obj,
                mesh.vertices[int(mesh.loops[first_loop].vertex_index)].co,
                space,
            ) - root_point
            delta_co1 = _direction_point(
                obj,
                mesh.vertices[int(mesh.loops[second_loop].vertex_index)].co,
                space,
            ) - root_point
            geometry_cross = delta_co0.cross(delta_co1)
            weight = float(geometry_cross.length)
            geometry_product = math.sqrt(
                max(float(delta_co0.length_squared), 0.0)
                * max(float(delta_co1.length_squared), 0.0)
            )
            if (
                not math.isfinite(determinant)
                or not math.isfinite(uv_product)
                or uv_product <= 0.0
                or abs(determinant)
                <= _GEOMETRY_JACOBIAN_RELATIVE_EPSILON * uv_product
                or not math.isfinite(weight)
                or not math.isfinite(geometry_product)
                or geometry_product <= 0.0
                or weight
                <= _GEOMETRY_JACOBIAN_RELATIVE_EPSILON * geometry_product
            ):
                continue
            inverse = 1.0 / determinant
            derivative_u = (
                delta_co0 * float(delta_uv1.y)
                - delta_co1 * float(delta_uv0.y)
            ) * inverse
            derivative_v = (
                delta_co1 * float(delta_uv0.x)
                - delta_co0 * float(delta_uv1.x)
            ) * inverse
            basis = math.hypot(
                float(derivative_u.length), float(derivative_v.length)
            )
            if (
                not math.isfinite(basis)
                or basis <= _EPSILON
                or not all(
                    math.isfinite(float(value))
                    for value in tuple(derivative_u) + tuple(derivative_v)
                )
            ):
                continue
            sum_u += derivative_u * weight
            sum_v += derivative_v * weight
            records.append({
                "face_index": int(face_index),
                "fan_index": int(fan),
                "area_weight": weight,
                "derivative_u": derivative_u,
                "derivative_v": derivative_v,
                "basis": basis,
                # The normal comes from model-space geometry, not from the
                # UV Jacobian.  This keeps parity diagnostics meaningful when
                # a chart is mirrored in UV space.
                "normal": geometry_cross / max(weight, _EPSILON),
            })
    return sum_u, sum_v, records


def _geometry_derivative_sums(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    space: str,
) -> Tuple[Vector, Vector, int]:
    """Compatibility wrapper returning only aggregate derivative sums."""

    sum_u, sum_v, records = _geometry_derivative_records(
        obj, mesh, uv_layer, face_indices, space
    )
    return sum_u, sum_v, len(records)


def _geometry_axis_statistics(
    records: Sequence[Mapping[str, Any]],
    axis_names: Sequence[str],
    energy: float,
    min_projection: float,
) -> Dict[str, Dict[str, float]]:
    """Measure aggregate strength and signed per-triangle coherence."""

    total_area = sum(
        max(float(record.get("area_weight", 0.0)), 0.0)
        for record in records
    )
    aggregate_basis = sum(
        max(float(record.get("area_weight", 0.0)), 0.0)
        * max(float(record.get("basis", 0.0)), 0.0)
        for record in records
    )
    statistics: Dict[str, Dict[str, float]] = {}
    for axis_name in axis_names:
        axis_vector = _DIRECTION_AXES.get(axis_name)
        if axis_vector is None:
            continue
        sum_u = 0.0
        sum_v = 0.0
        stable_count = 0
        signal_sum = 0.0
        circular_sin = 0.0
        circular_cos = 0.0
        circular_weight = 0.0
        for record in records:
            derivative_u = record["derivative_u"]
            derivative_v = record["derivative_v"]
            component_u = float(derivative_u.dot(axis_vector))
            component_v = float(derivative_v.dot(axis_vector))
            signal = math.hypot(component_u, component_v)
            basis = max(float(record.get("basis", 0.0)), _EPSILON)
            local_signal = _clamp(signal / basis, 0.0, 1.0)
            weight = max(float(record.get("area_weight", 0.0)), 0.0)
            sum_u += component_u * weight
            sum_v += component_v * weight
            signal_sum += local_signal * weight
            if local_signal + _EPSILON < float(min_projection):
                continue
            stable_count += 1
            angle = math.atan2(component_u, component_v)
            contribution = weight * max(local_signal, float(min_projection))
            circular_weight += contribution
            circular_sin += contribution * math.sin(angle)
            circular_cos += contribution * math.cos(angle)
        strength = math.hypot(sum_u, sum_v)
        concentration = (
            _clamp(
                math.hypot(circular_sin, circular_cos) / circular_weight,
                0.0,
                1.0,
            )
            if circular_weight > _EPSILON else 0.0
        )
        effective_fraction = stable_count / max(len(records), 1)
        statistics[axis_name] = {
            "sum_u": sum_u,
            "sum_v": sum_v,
            "strength": strength,
            "confidence": _clamp(strength / max(energy, _EPSILON), 0.0, 1.0),
            "normalized_strength": strength / max(aggregate_basis, _EPSILON),
            "mean_local_signal": signal_sum / max(total_area, _EPSILON),
            "concentration": concentration,
            "effective_fraction": effective_fraction,
            "stability_score": concentration * effective_fraction,
        }
    return statistics


def _select_auto_geometry_axis(
    axis_names: Sequence[str],
    statistics: Mapping[str, Mapping[str, float]],
    min_projection: float,
    cardinal_scores: Optional[Mapping[str, Optional[float]]] = None,
    cardinal_bias: float = 0.0,
) -> Optional[str]:
    """Choose an AUTO axis using stability, with an optional cardinal bias.

    The legacy resolver is intentionally unchanged when ``cardinal_bias`` is
    zero.  Hard-surface callers can opt in to the extra term: every candidate
    still has to pass the same tangent projection gate, and the cardinal term
    only chooses among those valid candidates.  This avoids the unsafe
    arbitrary post-pack rotations that used to break the signed +V contract.
    """

    eligible = [
        axis_name for axis_name in axis_names
        if axis_name in statistics
        and float(statistics[axis_name].get("confidence", 0.0)) + _EPSILON
        >= float(min_projection)
    ]
    if not eligible:
        return None

    try:
        bias = _clamp(float(cardinal_bias), 0.0, 1.0)
    except (TypeError, ValueError):
        bias = 0.0
    if bias > _EPSILON and cardinal_scores:
        scored = [
            axis_name for axis_name in eligible
            if cardinal_scores.get(axis_name) is not None
        ]
        if scored:
            priority = {axis_name: index for index, axis_name in enumerate(axis_names)}

            def score(axis_name: str) -> Tuple[float, float, float, int]:
                values = statistics[axis_name]
                stability = _clamp(
                    float(values.get("stability_score", 0.0))
                    * float(values.get("normalized_strength", 0.0)),
                    0.0,
                    1.0,
                )
                quality = _clamp(float(cardinal_scores[axis_name]), 0.0, 1.0)
                return (
                    bias * quality + (1.0 - bias) * stability,
                    quality,
                    stability,
                    -priority.get(axis_name, len(axis_names)),
                )

            return max(scored, key=score)

    def coherent(axis_name: str) -> bool:
        values = statistics[axis_name]
        return bool(
            float(values.get("concentration", 0.0)) + _EPSILON
            >= _GEOMETRY_AXIS_MIN_COHERENCE
            and float(values.get("effective_fraction", 0.0)) + _EPSILON
            >= _GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION
        )

    if coherent(eligible[0]):
        return eligible[0]
    coherent_axes = [axis_name for axis_name in eligible if coherent(axis_name)]
    pool = coherent_axes or eligible
    priority = {axis_name: index for index, axis_name in enumerate(axis_names)}
    return max(
        pool,
        key=lambda axis_name: (
            float(statistics[axis_name].get("stability_score", 0.0))
            * float(statistics[axis_name].get("normalized_strength", 0.0)),
            float(statistics[axis_name].get("normalized_strength", 0.0)),
            -priority.get(axis_name, len(axis_names)),
        ),
    )


def _geometry_direction_record(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    *,
    space: str = "OBJECT",
    axis: str = "AUTO",
    min_projection: float = 0.35,
    fixed_axis_name: Optional[str] = None,
    auto_priority: Any = _DEFAULT_DIRECTION_AUTO_PRIORITY,
    cardinal_bias: float = 0.0,
    cardinal_min_confidence: float = 0.15,
) -> Tuple[Optional[str], Vector, float, float]:
    """Resolve a positive 3D axis and its directed UV correction.

    The returned vector stores the selected model-axis components in the
    current UV basis.  ``rotation`` is a full modulo-360 correction that maps
    that positive axis to UV +V.  Confidence is normalized against the whole
    derivative basis, making the automatic fallback independent of UV scale.
    """

    normalized_space = str(space).upper()
    sum_u, sum_v, records = _geometry_derivative_records(
        obj, mesh, uv_layer, face_indices, normalized_space
    )
    energy = math.sqrt(
        max(float(sum_u.length_squared + sum_v.length_squared), 0.0)
    )
    if not records or not math.isfinite(energy) or energy <= _EPSILON:
        return None, Vector((0.0, 0.0)), 0.0, 0.0

    if fixed_axis_name is not None:
        axis_names = (str(fixed_axis_name).upper(),)
    else:
        axis_names = _direction_axis_order(axis, auto_priority)
    statistics = _geometry_axis_statistics(
        records, axis_names, energy, float(min_projection)
    )
    if not statistics:
        return None, Vector((0.0, 0.0)), 0.0, 0.0

    if fixed_axis_name is not None or str(axis).upper() != "AUTO":
        axis_name = axis_names[0]
        values = statistics.get(axis_name, {})
        confidence = float(values.get("confidence", 0.0))
        if confidence + _EPSILON < float(min_projection):
            return None, Vector((0.0, 0.0)), 0.0, confidence
    else:
        cardinal_scores = None
        try:
            configured_bias = _clamp(float(cardinal_bias), 0.0, 1.0)
        except (TypeError, ValueError):
            configured_bias = 0.0
        if configured_bias > _EPSILON:
            try:
                configured_min_confidence = _clamp(
                    float(cardinal_min_confidence), 0.0, 1.0
                )
            except (TypeError, ValueError):
                configured_min_confidence = 0.15
            reference, reference_confidence = _uv_cardinal_reference(
                mesh,
                uv_layer,
                face_indices,
                min_edge_confidence=configured_min_confidence,
            )
            cardinal_scores = {
                candidate_axis: _cardinal_axis_quality(
                    reference,
                    math.atan2(
                        float(statistics[candidate_axis].get("sum_u", 0.0)),
                        float(statistics[candidate_axis].get("sum_v", 0.0)),
                    ),
                    reference_confidence,
                    configured_min_confidence,
                )
                for candidate_axis in axis_names
                if candidate_axis in statistics
            }
        axis_name = _select_auto_geometry_axis(
            axis_names,
            statistics,
            float(min_projection),
            cardinal_scores=cardinal_scores,
            cardinal_bias=configured_bias,
        )
        if axis_name is None:
            strongest = max(
                (
                    float(values.get("confidence", 0.0))
                    for values in statistics.values()
                ),
                default=0.0,
            )
            return None, Vector((0.0, 0.0)), 0.0, strongest
        values = statistics[axis_name]
        confidence = float(values.get("confidence", 0.0))

    values = statistics[axis_name]
    component_u = float(values.get("sum_u", 0.0))
    component_v = float(values.get("sum_v", 0.0))
    direction = Vector((component_u, component_v))
    if direction.length_squared > _EPSILON:
        direction.normalize()
    rotation = _angle_wrap(math.atan2(component_u, component_v))
    return axis_name, direction, rotation, confidence


def _frame_vector_angle(vector: Vector) -> float:
    """Return a frame-vector angle measured from UV +V."""

    return _angle_wrap(math.atan2(float(vector.x), float(vector.y)))


def _weighted_frame_angle_median(
    samples: Sequence[Mapping[str, Any]],
    key: str,
) -> Optional[float]:
    """Return a deterministic weighted circular median for frame vectors."""

    angles: List[Tuple[float, float, int]] = []
    for index, sample in enumerate(samples):
        vector = sample.get(key)
        try:
            weight = float(sample.get("weight", 0.0))
            angle = _frame_vector_angle(vector)
        except (AttributeError, TypeError, ValueError):
            continue
        if (
            not math.isfinite(angle)
            or not math.isfinite(weight)
            or weight <= _EPSILON
        ):
            continue
        angles.append((angle, weight, index))
    if not angles:
        return None

    # Unwrap around a weighted circular mean before taking the ordinary
    # weighted median.  This avoids the +/-pi seam splitting an otherwise
    # tight cluster and remains deterministic for a bimodal chart.
    mean_x = sum(math.sin(angle) * weight for angle, weight, _ in angles)
    mean_y = sum(math.cos(angle) * weight for angle, weight, _ in angles)
    if math.hypot(mean_x, mean_y) > _EPSILON:
        reference = _angle_wrap(math.atan2(mean_x, mean_y))
    else:
        reference = min(
            angles,
            key=lambda item: (-item[1], item[2]),
        )[0]
    unwrapped = sorted(
        (
            _angle_wrap(angle - reference),
            weight,
            index,
        )
        for angle, weight, index in angles
    )
    total_weight = sum(item[1] for item in unwrapped)
    threshold = total_weight * 0.5
    accumulated = 0.0
    selected_delta = unwrapped[-1][0]
    for delta, weight, _index in unwrapped:
        accumulated += weight
        if accumulated + _EPSILON >= threshold:
            selected_delta = delta
            break
    return _angle_wrap(reference + selected_delta)


def _robust_frame_sample_subset(
    samples: Sequence[Mapping[str, Any]],
) -> Tuple[List[Mapping[str, Any]], float, bool]:
    """Select a dominant, internally orthogonal local-frame mode.

    The returned fraction is measured against the original weighted sample
    total.  ``False`` means the chart is too multimodal to justify trimming;
    callers should keep the raw aggregate but lower its confidence so the
    strict single-axis path remains in charge.
    """

    if len(samples) < 3:
        total = sum(
            max(float(sample.get("weight", 0.0)), 0.0)
            for sample in samples
        )
        return list(samples), 1.0 if total > _EPSILON else 0.0, True
    total_weight = sum(
        max(float(sample.get("weight", 0.0)), 0.0)
        for sample in samples
    )
    if total_weight <= _EPSILON:
        return [], 0.0, False

    positive_weight = sum(
        max(float(sample.get("weight", 0.0)), 0.0)
        for sample in samples
        if int(sample.get("parity", 0)) > 0
    )
    negative_weight = sum(
        max(float(sample.get("weight", 0.0)), 0.0)
        for sample in samples
        if int(sample.get("parity", 0)) < 0
    )
    dominant_parity = 1 if positive_weight >= negative_weight else -1
    dominant_weight = max(positive_weight, negative_weight)
    # Do not mix reflected local frames while looking for the dominant mode;
    # the parity confidence gate below still evaluates the full chart.
    parity_pool = [
        sample for sample in samples
        if int(sample.get("parity", 0)) == dominant_parity
    ]
    if dominant_weight / max(total_weight, _EPSILON) < _GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE:
        parity_pool = list(samples)
        dominant_parity = 1
    median_v = _weighted_frame_angle_median(parity_pool, "v")
    if median_v is None:
        return list(samples), 0.0, False

    trim_angle = _GEOMETRY_FRAME_ROBUST_TRIM_ANGLE
    inliers = []
    inlier_weight = 0.0
    for sample in parity_pool:
        try:
            v_angle = _frame_vector_angle(sample.get("v"))
            u_angle = _frame_vector_angle(sample.get("u"))
            weight = float(sample.get("weight", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if not math.isfinite(weight) or weight <= _EPSILON:
            continue
        v_error = abs(_angle_wrap(v_angle - median_v))
        expected_u = median_v + (
            math.pi * 0.5 if dominant_parity > 0 else -math.pi * 0.5
        )
        # The local U/V pair must remain orthogonal with the same handedness;
        # this second gate rejects a shear mode that happens to share V.
        pair_error = abs(_angle_wrap(u_angle - v_angle - (
            math.pi * 0.5 if int(sample.get("parity", 0)) > 0
            else -math.pi * 0.5
        )))
        expected_error = abs(_angle_wrap(u_angle - expected_u))
        if (
            v_error <= trim_angle + _EPSILON
            and pair_error <= trim_angle + _EPSILON
            and expected_error <= trim_angle + _EPSILON
        ):
            inliers.append(sample)
            inlier_weight += weight
    fraction = inlier_weight / max(total_weight, _EPSILON)
    if (
        not inliers
        or fraction + _EPSILON < _GEOMETRY_FRAME_MIN_INLIER_WEIGHT
    ):
        return list(samples), fraction, False
    return inliers, fraction, True


def _geometry_frame_record(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    face_indices: Sequence[int],
    *,
    space: str = "OBJECT",
    axis_name: Optional[str] = None,
    axis: str = "AUTO",
    min_projection: float = _GEOMETRY_AXIS_RELATIVE_EPSILON,
    auto_priority: Any = _DEFAULT_DIRECTION_AUTO_PRIORITY,
) -> Mapping[str, Any]:
    """Resolve the complete signed tangent frame for one UV island.

    ``_geometry_direction_record`` deliberately resolves only one model axis.
    A checker/label also needs the perpendicular tangent and its sign.  Each
    valid UV Jacobian is therefore inverted as a small least-squares system:
    the selected model axis is projected into the local face tangent plane,
    ``U = V x N`` is constructed with the model winding, and both vectors are
    mapped back into UV coordinates.  Area-weighted circular means provide a
    stable island frame while concentration/parity gates keep curved or
    mirrored charts from being silently rotated as if they were planar.

    The return value is a plain mapping so this helper remains usable by
    Blender 3.3 callers and by read-only audit tools.  No UV coordinates are
    changed here.
    """

    result: Dict[str, Any] = {
        "axis": None,
        "u_vector": Vector((0.0, 0.0)),
        "v_vector": Vector((0.0, 0.0)),
        "parity": 0,
        "parity_confidence": 0.0,
        "confidence": 0.0,
        "rotation": 0.0,
        "residual": None,
        "effective_fraction": 0.0,
        "u_concentration": 0.0,
        "v_concentration": 0.0,
        "records": 0,
        "valid_records": 0,
        "reason": "unresolved_axis",
    }

    selected_axis = None if axis_name is None else str(axis_name).upper()
    if selected_axis is None:
        selected_axis, _direction, _rotation, _confidence = (
            _geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                face_indices,
                space=space,
                axis=axis,
                min_projection=min_projection,
                auto_priority=auto_priority,
            )
        )
    if selected_axis not in _DIRECTION_AXES:
        return result
    result["axis"] = selected_axis

    _sum_u, _sum_v, records = _geometry_derivative_records(
        obj, mesh, uv_layer, face_indices, str(space).upper()
    )
    result["records"] = len(records)
    if not records:
        result["reason"] = "no_jacobian_records"
        return result

    axis_vector = _DIRECTION_AXES[selected_axis]
    frame_samples: List[Mapping[str, Any]] = []
    for record in records:
        derivative_u = record.get("derivative_u")
        derivative_v = record.get("derivative_v")
        if derivative_u is None or derivative_v is None:
            continue
        try:
            derivative_u = derivative_u.copy()
            derivative_v = derivative_v.copy()
        except (AttributeError, TypeError):
            continue
        if not all(
            math.isfinite(float(value))
            for value in tuple(derivative_u) + tuple(derivative_v)
        ):
            continue

        normal = record.get("normal")
        try:
            normal = normal.copy() if normal is not None else None
        except (AttributeError, TypeError):
            normal = None
        # Older light-weight test doubles may not carry the model normal.  A
        # Jacobian normal is a conservative fallback; real Blender records
        # always use the model-space normal added above.
        if normal is None or normal.length_squared <= _EPSILON:
            normal = derivative_u.cross(derivative_v)
        if normal.length_squared <= _EPSILON:
            continue
        normal.normalize()

        tangent_v = axis_vector - normal * float(axis_vector.dot(normal))
        projection = float(tangent_v.length)
        if (
            not math.isfinite(projection)
            or projection + _EPSILON < float(min_projection)
        ):
            continue
        tangent_v /= max(projection, _EPSILON)
        tangent_u = tangent_v.cross(normal)
        if tangent_u.length_squared <= _EPSILON:
            continue
        tangent_u.normalize()

        # Solve J * uv = tangent for uv, where J columns are dP/dU/dV.
        gram_00 = float(derivative_u.dot(derivative_u))
        gram_01 = float(derivative_u.dot(derivative_v))
        gram_11 = float(derivative_v.dot(derivative_v))
        gram_det = gram_00 * gram_11 - gram_01 * gram_01
        gram_scale = max(gram_00 * gram_11, _EPSILON)
        if (
            not math.isfinite(gram_det)
            or gram_det <= _GEOMETRY_JACOBIAN_RELATIVE_EPSILON * gram_scale
        ):
            continue

        def solve(target: Vector) -> Vector:
            rhs_0 = float(derivative_u.dot(target))
            rhs_1 = float(derivative_v.dot(target))
            return Vector((
                (gram_11 * rhs_0 - gram_01 * rhs_1) / gram_det,
                (gram_00 * rhs_1 - gram_01 * rhs_0) / gram_det,
            ))

        uv_u = solve(tangent_u)
        uv_v = solve(tangent_v)
        u_length = float(uv_u.length)
        v_length = float(uv_v.length)
        if (
            not math.isfinite(u_length)
            or not math.isfinite(v_length)
            or u_length <= _EPSILON
            or v_length <= _EPSILON
        ):
            continue
        uv_u /= u_length
        uv_v /= v_length
        determinant = float(uv_u.x * uv_v.y - uv_u.y * uv_v.x)
        if not math.isfinite(determinant) or abs(determinant) <= 1.0e-6:
            continue
        parity = 1.0 if determinant > 0.0 else -1.0
        area_weight = max(float(record.get("area_weight", 0.0)), 0.0)
        # Projection discounts faces where the selected axis is almost the
        # normal.  The bounded anisotropy factor avoids letting an unstable
        # near-zero solved vector dominate tiny bevel triangles.
        anisotropy = min(u_length, v_length) / max(u_length, v_length)
        weight = area_weight * _clamp(projection, 0.0, 1.0) * _clamp(
            anisotropy, 1.0e-3, 1.0
        )
        if not math.isfinite(weight) or weight <= _EPSILON:
            continue
        frame_samples.append({
            "u": uv_u,
            "v": uv_v,
            "parity": int(parity),
            "weight": weight,
        })

    raw_total_weight = sum(
        max(float(sample.get("weight", 0.0)), 0.0)
        for sample in frame_samples
    )
    raw_parity_sum = sum(
        float(sample.get("parity", 0))
        * max(float(sample.get("weight", 0.0)), 0.0)
        for sample in frame_samples
    )
    raw_valid_count = len(frame_samples)
    if raw_total_weight <= _EPSILON or raw_valid_count == 0:
        result["reason"] = "no_frame_signal"
        return result

    robust_samples, inlier_fraction, robust_ready = (
        _robust_frame_sample_subset(frame_samples)
    )
    # A multimodal chart remains diagnosable through the raw vectors, but its
    # robust support is part of frame confidence.  This ensures a curved or
    # sheared island cannot opt into a pseudo-frame merely because its raw
    # circular mean happened to be sharp.
    samples = robust_samples if robust_ready else frame_samples
    used_weight = sum(
        max(float(sample.get("weight", 0.0)), 0.0)
        for sample in samples
    )
    if used_weight <= _EPSILON or not samples:
        result["reason"] = "no_frame_signal"
        return result
    u_sum = sum(
        (
            sample["u"] * max(float(sample.get("weight", 0.0)), 0.0)
            for sample in samples
        ),
        Vector((0.0, 0.0)),
    )
    v_sum = sum(
        (
            sample["v"] * max(float(sample.get("weight", 0.0)), 0.0)
            for sample in samples
        ),
        Vector((0.0, 0.0)),
    )
    parity_sum = raw_parity_sum
    valid_count = len(samples)
    total_weight = used_weight
    result["valid_records"] = valid_count
    result["effective_fraction"] = min(
        valid_count / max(len(records), 1),
        _clamp(inlier_fraction, 0.0, 1.0),
    )
    result["robust_inlier_fraction"] = _clamp(inlier_fraction, 0.0, 1.0)

    u_concentration = _clamp(float(u_sum.length) / total_weight, 0.0, 1.0)
    v_concentration = _clamp(float(v_sum.length) / total_weight, 0.0, 1.0)
    # Parity is a chart-wide winding contract.  Keep the denominator on the
    # untrimmed sample weight so discarding a minority angular mode cannot
    # inflate a mixed/reflected chart into a falsely positive frame.
    parity_confidence = _clamp(
        abs(parity_sum) / max(raw_total_weight, _EPSILON), 0.0, 1.0
    )
    result["u_concentration"] = u_concentration
    result["v_concentration"] = v_concentration
    result["parity_confidence"] = parity_confidence
    result["confidence"] = min(
        u_concentration,
        v_concentration,
        float(result["effective_fraction"]),
        parity_confidence,
    )

    if u_sum.length_squared > _EPSILON:
        u_sum.normalize()
    if v_sum.length_squared > _EPSILON:
        v_sum.normalize()
    result["u_vector"] = u_sum
    result["v_vector"] = v_sum
    if v_sum.length_squared <= _EPSILON or u_sum.length_squared <= _EPSILON:
        result["reason"] = "zero_frame_mean"
        return result

    parity = 0
    if parity_confidence + _EPSILON >= _GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE:
        parity = 1 if parity_sum >= 0.0 else -1
    result["parity"] = parity
    rotation = _angle_wrap(math.atan2(float(v_sum.x), float(v_sum.y)))
    result["rotation"] = rotation
    rotated_u = Vector((
        float(u_sum.x) * math.cos(rotation)
        - float(u_sum.y) * math.sin(rotation),
        float(u_sum.x) * math.sin(rotation)
        + float(u_sum.y) * math.cos(rotation),
    ))
    expected_u_angle = 0.0 if parity >= 0 else math.pi
    residual = abs(_angle_wrap(
        math.atan2(float(rotated_u.y), float(rotated_u.x))
        - expected_u_angle
    ))
    result["residual"] = residual

    if parity == 0:
        result["reason"] = "ambiguous_parity"
    elif parity < 0:
        # A rotation cannot repair a reflection.  Keep the signed diagnosis so
        # callers can downgrade/report it without violating positive winding.
        result["reason"] = "negative_parity_no_reflection"
    elif (
        float(result["confidence"]) + _EPSILON
        < min(
            _GEOMETRY_FRAME_MIN_CONCENTRATION,
            _GEOMETRY_FRAME_MIN_EFFECTIVE_FRACTION,
        )
    ):
        result["reason"] = "low_frame_concentration"
    elif not robust_ready:
        result["reason"] = "low_frame_inlier_support"
    else:
        result["reason"] = None
    return result


def _clear_geometry_frame_metadata(island: IslandRecord, reason: Optional[str]) -> None:
    """Reset complete-frame fields while preserving the single-axis contract."""

    island.geometry_frame_u_vector = Vector((0.0, 0.0))
    island.geometry_frame_v_vector = Vector((0.0, 0.0))
    island.geometry_frame_parity = 0
    island.geometry_frame_parity_confidence = 0.0
    island.geometry_frame_confidence = 0.0
    island.geometry_frame_rotation_angle = 0.0
    island.geometry_frame_residual = None
    island.geometry_frame_downgrade_reason = reason


def _bind_geometry_frame_metadata(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    island: IslandRecord,
    settings: GroupLayoutOptions,
    *,
    axis_name: Optional[str] = None,
) -> Mapping[str, Any]:
    """Compute and attach frame metadata for one island in-place."""

    if not settings.align_geometry_direction or not settings.align_geometry_frame:
        _clear_geometry_frame_metadata(island, "disabled")
        return {"reason": "disabled", "axis": None}
    selected_axis = axis_name or island.geometry_axis_name
    frame = _geometry_frame_record(
        obj,
        mesh,
        uv_layer,
        island.face_indices,
        space=island.geometry_direction_space or settings.direction_space,
        axis_name=selected_axis,
        axis=settings.direction_axis,
        min_projection=settings.direction_axis_min_projection,
        auto_priority=settings.direction_auto_priority,
    )
    island.geometry_frame_u_vector = frame["u_vector"].copy()
    island.geometry_frame_v_vector = frame["v_vector"].copy()
    island.geometry_frame_parity = int(frame.get("parity", 0))
    island.geometry_frame_parity_confidence = float(
        frame.get("parity_confidence", 0.0)
    )
    island.geometry_frame_confidence = float(frame.get("confidence", 0.0))
    island.geometry_frame_rotation_angle = float(frame.get("rotation", 0.0))
    island.geometry_frame_residual = frame.get("residual")
    island.geometry_frame_downgrade_reason = frame.get("reason")
    return frame


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
        dominant_edge_angle, dominant_edge_confidence = (
            _dominant_uv_edge_angle(
                mesh,
                uv_layer,
                face_indices,
                uv_extent,
            )
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
        (
            geometry_axis_name,
            geometry_direction_vector,
            geometry_rotation_angle,
            geometry_direction_confidence,
        ) = _geometry_direction_record(
            obj,
            mesh,
            uv_layer,
            face_indices,
            space=settings.direction_space,
            axis=settings.direction_axis,
            min_projection=settings.direction_axis_min_projection,
            auto_priority=settings.direction_auto_priority,
            cardinal_bias=settings.direction_auto_cardinal_bias,
            cardinal_min_confidence=settings.direction_auto_cardinal_min_confidence,
        )
        if settings.align_geometry_direction and settings.align_geometry_frame:
            geometry_frame = _geometry_frame_record(
                obj,
                mesh,
                uv_layer,
                face_indices,
                space=settings.direction_space,
                axis_name=geometry_axis_name,
                axis=settings.direction_axis,
                min_projection=settings.direction_axis_min_projection,
                auto_priority=settings.direction_auto_priority,
            )
        else:
            geometry_frame = {
                "u_vector": Vector((0.0, 0.0)),
                "v_vector": Vector((0.0, 0.0)),
                "parity": 0,
                "parity_confidence": 0.0,
                "confidence": 0.0,
                "rotation": 0.0,
                "residual": None,
                "reason": "disabled",
            }
        uv_width = max(float(bounds_uv[2] - bounds_uv[0]), 0.0)
        uv_height = max(float(bounds_uv[3] - bounds_uv[1]), 0.0)
        uv_aabb_area = uv_width * uv_height
        uv_polygon_fill = (
            _clamp(uv_area / uv_aabb_area, 0.0, 1.0)
            if uv_aabb_area > _EPSILON else 0.0
        )
        uv_edges = []
        for face_index in face_indices:
            face_loops = list(mesh.polygons[face_index].loop_indices)
            for offset, loop_index in enumerate(face_loops):
                next_loop = face_loops[(offset + 1) % len(face_loops)]
                length = (
                    uv_layer.data[next_loop].uv
                    - uv_layer.data[loop_index].uv
                ).length
                if math.isfinite(float(length)) and length > settings.uv_epsilon:
                    uv_edges.append(float(length))
        uv_short_edge = min(uv_edges) if uv_edges else 0.0
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
            dominant_edge_angle=dominant_edge_angle,
            dominant_edge_confidence=dominant_edge_confidence,
            uv_aabb_area=uv_aabb_area,
            uv_polygon_fill=uv_polygon_fill,
            uv_short_edge=uv_short_edge,
            geometry_axis_name=geometry_axis_name,
            geometry_direction_space=str(settings.direction_space).upper(),
            geometry_direction_vector=geometry_direction_vector,
            geometry_direction_confidence=geometry_direction_confidence,
            geometry_rotation_angle=geometry_rotation_angle,
            geometry_frame_u_vector=geometry_frame["u_vector"],
            geometry_frame_v_vector=geometry_frame["v_vector"],
            geometry_frame_parity=int(geometry_frame.get("parity", 0)),
            geometry_frame_parity_confidence=float(
                geometry_frame.get("parity_confidence", 0.0)
            ),
            geometry_frame_confidence=float(
                geometry_frame.get("confidence", 0.0)
            ),
            geometry_frame_rotation_angle=float(
                geometry_frame.get("rotation", 0.0)
            ),
            geometry_frame_residual=geometry_frame.get("residual"),
            geometry_frame_downgrade_reason=geometry_frame.get("reason"),
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


def _island_model_centroid(island: Any) -> Vector:
    """Return a stable model-space centroid for full and light-weight records.

    ``build_layout_groups`` is part of the public compatibility surface and is
    also used by older smoke callers that provide only ``model_bounds``.  The
    production ``IslandRecord`` carries an area-weighted ``model_centroid``;
    the bounds center is a deterministic, conservative fallback for those
    callers and for malformed records.  It is used only to order attached
    fragments, never to authorize a topology merge.
    """

    value = getattr(island, "model_centroid", None)
    try:
        candidate = Vector(value)
        if len(candidate) >= 3 and all(
            math.isfinite(float(component)) for component in candidate[:3]
        ):
            return Vector((candidate.x, candidate.y, candidate.z))
    except (TypeError, ValueError, AttributeError, IndexError):
        pass
    bounds = getattr(island, "model_bounds", None)
    try:
        values = tuple(float(component) for component in bounds)
    except (TypeError, ValueError):
        values = ()
    if len(values) == 6 and all(math.isfinite(value) for value in values):
        return Vector((
            (values[0] + values[3]) * 0.5,
            (values[1] + values[4]) * 0.5,
            (values[2] + values[5]) * 0.5,
        ))
    return Vector((0.0, 0.0, 0.0))


def _union_model_bounds(
    bounds: Iterable[Sequence[float]],
) -> Tuple[float, float, float, float, float, float]:
    """Return the finite axis-aligned union of model-space bounds."""

    values = []
    for item in bounds:
        try:
            normalized = tuple(float(value) for value in item)
        except (TypeError, ValueError):
            continue
        if len(normalized) != 6 or not all(
            math.isfinite(value) for value in normalized
        ):
            continue
        values.append(normalized)
    if not values:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return (
        min(item[0] for item in values),
        min(item[1] for item in values),
        min(item[2] for item in values),
        max(item[3] for item in values),
        max(item[4] for item in values),
        max(item[5] for item in values),
    )


def _model_bounds_diagonal(bounds: Sequence[float]) -> float:
    return math.sqrt(sum(
        max(float(bounds[index + 3]) - float(bounds[index]), 0.0) ** 2
        for index in range(3)
    ))


def _group_model_bounds(
    group: Mapping[str, Any],
    by_id: Mapping[int, IslandRecord],
) -> Tuple[float, float, float, float, float, float]:
    return _union_model_bounds(
        by_id[member_id].model_bounds
        for member_id in group.get("members", ())
        if member_id in by_id
    )


def _structure_edge_score(
    left: IslandRecord,
    right: IslandRecord,
    edge: IslandAdjacency,
    settings: GroupLayoutOptions,
) -> Optional[float]:
    """Score one topological boundary for conservative macro grouping.

    A layout relationship is useful only when a real mesh boundary carries a
    meaningful amount of the neighboring charts' area.  Material-separated
    seam boundaries remain independent unless both charts share a material;
    they can still be packed near one another by the ordinary repeat logic.
    """

    if edge.edge_count <= 0 or edge.shared_length <= _EPSILON:
        return None
    if (
        edge.seam_edge_count >= edge.edge_count
        and not set(left.material_indices).intersection(right.material_indices)
    ):
        return None
    area_scale = math.sqrt(max(
        min(float(left.area_3d), float(right.area_3d)),
        _EPSILON,
    ))
    contact_ratio = float(edge.shared_length) / area_scale
    if not math.isfinite(contact_ratio) or contact_ratio + _EPSILON < float(
        settings.structure_group_min_contact_ratio
    ):
        return None

    normal_score = 0.5
    if (
        left.average_normal.length_squared > _EPSILON
        and right.average_normal.length_squared > _EPSILON
    ):
        dot = _clamp(
            float(left.average_normal.normalized().dot(
                right.average_normal.normalized()
            )),
            -1.0,
            1.0,
        )
        angle = math.acos(dot)
        if angle > float(settings.structure_group_max_normal_angle) + 1.0e-12:
            return None
        normal_score = 1.0 - angle / math.pi

    same_material = bool(
        set(left.material_indices).intersection(right.material_indices)
    )
    # Contact dominates; normal continuity and material agreement only break
    # ties.  The score is bounded so traversal remains deterministic.
    return float(
        _clamp(contact_ratio, 0.0, 4.0) / 4.0
        + 0.30 * _clamp(normal_score, 0.0, 1.0)
        + (0.10 if same_material else 0.0)
        - (0.05 if edge.seam_edge_count else 0.0)
    )


def _soft_structure_edge_score(
    left: IslandRecord,
    right: IslandRecord,
    edge: IslandAdjacency,
    settings: GroupLayoutOptions,
) -> Optional[float]:
    """Return a conservative score for a rejected real mesh boundary.

    The normal structure score intentionally rejects all-seam/material-
    separated boundaries and contacts below the artist-facing threshold.  A
    boundary that failed those policy gates can still be a useful *layout*
    hint for a hard-surface part.  This fallback therefore relaxes only the
    contact/material/seam terms, keeps a generous normal guard, and ignores
    micro-islands whose owner assignment already handles locality.  Callers
    must apply the separate block/component envelope before consuming the
    score.
    """

    if (
        left.is_small
        or right.is_small
        or edge.edge_count <= 0
        or edge.shared_length <= _EPSILON
    ):
        return None
    area_scale = math.sqrt(max(
        min(float(left.area_3d), float(right.area_3d)),
        _EPSILON,
    ))
    contact_ratio = float(edge.shared_length) / area_scale
    if not math.isfinite(contact_ratio):
        return None
    # Keep the fallback meaningful for true mesh contacts while allowing it
    # to recover the low-contact bevel/panel boundaries that the strict pass
    # intentionally leaves independent.  A caller that lowers the strict
    # threshold also gets a proportionally lower soft floor, but it can never
    # admit numerical noise.
    strict_floor = float(getattr(
        settings, "structure_group_min_contact_ratio", 0.12
    ))
    if not math.isfinite(strict_floor):
        strict_floor = 0.12
    soft_floor = max(
        _CONTINUITY_SOFT_MIN_CONTACT_RATIO,
        min(0.08, max(strict_floor, 0.0) * 0.5),
    )
    if contact_ratio + _EPSILON < soft_floor:
        return None

    normal_score = 0.5
    if (
        left.average_normal.length_squared > _EPSILON
        and right.average_normal.length_squared > _EPSILON
    ):
        dot = _clamp(
            float(left.average_normal.normalized().dot(
                right.average_normal.normalized()
            )),
            -1.0,
            1.0,
        )
        angle = math.acos(dot)
        # A soft link may bridge a deliberate hard-surface seam, but nearly
        # opposite normals are more likely to be unrelated facing surfaces.
        normal_limit = min(
            _CONTINUITY_SOFT_MAX_NORMAL_ANGLE,
            max(
                math.radians(130.0),
                float(getattr(
                    settings, "structure_group_max_normal_angle",
                    math.radians(100.0),
                )),
            ),
        )
        if angle > normal_limit + 1.0e-12:
            return None
        normal_score = 1.0 - angle / math.pi

    same_material = bool(
        set(left.material_indices).intersection(right.material_indices)
    )
    relative_contact = float(edge.shared_length) / math.sqrt(max(
        float(left.area_3d) + float(right.area_3d),
        _EPSILON,
    ))
    if not math.isfinite(relative_contact):
        relative_contact = 0.0
    seam_fraction = _clamp(
        float(edge.seam_edge_count) / max(float(edge.edge_count), 1.0),
        0.0,
        1.0,
    )
    # Contact and normal agreement dominate.  Seam/material penalties keep
    # these candidates below accepted structure edges without making a real
    # all-seam boundary disappear entirely.
    score = (
        0.62 * _clamp(contact_ratio / 4.0, 0.0, 1.0)
        + 0.24 * _clamp(normal_score, 0.0, 1.0)
        + 0.14 * _clamp(relative_contact, 0.0, 1.0)
        - 0.06 * seam_fraction
        - (0.04 if not same_material else 0.0)
    )
    if not math.isfinite(score) or score <= _EPSILON:
        return None
    return float(score)


def _merge_structure_groups(
    groups: Sequence[Dict[str, Any]],
    islands: Sequence[IslandRecord],
    adjacency: Sequence[IslandAdjacency],
    object_diagonal: float,
    settings: GroupLayoutOptions,
) -> List[Dict[str, Any]]:
    """Merge bounded topological macro charts without welding UV islands.

    The previous layout stage made every non-small island a separate packing
    rectangle.  Here only strong mesh-boundary contacts are merged, with a
    member/diameter/degree budget.  This keeps a panel, its bevel band, and a
    neighboring inset together while preventing one connected weapon mesh from
    becoming a single atlas block.
    """

    groups = [dict(group) for group in groups]
    if (
        not settings.structure_group_enabled
        or not groups
        or not adjacency
    ):
        return groups
    by_id = {island.island_id: island for island in islands}
    member_group = {
        member_id: group_index
        for group_index, group in enumerate(groups)
        for member_id in group.get("members", ())
    }
    parent = list(range(len(groups)))
    component_members = [
        set(group.get("members", ())) for group in groups
    ]
    component_affinity = [
        {
            tuple(sorted((int(pair[0]), int(pair[1]))))
            for pair in group.get("affinity_pairs", ())
            if len(pair) == 2 and int(pair[0]) != int(pair[1])
        }
        for group in groups
    ]
    endpoint_degree: Dict[int, int] = defaultdict(int)
    max_diameter = max(float(object_diagonal), _EPSILON) * float(
        settings.structure_group_max_diameter_ratio
    )

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    candidates = []
    for edge in adjacency:
        left_id = int(edge.left_id)
        right_id = int(edge.right_id)
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        if left is None or right is None or left.is_small or right.is_small:
            continue
        left_group = member_group.get(left_id)
        right_group = member_group.get(right_id)
        if left_group is None or right_group is None:
            continue
        score = _structure_edge_score(left, right, edge, settings)
        if score is None:
            continue
        candidates.append((
            -score,
            min(left_id, right_id),
            max(left_id, right_id),
            left_id,
            right_id,
            left_group,
            right_group,
        ))
    candidates.sort()

    for (
        _negative_score,
        _left_sort,
        _right_sort,
        left_id,
        right_id,
        left_group,
        right_group,
    ) in candidates:
        if endpoint_degree[left_id] >= settings.structure_group_max_degree:
            continue
        if endpoint_degree[right_id] >= settings.structure_group_max_degree:
            continue
        left_root = find(left_group)
        right_root = find(right_group)
        if left_root == right_root:
            component_affinity[left_root].add(
                tuple(sorted((int(left_id), int(right_id))))
            )
            endpoint_degree[left_id] += 1
            endpoint_degree[right_id] += 1
            continue
        merged_members = component_members[left_root] | component_members[right_root]
        if len(merged_members) > settings.structure_group_max_members:
            continue
        merged_bounds = _union_model_bounds(
            by_id[member_id].model_bounds
            for member_id in merged_members
            if member_id in by_id
        )
        if _model_bounds_diagonal(merged_bounds) > max_diameter + _EPSILON:
            continue
        # Union by the stable group index; this makes the output independent of
        # dictionary/set iteration order while retaining all member metadata.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        component_members[left_root].update(component_members[right_root])
        component_members[right_root].clear()
        component_affinity[left_root].update(component_affinity[right_root])
        component_affinity[left_root].add(
            tuple(sorted((int(left_id), int(right_id))))
        )
        component_affinity[right_root].clear()
        endpoint_degree[left_id] += 1
        endpoint_degree[right_id] += 1

    components: Dict[int, List[int]] = defaultdict(list)
    for group_index in range(len(groups)):
        components[find(group_index)].append(group_index)
    if all(len(indices) == 1 for indices in components.values()):
        result = []
        for group_index, group in enumerate(groups):
            item = dict(group)
            affinities = set(component_affinity[find(group_index)])
            affinities.update(
                tuple(sorted((int(pair[0]), int(pair[1]))))
                for pair in item.get("affinity_pairs", ())
                if len(pair) == 2 and int(pair[0]) != int(pair[1])
            )
            item["affinity_pairs"] = sorted(affinities)
            result.append(item)
        return result

    merged_groups: List[Dict[str, Any]] = []
    repeat_reasons = {"MIRROR", "ROTATIONAL", "REPEATED", "TRANSLATED"}
    for _root, indices in sorted(
        components.items(), key=lambda item: min(item[1])
    ):
        if len(indices) == 1:
            merged_groups.append(groups[indices[0]])
            continue
        members: List[int] = []
        anchors: List[int] = []
        small: List[int] = []
        small_anchors: List[int] = []
        reasons = set()
        for group_index in sorted(indices):
            group = groups[group_index]
            reasons.add(str(group.get("reason", "")))
            for member in group.get("members", ()):
                if member not in members:
                    members.append(member)
            for anchor in group.get("anchors", ()):
                if anchor not in anchors:
                    anchors.append(anchor)
            for member, anchor in zip(
                group.get("small", ()), group.get("small_anchors", ())
            ):
                if member not in small:
                    small.append(member)
                    small_anchors.append(anchor)
        preserved_repeat = sorted(reasons.intersection(repeat_reasons))
        reason = preserved_repeat[0] if preserved_repeat else "MACRO_ADJACENT"
        merged_groups.append({
            "members": members,
            "anchors": anchors,
            "small": small,
            "small_anchors": small_anchors,
            "reason": reason,
            "affinity_pairs": sorted(component_affinity[find(_root)]),
        })
    return merged_groups


def _merge_small_repeat_owner_groups(
    groups: Sequence[Dict[str, Any]],
    repeat_groups: Sequence[RepeatGroup],
    by_id: Mapping[int, IslandRecord],
    settings: GroupLayoutOptions,
    object_diagonal: float = 0.0,
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
    member_count = [len(group.get("members", ())) for group in groups]
    component_group_indices = [{index} for index in range(len(groups))]
    max_members = max(
        int(settings.repeat_group_max_members),
        int(settings.max_repeat_group_size),
    )
    max_diameter = max(float(object_diagonal), _EPSILON) * float(
        settings.repeat_group_max_diameter_ratio
    )

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
        combined_members = sum(member_count[root] for root in roots)
        if combined_members > max_members:
            continue
        combined_group_indices = set().union(
            *(component_group_indices[root] for root in roots)
        )
        combined_bounds = _union_model_bounds(
            by_id[member_id].model_bounds
            for group_index in combined_group_indices
            for member_id in groups[group_index].get("members", ())
            if member_id in by_id
        )
        # A direct repeat cohort is an intentional semantic relationship and
        # may span mirrored or radial locations on the asset.  Apply the model
        # diameter limit only when a later cohort would merge an already
        # combined component transitively; this preserves direct repeated
        # structures while preventing one chain of micro-parts from absorbing
        # the whole weapon.
        transitive_merge = any(
            len(component_group_indices[root]) > 1 for root in roots
        )
        if (
            transitive_merge
            and _model_bounds_diagonal(combined_bounds)
            > max_diameter + _EPSILON
        ):
            # A repeated micro-part is only a useful layout cohort while its
            # owner cells remain in the same local structure.  Leave distant
            # owners as separate groups; the repeat relation still records
            # their common orientation for the independent direction pass.
            continue
        target = roots[0]
        for root in roots[1:]:
            parent[root] = target
            anchor_count[target] += anchor_count[root]
            member_count[target] += member_count[root]
            component_group_indices[target].update(
                component_group_indices[root]
            )
            component_group_indices[root].clear()

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
        affinity_pairs = set()
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
            affinity_pairs.update(
                tuple(sorted((int(pair[0]), int(pair[1]))))
                for pair in group.get("affinity_pairs", ())
                if len(pair) == 2 and int(pair[0]) != int(pair[1])
            )
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
            "affinity_pairs": sorted(affinity_pairs),
        })
    return merged_groups


def _merge_topology_continuity_groups(
    groups: Sequence[Dict[str, Any]],
    islands: Sequence[IslandRecord],
    adjacency: Sequence[IslandAdjacency],
    object_diagonal: float,
    settings: GroupLayoutOptions,
) -> List[Dict[str, Any]]:
    """Keep real topology chains together after small-owner assignment.

    ``build_layout_groups`` deliberately attaches every small chart to one
    model-space owner.  A chain of bevel/inset charts can nevertheless cross
    owners, so the old pre-assignment structure pass could not express that
    continuity.  This pass translates each real mesh adjacency into an
    owner-level affinity (the packer only accepts anchor ids), then performs
    a conservative cross-group merge when the resulting macro chart remains
    bounded.  No mesh edge, seam, or UV island is changed here.
    """

    groups = [dict(group) for group in groups]
    if (
        not settings.structure_group_enabled
        or not groups
        or not adjacency
    ):
        return groups

    by_id = {int(island.island_id): island for island in islands}
    member_group = {
        int(member_id): group_index
        for group_index, group in enumerate(groups)
        for member_id in group.get("members", ())
    }
    member_owner: Dict[int, int] = {}
    for group in groups:
        anchors = tuple(int(anchor) for anchor in group.get("anchors", ()))
        anchor_set = set(anchors)
        for anchor in anchors:
            member_owner[anchor] = anchor
        for member, anchor in zip(
            group.get("small", ()), group.get("small_anchors", ())
        ):
            member_owner[int(member)] = int(anchor)
        # A defensive fallback keeps malformed caller-provided records from
        # creating an affinity that references a non-anchor id.
        for member in group.get("members", ()):
            member = int(member)
            if member not in member_owner:
                member_owner[member] = member if member in anchor_set else member

    parent = list(range(len(groups)))
    component_members = [
        set(int(member) for member in group.get("members", ()))
        for group in groups
    ]
    component_anchors = [
        set(int(anchor) for anchor in group.get("anchors", ()))
        for group in groups
    ]
    component_affinity = []
    endpoint_degree: Dict[int, int] = defaultdict(int)
    for group in groups:
        affinities = set()
        for pair in group.get("affinity_pairs", ()):
            if len(pair) != 2:
                continue
            left, right = sorted((int(pair[0]), int(pair[1])))
            if left == right:
                continue
            affinities.add((left, right))
        component_affinity.append(affinities)
        for left, right in affinities:
            endpoint_degree[left] += 1
            endpoint_degree[right] += 1

    max_diameter = max(float(object_diagonal), _EPSILON) * float(
        settings.structure_group_max_diameter_ratio
    )

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    # Keep only the strongest edge for one owner pair.  Dense triangulated
    # boundaries otherwise consume the degree budget multiple times while
    # conveying the same layout relationship.
    best_candidates: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    for edge in adjacency:
        left_id = int(edge.left_id)
        right_id = int(edge.right_id)
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        if left is None or right is None:
            continue
        left_owner = member_owner.get(left_id)
        right_owner = member_owner.get(right_id)
        if left_owner is None or right_owner is None or left_owner == right_owner:
            continue
        score = _structure_edge_score(left, right, edge, settings)
        if score is None or not math.isfinite(float(score)):
            continue
        owner_pair = tuple(sorted((int(left_owner), int(right_owner))))
        candidate = (float(score), min(left_id, right_id), max(left_id, right_id))
        previous = best_candidates.get(owner_pair)
        if previous is None or candidate > previous:
            best_candidates[owner_pair] = candidate

    candidates = []
    for (left_owner, right_owner), (score, left_id, right_id) in (
        best_candidates.items()
    ):
        left_group = member_group.get(left_owner)
        right_group = member_group.get(right_owner)
        if left_group is None or right_group is None:
            continue
        candidates.append((
            -score,
            left_id,
            right_id,
            left_owner,
            right_owner,
            left_group,
            right_group,
        ))
    candidates.sort()

    for (
        _negative_score,
        _left_sort,
        _right_sort,
        left_owner,
        right_owner,
        left_group,
        right_group,
    ) in candidates:
        left_root = find(left_group)
        right_root = find(right_group)
        pair = tuple(sorted((int(left_owner), int(right_owner))))
        if pair in component_affinity[left_root] or (
            left_root != right_root and pair in component_affinity[right_root]
        ):
            continue
        if endpoint_degree[left_owner] >= int(settings.structure_group_max_degree):
            continue
        if endpoint_degree[right_owner] >= int(settings.structure_group_max_degree):
            continue
        if left_root == right_root:
            # The relationship is already in one macro chart; only the
            # owner-level pair needs recording for the local packer.
            component_affinity[left_root].add(pair)
            endpoint_degree[left_owner] += 1
            endpoint_degree[right_owner] += 1
            continue

        merged_members = component_members[left_root] | component_members[right_root]
        if len(merged_members) > int(settings.structure_group_max_members):
            continue
        merged_bounds = _union_model_bounds(
            by_id[member_id].model_bounds
            for member_id in merged_members
            if member_id in by_id
        )
        if _model_bounds_diagonal(merged_bounds) > max_diameter + _EPSILON:
            continue

        # Stable union by original group index keeps reports/replays
        # deterministic even when Blender returns adjacency in a different
        # edge order.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        component_members[left_root].update(component_members[right_root])
        component_members[right_root].clear()
        component_anchors[left_root].update(component_anchors[right_root])
        component_anchors[right_root].clear()
        component_affinity[left_root].update(component_affinity[right_root])
        component_affinity[left_root].add(pair)
        component_affinity[right_root].clear()
        endpoint_degree[left_owner] += 1
        endpoint_degree[right_owner] += 1

    components: Dict[int, List[int]] = defaultdict(list)
    for group_index in range(len(groups)):
        components[find(group_index)].append(group_index)

    repeat_reasons = {"MIRROR", "ROTATIONAL", "REPEATED", "TRANSLATED"}
    result: List[Dict[str, Any]] = []
    for root, indices in sorted(
        components.items(), key=lambda item: min(item[1])
    ):
        if len(indices) == 1:
            group_index = indices[0]
            item = dict(groups[group_index])
            anchors = set(int(anchor) for anchor in item.get("anchors", ()))
            item["affinity_pairs"] = sorted(
                pair for pair in component_affinity[root]
                if pair[0] in anchors and pair[1] in anchors
            )
            result.append(item)
            continue

        members: List[int] = []
        anchors: List[int] = []
        small: List[int] = []
        small_anchors: List[int] = []
        reasons = set()
        for group_index in sorted(indices):
            group = groups[group_index]
            reasons.add(str(group.get("reason", "")))
            for member in group.get("members", ()):
                member = int(member)
                if member not in members:
                    members.append(member)
            for anchor in group.get("anchors", ()):
                anchor = int(anchor)
                if anchor not in anchors:
                    anchors.append(anchor)
            for member, anchor in zip(
                group.get("small", ()), group.get("small_anchors", ())
            ):
                member = int(member)
                anchor = int(anchor)
                if member not in small:
                    small.append(member)
                    small_anchors.append(anchor)
        preserved_repeat = sorted(reasons.intersection(repeat_reasons))
        reason = preserved_repeat[0] if preserved_repeat else "TOPOLOGY_CONTINUITY"
        anchor_set = set(anchors)
        result.append({
            "members": members,
            "anchors": anchors,
            "small": small,
            "small_anchors": small_anchors,
            "reason": reason,
            "affinity_pairs": sorted(
                pair for pair in component_affinity[root]
                if pair[0] in anchor_set and pair[1] in anchor_set
            ),
        })
    return result


def _build_geometry_continuity_blocks(
    groups: Sequence[Dict[str, Any]],
    islands: Sequence[IslandRecord],
    adjacency: Sequence[IslandAdjacency],
    object_diagonal: float,
    settings: GroupLayoutOptions,
) -> List[Dict[str, Any]]:
    """Partition connected layout groups into bounded rigid continuity blocks.

    Existing owner/repeat groups are kept intact unless two neighboring
    groups fit the configured block budget.  Groups that cannot be merged
    still receive deterministic peer links, allowing the top-level packer to
    keep the resulting blocks near one another without creating one enormous
    affinity search component.
    """

    groups = [dict(group) for group in groups]
    if (
        not settings.structure_group_enabled
        or not settings.continuity_block_enabled
        or len(groups) < 2
    ):
        return groups

    by_id = {int(island.island_id): island for island in islands}
    member_group = {
        int(member_id): group_index
        for group_index, group in enumerate(groups)
        for member_id in group.get("members", ())
    }
    member_owner: Dict[int, int] = {}
    for group in groups:
        anchors = tuple(int(anchor) for anchor in group.get("anchors", ()))
        for anchor in anchors:
            member_owner[anchor] = anchor
        for member, anchor in zip(
            group.get("small", ()), group.get("small_anchors", ())
        ):
            member_owner[int(member)] = int(anchor)
        for member in group.get("members", ()):
            member = int(member)
            member_owner.setdefault(member, member)

    # Collapse dense mesh boundaries to one strongest edge per pair of
    # existing layout groups.  The same candidate stream also drives the
    # maximum-spanning peer links below.
    group_edges: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    owner_edges: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    # A strict structure edge is allowed to merge blocks.  Rejected real
    # boundaries are retained separately as low-weight locality hints; they
    # can only form the one-shot soft pairing performed after bounded strong
    # components have been assigned.
    soft_topology_edges: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    for edge in adjacency:
        left_id = int(edge.left_id)
        right_id = int(edge.right_id)
        left_group = member_group.get(left_id)
        right_group = member_group.get(right_id)
        if left_group is None or right_group is None or left_group == right_group:
            continue
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        if left is None or right is None:
            continue
        score = _structure_edge_score(left, right, edge, settings)
        if score is None or not math.isfinite(float(score)):
            soft_score = _soft_structure_edge_score(left, right, edge, settings)
            if soft_score is None:
                continue
            group_pair = tuple(sorted((left_group, right_group)))
            candidate = (
                float(soft_score), min(left_id, right_id), max(left_id, right_id)
            )
            previous = soft_topology_edges.get(group_pair)
            if previous is None or candidate > previous:
                soft_topology_edges[group_pair] = candidate
            continue
        score = float(score)
        group_pair = tuple(sorted((left_group, right_group)))
        candidate = (score, min(left_id, right_id), max(left_id, right_id))
        previous = group_edges.get(group_pair)
        if previous is None or candidate > previous:
            group_edges[group_pair] = candidate
        left_owner = member_owner.get(left_id)
        right_owner = member_owner.get(right_id)
        if left_owner is None or right_owner is None or left_owner == right_owner:
            continue
        owner_pair = tuple(sorted((left_owner, right_owner)))
        previous = owner_edges.get(owner_pair)
        if previous is None or candidate > previous:
            owner_edges[owner_pair] = candidate

    if not group_edges and not soft_topology_edges:
        return groups

    # First form connected group components.  Components are later split into
    # bounded blocks, but their topology relation remains visible in the
    # deterministic peer links.
    graph: Dict[int, Set[int]] = defaultdict(set)
    for left_group, right_group in group_edges:
        graph[left_group].add(right_group)
        graph[right_group].add(left_group)
    seen: Set[int] = set()
    group_components: List[Tuple[int, ...]] = []
    for seed in sorted(graph):
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(graph[current], reverse=True):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        group_components.append(tuple(sorted(component)))

    max_members = max(
        int(settings.continuity_block_max_members),
        int(settings.continuity_block_target_members),
    )
    target_members = min(
        int(settings.continuity_block_target_members), max_members
    )
    # ``structure_group_max_members`` predates continuity blocks and is used
    # by callers as an explicit hard safety budget.  Preserve that contract
    # for deliberately lowered values (the historical default is 12), while
    # allowing the new 32/48 block defaults to provide a coarser second layer.
    legacy_structure_default = int(
        GroupLayoutOptions().structure_group_max_members
    )
    if int(settings.structure_group_max_members) < legacy_structure_default:
        max_members = min(max_members, int(settings.structure_group_max_members))
        target_members = min(target_members, max_members)
    # Continuity blocks intentionally have a coarser envelope than the
    # strict owner/structure merge.  Preserve compatibility for callers that
    # explicitly raised the legacy structure ratio by taking the larger value.
    continuity_ratio = max(
        float(settings.structure_group_max_diameter_ratio),
        float(getattr(settings, "continuity_block_max_diameter_ratio", 0.35)),
    )
    max_diameter = max(float(object_diagonal), _EPSILON) * continuity_ratio

    # Union-find state for the bounded block merge.
    block_parent = list(range(len(groups)))
    block_members = [
        set(int(member) for member in group.get("members", ()))
        for group in groups
    ]
    block_group_indices = [{index} for index in range(len(groups))]

    def find(index: int) -> int:
        while block_parent[index] != index:
            block_parent[index] = block_parent[block_parent[index]]
            index = block_parent[index]
        return index

    sorted_edges = sorted(
        (
            -candidate[0],
            candidate[1],
            candidate[2],
            left_group,
            right_group,
        )
        for (left_group, right_group), candidate in group_edges.items()
    )
    for (
        _negative_score,
        _left_id,
        _right_id,
        left_group,
        right_group,
    ) in sorted_edges:
        left_root = find(left_group)
        right_root = find(right_group)
        if left_root == right_root:
            continue
        merged_members = block_members[left_root] | block_members[right_root]
        if len(merged_members) > max_members:
            continue
        # Once both sides already reach the target, leave them as separate
        # blocks unless they fit exactly inside the target.  This keeps block
        # sizes in the intended 24-48 range instead of greedily absorbing
        # every neighboring owner group.
        left_count = len(block_members[left_root])
        right_count = len(block_members[right_root])
        if (
            left_count >= target_members
            and right_count >= target_members
            and len(merged_members) > target_members
        ):
            continue
        merged_bounds = _union_model_bounds(
            by_id[member_id].model_bounds
            for member_id in merged_members
            if member_id in by_id
        )
        if _model_bounds_diagonal(merged_bounds) > max_diameter + _EPSILON:
            continue
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        block_parent[right_root] = left_root
        block_members[left_root].update(block_members[right_root])
        block_members[right_root].clear()
        block_group_indices[left_root].update(block_group_indices[right_root])
        block_group_indices[right_root].clear()

    # Map every original group to its final block root, then materialize block
    # dictionaries in stable order while retaining all owner metadata.
    group_to_root = {index: find(index) for index in range(len(groups))}
    block_roots = sorted(
        {root for root in group_to_root.values()},
        key=lambda root: min(block_group_indices[root]),
    )
    root_to_key = {
        root: min(block_group_indices[root]) for root in block_roots
    }
    result: List[Dict[str, Any]] = []
    for root in block_roots:
        indices = sorted(block_group_indices[root])
        members: List[int] = []
        anchors: List[int] = []
        small: List[int] = []
        small_anchors: List[int] = []
        reasons = set()
        affinities = set()
        for group_index in indices:
            group = groups[group_index]
            reasons.add(str(group.get("reason", "")))
            for member in group.get("members", ()):
                member = int(member)
                if member not in members:
                    members.append(member)
            for anchor in group.get("anchors", ()):
                anchor = int(anchor)
                if anchor not in anchors:
                    anchors.append(anchor)
            for member, anchor in zip(
                group.get("small", ()), group.get("small_anchors", ())):
                member = int(member)
                anchor = int(anchor)
                if member not in small:
                    small.append(member)
                    small_anchors.append(anchor)
            for pair in group.get("affinity_pairs", ()):
                if len(pair) == 2:
                    canonical = tuple(sorted((int(pair[0]), int(pair[1]))))
                    if canonical[0] != canonical[1]:
                        affinities.add(canonical)
        block_anchor_set = set(anchors)
        block_id_set = set(members)
        # Add strongest owner-level topology affinities that are now inside
        # this block, respecting the same endpoint degree budget as the
        # earlier structure pass.
        degree: Dict[int, int] = defaultdict(int)
        for left, right in affinities:
            degree[left] += 1
            degree[right] += 1
        owner_candidates = sorted(
            (
                -candidate[0], candidate[1], candidate[2], left_owner, right_owner
            )
            for (left_owner, right_owner), candidate in owner_edges.items()
            if left_owner in block_anchor_set and right_owner in block_anchor_set
            and left_owner in block_id_set and right_owner in block_id_set
            and group_to_root.get(member_group.get(left_owner, -1), -1) == root
            and group_to_root.get(member_group.get(right_owner, -1), -1) == root
        )
        for _negative_score, _left_id, _right_id, left_owner, right_owner in owner_candidates:
            pair = tuple(sorted((int(left_owner), int(right_owner))))
            if pair in affinities:
                continue
            if (
                degree[left_owner] >= int(settings.structure_group_max_degree)
                or degree[right_owner] >= int(settings.structure_group_max_degree)
            ):
                continue
            affinities.add(pair)
            degree[left_owner] += 1
            degree[right_owner] += 1

        preserved_repeat = sorted(reasons.intersection({
            "MIRROR", "ROTATIONAL", "REPEATED", "TRANSLATED"
        }))
        reason = preserved_repeat[0] if preserved_repeat else (
            "TOPOLOGY_BLOCK" if len(indices) > 1 else str(
                groups[indices[0]].get("reason", "")
            )
        )
        item: Dict[str, Any] = {
            "members": members,
            "anchors": anchors,
            "small": small,
            "small_anchors": small_anchors,
            "reason": reason,
            "affinity_pairs": sorted(
                pair for pair in affinities
                if pair[0] in block_anchor_set and pair[1] in block_anchor_set
            ),
            "_continuity_key": root_to_key[root],
        }
        result.append(item)

    # Rebuild continuity components from the *final* bounded blocks.  The
    # original connected-component graph is only a candidate source: using it
    # as the component label would mark every block on a long shell/weapon
    # chain as one rigid unit even after the diameter gate rejected the joins.
    block_edges: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    for (left_group, right_group), candidate in group_edges.items():
        left_root = group_to_root[left_group]
        right_root = group_to_root[right_group]
        if left_root == right_root:
            continue
        left_key = root_to_key[left_root]
        right_key = root_to_key[right_root]
        pair = tuple(sorted((left_key, right_key)))
        previous = block_edges.get(pair)
        if previous is None or candidate > previous:
            block_edges[pair] = candidate

    block_keys = tuple(int(item["_continuity_key"]) for item in result)
    block_bounds = {
        int(item["_continuity_key"]): _union_model_bounds(
            by_id[member_id].model_bounds
            for member_id in item.get("members", ())
            if member_id in by_id
        )
        for item in result
    }
    block_member_counts = {
        int(item["_continuity_key"]): len(item.get("members", ()))
        for item in result
    }
    component_parent = {key: key for key in block_keys}
    component_groups = {key: 1 for key in block_keys}
    component_members = {
        key: int(block_member_counts.get(key, 0)) for key in block_keys
    }
    component_bounds = {
        key: block_bounds[key] for key in block_keys
    }

    def component_find(key: int) -> int:
        component_parent.setdefault(key, key)
        while component_parent[key] != key:
            component_parent[key] = component_parent[component_parent[key]]
            key = component_parent[key]
        return key

    continuity_ratio = max(
        float(settings.structure_group_max_diameter_ratio),
        float(getattr(settings, "continuity_block_max_diameter_ratio", 0.35)),
    )
    continuity_diameter = max(float(object_diagonal), _EPSILON) * continuity_ratio
    max_component_groups = max(
        2, int(getattr(settings, "continuity_component_max_groups", 6))
    )
    max_component_members = max(
        2, int(getattr(settings, "continuity_component_max_members", 96))
    )
    # A bounded union forest keeps each physical rack local.  The edge score
    # remains useful for choosing which contacts survive when several shell
    # strips compete for the same component budget.
    for (left_key, right_key), _candidate in sorted(
        block_edges.items(),
        key=lambda item: (-item[1][0], item[1][1], item[1][2], item[0]),
    ):
        left_root = component_find(left_key)
        right_root = component_find(right_key)
        if left_root == right_root:
            continue
        merged_groups = component_groups[left_root] + component_groups[right_root]
        merged_members = component_members[left_root] + component_members[right_root]
        if merged_groups > max_component_groups or merged_members > max_component_members:
            continue
        merged_bounds = _union_model_bounds((
            component_bounds[left_root], component_bounds[right_root]
        ))
        if (
            continuity_ratio < 0.999999
            and _model_bounds_diagonal(merged_bounds)
            > continuity_diameter + _EPSILON
        ):
            continue
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        component_parent[right_root] = left_root
        component_groups[left_root] = merged_groups
        component_members[left_root] = merged_members
        component_bounds[left_root] = merged_bounds

    component_roots: Dict[int, List[int]] = defaultdict(list)
    for key in block_keys:
        component_roots[component_find(key)].append(key)
    component_by_key: Dict[int, int] = {}
    for component_index, (_root, keys) in enumerate(sorted(
        component_roots.items(), key=lambda item: min(item[1])
    ), 1):
        if len(keys) < 2:
            continue
        for key in keys:
            component_by_key[int(key)] = component_index

    # Build a maximum-spanning forest *inside* each local component.  Edges
    # crossing the diameter/member envelope remain diagnostic only and never
    # reintroduce a global affinity chain in the packer.
    block_parent_links: Dict[int, int] = {
        key: key for key in component_by_key
    }

    def link_find(key: int) -> int:
        block_parent_links.setdefault(key, key)
        while block_parent_links[key] != key:
            block_parent_links[key] = block_parent_links[block_parent_links[key]]
            key = block_parent_links[key]
        return key

    peer_map: Dict[int, Set[int]] = defaultdict(set)
    peer_scores: Dict[Tuple[int, int], float] = {}
    for (left_key, right_key), candidate in sorted(
        block_edges.items(),
        key=lambda item: (-item[1][0], item[1][1], item[1][2], item[0]),
    ):
        if (
            component_by_key.get(left_key) is None
            or component_by_key.get(left_key) != component_by_key.get(right_key)
        ):
            continue
        left_root = link_find(left_key)
        right_root = link_find(right_key)
        if left_root == right_root:
            continue
        block_parent_links[right_root] = left_root
        peer_map[left_key].add(right_key)
        peer_map[right_key].add(left_key)
        pair_key = tuple(sorted((left_key, right_key)))
        peer_scores[pair_key] = float(candidate[0])

    # Collapse the rejected real-boundary candidates to final block ids.  A
    # strong edge can also reach this stage when its component was rejected by
    # the component envelope; treating it as a fallback locality hint is safe
    # and prevents a genuine neighboring pair from becoming an atlas-wide
    # singleton merely because another edge consumed its component budget.
    soft_block_edges: Dict[Tuple[int, int], Tuple[float, int, int]] = {}
    # ``block_edges`` already uses final block keys; do not run it through the
    # original-group union map below.
    for pair, candidate in block_edges.items():
        left_key, right_key = (int(pair[0]), int(pair[1]))
        if left_key == right_key:
            continue
        soft_block_edges[(min(left_key, right_key), max(left_key, right_key))] = (
            float(candidate[0]), int(candidate[1]), int(candidate[2])
        )
    # Rejected candidates still use original group indices and must be
    # collapsed through the final block roots.
    for (left_group, right_group), candidate in soft_topology_edges.items():
        left_root = group_to_root.get(left_group)
        right_root = group_to_root.get(right_group)
        if left_root is None or right_root is None or left_root == right_root:
            continue
        left_key = root_to_key.get(left_root)
        right_key = root_to_key.get(right_root)
        if left_key is None or right_key is None or left_key == right_key:
            continue
        pair = tuple(sorted((int(left_key), int(right_key))))
        normalized = (
            float(candidate[0]), int(candidate[1]), int(candidate[2])
        )
        previous = soft_block_edges.get(pair)
        if previous is None or normalized > previous:
            soft_block_edges[pair] = normalized

    # Blocks that did not enter a multi-block strong component get one
    # strongest-edge pairing pass.  This is a bounded maximum-weight matching,
    # rather than a transitive union: each block can be paired once, and the
    # resulting component is always exactly two blocks.  Strong components
    # also participate in a separate sparse peer pass below.  The old
    # implementation considered only the two-unassigned case, so a genuine
    # boundary from a large component to a singleton was silently discarded.
    # Keeping that relation as a peer (without merging component labels) lets
    # the hierarchical packer place the blocks together while preserving the
    # component/member bounds.
    unassigned_keys = set(block_keys).difference(component_by_key)
    used_soft_blocks: Set[int] = set()
    soft_endpoint_degree: Dict[int, int] = defaultdict(int)
    # ``peer_map`` already contains the maximum-spanning forest selected for
    # strong components.  Limit additional cross-component links by block,
    # rather than by raw face endpoint, so a dense bevel boundary cannot turn
    # one block into a high-degree affinity hub.
    soft_block_degree: Dict[int, int] = defaultdict(int)
    for block_key, peers in peer_map.items():
        soft_block_degree[int(block_key)] = len(peers)
    soft_component_degree: Dict[int, int] = defaultdict(int)
    for block_key, peers in peer_map.items():
        component = component_by_key.get(int(block_key))
        if component is None:
            continue
        # The strong forest is not counted against the cross-component budget;
        # this counter is incremented only for links added in this pass.
        _ = peers
        soft_component_degree.setdefault(int(component), 0)
    max_cross_peer_degree = max(
        1,
        min(2, int(getattr(settings, "structure_group_max_degree", 2))),
    )
    max_cross_component_degree = max(1, min(2, max_cross_peer_degree))
    next_soft_component = max(component_by_key.values(), default=0) + 1
    for (left_key, right_key), candidate in sorted(
        soft_block_edges.items(),
        key=lambda item: (-item[1][0], item[1][1], item[1][2], item[0]),
    ):
        if left_key == right_key:
            continue
        left_endpoint = int(candidate[1])
        right_endpoint = int(candidate[2])
        if left_endpoint == right_endpoint:
            continue
        degree_limit = max(
            1, int(getattr(settings, "structure_group_max_degree", 1))
        )
        if (
            soft_endpoint_degree[left_endpoint] >= degree_limit
            or soft_endpoint_degree[right_endpoint] >= degree_limit
        ):
            continue
        merged_member_count = (
            int(block_member_counts.get(left_key, 0))
            + int(block_member_counts.get(right_key, 0))
        )
        if merged_member_count > max_component_members:
            continue
        # Use the actual boundary endpoints for the soft envelope.  A block
        # can contain several physically separated owners; checking its full
        # AABB here incorrectly rejects a local contact merely because an
        # unrelated member expanded the block bounds.
        left_endpoint_record = by_id.get(left_endpoint)
        right_endpoint_record = by_id.get(right_endpoint)
        if left_endpoint_record is not None and right_endpoint_record is not None:
            merged_bounds = _union_model_bounds((
                left_endpoint_record.model_bounds,
                right_endpoint_record.model_bounds,
            ))
        else:
            merged_bounds = _union_model_bounds((
                block_bounds[left_key], block_bounds[right_key]
            ))
        if (
            continuity_ratio < 0.999999
            and _model_bounds_diagonal(merged_bounds)
            > continuity_diameter + _EPSILON
        ):
            continue
        left_component = component_by_key.get(int(left_key))
        right_component = component_by_key.get(int(right_key))

        if left_component is None and right_component is None:
            # Preserve the historical one-shot matching contract for two
            # isolated blocks.  Do not let an already paired block re-enter a
            # second synthetic rigid component.
            if (
                left_key not in unassigned_keys
                or right_key not in unassigned_keys
                or left_key in used_soft_blocks
                or right_key in used_soft_blocks
            ):
                continue
            if max_component_groups < 2:
                break
            pair = (int(left_key), int(right_key))
            component_by_key[left_key] = next_soft_component
            component_by_key[right_key] = next_soft_component
            peer_map[left_key].add(right_key)
            peer_map[right_key].add(left_key)
            peer_scores[pair] = float(candidate[0])
            used_soft_blocks.update(pair)
            soft_block_degree[left_key] += 1
            soft_block_degree[right_key] += 1
            soft_endpoint_degree[left_endpoint] += 1
            soft_endpoint_degree[right_endpoint] += 1
            next_soft_component += 1
            continue

        # A candidate touching a strong component (or a previously paired
        # soft component) is retained as a locality hint only.  It must not
        # relabel/merge the rigid component, but it should still reach the
        # top-level packer through ``continuity_peers``.
        if left_component is not None and right_component is not None:
            if int(left_component) == int(right_component):
                continue
            component_keys = (int(left_component), int(right_component))
        else:
            component_keys = tuple(
                int(value)
                for value in (left_component, right_component)
                if value is not None
            )
        if (
            soft_block_degree[int(left_key)] >= max_cross_peer_degree
            or soft_block_degree[int(right_key)] >= max_cross_peer_degree
        ):
            continue
        if any(
            soft_component_degree[value] >= max_cross_component_degree
            for value in component_keys
        ):
            continue
        pair = (int(left_key), int(right_key))
        peer_map[left_key].add(right_key)
        peer_map[right_key].add(left_key)
        peer_scores[pair] = float(candidate[0])
        soft_block_degree[left_key] += 1
        soft_block_degree[right_key] += 1
        for value in component_keys:
            soft_component_degree[value] += 1
        soft_endpoint_degree[left_endpoint] += 1
        soft_endpoint_degree[right_endpoint] += 1

    for item in result:
        key = item["_continuity_key"]
        component = component_by_key.get(key)
        if component is not None:
            item["_continuity_component"] = component
        # Keep peer metadata even when one endpoint is a singleton without a
        # continuity component.  Such cross-component links are intentionally
        # layout-only, but dropping them here prevents the packer from ever
        # seeing the local boundary we just selected.
        item["_continuity_peers"] = tuple(sorted(
            peer_map.get(key, ()),
            key=lambda peer: (
                -peer_scores.get(tuple(sorted((key, peer))), -math.inf),
                int(peer),
            ),
        ))
    return result


def build_layout_groups(
    islands: Sequence[IslandRecord],
    repeat_groups: Sequence[RepeatGroup],
    object_diagonal: float,
    options: Optional[GroupLayoutOptions] = None,
    *,
    adjacency: Sequence[IslandAdjacency] = (),
) -> List[LayoutGroup]:
    """Build bounded layout macro-groups from the active mesh structure.

    ``adjacency`` is keyword-only for backwards compatibility with callers
    that passed ``options`` as the fourth positional argument.  It carries
    real mesh-boundary contacts and is used only to keep nearby charts in one
    packing block; UV continuity and seam flags are never changed.
    """

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
            "affinity_pairs": [],
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
            "affinity_pairs": [],
        })
        assigned.add(island.island_id)

    # Merge only strong, bounded mesh-adjacency contacts before small pieces
    # choose their local owners.  This is the missing link that makes the
    # structural layout policy affect the actual pack rather than just the
    # analysis report.
    mutable_groups = _merge_structure_groups(
        mutable_groups,
        islands,
        tuple(adjacency or ()),
        object_diagonal,
        settings,
    )

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
            "affinity_pairs": [],
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
            "affinity_pairs": [],
        })
        assigned.update(selected)

    mutable_groups = _merge_small_repeat_owner_groups(
        mutable_groups, repeat_groups, by_id, settings, object_diagonal
    )

    # Small-owner assignment can leave a continuous bevel/inset chain split
    # across owner cells.  Add bounded topology affinities after that pass so
    # the packer can keep the chain readable without changing its UV islands.
    mutable_groups = _merge_topology_continuity_groups(
        mutable_groups,
        islands,
        tuple(adjacency or ()),
        object_diagonal,
        settings,
    )

    # Build a second, coarser continuity layer after owner assignment and
    # topology affinities have settled.  Blocks are bounded and retain their
    # constituent UV charts; peer links are consumed by the top-level packer
    # so adjacent blocks remain near one another without becoming one giant
    # affinity component.
    mutable_groups = _build_geometry_continuity_blocks(
        mutable_groups,
        islands,
        tuple(adjacency or ()),
        object_diagonal,
        settings,
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
                "affinity_pairs": [],
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

    affinity_pairs_by_group: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for group_index, group in enumerate(mutable_groups):
        for left_id, right_id in group.get("affinity_pairs", ()):
            left_owner = member_owner.get(int(left_id))
            right_owner = member_owner.get(int(right_id))
            if (
                left_owner is None
                or right_owner is None
                or left_owner == right_owner
                or member_group_index.get(left_owner) != group_index
                or member_group_index.get(right_owner) != group_index
            ):
                continue
            pair = tuple(sorted((int(left_owner), int(right_owner))))
            if pair not in affinity_pairs_by_group[group_index]:
                affinity_pairs_by_group[group_index].append(pair)

    # Continuity peers are recorded by the helper using its stable block key
    # (the minimum source group index).  Resolve those keys only after the
    # final defensive singleton pass, whose additions can shift group ids.
    continuity_key_to_group_id = {
        int(group["_continuity_key"]): index
        for index, group in enumerate(mutable_groups)
        if group.get("_continuity_key") is not None
    }

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
            anchor_centroid = _island_model_centroid(by_id[anchor_id])
            coordinate_scale = max(abs(float(object_diagonal)), _EPSILON)
            attached = sorted(
                small_by_anchor.get(anchor_id, ()),
                key=lambda member_id: (
                    round(
                        float(
                            _island_model_centroid(by_id[member_id]).x
                            - anchor_centroid.x
                        ) / coordinate_scale,
                        10,
                    ),
                    round(
                        float(
                            _island_model_centroid(by_id[member_id]).y
                            - anchor_centroid.y
                        ) / coordinate_scale,
                        10,
                    ),
                    round(
                        float(
                            _island_model_centroid(by_id[member_id]).z
                            - anchor_centroid.z
                        ) / coordinate_scale,
                        10,
                    ),
                    int(member_id),
                ),
            )
            ordered_members.extend(attached)
            ordered_small.extend(attached)
            ordered_small_anchors.extend([anchor_id] * len(attached))
        ordered_members.extend(
            member_id
            for member_id in group["members"]
            if member_id not in ordered_members
        )
        continuity_peer_ids: List[int] = []
        seen_continuity_peers = set()
        for peer in group.get("_continuity_peers", ()):
            peer_id = continuity_key_to_group_id.get(int(peer))
            if (
                peer_id is None
                or peer_id == index
                or peer_id in seen_continuity_peers
            ):
                continue
            seen_continuity_peers.add(peer_id)
            continuity_peer_ids.append(peer_id)
        continuity_peers = tuple(continuity_peer_ids)
        layout_groups.append(LayoutGroup(
            group_id=index,
            member_ids=tuple(ordered_members),
            reason=str(group["reason"]),
            anchor_ids=tuple(group["anchors"]),
            small_member_ids=tuple(ordered_small),
            small_anchor_ids=tuple(ordered_small_anchors),
            owner_cohorts=tuple(owner_cohorts_by_group.get(index, ())),
            affinity_pairs=tuple(affinity_pairs_by_group.get(index, ())),
            continuity_component=(
                None
                if group.get("_continuity_component") is None
                else int(group["_continuity_component"])
            ),
            continuity_peers=continuity_peers,
        ))
    return layout_groups


def _constraint_components(
    constraints: Sequence[Sequence[int]],
    valid_ids: Iterable[int],
) -> Tuple[Tuple[int, ...], ...]:
    """Return deterministic connected components for axis contracts."""

    valid = set(int(island_id) for island_id in valid_ids)
    parent: Dict[int, int] = {}

    def find(island_id: int) -> int:
        parent.setdefault(island_id, island_id)
        while parent[island_id] != island_id:
            parent[island_id] = parent[parent[island_id]]
            island_id = parent[island_id]
        return island_id

    for constraint in constraints:
        members = tuple(dict.fromkeys(
            int(island_id) for island_id in constraint
            if int(island_id) in valid
        ))
        if len(members) < 2:
            continue
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


def _select_common_geometry_axis(
    member_candidates: Sequence[Mapping[str, Mapping[str, Any]]],
    priority: Sequence[str],
    preferred_axis: Optional[str] = None,
    *,
    prefer_geometry_axis_cardinal: bool = False,
    geometry_axis_cardinal_min_gain: float = math.radians(5.0),
    geometry_axis_cardinal_max_quality_loss: float = 0.15,
) -> Optional[str]:
    """Choose one stable axis shared by every structural member.

    A priority-first choice is brittle when its weakest member sits exactly
    on a float32 coherence boundary: replay can then drop that axis and make
    a fresh analysis select a different contract.  Rank fully coherent common
    axes by their weakest stability/confidence, retaining the declared axis
    priority only as the deterministic final tie-break.  An inherited fixed
    axis keeps its explicit replay semantics.
    """

    common = [
        axis_name for axis_name in priority
        if member_candidates
        and all(axis_name in candidates for candidates in member_candidates)
    ]
    if preferred_axis is not None:
        return preferred_axis if preferred_axis in common else None
    if not common:
        return None
    fully_coherent = [
        axis_name for axis_name in common
        if all(
            bool(candidates[axis_name].get("coherent", False))
            for candidates in member_candidates
        )
    ]
    pool = fully_coherent or common
    priority_rank = {
        axis_name: index for index, axis_name in enumerate(priority)
    }
    weakest = {
        axis_name: (
            min(
                float(candidates[axis_name].get("stability", 0.0))
                for candidates in member_candidates
            ),
            min(
                float(candidates[axis_name].get("confidence", 0.0))
                for candidates in member_candidates
            ),
        )
        for axis_name in pool
    }
    best_stability = max(value[0] for value in weakest.values())
    stable_pool = [
        axis_name for axis_name in pool
        if weakest[axis_name][0] + _GEOMETRY_AXIS_GROUP_TIE_EPSILON
        >= best_stability
    ]
    best_confidence = max(weakest[axis_name][1] for axis_name in stable_pool)
    confidence_pool = [
        axis_name for axis_name in stable_pool
        if weakest[axis_name][1] + _GEOMETRY_AXIS_GROUP_TIE_EPSILON
        >= best_confidence
    ]
    baseline = min(confidence_pool, key=lambda axis_name: priority_rank[axis_name])

    # A structural/repeat lock is an explicit contract.  The cardinal pass is
    # only a presentation preference and must never replace that contract.
    if not prefer_geometry_axis_cardinal:
        return baseline

    try:
        min_gain = float(geometry_axis_cardinal_min_gain)
    except (TypeError, ValueError):
        min_gain = math.radians(5.0)
    if not math.isfinite(min_gain):
        min_gain = math.radians(5.0)
    min_gain = _clamp(min_gain, 0.0, math.pi * 0.25)
    try:
        max_quality_loss = float(geometry_axis_cardinal_max_quality_loss)
    except (TypeError, ValueError):
        max_quality_loss = 0.15
    if not math.isfinite(max_quality_loss):
        max_quality_loss = 0.15
    max_quality_loss = _clamp(max_quality_loss, 0.0, 1.0)

    def cardinal_stats(axis_name: str) -> Optional[Tuple[float, float, int]]:
        """Return weighted line error, cue weight, and member coverage.

        Candidates are produced by ``_cohere_auto_geometry_axes``.  Keeping
        this reader tolerant of absent fields preserves compatibility with
        older test doubles and callers that only provide stability/confidence.
        A candidate with no reliable edge cue is not allowed to win a visual
        orientation vote.
        """

        weighted_error = 0.0
        total_weight = 0.0
        covered = 0
        for candidates in member_candidates:
            values = candidates.get(axis_name)
            if values is None:
                continue
            raw_error = values.get("cardinal_error")
            if raw_error is None:
                # Accept the descriptive spelling used by external probes.
                raw_error = values.get("cardinal_error_radians")
            try:
                error = abs(float(raw_error))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(error):
                continue
            raw_weight = values.get("cardinal_weight", values.get("area", 1.0))
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                weight = 0.0
            if not math.isfinite(weight) or weight <= _EPSILON:
                continue
            weighted_error += error * weight
            total_weight += weight
            covered += 1
        if total_weight <= _EPSILON or covered == 0:
            return None
        return weighted_error / total_weight, total_weight, covered

    baseline_cardinal = cardinal_stats(baseline)
    if baseline_cardinal is None:
        return baseline

    # ``weakest`` mirrors the legacy resolver's stability comparison.  The
    # cardinal candidate may trade a little stability for a clearly straighter
    # long edge, but never enough to make a weak chart define the whole cohort.
    baseline_stability = weakest[baseline][0]
    candidates = []
    # Alternatives are drawn from the full coherent/common pool rather than
    # the weakest-stability tie pool.  The explicit quality-loss gate below is
    # what permits a *bounded* stability trade for a meaningfully straighter
    # long edge; restricting this to ``confidence_pool`` would make that gate
    # unreachable in practice.
    for axis_name in pool:
        if axis_name == baseline:
            continue
        stats = cardinal_stats(axis_name)
        if stats is None:
            continue
        cardinal_error, _weight, covered = stats
        if covered < baseline_cardinal[2]:
            continue
        gain = baseline_cardinal[0] - cardinal_error
        if gain + _EPSILON < min_gain:
            continue
        stability_loss = max(0.0, baseline_stability - weakest[axis_name][0])
        if stability_loss > max_quality_loss + _EPSILON:
            continue
        candidates.append((
            cardinal_error,
            -gain,
            stability_loss,
            -weakest[axis_name][0],
            -weakest[axis_name][1],
            priority_rank[axis_name],
            axis_name,
        ))
    if not candidates:
        return baseline
    return min(candidates)[-1]


def _cohere_auto_geometry_axes(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> None:
    """Bind structure/repeat components to one signed AUTO geometry axis.

    Topological structure affinities are processed first and become locks.
    Repeat components may extend a compatible lock but cannot replace it.
    This prevents a panel initially choosing Z while its neighboring cap
    chooses X, yet never invents an axis that one member cannot resolve.
    """

    analysis.geometry_axis_groups = []
    if (
        not settings.align_geometry_direction
        or str(settings.direction_axis).upper() != "AUTO"
    ):
        return

    by_id = {island.island_id: island for island in analysis.islands}
    valid_ids = set(by_id)
    priority = _direction_axis_order(
        "AUTO", settings.direction_auto_priority
    )
    candidate_cache: Dict[Tuple[int, float], Dict[str, Mapping[str, Any]]] = {}
    # A matching persisted contract is the result of a previous successful
    # layout.  Do not let a later visual preference silently choose another
    # axis during replay; ``_apply_persisted_geometry_axis_contract`` will
    # rebind the exact recorded axes immediately after this pass.
    cardinal_common_enabled = bool(
        getattr(settings, "prefer_geometry_axis_cardinal", False)
    )
    if cardinal_common_enabled:
        try:
            persisted = _load_geometry_axis_contract(mesh, uv_layer, settings)
            current_face_sets = {
                tuple(sorted(int(index) for index in island.face_indices))
                for island in analysis.islands
                if island.face_indices
            }
            if persisted and set(persisted) == current_face_sets:
                cardinal_common_enabled = False
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            # A lightweight test double may not expose ID properties.  In that
            # case there is no replay contract to protect.
            pass

    def island_candidates(
        island_id: int,
        minimum_confidence: Optional[float] = None,
    ) -> Dict[str, Mapping[str, Any]]:
        """Return axis candidates with a caller-specific numerical gate.

        Topology reconciliation retains the configured projection threshold.
        The normal-domain pass asks for the relaxed numerical set so a highly
        anisotropic but valid rectangle can inherit the same axis as its
        coplanar twin; its lower confidence is recorded as a downgrade.
        """

        if minimum_confidence is None:
            minimum_confidence = float(settings.direction_axis_min_projection)
        try:
            candidate_gate = max(float(minimum_confidence), _GEOMETRY_AXIS_RELATIVE_EPSILON)
        except (TypeError, ValueError):
            candidate_gate = _GEOMETRY_AXIS_RELATIVE_EPSILON
        if not math.isfinite(candidate_gate):
            candidate_gate = _GEOMETRY_AXIS_RELATIVE_EPSILON
        cache_key = (int(island_id), candidate_gate)
        if cache_key in candidate_cache:
            return candidate_cache[cache_key]
        island = by_id[island_id]
        sum_u, sum_v, records = _geometry_derivative_records(
            obj,
            mesh,
            uv_layer,
            island.face_indices,
            str(settings.direction_space).upper(),
        )
        energy = math.sqrt(max(
            float(sum_u.length_squared + sum_v.length_squared), 0.0
        ))
        if not records or not math.isfinite(energy) or energy <= _EPSILON:
            candidate_cache[cache_key] = {}
            return candidate_cache[cache_key]
        statistics = _geometry_axis_statistics(
            records,
            priority,
            energy,
            candidate_gate,
        )
        candidates: Dict[str, Mapping[str, Any]] = {}
        for axis_name in priority:
            values = statistics.get(axis_name)
            if values is None:
                continue
            confidence = float(values.get("confidence", 0.0))
            if confidence + _EPSILON < candidate_gate:
                continue
            component_u = float(values.get("sum_u", 0.0))
            component_v = float(values.get("sum_v", 0.0))
            direction = Vector((component_u, component_v))
            if direction.length_squared <= _EPSILON:
                continue
            direction.normalize()
            coherent = bool(
                float(values.get("concentration", 0.0)) + _EPSILON
                >= _GEOMETRY_AXIS_MIN_COHERENCE
                and float(values.get("effective_fraction", 0.0)) + _EPSILON
                >= _GEOMETRY_AXIS_MIN_EFFECTIVE_FRACTION
            )
            # Keep the long-edge cue attached to each axis candidate.  The
            # same source edge is evaluated after that candidate's signed
            # +V correction, so a structural cohort can compare X/Y/Z without
            # applying a post-hoc rotation that would invalidate the contract.
            cardinal_error = None
            cardinal_weight = 0.0
            # Keep the common-axis cardinal cue identical to the later
            # orientation policy: anisotropic charts use their PCA long axis,
            # while round/low-anisotropy charts fall back to a reliable
            # boundary edge.  Looking only at ``dominant_edge_angle`` here
            # made large panels lose their strongest directional evidence.
            edge_angle = _orientation_reference_angle(
                island,
                min_pca_anisotropy=float(settings.min_pca_anisotropy),
                min_edge_confidence=float(settings.min_cardinal_edge_confidence),
            )
            try:
                edge_confidence = float(
                    getattr(island, "dominant_edge_confidence", 0.0)
                )
            except (AttributeError, TypeError, ValueError):
                edge_confidence = 0.0
            try:
                area = abs(float(island.area_3d))
            except (AttributeError, TypeError, ValueError):
                area = 0.0
            if not math.isfinite(edge_confidence):
                edge_confidence = 0.0
            if not math.isfinite(area):
                area = 0.0
            # PCA has no independent confidence field on IslandRecord.  Its
            # anisotropy is the corresponding bounded salience measure; use
            # it when the reference helper selected ``principal_angle``.
            try:
                anisotropy = _clamp(float(island.anisotropy), 0.0, 1.0)
            except (AttributeError, TypeError, ValueError):
                anisotropy = 0.0
            if (
                edge_angle is not None
                and anisotropy + _EPSILON >= float(settings.min_pca_anisotropy)
                and abs(
                    _line_angle_wrap(
                        float(edge_angle) - float(island.principal_angle)
                    )
                ) <= 1.0e-7
            ):
                edge_confidence = max(edge_confidence, anisotropy)
            try:
                edge_value = float(edge_angle)
            except (TypeError, ValueError):
                edge_value = None
            if (
                edge_value is not None
                and math.isfinite(edge_value)
                and edge_confidence + _EPSILON
                >= float(settings.direction_auto_cardinal_min_confidence)
            ):
                final_line = _line_angle_wrap(
                    edge_value + float(
                        math.atan2(component_u, component_v)
                    )
                )
                cardinal_error = abs(_nearest_cardinal_delta(final_line))
                # Area and candidate confidence keep tiny bevels/weak tangent
                # projections from overturning the main panel's vote.
                cardinal_weight = max(area, _EPSILON) * max(
                    edge_confidence, float(settings.direction_auto_cardinal_min_confidence)
                ) * max(confidence, float(settings.direction_axis_min_projection), 0.05)
            candidates[axis_name] = {
                "direction": direction,
                "rotation": _angle_wrap(math.atan2(component_u, component_v)),
                "confidence": confidence,
                "coherent": coherent,
                "stability": (
                    float(values.get("stability_score", 0.0))
                    * float(values.get("normalized_strength", 0.0))
                ),
                "cardinal_error": cardinal_error,
                "cardinal_weight": cardinal_weight,
                "cardinal_edge_confidence": edge_confidence,
            }
        candidate_cache[cache_key] = candidates
        return candidates

    def choose_axis(
        member_ids: Sequence[int],
        preferred_axis: Optional[str] = None,
    ) -> Optional[str]:
        member_candidates = [
            island_candidates(island_id) for island_id in member_ids
        ]
        return _select_common_geometry_axis(
            member_candidates,
            priority,
            preferred_axis=preferred_axis,
            prefer_geometry_axis_cardinal=cardinal_common_enabled,
            geometry_axis_cardinal_min_gain=float(
                settings.geometry_axis_cardinal_min_gain
            ),
            geometry_axis_cardinal_max_quality_loss=float(
                settings.geometry_axis_cardinal_max_quality_loss
            ),
        )

    locked_axes: Dict[int, str] = {}
    unresolved_structure_members: Set[int] = set()

    priority_rank = {
        axis_name: index for index, axis_name in enumerate(priority)
    }

    def partition_compatible_axes(
        member_ids: Sequence[int],
        blocked_ids: Set[int],
        preferred_ids: Set[int],
    ) -> Tuple[List[Tuple[str, Tuple[int, ...]]], Tuple[int, ...]]:
        """Split one constraint component into maximal compatible cohorts.

        A closed hard-surface shell often has no axis tangent to every chart.
        Treating that ordinary case as wholly unresolved discards otherwise
        valid per-island AUTO directions.  Keep the historical one-axis path
        whenever the whole component has a common candidate; otherwise build
        deterministic, largest-first cohorts.  Structure locks are processed
        before flexible members so a repeat relation can extend but never
        replace an already chosen topology axis.
        """

        ordered = tuple(dict.fromkeys(int(island_id) for island_id in member_ids))
        fixed_by_axis: Dict[str, List[int]] = defaultdict(list)
        hard_fixed_axes: Set[str] = set()
        soft_fixed_axes: Set[str] = set()
        flexible: List[int] = []
        unresolved: List[int] = []
        for island_id in ordered:
            candidates = island_candidates(island_id)
            fixed_axis = locked_axes.get(island_id)
            if island_id in blocked_ids:
                unresolved.append(island_id)
            elif fixed_axis is not None:
                if fixed_axis in candidates:
                    fixed_by_axis[fixed_axis].append(island_id)
                    hard_fixed_axes.add(fixed_axis)
                else:
                    unresolved.append(island_id)
            elif island_id in preferred_ids:
                preferred_axis = by_id[island_id].geometry_axis_name
                if preferred_axis in candidates:
                    fixed_by_axis[preferred_axis].append(island_id)
                    soft_fixed_axes.add(preferred_axis)
                elif candidates:
                    flexible.append(island_id)
                else:
                    unresolved.append(island_id)
            elif candidates:
                flexible.append(island_id)
            else:
                unresolved.append(island_id)

        eligible = tuple(
            island_id for island_id in ordered if island_id not in unresolved
        )
        inherited = set(hard_fixed_axes)
        preferred = next(iter(inherited)) if len(inherited) == 1 else None
        if preferred is None and not inherited and len(soft_fixed_axes) == 1:
            preferred = next(iter(soft_fixed_axes))
        shared_axis = (
            choose_axis(eligible, preferred_axis=preferred)
            if eligible and len(inherited) <= 1 else None
        )
        # An owner axis is a split seed, not a topology lock. If that axis is
        # unavailable to one child but another axis is shared by the complete
        # owner cell, prefer one coherent direction over an unnecessary split.
        if shared_axis is None and eligible and not inherited and preferred is not None:
            shared_axis = choose_axis(eligible)
        if shared_axis is not None:
            return [(shared_axis, eligible)], tuple(unresolved)

        remaining = set(flexible)
        cohorts: List[Tuple[str, Tuple[int, ...]]] = []

        def members_for_axis(
            axis_name: str,
            fixed_members: Sequence[int] = (),
        ) -> Tuple[int, ...]:
            selected = set(int(island_id) for island_id in fixed_members)
            selected.update(
                island_id for island_id in remaining
                if axis_name in island_candidates(island_id)
            )
            return tuple(
                island_id for island_id in ordered if island_id in selected
            )

        def cohort_key(
            axis_name: str,
            cohort_members: Sequence[int],
            fixed_count: int,
        ) -> Tuple[Any, ...]:
            values = [
                island_candidates(island_id)[axis_name]
                for island_id in cohort_members
            ]
            return (
                len(cohort_members),
                fixed_count,
                int(all(bool(value.get("coherent", False)) for value in values)),
                min(
                    float(value.get("stability", 0.0)) for value in values
                ),
                min(
                    float(value.get("confidence", 0.0)) for value in values
                ),
                -priority_rank[axis_name],
            )

        # Resolve inherited structure axes first.  Each flexible member joins
        # the largest compatible locked cohort at most once.
        pending_fixed = set(fixed_by_axis)
        while pending_fixed:
            candidates = []
            for axis_name in sorted(
                pending_fixed, key=lambda value: priority_rank[value]
            ):
                fixed_members = fixed_by_axis[axis_name]
                cohort_members = members_for_axis(axis_name, fixed_members)
                candidates.append((
                    cohort_key(axis_name, cohort_members, len(fixed_members)),
                    axis_name,
                    cohort_members,
                ))
            _score, axis_name, cohort_members = max(
                candidates,
                key=lambda item: (item[0], -priority_rank[item[1]]),
            )
            cohorts.append((axis_name, cohort_members))
            remaining.difference_update(cohort_members)
            pending_fixed.remove(axis_name)

        # No inherited axis applies to the remaining charts.  Greedily taking
        # the axis with the greatest coverage yields inclusion-maximal cohorts;
        # all ties are stable across Blender/Python versions.
        while remaining:
            candidates = []
            for axis_name in priority:
                cohort_members = members_for_axis(axis_name)
                if not cohort_members:
                    continue
                candidates.append((
                    cohort_key(axis_name, cohort_members, 0),
                    axis_name,
                    cohort_members,
                ))
            if not candidates:
                unresolved.extend(
                    island_id for island_id in ordered if island_id in remaining
                )
                break
            _score, axis_name, cohort_members = max(
                candidates,
                key=lambda item: (item[0], -priority_rank[item[1]]),
            )
            cohorts.append((axis_name, cohort_members))
            remaining.difference_update(cohort_members)

        return cohorts, tuple(dict.fromkeys(unresolved))

    def apply_components(
        components: Sequence[Sequence[int]],
        source: str,
        lock_members: bool,
        preferred_ids: Optional[Set[int]] = None,
    ) -> None:
        preferred_ids = set(preferred_ids or ())
        for member_ids in components:
            ordered_members = tuple(dict.fromkeys(
                int(island_id) for island_id in member_ids
            ))
            blocked_ids = (
                set(ordered_members) & unresolved_structure_members
                if source == "REPEAT_COHORT" else set()
            )
            previous_by_id = {
                island_id: by_id[island_id].geometry_axis_name
                for island_id in ordered_members
            }
            cohorts, unresolved = partition_compatible_axes(
                ordered_members, blocked_ids, preferred_ids
            )
            split = len(cohorts) + int(bool(unresolved)) > 1
            for cohort_index, (axis_name, cohort_members) in enumerate(cohorts):
                for island_id in cohort_members:
                    candidate = island_candidates(island_id)[axis_name]
                    island = by_id[island_id]
                    island.geometry_axis_name = axis_name
                    island.geometry_direction_space = str(
                        settings.direction_space
                    ).upper()
                    island.geometry_direction_vector = candidate["direction"].copy()
                    island.geometry_rotation_angle = float(candidate["rotation"])
                    island.geometry_direction_confidence = float(
                        candidate["confidence"]
                    )
                    _bind_geometry_frame_metadata(
                        obj,
                        mesh,
                        uv_layer,
                        island,
                        settings,
                        axis_name=axis_name,
                    )
                    if lock_members:
                        locked_axes[island_id] = axis_name
                analysis.geometry_axis_groups.append({
                    "source": source,
                    "members": [int(island_id) for island_id in cohort_members],
                    "component_members": [
                        int(island_id) for island_id in ordered_members
                    ],
                    "selected_axis": axis_name,
                    "previous_axes": [
                        previous_by_id[island_id]
                        for island_id in cohort_members
                    ],
                    "resolved": True,
                    "compatibility_split": split,
                    "compatibility_rank": cohort_index,
                })
            if unresolved:
                # Only charts with no usable candidate (or a previously lost
                # structure lock) are truly unresolved.  Do not erase valid
                # per-island contracts merely because a larger shell needed
                # more than one compatible axis cohort.
                for island_id in unresolved:
                    island = by_id[island_id]
                    island.geometry_axis_name = None
                    island.geometry_direction_vector = Vector((0.0, 0.0))
                    island.geometry_direction_confidence = 0.0
                    island.geometry_rotation_angle = 0.0
                    island.geometry_final_residual = None
                    _clear_geometry_frame_metadata(
                        island, "axis_unresolved"
                    )
                    locked_axes.pop(island_id, None)
                    if source == "STRUCTURE_AFFINITY":
                        unresolved_structure_members.add(island_id)
                analysis.geometry_axis_groups.append({
                    "source": source,
                    "members": [int(island_id) for island_id in unresolved],
                    "component_members": [
                        int(island_id) for island_id in ordered_members
                    ],
                    "selected_axis": None,
                    "previous_axes": [
                        previous_by_id[island_id] for island_id in unresolved
                    ],
                    "resolved": False,
                    "compatibility_split": split,
                    "compatibility_rank": len(cohorts),
                })

    structure_constraints = [
        pair
        for group in analysis.layout_groups
        for pair in group.affinity_pairs
    ]
    structure_components = _constraint_components(
        structure_constraints, valid_ids
    )
    apply_components(structure_components, "STRUCTURE_AFFINITY", True)

    owner_constraints = [
        (int(owner_id), int(child_id))
        for group in analysis.layout_groups
        for child_id, owner_id in zip(
            group.small_member_ids, group.small_anchor_ids
        )
    ]
    owner_ids = {owner_id for owner_id, _child_id in owner_constraints}
    owner_components = _constraint_components(owner_constraints, valid_ids)
    apply_components(
        owner_components,
        "OWNER_ATTACHMENT",
        False,
        preferred_ids=owner_ids,
    )

    repeat_constraints = [
        repeat.member_ids for repeat in analysis.repeat_groups
    ]
    repeat_constraints.extend(
        cohort
        for group in analysis.layout_groups
        for cohort in group.owner_cohorts
    )
    repeat_components = _constraint_components(repeat_constraints, valid_ids)
    apply_components(repeat_components, "REPEAT_COHORT", True)
    # Repeat cohorts can legitimately select a different shared axis from an
    # earlier soft owner pass. Reconcile once more with both topology and
    # repeat locks present: an unlocked owner may follow a locked child, while
    # incompatible hard contracts split instead of overwriting one another.
    apply_components(
        owner_components,
        "OWNER_ATTACHMENT_RECONCILE",
        True,
        preferred_ids=owner_ids,
    )

    # Continuity blocks are a soft locality hint, not a single signed-axis
    # contract: a curved shell commonly contains X-, Y-, and Z-tangent
    # panels in one connected component.  Reconcile only the members that
    # already have a usable axis and let ``partition_compatible_axes`` split
    # incompatible cohorts.  Existing structure/repeat locks remain hard and
    # therefore cannot be replaced by this pass.
    if getattr(settings, "continuity_block_enabled", False):
        groups_by_id = {
            int(group.group_id): group for group in analysis.layout_groups
        }
        continuity_constraints: List[Tuple[int, ...]] = []
        component_groups: Dict[int, List[LayoutGroup]] = defaultdict(list)
        for group in analysis.layout_groups:
            component = group.continuity_component
            if component is None:
                continue
            component_groups[int(component)].append(group)
        for _component, groups in sorted(component_groups.items()):
            if len(groups) < 2:
                continue
            usable_by_group = {
                int(group.group_id): tuple(
                    int(island_id)
                    for island_id in group.member_ids
                    if by_id[int(island_id)].geometry_axis_name in _DIRECTION_AXES
                )
                for group in groups
            }
            # Keep each local owner cell together in the soft pass.  A cell
            # with only one usable member is still linked through its peer
            # representative below when possible.
            for members in usable_by_group.values():
                if len(members) >= 2:
                    continuity_constraints.append(members)
            for group in groups:
                left_members = usable_by_group.get(int(group.group_id), ())
                if not left_members:
                    continue
                left = left_members[0]
                for peer_id in group.continuity_peers:
                    peer_members = usable_by_group.get(int(peer_id), ())
                    if not peer_members:
                        continue
                    continuity_constraints.append((left, peer_members[0]))
        continuity_components = _constraint_components(
            continuity_constraints, valid_ids
        )
        apply_components(
            continuity_components,
            "CONTINUITY_SOFT",
            False,
        )

    # A source unwrap can leave unrelated hard-surface panels in arbitrary UV
    # orientations.  Resolve those charts by *normal domain*, rather than by
    # choosing the first candidate independently.  UV aspect ratio then only
    # affects the quality tie-break; it cannot make two coplanar panels pick
    # different model axes.  Existing structure/repeat/owner/continuity
    # records above are contracts and remain deliberately excluded here.
    if getattr(settings, "cohere_auto_geometry_axis", False):
        try:
            tangent_threshold = _clamp(
                float(settings.geometry_axis_consensus_min_tangent),
                0.0,
                1.0,
            )
        except (AttributeError, TypeError, ValueError):
            tangent_threshold = 0.70
        try:
            confidence_threshold = _clamp(
                float(settings.geometry_axis_consensus_min_confidence),
                0.0,
                1.0,
            )
        except (AttributeError, TypeError, ValueError):
            confidence_threshold = 0.30

        constrained_ids: Set[int] = set()
        constrained_sources = {
            "STRUCTURE_AFFINITY",
            "OWNER_ATTACHMENT",
            "OWNER_ATTACHMENT_RECONCILE",
            "REPEAT_COHORT",
            "CONTINUITY_SOFT",
        }
        for record in analysis.geometry_axis_groups:
            if str(record.get("source", "")) not in constrained_sources:
                continue
            constrained_ids.update(
                int(island_id) for island_id in record.get("members", ())
            )

        normals: Dict[int, Vector] = {}
        consensus_skipped: Dict[str, int] = defaultdict(int)
        for island in analysis.islands:
            island_id = int(island.island_id)
            if island_id in constrained_ids:
                continue
            normal = _geometry_island_normal(
                obj,
                mesh,
                island.face_indices,
                str(settings.direction_space).upper(),
            )
            if normal.length_squared <= _EPSILON:
                consensus_skipped["normal_unresolved"] += 1
                continue
            normals[island_id] = normal

        normal_domains = _geometry_normal_domains(normals)
        consensus_domain_count = 0
        for domain_index, member_ids in enumerate(normal_domains):
            if not member_ids:
                continue
            consensus_domain_count += 1
            total_area = sum(
                max(float(by_id[island_id].area_3d), _EPSILON)
                for island_id in member_ids
            )
            # Use the numerical candidate set here.  A very elongated but
            # valid chart can have confidence below the perceptual threshold
            # simply because one UV side is short; dropping it would recreate
            # the very X/Z split this pass is intended to remove.
            domain_candidates = {
                island_id: island_candidates(
                    island_id, _GEOMETRY_AXIS_RELATIVE_EPSILON
                )
                for island_id in member_ids
            }
            axis_stats: Dict[str, Dict[str, Any]] = {}
            for axis_name in priority:
                support_ids: List[int] = []
                support_area = 0.0
                weighted_quality = 0.0
                weighted_tangent = 0.0
                weakest_confidence = 1.0
                weakest_stability = 1.0
                for island_id in member_ids:
                    candidate = domain_candidates[island_id].get(axis_name)
                    tangent = _geometry_axis_tangent_projection(
                        normals[island_id], axis_name
                    )
                    if candidate is not None:
                        try:
                            candidate["tangent_projection"] = tangent
                        except (TypeError, AttributeError):
                            pass
                    # Coherence is the safety gate: it proves that one signed
                    # +V correction is meaningful throughout this chart.  The
                    # confidence value is retained for ranking/diagnostics,
                    # but is not used to eject a numerically valid member from
                    # an otherwise well-supported domain.
                    if (
                        candidate is None
                        or tangent + _EPSILON < tangent_threshold
                        or not bool(candidate.get("coherent", False))
                    ):
                        continue
                    try:
                        confidence = _clamp(
                            float(candidate.get("confidence", 0.0)),
                            0.0,
                            1.0,
                        )
                    except (TypeError, ValueError):
                        confidence = 0.0
                    try:
                        stability = _clamp(
                            float(candidate.get("stability", 0.0)),
                            0.0,
                            1.0,
                        )
                    except (TypeError, ValueError):
                        stability = 0.0
                    area = max(float(by_id[island_id].area_3d), _EPSILON)
                    support_ids.append(island_id)
                    support_area += area
                    weighted_tangent += area * tangent
                    # Geometry/tangent quality dominates; confidence is a
                    # gentle tie-break so source UV scale cannot overturn a
                    # broad common-domain vote.
                    weighted_quality += area * tangent * (
                        0.70 + 0.20 * stability + 0.10 * confidence
                    )
                    weakest_confidence = min(weakest_confidence, confidence)
                    weakest_stability = min(weakest_stability, stability)
                coverage = support_area / max(total_area, _EPSILON)
                axis_stats[axis_name] = {
                    "members": tuple(support_ids),
                    "support_area": support_area,
                    "coverage": coverage,
                    "mean_tangent": weighted_tangent / max(
                        support_area, _EPSILON
                    ),
                    "quality": weighted_quality / max(support_area, _EPSILON),
                    "weakest_confidence": (
                        weakest_confidence if support_ids else 0.0
                    ),
                    "weakest_stability": (
                        weakest_stability if support_ids else 0.0
                    ),
                }

            supported_axes = [
                axis_name for axis_name in priority
                if axis_stats[axis_name]["coverage"] + _EPSILON
                >= _GEOMETRY_AXIS_CONSENSUS_MIN_COVERAGE
            ]
            selected_axis: Optional[str] = None
            if supported_axes:
                # Coverage is the primary contract.  Among near-equal covers,
                # favor geometric tangent/stability and use configured
                # priority only as the deterministic final tie-break.
                best_coverage = max(
                    axis_stats[axis_name]["coverage"]
                    for axis_name in supported_axes
                )
                coverage_pool = [
                    axis_name for axis_name in supported_axes
                    if axis_stats[axis_name]["coverage"] + 0.05
                    >= best_coverage
                ]
                selected_axis = max(
                    coverage_pool,
                    key=lambda axis_name: (
                        axis_stats[axis_name]["quality"],
                        axis_stats[axis_name]["mean_tangent"],
                        axis_stats[axis_name]["weakest_stability"],
                        axis_stats[axis_name]["weakest_confidence"],
                        -priority.index(axis_name),
                    ),
                )

            if selected_axis is None:
                consensus_skipped["no_common_tangent_axis"] += 1
                analysis.geometry_axis_groups.append({
                    "source": "OBJECT_AXIS_CONSENSUS_SKIPPED",
                    "members": [int(island_id) for island_id in member_ids],
                    "component_members": [
                        int(island_id) for island_id in member_ids
                    ],
                    "selected_axis": None,
                    "resolved": False,
                    "compatibility_split": False,
                    "compatibility_rank": 0,
                    "normal_domain": int(domain_index),
                    "min_tangent_projection": round(tangent_threshold, 6),
                    "min_confidence": round(confidence_threshold, 6),
                    "axis_stats": {
                        axis_name: {
                            key: value
                            for key, value in values.items()
                            if key != "members"
                        }
                        for axis_name, values in axis_stats.items()
                    },
                })
                continue

            selected_stats = axis_stats[selected_axis]
            assigned_ids: List[int] = []
            downgraded_ids: List[int] = []
            fallback_ids: List[int] = []
            unavailable_ids: List[int] = []
            for island_id in member_ids:
                island = by_id[island_id]
                candidates = domain_candidates[island_id]
                candidate = candidates.get(selected_axis)
                tangent = _geometry_axis_tangent_projection(
                    normals[island_id], selected_axis
                )
                if (
                    candidate is not None
                    and tangent + _EPSILON >= tangent_threshold
                    and bool(candidate.get("coherent", False))
                ):
                    chosen_axis = selected_axis
                else:
                    # The shared axis is allowed to fall back only when it is
                    # genuinely unavailable for this chart.  Prefer the
                    # existing resolver's axis if it is safe; otherwise use
                    # the strongest coherent tangent candidate.
                    old_axis = (
                        None
                        if island.geometry_axis_name is None
                        else str(island.geometry_axis_name).upper()
                    )
                    old_candidate = candidates.get(old_axis)
                    old_tangent = (
                        _geometry_axis_tangent_projection(
                            normals[island_id], old_axis
                        )
                        if old_axis in _DIRECTION_AXES else 0.0
                    )
                    if (
                        old_candidate is not None
                        and old_tangent + _EPSILON >= tangent_threshold
                        and bool(old_candidate.get("coherent", False))
                    ):
                        chosen_axis = old_axis
                    else:
                        fallback_options = [
                            axis_name for axis_name in priority
                            if axis_name in candidates
                            and _geometry_axis_tangent_projection(
                                normals[island_id], axis_name
                            ) + _EPSILON >= tangent_threshold
                            and bool(candidates[axis_name].get("coherent", False))
                        ]
                        if not fallback_options:
                            consensus_skipped["member_axis_unavailable"] += 1
                            unavailable_ids.append(island_id)
                            continue
                        chosen_axis = max(
                            fallback_options,
                            key=lambda axis_name: (
                                float(candidates[axis_name].get("stability", 0.0)),
                                float(candidates[axis_name].get("confidence", 0.0)),
                                -priority.index(axis_name),
                            ),
                        )
                    fallback_ids.append(island_id)

                chosen_candidate = candidates[chosen_axis]
                old_axis = (
                    None
                    if island.geometry_axis_name is None
                    else str(island.geometry_axis_name).upper()
                )
                island.geometry_axis_name = chosen_axis
                island.geometry_direction_space = str(
                    settings.direction_space
                ).upper()
                island.geometry_direction_vector = chosen_candidate[
                    "direction"
                ].copy()
                island.geometry_direction_confidence = float(
                    chosen_candidate["confidence"]
                )
                island.geometry_rotation_angle = float(
                    chosen_candidate["rotation"]
                )
                island.geometry_final_residual = None
                _bind_geometry_frame_metadata(
                    obj,
                    mesh,
                    uv_layer,
                    island,
                    settings,
                    axis_name=chosen_axis,
                )
                assigned_ids.append(island_id)
                if (
                    chosen_axis == selected_axis
                    and float(chosen_candidate.get("confidence", 0.0))
                    + _EPSILON < confidence_threshold
                ):
                    downgraded_ids.append(island_id)

            if assigned_ids:
                analysis.geometry_axis_groups.append({
                    "source": "OBJECT_AXIS_CONSENSUS",
                    "members": [int(island_id) for island_id in assigned_ids],
                    "component_members": [
                        int(island_id) for island_id in member_ids
                    ],
                    "selected_axis": selected_axis,
                    "resolved": True,
                    "compatibility_split": bool(fallback_ids or unavailable_ids),
                    "compatibility_rank": 0,
                    "normal_domain": int(domain_index),
                    "normal_reference": [
                        round(float(value), 6)
                        for value in tuple(
                            sum(
                                (
                                    normals[island_id]
                                    * max(float(by_id[island_id].area_3d), _EPSILON)
                                    for island_id in member_ids
                                ),
                                Vector((0.0, 0.0, 0.0)),
                            ).normalized()
                        )
                    ],
                    "coverage": round(float(selected_stats["coverage"]), 6),
                    "support_area": round(
                        float(selected_stats["support_area"]), 9
                    ),
                    "support_count": len(selected_stats["members"]),
                    "weakest_confidence": round(
                        float(selected_stats["weakest_confidence"]), 6
                    ),
                    "weakest_stability": round(
                        float(selected_stats["weakest_stability"]), 6
                    ),
                    "min_tangent_projection": round(tangent_threshold, 6),
                    "min_confidence": round(confidence_threshold, 6),
                    "downgraded_ids": [int(island_id) for island_id in downgraded_ids],
                    "fallback_ids": [int(island_id) for island_id in fallback_ids],
                    "unavailable_ids": [
                        int(island_id) for island_id in unavailable_ids
                    ],
                })

        if consensus_skipped:
            analysis.geometry_axis_groups.append({
                "source": "OBJECT_AXIS_CONSENSUS_SKIPPED",
                "members": [],
                "component_members": [],
                "selected_axis": None,
                "resolved": False,
                "compatibility_split": False,
                "compatibility_rank": 0,
                "normal_domains": int(consensus_domain_count),
                "min_tangent_projection": round(tangent_threshold, 6),
                "min_confidence": round(confidence_threshold, 6),
                "skipped": {
                    str(reason): int(count)
                    for reason, count in sorted(consensus_skipped.items())
                },
            })


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
        islands,
        repeat_groups,
        object_diagonal,
        settings,
        adjacency=adjacency,
    )
    analysis = UVLayoutAnalysis(
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
    _cohere_auto_geometry_axes(
        obj, mesh, uv_layer, analysis, settings
    )
    # Reuse the axis selected by the last successful layout when the same
    # face-set/topology is analyzed again.  This closes the save/reopen gap in
    # AUTO mode where a rotated UV can make a different tangent axis win.
    _apply_persisted_geometry_axis_contract(
        obj, mesh, uv_layer, analysis, settings
    )
    return _apply_orientation_policy(analysis, settings)


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
    min_pca_anisotropy: float = 0.06,
    min_edge_confidence: float = 0.15,
) -> float:
    return _member_target_angle(
        group.member_ids,
        by_id,
        min_pca_anisotropy=min_pca_anisotropy,
        min_edge_confidence=min_edge_confidence,
    )


def _member_target_angle(
    member_ids: Sequence[int],
    by_id: Mapping[int, IslandRecord],
    min_pca_anisotropy: float = 0.06,
    min_edge_confidence: float = 0.15,
) -> float:
    scores = []
    for target in (0.0, math.pi * 0.5):
        score = sum(
            abs(_line_angle_wrap(
                target - (
                    _orientation_reference_angle(
                        by_id[index],
                        min_pca_anisotropy=min_pca_anisotropy,
                        min_edge_confidence=min_edge_confidence,
                    )
                    or 0.0
                )
            ))
            * max(
                by_id[index].anisotropy,
                float(getattr(by_id[index], "dominant_edge_confidence", 0.0)),
                0.05,
            )
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


def _geometry_orientation_components(
    analysis: UVLayoutAnalysis,
) -> Tuple[Tuple[int, ...], ...]:
    """Return signed-direction components, including bounded structure pairs.

    Repeat and owner cohorts are the primary semantic contracts.  A topology
    affinity pair is a weaker signal, but it is still useful for keeping two
    adjacent panels on the same visual heading.  The caller splits each
    component by the already selected geometry axis before applying any
    angle, so an affinity chain that crosses X/Y/Z tangent domains can never
    force one signed rotation onto incompatible surfaces.
    """

    valid_ids = {island.island_id for island in analysis.islands}
    constraints: List[Tuple[int, ...]] = list(
        _orientation_components(analysis)
    )
    for group in analysis.layout_groups:
        for pair in group.affinity_pairs:
            if len(pair) != 2:
                continue
            members = tuple(
                int(island_id)
                for island_id in pair
                if int(island_id) in valid_ids
            )
            if len(set(members)) >= 2:
                constraints.append(members)
    return _constraint_components(constraints, valid_ids)


def _geometry_angle_cohorts(
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
    rotation_overrides: Optional[Mapping[int, float]] = None,
) -> Tuple[Tuple[Tuple[int, ...], float], ...]:
    """Find same-axis cohorts whose strict rotations can be safely shared.

    ``geometry_rotation_angle`` is the correction that maps the selected
    positive model axis to UV ``+V``.  If two related charts differ by a few
    degrees, writing the exact per-chart corrections makes a checker pattern
    visibly fan out even though every chart is technically valid.  Choose a
    deterministic circular median in that narrow case.  A cohort with a real
    90/180-degree difference is left untouched: its individual correction is
    required to preserve the signed model-axis contract.
    """

    by_id = {island.island_id: island for island in analysis.islands}
    try:
        configured_tolerance = abs(float(settings.direction_residual_tolerance))
    except (TypeError, ValueError):
        configured_tolerance = _GEOMETRY_GROUP_ANGLE_TOLERANCE
    tolerance = min(
        _GEOMETRY_GROUP_ANGLE_TOLERANCE,
        configured_tolerance if math.isfinite(configured_tolerance)
        else _GEOMETRY_GROUP_ANGLE_TOLERANCE,
    )
    if tolerance <= _EPSILON:
        return ()

    result: List[Tuple[Tuple[int, ...], float]] = []
    for component in _geometry_orientation_components(analysis):
        by_axis: Dict[str, List[int]] = defaultdict(list)
        for island_id in component:
            island = by_id.get(int(island_id))
            if island is None or not _geometry_direction_is_reliable(
                island, settings
            ):
                continue
            axis_name = str(island.geometry_axis_name).upper()
            if axis_name in _DIRECTION_AXES:
                by_axis[axis_name].append(int(island_id))

        for axis_name in sorted(by_axis):
            members = tuple(sorted(set(by_axis[axis_name])))
            if len(members) < 2:
                continue
            rotations = {
                island_id: _angle_wrap(
                    float(
                        rotation_overrides.get(
                            island_id,
                            by_id[island_id].geometry_rotation_angle,
                        )
                        if rotation_overrides is not None
                        else by_id[island_id].geometry_rotation_angle
                    )
                )
                for island_id in members
            }
            weights = {}
            for island_id in members:
                island = by_id[island_id]
                try:
                    area = abs(float(island.area_3d))
                except (AttributeError, TypeError, ValueError):
                    area = 0.0
                try:
                    confidence = abs(
                        float(island.geometry_direction_confidence)
                    )
                except (AttributeError, TypeError, ValueError):
                    confidence = 0.0
                if not math.isfinite(area):
                    area = 0.0
                if not math.isfinite(confidence):
                    confidence = 0.0
                # Area gives a stable anchor for a panel plus its bevels, but
                # never let a zero-area test double disappear from the vote.
                weights[island_id] = max(area, _EPSILON) * max(
                    confidence, 0.05
                )

            def score(candidate_id: int) -> Tuple[float, float, int]:
                candidate = rotations[candidate_id]
                total = sum(
                    weights[island_id]
                    * abs(_angle_wrap(candidate - rotations[island_id]))
                    for island_id in members
                )
                return (
                    total,
                    -weights[candidate_id],
                    int(candidate_id),
                )

            target_id = min(members, key=score)
            target = rotations[target_id]
            residuals = [
                abs(_angle_wrap(target - rotations[island_id]))
                for island_id in members
            ]
            if max(residuals, default=math.inf) > tolerance + _EPSILON:
                continue
            if rotation_overrides is not None and any(
                abs(_angle_wrap(
                    target
                    - float(by_id[island_id].geometry_rotation_angle)
                )) > configured_tolerance + _EPSILON
                for island_id in members
            ):
                # A frame-smoothed target must still satisfy every member's
                # positive-axis contract.  Two individually valid frame
                # offsets can otherwise accumulate beyond the gate.
                continue
            result.append((members, target))
    return tuple(result)


def _geometry_long_edge_components(
    analysis: UVLayoutAnalysis,
    *,
    transitive: bool = True,
) -> Tuple[Mapping[str, Any], ...]:
    """Return semantic cohorts eligible for long-edge heading coherence.

    The existing direction pass intentionally keeps repeat, owner, structure,
    and continuity constraints separate.  The visual long-edge pass needs the
    same relationships, but must retain their provenance for an auditable
    downgrade report.  Build one deterministic union-find over those
    relationships, then split by ``geometry_axis_name`` in the caller.  No
    UV/topology data is changed here.
    """

    valid_ids = {
        int(island.island_id) for island in analysis.islands
    }
    constraints: List[Tuple[Tuple[int, ...], str]] = []

    def add_constraint(raw_members: Iterable[int], source: str) -> None:
        members = tuple(dict.fromkeys(
            int(island_id)
            for island_id in raw_members
            if int(island_id) in valid_ids
        ))
        if len(members) >= 2:
            constraints.append((members, str(source)))

    for repeat in analysis.repeat_groups:
        add_constraint(repeat.member_ids, "REPEAT")

    groups_by_id = {
        int(group.group_id): group for group in analysis.layout_groups
    }
    continuity_component_groups: Dict[int, List[LayoutGroup]] = defaultdict(list)
    for group in analysis.layout_groups:
        # A bounded structure/layout block is a useful cohort even when it
        # contains no explicit owner cohort (for example, a panel plus its
        # bevel charts).
        add_constraint(group.member_ids, "STRUCTURE")
        for cohort in group.owner_cohorts:
            add_constraint(cohort, "OWNER")
        for pair in group.affinity_pairs:
            add_constraint(pair, "STRUCTURE")
        if group.continuity_component is not None:
            continuity_component_groups[int(group.continuity_component)].append(
                group
            )
        # Small-owner attachments are a semantic owner relation even when a
        # caller did not populate ``owner_cohorts``.
        for child_id, owner_id in zip(
            group.small_member_ids, group.small_anchor_ids
        ):
            add_constraint((owner_id, child_id), "OWNER")

    # Continuity peers are intentionally linked by one representative per
    # layout group.  Linking every child across peer groups would turn a long
    # shell into one mixed-axis clique and recreate the scattered atlas this
    # pass is meant to avoid.  The full local group was already added above.
    def representative(group: LayoutGroup) -> Optional[int]:
        for island_id in tuple(group.anchor_ids) + tuple(group.member_ids):
            island_id = int(island_id)
            if island_id in valid_ids:
                return island_id
        return None

    for group in analysis.layout_groups:
        left = representative(group)
        if left is None:
            continue
        for peer_id in group.continuity_peers:
            peer = groups_by_id.get(int(peer_id))
            if peer is None:
                continue
            right = representative(peer)
            if right is not None:
                add_constraint((left, right), "CONTINUITY")

    # Some callers retain only the component token and omit the sparse peer
    # forest.  Connect representatives within that bounded component so the
    # option still honors the declared continuity relation.
    for _component, groups in sorted(continuity_component_groups.items()):
        reps = tuple(
            rep for rep in (representative(group) for group in groups)
            if rep is not None
        )
        if len(reps) >= 2:
            add_constraint(reps, "CONTINUITY")

    if not constraints:
        return ()

    if not transitive:
        # Long-edge coherence is intentionally more conservative than the
        # signed-axis resolver: a transitive repeat/continuity chain can span
        # an entire weapon and hide the fact that its local panels have
        # different headings.  Keep each semantic relation as its own cohort
        # and merge only exact duplicate member sets for deterministic output.
        relation_sources: Dict[frozenset, Set[str]] = defaultdict(set)
        relation_members: Dict[frozenset, Tuple[int, ...]] = {}
        for members, source in constraints:
            key = frozenset(members)
            relation_sources[key].add(source)
            relation_members.setdefault(key, tuple(sorted(key)))
        return tuple({
            "members": relation_members[key],
            "sources": tuple(sorted(relation_sources[key])),
        } for key in sorted(
            relation_members,
            key=lambda value: (min(value), tuple(sorted(value))),
        ) if len(key) >= 2)

    parent: Dict[int, int] = {}

    def find(island_id: int) -> int:
        parent.setdefault(island_id, island_id)
        while parent[island_id] != island_id:
            parent[island_id] = parent[parent[island_id]]
            island_id = parent[island_id]
        return island_id

    for members, _source in constraints:
        root = find(members[0])
        for island_id in members[1:]:
            other = find(island_id)
            if other != root:
                parent[other] = root

    components: Dict[int, List[int]] = defaultdict(list)
    sources: Dict[int, Set[str]] = defaultdict(set)
    for island_id in sorted(parent):
        components[find(island_id)].append(island_id)
    for members, source in constraints:
        root = find(members[0])
        sources[root].add(source)

    result = []
    for root, members in sorted(
        components.items(), key=lambda item: (min(item[1]), tuple(item[1]))
    ):
        if len(members) < 2:
            continue
        result.append({
            "members": tuple(members),
            "sources": tuple(sorted(sources.get(root, ()))),
        })
    return tuple(result)


def _geometry_long_edge_weight(island: IslandRecord) -> float:
    """Return a stable vote weight for a chart's long-edge direction."""

    try:
        area = abs(float(island.area_3d))
    except (AttributeError, TypeError, ValueError):
        area = 0.0
    try:
        anisotropy = abs(float(island.anisotropy))
    except (AttributeError, TypeError, ValueError):
        anisotropy = 0.0
    try:
        edge_confidence = abs(float(
            getattr(island, "dominant_edge_confidence", 0.0)
        ))
    except (AttributeError, TypeError, ValueError):
        edge_confidence = 0.0
    if not math.isfinite(area):
        area = 0.0
    if not math.isfinite(anisotropy):
        anisotropy = 0.0
    if not math.isfinite(edge_confidence):
        edge_confidence = 0.0
    confidence = _clamp(max(anisotropy, edge_confidence), 0.05, 1.0)
    return max(area, _EPSILON) * confidence


def _geometry_long_edge_coherence(
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
    angles: Mapping[int, float],
) -> Tuple[Dict[int, float], Mapping[str, Any]]:
    """Snap related same-axis charts to one cardinal long-edge heading.

    Geometry alignment is a signed contract: the selected positive model axis
    must still land on UV ``+V``.  Therefore this pass only consumes the
    residual angle budget left by the base (single-axis or complete-frame)
    correction.  A member that would exceed that budget is left at its base
    rotation and receives an individual, machine-readable downgrade record.
    """

    by_id = {int(island.island_id): island for island in analysis.islands}
    result_angles = {
        int(island_id): _angle_wrap(float(angle))
        for island_id, angle in angles.items()
    }
    report: Dict[str, Any] = {
        "enabled": bool(
            settings.cohere_geometry_long_edge
            and settings.align_geometry_direction
        ),
        "max_spread_degrees": round(
            math.degrees(_GEOMETRY_LONG_EDGE_MAX_SPREAD), 6
        ),
        "strict_tolerance_degrees": round(
            math.degrees(abs(float(settings.direction_residual_tolerance))), 6
        ),
        "cohorts_considered": 0,
        "cohorts_with_reference": 0,
        "cohorts_aligned": 0,
        "attempted_islands": 0,
        "aligned_islands": 0,
        "downgraded_islands": 0,
        "downgraded_ids": [],
        "max_final_spread_degrees": 0.0,
        "max_source_spread_degrees": 0.0,
        "targets": {"0": 0, "90": 0},
        "cohorts": [],
    }

    # Reset transient diagnostics so repeated adaptive trials cannot append
    # stale records to the manifest.
    for island in analysis.islands:
        island.geometry_long_edge_angle = None
        island.geometry_long_edge_target_angle = None
        island.geometry_long_edge_correction = 0.0
        island.geometry_long_edge_aligned = False
        island.geometry_long_edge_downgrade_reason = None

    if not report["enabled"]:
        report["disabled_reason"] = (
            "geometry_direction_disabled"
            if not settings.align_geometry_direction
            else "option_disabled"
        )
        analysis.geometry_long_edge_metrics = report
        return result_angles, report

    try:
        tolerance = abs(float(settings.direction_residual_tolerance))
    except (TypeError, ValueError):
        tolerance = _GEOMETRY_GROUP_ANGLE_TOLERANCE
    if not math.isfinite(tolerance):
        tolerance = _GEOMETRY_GROUP_ANGLE_TOLERANCE
    tolerance = max(tolerance, 0.0)

    # Drop only records generated by this pass; cardinal landmark downgrades
    # from ``_apply_orientation_policy`` remain intact.
    analysis.orientation_downgrades = [
        item for item in analysis.orientation_downgrades
        if not str(item.get("reason", "")).startswith("long_edge_")
    ]

    relation_candidates = []
    for component in _geometry_long_edge_components(
        analysis,
        transitive=False,
    ):
        component_members = tuple(int(value) for value in component["members"])
        sources = tuple(component.get("sources", ()))
        by_axis: Dict[str, List[int]] = defaultdict(list)
        for island_id in component_members:
            island = by_id.get(island_id)
            if island is None:
                continue
            axis_name = str(island.geometry_axis_name or "").upper()
            if axis_name in _DIRECTION_AXES:
                by_axis[axis_name].append(island_id)

        for axis_name in sorted(by_axis):
            members = tuple(sorted(set(by_axis[axis_name])))
            if len(members) < 2:
                continue
            relation_candidates.append({
                "members": members,
                "axis": axis_name,
                "sources": sources,
            })

    # Relations overlap by design (an owner pair can also be in a repeat and
    # a continuity block).  Claim each island once, preferring the strongest
    # semantic relation and then the smallest/local cohort.  This prevents a
    # long transitive chain from inflating metrics or repeatedly overwriting a
    # chart's downgrade reason.
    source_rank = {
        "REPEAT": 0,
        "OWNER": 1,
        "STRUCTURE": 2,
        "CONTINUITY": 3,
    }
    relation_candidates.sort(key=lambda item: (
        min(
            source_rank.get(str(source), len(source_rank))
            for source in item["sources"]
        ) if item["sources"] else len(source_rank),
        len(item["members"]),
        min(item["members"]),
        tuple(item["members"]),
    ))
    claimed_islands: Set[int] = set()
    for candidate in relation_candidates:
        members = tuple(
            int(island_id)
            for island_id in candidate["members"]
            if int(island_id) not in claimed_islands
        )
        if len(members) < 2:
            continue
        report["cohorts_considered"] += 1
        axis_name = str(candidate["axis"])
        sources = tuple(candidate.get("sources", ()))
        claimed_islands.update(members)
        report["cohorts_with_reference"] += 1
        member_data = []
        for island_id in members:
            island = by_id[island_id]
            reference = _orientation_reference_angle(
                island,
                min_pca_anisotropy=settings.min_pca_anisotropy,
                min_edge_confidence=settings.min_cardinal_edge_confidence,
            )
            base_angle = _angle_wrap(result_angles.get(island_id, 0.0))
            strict_angle = _angle_wrap(
                float(getattr(island, "geometry_rotation_angle", 0.0))
            )
            base_residual = abs(_angle_wrap(base_angle - strict_angle))
            if reference is None or not math.isfinite(float(reference)):
                member_data.append({
                    "id": island_id,
                    "island": island,
                    "reference": None,
                    "base_angle": base_angle,
                    "strict_angle": strict_angle,
                    "base_residual": base_residual,
                    "weight": _geometry_long_edge_weight(island),
                })
                continue
            member_data.append({
                "id": island_id,
                "island": island,
                "reference": _line_angle_wrap(float(reference)),
                "base_angle": base_angle,
                "strict_angle": strict_angle,
                "base_residual": base_residual,
                "weight": _geometry_long_edge_weight(island),
            })

        with_reference = [
            item for item in member_data if item["reference"] is not None
        ]
        if len(with_reference) < 2:
            # There is no reliable cohort vote.  Preserve the base
            # rotation and record the reason per member for auditability.
            for item in member_data:
                island = item["island"]
                island.geometry_long_edge_angle = None
                island.geometry_long_edge_downgrade_reason = (
                    "long_edge_reference_unresolved"
                )
                analysis.orientation_downgrades.append({
                    "members": [int(item["id"])],
                    "cohort_members": [int(value) for value in members],
                    "axis": axis_name,
                    "sources": list(sources),
                    "reason": "long_edge_reference_unresolved",
                })
            report["downgraded_islands"] += len(member_data)
            report["downgraded_ids"].extend(
                int(item["id"]) for item in member_data
            )
            continue

        for item in with_reference:
            item["current"] = _line_angle_wrap(
                item["reference"] + item["base_angle"]
            )

        # A cohort that already spans two materially different headings
        # is not a safe candidate for a shared cardinal target.  Reject
        # it as one unit before voting; otherwise a large structural
        # component could opportunistically rotate a handful of members
        # while leaving the remainder at a conflicting angle.  This is
        # the explicit 45-degree cohort contract, while the per-island
        # residual gate below handles smaller, individually unsafe
        # corrections.
        source_spread = 0.0
        source_lines = [item["current"] for item in with_reference]
        for left_index, left in enumerate(source_lines):
            for right in source_lines[left_index + 1:]:
                source_spread = max(
                    source_spread,
                    abs(_line_angle_wrap(left - right)),
                )
        if source_spread > _GEOMETRY_LONG_EDGE_MAX_SPREAD + _EPSILON:
            report["attempted_islands"] += len(members)
            skipped_ids = []
            for item in member_data:
                island = item["island"]
                island.geometry_long_edge_target_angle = None
                island.geometry_long_edge_correction = 0.0
                if item.get("reference") is None:
                    reason = "long_edge_reference_unresolved"
                    island.geometry_long_edge_angle = None
                else:
                    reason = "long_edge_cohort_spread_exceeds_45_degrees"
                    island.geometry_long_edge_angle = _line_angle_wrap(
                        float(item["current"])
                    )
                island.geometry_long_edge_aligned = False
                island.geometry_long_edge_downgrade_reason = reason
                skipped_ids.append(int(item["id"]))
                analysis.orientation_downgrades.append({
                    "members": [int(item["id"])],
                    "cohort_members": [int(value) for value in members],
                    "axis": axis_name,
                    "sources": list(sources),
                    "source_spread_degrees": round(
                        math.degrees(source_spread), 6
                    ),
                    "reason": reason,
                })
            report["downgraded_islands"] += len(member_data)
            report["downgraded_ids"].extend(skipped_ids)
            report["max_source_spread_degrees"] = max(
                float(report["max_source_spread_degrees"]),
                math.degrees(source_spread),
            )
            report["cohorts"].append({
                "members": [int(value) for value in members],
                "axis": axis_name,
                "sources": list(sources),
                "target_degrees": None,
                "aligned_ids": [],
                "downgraded_ids": skipped_ids,
                "spread_degrees": round(
                    math.degrees(source_spread), 6
                ),
                "status": "skipped_source_spread",
            })
            continue

        def target_score(target: float) -> Tuple[float, int, float, int]:
            accepted_weight = 0.0
            accepted_count = 0
            residual_cost = 0.0
            for item in with_reference:
                correction = _line_angle_wrap(target - item["current"])
                final_residual = abs(_angle_wrap(
                    item["base_angle"] + correction
                    - item["strict_angle"]
                ))
                accepted = bool(
                    abs(correction)
                    <= _GEOMETRY_LONG_EDGE_MAX_SPREAD + _EPSILON
                    and final_residual <= tolerance + _EPSILON
                )
                if accepted:
                    accepted_weight += float(item["weight"])
                    accepted_count += 1
                    residual_cost += float(item["weight"]) * abs(correction)
            # Prefer the horizontal target on a complete tie.  The
            # negative target index is deterministic for ``max``.
            target_index = 1 if abs(target) > _EPSILON else 0
            return (
                accepted_weight,
                accepted_count,
                -residual_cost,
                -target_index,
            )

        target_candidates = (0.0, math.pi * 0.5)
        target = max(target_candidates, key=target_score)
        target_index = 1 if abs(target) > _EPSILON else 0
        target_label = "90" if target_index else "0"
        report["targets"][target_label] += 1
        report["attempted_islands"] += len(members)
        accepted_ids: List[int] = []
        final_angles: Dict[int, float] = {}
        cohort_downgrades = []

        for item in member_data:
            island = item["island"]
            island.geometry_long_edge_target_angle = target
            island.geometry_long_edge_correction = 0.0
            if item.get("reference") is None:
                reason = "long_edge_reference_unresolved"
                island.geometry_long_edge_downgrade_reason = reason
                cohort_downgrades.append((item, reason, None))
                final_angles[item["id"]] = _line_angle_wrap(
                    float(item.get("base_angle", 0.0))
                )
                continue
            correction = _line_angle_wrap(
                target - float(item["current"])
            )
            final_angle = _angle_wrap(item["base_angle"] + correction)
            final_residual = abs(_angle_wrap(
                final_angle - item["strict_angle"]
            ))
            if abs(correction) > _GEOMETRY_LONG_EDGE_MAX_SPREAD + _EPSILON:
                reason = "long_edge_correction_exceeds_45_degrees"
            elif final_residual > tolerance + _EPSILON:
                reason = "long_edge_strict_tolerance_budget_exceeded"
            else:
                result_angles[item["id"]] = final_angle
                island.geometry_long_edge_correction = correction
                island.geometry_long_edge_aligned = True
                island.geometry_long_edge_downgrade_reason = None
                final_long = _line_angle_wrap(
                    float(item["reference"]) + final_angle
                )
                island.geometry_long_edge_angle = final_long
                accepted_ids.append(item["id"])
                final_angles[item["id"]] = final_long
                continue
            # Keep the base (strict) rotation when the visual correction
            # cannot be admitted.  This is the per-island fallback.
            island.geometry_long_edge_downgrade_reason = reason
            final_long = _line_angle_wrap(
                float(item["reference"]) + item["base_angle"]
            )
            island.geometry_long_edge_angle = final_long
            final_angles[item["id"]] = final_long
            cohort_downgrades.append((item, reason, correction))

        # A cardinal target makes accepted members coincident in line
        # space.  Keep an explicit spread guard so future target policies
        # cannot silently violate the public 45-degree contract.
        accepted_lines = [
            final_angles[item_id] for item_id in accepted_ids
            if item_id in final_angles
        ]
        spread = 0.0
        for left_index, left in enumerate(accepted_lines):
            for right in accepted_lines[left_index + 1:]:
                spread = max(spread, abs(_line_angle_wrap(left - right)))
        report["max_final_spread_degrees"] = max(
            float(report["max_final_spread_degrees"]),
            math.degrees(spread),
        )
        if spread > _GEOMETRY_LONG_EDGE_MAX_SPREAD + _EPSILON:
            # Defensive rollback of the whole accepted subset.  This is
            # unlikely with the two cardinal targets, but keeps the
            # contract true if the target selector is extended later.
            for item_id in accepted_ids:
                item = by_id[item_id]
                result_angles[item_id] = item.geometry_rotation_angle
                item.geometry_long_edge_aligned = False
                item.geometry_long_edge_correction = 0.0
                item.geometry_long_edge_downgrade_reason = (
                    "long_edge_cohort_spread_exceeds_45_degrees"
                )
                cohort_downgrades.append((
                    next(value for value in member_data if value["id"] == item_id),
                    "long_edge_cohort_spread_exceeds_45_degrees",
                    None,
                ))
            accepted_ids = []

        if accepted_ids:
            report["cohorts_aligned"] += 1
            report["aligned_islands"] += len(accepted_ids)
        for item, reason, correction in cohort_downgrades:
            island_id = int(item["id"])
            correction_degrees = (
                None
                if correction is None
                else round(math.degrees(float(correction)), 6)
            )
            analysis.orientation_downgrades.append({
                "members": [island_id],
                "cohort_members": [int(value) for value in members],
                "axis": axis_name,
                "sources": list(sources),
                "target_degrees": round(math.degrees(target), 6),
                "base_long_edge_degrees": (
                    None
                    if item.get("reference") is None
                    else round(math.degrees(item["current"]), 6)
                ),
                "correction_degrees": correction_degrees,
                "reason": reason,
            })
        report["downgraded_islands"] += len(cohort_downgrades)
        report["downgraded_ids"].extend(
            int(item["id"]) for item, _reason, _correction
            in cohort_downgrades
        )
        report["cohorts"].append({
            "members": [int(value) for value in members],
            "axis": axis_name,
            "sources": list(sources),
            "target_degrees": round(math.degrees(target), 6),
            "aligned_ids": [int(value) for value in accepted_ids],
            "downgraded_ids": [
                int(item["id"])
                for item, _reason, _correction in cohort_downgrades
            ],
            "spread_degrees": round(math.degrees(spread), 6),
        })

    report["downgraded_ids"] = sorted(set(report["downgraded_ids"]))
    analysis.geometry_long_edge_metrics = report
    return result_angles, report


def _direction_is_reliable(
    island: IslandRecord,
    settings: GroupLayoutOptions,
) -> bool:
    """Return whether an island has a finite, sufficiently salient landmark."""

    vector = island.direction_vector
    return bool(
        island.direction_confidence >= settings.min_direction_confidence
        and vector.length_squared > _EPSILON
        and all(math.isfinite(float(component)) for component in vector)
    )


def _geometry_direction_is_reliable(
    island: IslandRecord,
    settings: GroupLayoutOptions,
) -> bool:
    vector = island.geometry_direction_vector
    return bool(
        settings.align_geometry_direction
        and island.geometry_axis_name in _DIRECTION_AXES
        and float(island.geometry_direction_confidence) + _EPSILON
        >= float(settings.direction_axis_min_projection)
        and vector.length_squared > _EPSILON
        and all(math.isfinite(float(component)) for component in vector)
        and math.isfinite(float(island.geometry_rotation_angle))
    )


def _geometry_frame_axis_residual(
    island: IslandRecord,
) -> Optional[float]:
    """Return the signed-frame rotation error relative to strict ``+V``.

    The complete-frame solver averages the perpendicular tangent over all
    chart faces.  That average can be internally orthogonal yet disagree with
    the selected model-axis rotation.  Keep this check modulo 360 degrees:
    a 180-degree difference is a real texture-direction flip, not an
    equivalent line orientation.
    """

    try:
        frame_rotation = float(
            getattr(island, "geometry_frame_rotation_angle")
        )
        axis_rotation = float(
            getattr(island, "geometry_rotation_angle")
        )
    except (AttributeError, TypeError, ValueError):
        return None
    residual = abs(_angle_wrap(frame_rotation - axis_rotation))
    return residual if math.isfinite(residual) else None


def _geometry_frame_is_reliable(
    island: IslandRecord,
    settings: GroupLayoutOptions,
) -> bool:
    """Return whether a positive, coherent signed U/V frame is available.

    A high-confidence frame can still be a poor orientation cue when the
    island is curved or its UV Jacobians contain shear.  In that case the
    averaged perpendicular axis can be several degrees away from the axis
    used to resolve ``+V``.  Applying its rotation would visibly skew checker
    directions even though the strict single-axis contract is valid.  Treat
    the residual as part of frame reliability so callers fall back to the
    single-axis rotation for those charts; no reflection or UV writeback is
    performed here.
    """

    if not settings.align_geometry_direction or not settings.align_geometry_frame:
        return False
    if not _geometry_direction_is_reliable(island, settings):
        return False
    if int(getattr(island, "geometry_frame_parity", 0)) != 1:
        return False
    vectors = (
        island.geometry_frame_u_vector,
        island.geometry_frame_v_vector,
    )
    if any(
        vector is None
        or vector.length_squared <= _EPSILON
        or not all(math.isfinite(float(component)) for component in vector)
        for vector in vectors
    ):
        return False
    confidence = float(getattr(island, "geometry_frame_confidence", 0.0))
    if not (
        math.isfinite(confidence)
        and confidence + _EPSILON
        >= min(
            _GEOMETRY_FRAME_MIN_CONCENTRATION,
            _GEOMETRY_FRAME_MIN_EFFECTIVE_FRACTION,
            _GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE,
        )
    ):
        return False
    residual = getattr(island, "geometry_frame_residual", None)
    try:
        residual = float(residual)
        tolerance = float(settings.direction_residual_tolerance)
    except (TypeError, ValueError):
        return False
    axis_residual = _geometry_frame_axis_residual(island)
    return bool(
        math.isfinite(residual)
        and math.isfinite(tolerance)
        and tolerance >= 0.0
        and abs(residual) <= tolerance + _EPSILON
        and axis_residual is not None
        and abs(axis_residual) <= tolerance + _EPSILON
    )


def _direction_reference_residual(
    island: IslandRecord,
    settings: GroupLayoutOptions,
) -> Optional[float]:
    """Return the landmark-vs-geometry residual under a modulo-180 line."""

    if not _direction_is_reliable(island, settings):
        return None
    reference = _orientation_reference_angle(
        island,
        min_pca_anisotropy=settings.min_pca_anisotropy,
        min_edge_confidence=settings.min_cardinal_edge_confidence,
    )
    if reference is None:
        # There is no stable geometric cue to contradict the landmark.  Keep
        # the directed evidence and let the shared target choose cardinality.
        return None
    direction_angle = math.atan2(
        island.direction_vector.y,
        island.direction_vector.x,
    )
    return abs(_line_angle_wrap(direction_angle - reference))


def _component_direction_policy(
    member_ids: Sequence[int],
    by_id: Mapping[int, IslandRecord],
    settings: GroupLayoutOptions,
) -> Tuple[str, Dict[int, Optional[float]]]:
    """Resolve directed vs center-symmetric semantics for one component.

    A component is allowed to use the strict modulo-360 landmark contract
    only when every member has a reliable landmark and every available
    geometric reference agrees with that landmark.  One contradictory cue
    downgrades the whole component, so mirrored hard-surface panels cannot be
    left with one diagonal directed member and one cardinal center-symmetric
    member.
    """

    residuals = {
        island_id: _direction_reference_residual(by_id[island_id], settings)
        for island_id in member_ids
        if island_id in by_id
    }
    if len(residuals) != len(member_ids) or not all(
        _direction_is_reliable(by_id[island_id], settings)
        for island_id in member_ids
        if island_id in by_id
    ):
        return "center_symmetric", residuals
    if not settings.align_directed_cardinal:
        return "directed", residuals
    incompatible = [
        island_id
        for island_id, residual in residuals.items()
        if residual is not None
        and residual > settings.directed_cardinal_tolerance + 1.0e-12
    ]
    if incompatible:
        return "center_symmetric_downgraded", residuals
    return "directed", residuals


def _apply_orientation_policy(
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> UVLayoutAnalysis:
    """Annotate analysis and persist cardinal-compatibility downgrades.

    ``analyze_active_uv`` is also called after a layout commit to create the
    manifest snapshot.  Applying the same policy during every analysis means
    the exported direction metadata and the independent semantic audit agree
    without relying on transient in-memory state from the first pass.
    """

    by_id = {island.island_id: island for island in analysis.islands}
    for island in analysis.islands:
        island.direction_mode = (
            "directed"
            if _direction_is_reliable(island, settings)
            else "center_symmetric"
        )
        island.direction_downgrade_reason = None

    downgrades: List[Mapping[str, Any]] = []
    for member_ids in _orientation_components(analysis):
        policy, residuals = _component_direction_policy(
            member_ids,
            by_id,
            settings,
        )
        if policy != "center_symmetric_downgraded":
            continue
        incompatible = [
            island_id
            for island_id in member_ids
            if residuals.get(island_id) is not None
            and residuals[island_id]
            > settings.directed_cardinal_tolerance + 1.0e-12
        ]
        for island_id in member_ids:
            island = by_id[island_id]
            # Keep the public record honest: once the component is geometric,
            # no member should be exported as a reliable directed landmark.
            island.direction_vector = Vector((0.0, 0.0))
            island.direction_confidence = 0.0
            island.landmark_vertex = None
            island.direction_mode = "center_symmetric"
            island.direction_downgrade_reason = (
                "landmark_geometry_residual_exceeds_cardinal_tolerance"
            )
        downgrades.append({
            "members": [int(island_id) for island_id in member_ids],
            "incompatible_members": [int(island_id) for island_id in incompatible],
            "residual_degrees": {
                str(island_id): (
                    None
                    if residuals.get(island_id) is None
                    else round(math.degrees(residuals[island_id]), 6)
                )
                for island_id in member_ids
            },
            "tolerance_degrees": round(
                math.degrees(settings.directed_cardinal_tolerance), 6
            ),
            "policy": "center_symmetric_modulo_180",
            "reason": "landmark_geometry_residual_exceeds_cardinal_tolerance",
        })

    analysis.orientation_downgrades = downgrades
    analysis.orientation_policy = {
        "geometry_direction_alignment": (
            "positive_{}_axis_to_uv_positive_v".format(
                str(settings.direction_space).lower()
            )
            if settings.align_geometry_direction
            else "disabled"
        ),
        "geometry_direction_axis": str(settings.direction_axis).upper(),
        "geometry_axis_auto_priority": list(_direction_axis_order(
            "AUTO", settings.direction_auto_priority
        )),
        "geometry_axis_cardinal_preference": bool(
            getattr(settings, "prefer_geometry_axis_cardinal", False)
        ),
        "geometry_axis_cardinal_min_gain_degrees": round(
            math.degrees(float(getattr(
                settings, "geometry_axis_cardinal_min_gain", math.radians(5.0)
            ))),
            6,
        ),
        "geometry_axis_cardinal_max_quality_loss": round(
            float(getattr(
                settings, "geometry_axis_cardinal_max_quality_loss", 0.15
            )),
            6,
        ),
        "geometry_axis_auto_cardinal_bias": round(
            float(getattr(settings, "direction_auto_cardinal_bias", 0.0)),
            6,
        ),
        "geometry_axis_auto_cardinal_min_confidence": round(
            float(getattr(
                settings, "direction_auto_cardinal_min_confidence", 0.15
            )),
            6,
        ),
        "geometry_axis_object_consensus": bool(
            getattr(settings, "cohere_auto_geometry_axis", False)
        ),
        "geometry_axis_consensus_min_tangent_projection": round(
            float(getattr(
                settings, "geometry_axis_consensus_min_tangent", 0.70
            )),
            6,
        ),
        "geometry_axis_consensus_min_confidence": round(
            float(getattr(
                settings, "geometry_axis_consensus_min_confidence", 0.30
            )),
            6,
        ),
        "geometry_axis_consensus_normal_angle_degrees": round(
            math.degrees(_GEOMETRY_AXIS_CONSENSUS_NORMAL_ANGLE), 6
        ),
        "geometry_axis_consensus_min_coverage": round(
            float(_GEOMETRY_AXIS_CONSENSUS_MIN_COVERAGE), 6
        ),
        "geometry_resolution_contract": (
            "all_islands"
            if str(settings.direction_axis).upper() == "AUTO"
            else "selected_axis_tangent_islands"
        ),
        "geometry_axis_min_projection": round(
            float(settings.direction_axis_min_projection), 6
        ),
        "geometry_frame_contract": (
            "signed_UV_frame_positive_parity_when_residual_within_tolerance"
            if settings.align_geometry_direction
            and settings.align_geometry_frame
            else "disabled"
        ),
        "geometry_frame_min_confidence": round(
            min(
                _GEOMETRY_FRAME_MIN_CONCENTRATION,
                _GEOMETRY_FRAME_MIN_EFFECTIVE_FRACTION,
                _GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE,
            ),
            6,
        ),
        "geometry_frame_residual_tolerance_degrees": round(
            math.degrees(float(settings.direction_residual_tolerance)), 6
        ),
        "geometry_frame_axis_residual_gate": (
            "frame_rotation_minus_strict_axis_rotation_modulo_360"
        ),
        "pack_rotation_locked": bool(settings.align_geometry_direction),
        "geometry_long_edge_coherence": bool(
            settings.cohere_geometry_long_edge
            and settings.align_geometry_direction
        ),
        "geometry_long_edge_max_spread_degrees": round(
            math.degrees(_GEOMETRY_LONG_EDGE_MAX_SPREAD), 6
        ),
        "geometry_long_edge_strict_tolerance_degrees": round(
            math.degrees(abs(float(settings.direction_residual_tolerance))), 6
        ),
        "geometry_resolved_islands": sum(
            _geometry_direction_is_reliable(island, settings)
            for island in analysis.islands
        ),
        "geometry_frame_resolved_islands": sum(
            _geometry_frame_is_reliable(island, settings)
            for island in analysis.islands
        ),
        "directed_alignment": (
            "modulo_360_when_cardinal_compatible"
            if settings.align_directed_cardinal
            else "modulo_360"
        ),
        "fallback_alignment": "center_symmetric_modulo_180",
        "directed_cardinal_tolerance_degrees": round(
            math.degrees(settings.directed_cardinal_tolerance), 6
        ),
        "min_direction_confidence": round(
            float(settings.min_direction_confidence), 6
        ),
        "downgraded_components": len(downgrades),
    }
    return analysis


def _orientation_angles(
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> Dict[int, float]:
    by_id = {island.island_id: island for island in analysis.islands}
    # The selected model axis remains the primary orientation contract.  An
    # opt-in complete-frame correction is accepted only after the frame gate
    # proves that it has positive parity and remains within the same strict
    # residual window.  Curved or sheared charts therefore retain the stable
    # single-axis correction instead of receiving an unsafe pseudo-fix.
    angles: Dict[int, float] = {}
    for island in analysis.islands:
        if not _geometry_direction_is_reliable(island, settings):
            continue
        rotation = float(island.geometry_rotation_angle)
        if (
            settings.use_geometry_frame_rotation
            and _geometry_frame_is_reliable(island, settings)
        ):
            rotation = float(island.geometry_frame_rotation_angle)
        angles[island.island_id] = _angle_wrap(rotation)
    constrained = set(angles)
    # Keep the strict per-island +V contract as the baseline, then smooth only
    # a same-axis cohort whose corrections already agree within the configured
    # residual window.  This removes small checker-grid drift while preserving
    # genuine quarter-turns and cross-axis tangent domains.
    if settings.cohere_geometry_angle_groups:
        for member_ids, target in _geometry_angle_cohorts(
            analysis, settings, rotation_overrides=angles
        ):
            for island_id in member_ids:
                if island_id in angles:
                    angles[island_id] = target
    angles, long_edge_metrics = _geometry_long_edge_coherence(
        analysis,
        settings,
        angles,
    )
    # Keep the policy and the detailed per-cohort report together in the
    # analysis snapshot consumed by manifests and adaptive candidate scoring.
    analysis.orientation_policy = dict(analysis.orientation_policy)
    analysis.orientation_policy.update({
        "geometry_long_edge_coherence": bool(
            settings.cohere_geometry_long_edge
            and settings.align_geometry_direction
        ),
        "geometry_long_edge_max_spread_degrees": round(
            math.degrees(_GEOMETRY_LONG_EDGE_MAX_SPREAD), 6
        ),
        "geometry_long_edge_strict_tolerance_degrees": round(
            math.degrees(abs(float(settings.direction_residual_tolerance))), 6
        ),
        "geometry_long_edge_attempted_islands": int(
            long_edge_metrics.get("attempted_islands", 0)
        ),
        "geometry_long_edge_aligned_islands": int(
            long_edge_metrics.get("aligned_islands", 0)
        ),
        "geometry_long_edge_downgraded_islands": int(
            long_edge_metrics.get("downgraded_islands", 0)
        ),
    })
    for member_ids in _orientation_components(analysis):
        target = _member_target_angle(
            member_ids,
            by_id,
            min_pca_anisotropy=settings.min_pca_anisotropy,
            min_edge_confidence=settings.min_cardinal_edge_confidence,
        )
        direction_policy, _residuals = _component_direction_policy(
            member_ids,
            by_id,
            settings,
        )
        directed_group = direction_policy == "directed"
        for island_id in member_ids:
            if island_id in constrained:
                continue
            island = by_id[island_id]
            constrained.add(island_id)
            if directed_group:
                direction_angle = math.atan2(
                    island.direction_vector.y, island.direction_vector.x
                )
                # A cardinal-compatible directed landmark is the strict
                # modulo-360 contract.  Components with a contradictory
                # landmark are classified center-symmetric above and take the
                # geometric branch below, keeping hard-surface panels upright.
                angle = _angle_wrap(target - direction_angle)
            else:
                reference = _orientation_reference_angle(
                    island,
                    min_pca_anisotropy=settings.min_pca_anisotropy,
                    min_edge_confidence=settings.min_cardinal_edge_confidence,
                )
                if reference is not None:
                    angle = _line_angle_wrap(target - reference)
                else:
                    angle = 0.0
            angles[island_id] = angle

    for island in analysis.islands:
        if island.island_id in constrained:
            continue
        if (
            settings.align_non_repeat_cardinal
        ):
            reference = _orientation_reference_angle(
                island,
                min_pca_anisotropy=settings.min_pca_anisotropy,
                min_edge_confidence=settings.min_cardinal_edge_confidence,
            )
            angles[island.island_id] = (
                0.0
                if reference is None
                else _nearest_cardinal_delta(reference)
            )
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


def _boost_small_island_coordinates(
    oriented: Mapping[int, Mapping[int, Vector]],
    analysis: UVLayoutAnalysis,
    boost: float,
) -> Tuple[Dict[int, Dict[int, Vector]], Dict[int, float], int]:
    """Apply a bounded uniform scale to classified micro-islands.

    The operation happens before packing, so the normal disjoint-rectangle
    packer can reserve space for the boosted charts.  Group membership and
    repeat detection come from the pre-boost analysis; a chart cannot cease to
    be a small candidate merely because this presentation weighting was
    applied.  Every non-small chart is copied without modification.
    """

    factor = _clamp(float(boost), 1.0, 3.0)
    result: Dict[int, Dict[int, Vector]] = {}
    scales: Dict[int, float] = {}
    scaled_count = 0
    for island in analysis.islands:
        source = oriented.get(island.island_id, {})
        if not source:
            result[island.island_id] = {}
            scales[island.island_id] = 1.0
            continue
        island_factor = factor if island.is_small else 1.0
        if island_factor > 1.0 + 1.0e-9:
            center = sum(source.values(), Vector((0.0, 0.0))) / len(source)
            result[island.island_id] = {
                loop_index: center + (point - center) * island_factor
                for loop_index, point in source.items()
            }
            scaled_count += 1
        else:
            result[island.island_id] = {
                loop_index: point.copy()
                for loop_index, point in source.items()
            }
            island_factor = 1.0
        scales[island.island_id] = island_factor
    return result, scales, scaled_count


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
    # Shelf packing is discontinuous: a tiny change in target width can move
    # one rectangle to another row.  The old sparse candidates could therefore
    # select a long strip even when a nearby width produced a much squarer
    # layout.  Sample the interval deterministically while keeping the list
    # bounded for dense meshes.
    upper = max(total_width, maximum)
    if upper > maximum + _EPSILON:
        for index in range(1, 17):
            fraction = index / 16.0
            values.append(maximum + (upper - maximum) * fraction)
    # Include widths at the area-root neighbourhood with a finer resolution;
    # this is where most hard-surface atlases achieve their best utilization.
    for factor in (0.90, 0.95, 1.05, 1.10):
        values.append(root * factor)
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


def _shelf_rectangle_orders(
    rectangles: Sequence[_Rect],
    enumerate_small_orders: bool = True,
) -> Tuple[Tuple[_Rect, ...], ...]:
    """Return a bounded set of deterministic shelf insertion orders.

    Shelf row breaks depend heavily on insertion order.  Trying a few common
    decreasing-size policies closes large holes without changing the packer's
    rectangle, rotation, or affinity contracts.  For small batches (the common
    case for detached mechanical parts), enumerate every order as a bounded
    exact search; this catches useful 2-row arrangements that no monotone sort
    can express.  Duplicate orders are removed so meshes whose charts already
    share one size do not pay extra work.
    """

    policies = (
        lambda rect: (
            -max(rect.width, rect.height),
            -(rect.width * rect.height),
            rect.sort_rank,
            rect.key,
        ),
        lambda rect: (
            -(rect.width * rect.height),
            -max(rect.width, rect.height),
            rect.sort_rank,
            rect.key,
        ),
        lambda rect: (
            -rect.height,
            -rect.width,
            -(rect.width * rect.height),
            rect.sort_rank,
            rect.key,
        ),
        lambda rect: (
            -rect.width,
            -rect.height,
            -(rect.width * rect.height),
            rect.sort_rank,
            rect.key,
        ),
    )
    orders = []
    seen = set()
    for policy in policies:
        ordered = tuple(sorted(rectangles, key=policy))
        signature = tuple(rect.key for rect in ordered)
        if signature in seen:
            continue
        seen.add(signature)
        orders.append(ordered)
    # Four-island hard-surface groups are where a single misplaced insert can
    # leave an entire half-row empty.  Factorial search is deliberately capped
    # at four rectangles (24 orders); larger owner-cell groups retain the
    # inexpensive heuristic path so dense assets do not pay a combinatorial
    # cost for a marginal packing gain.
    if enumerate_small_orders and len(rectangles) <= 4:
        for ordered in itertools.permutations(rectangles):
            signature = tuple(rect.key for rect in ordered)
            if signature in seen:
                continue
            seen.add(signature)
            orders.append(tuple(ordered))
    return tuple(orders)


def _best_shelf_pack(
    rectangles: Sequence[_Rect],
    gap: float,
    allow_rotate: bool,
    preserve_order: bool = False,
    fixed_quarter_turns: Optional[Mapping[int, bool]] = None,
    square_pack_bias: float = 0.35,
    square_pack_max_edge_relaxation: float = 0.02,
) -> Tuple[Dict[int, _Placement], float, float]:
    if not rectangles:
        return {}, 0.0, 0.0
    candidates = []
    square_pack_bias = _clamp(float(square_pack_bias), 0.0, 1.0)
    square_pack_max_edge_relaxation = float(square_pack_max_edge_relaxation)
    if not math.isfinite(square_pack_max_edge_relaxation):
        raise ValueError("square_pack_max_edge_relaxation must be finite")
    square_pack_max_edge_relaxation = _clamp(
        square_pack_max_edge_relaxation, 0.0, 0.25
    )
    # Exhaustive insertion orders and a longest-edge relaxation are reserved
    # for a small, freely rotatable top-level batch.  Dense owner-cell and
    # large atlas packing keeps the historical deterministic heuristic; local
    # square improvements there can otherwise reduce the global UV scale.
    small_batch_search = bool(
        not preserve_order
        and allow_rotate
        and len(rectangles) <= 4
        and square_pack_bias > 1.0e-12
    )
    bounded_small_search = bool(
        small_batch_search
        and square_pack_bias > 1.0e-12
        and square_pack_max_edge_relaxation > 1.0e-12
    )
    effective_relaxation = (
        square_pack_max_edge_relaxation if bounded_small_search else 0.0
    )
    # Keep the historical insertion order for dense atlases.  Even when the
    # square-search knobs are disabled, trying alternate shelf orders can
    # move a large number of equal-sized owner blocks across row breaks and
    # reduce global texel density.  Exhaustive order search is intentionally
    # limited to the small, freely rotatable batch above.
    orders = (
        _shelf_rectangle_orders(
            rectangles,
            enumerate_small_orders=True,
        )
        if small_batch_search
        else (tuple(rectangles),)
    )
    # A raw singleton order retains the historical longest-edge sort inside
    # ``_shelf_pack_once``.  Explicitly enumerated orders are already sorted
    # by their policy and must be replayed verbatim.
    replay_order = bool(small_batch_search or preserve_order)
    target_widths = _candidate_shelf_widths(rectangles, gap)
    for order_index, ordered in enumerate(orders):
        for target_width in target_widths:
            placements, width, height = _shelf_pack_once(
                ordered,
                target_width,
                gap,
                allow_rotate,
                preserve_order=replay_order,
                fixed_quarter_turns=fixed_quarter_turns,
            )
            maximum = max(width, height)
            minimum = max(min(width, height), _EPSILON)
            aspect = maximum / minimum
            square_score = maximum * (
                1.0
                + square_pack_bias * min(max(aspect - 1.0, 0.0), 2.0)
            )
            candidates.append((
                square_score,
                maximum,
                width * height,
                aspect,
                abs(width - height),
                order_index,
                target_width,
                placements,
                width,
                height,
            ))

    # The first (longest-side) order is the stable compact baseline.  Define
    # that baseline by longest packed edge rather than by the aesthetic score,
    # then permit only the explicitly configured relative relaxation.  This
    # makes the texel-density trade-off auditable and prevents a square bias
    # from silently shrinking every island.
    baseline = min(
        (item for item in candidates if item[5] == 0),
        key=lambda item: (
            item[1],
            item[2],
            item[3],
            item[4],
            item[6],
        ),
    )
    baseline_maximum = baseline[1]
    maximum_allowed = baseline_maximum * (
        1.0 + effective_relaxation
    )
    bounded = [
        item for item in candidates
        if item[1] <= maximum_allowed
        + max(baseline_maximum, 1.0) * 1.0e-12
    ]
    if not bounded:
        # The baseline is always present, but retaining this guard makes the
        # function robust to future candidate filters.
        bounded = [baseline]

    def candidate_key(item):
        _square_score, maximum, area, aspect, delta, order_index, target = item[:7]
        if not bounded_small_search:
            # Preserve the historical compact mode for dense/large layouts:
            # no new aesthetic tie-break should reorder equal-scale shelves.
            return (
                maximum,
                area,
                aspect,
                delta,
                order_index,
                target,
            )
        relative_scale = baseline_maximum / max(maximum, _EPSILON)
        fill = 1.0 / max(aspect, 1.0)
        # Geometric blending keeps both objectives meaningful: bias=0 is pure
        # scale preservation, while larger bias favors a square, area-efficient
        # tile without ever leaving the longest-edge bound above.
        quality = (
            max(relative_scale, _EPSILON) ** (1.0 - square_pack_bias)
            * max(fill, _EPSILON) ** square_pack_bias
        )
        return (
            -quality,
            -relative_scale,
            -fill,
            aspect,
            area,
            order_index,
            target,
        )

    best = min(bounded, key=candidate_key)
    return best[7], best[8], best[9]


def _free_rect_intersects(left: _FreeRect, right: _FreeRect) -> bool:
    return not (
        right.x >= left.x + left.width - _EPSILON
        or right.x + right.width <= left.x + _EPSILON
        or right.y >= left.y + left.height - _EPSILON
        or right.y + right.height <= left.y + _EPSILON
    )


def _free_rect_contains(outer: _FreeRect, inner: _FreeRect) -> bool:
    return bool(
        inner.x >= outer.x - _EPSILON
        and inner.y >= outer.y - _EPSILON
        and inner.x + inner.width <= outer.x + outer.width + _EPSILON
        and inner.y + inner.height <= outer.y + outer.height + _EPSILON
    )


def _split_free_rect(free: _FreeRect, used: _FreeRect) -> List[_FreeRect]:
    if not _free_rect_intersects(free, used):
        return [free]
    free_right = free.x + free.width
    free_top = free.y + free.height
    used_right = used.x + used.width
    used_top = used.y + used.height
    pieces = []
    if used.x > free.x + _EPSILON:
        pieces.append(_FreeRect(
            free.x, free.y, used.x - free.x, free.height
        ))
    if used_right < free_right - _EPSILON:
        pieces.append(_FreeRect(
            used_right, free.y, free_right - used_right, free.height
        ))
    if used.y > free.y + _EPSILON:
        pieces.append(_FreeRect(
            free.x, free.y, free.width, used.y - free.y
        ))
    if used_top < free_top - _EPSILON:
        pieces.append(_FreeRect(
            free.x, used_top, free.width, free_top - used_top
        ))
    return [
        item for item in pieces
        if item.width > _EPSILON and item.height > _EPSILON
    ]


def _prune_free_rectangles(rectangles: Sequence[_FreeRect]) -> List[_FreeRect]:
    ordered = sorted(
        set(rectangles),
        key=lambda item: (item.y, item.x, item.width, item.height),
    )
    return [
        candidate
        for index, candidate in enumerate(ordered)
        if not any(
            index != other_index and _free_rect_contains(other, candidate)
            for other_index, other in enumerate(ordered)
        )
    ]


def _maxrects_pack_once(
    rectangles: Sequence[_Rect],
    target_width: float,
    gap: float,
) -> Optional[Tuple[Dict[int, _Placement], float, float]]:
    """Pack a fixed order with deterministic Best Short Side Fit.

    Rectangles are padded on their right/top edges during subdivision.  The
    reported placements retain the original dimensions, so the pack is a
    translation-only transform with at least ``gap`` between every pair.
    """

    if not rectangles:
        return {}, 0.0, 0.0
    padded_width = float(target_width) + gap
    padded_height = sum(rect.height + gap for rect in rectangles)
    free_rectangles = [_FreeRect(0.0, 0.0, padded_width, padded_height)]
    placements = {}
    for rect in rectangles:
        width = rect.width + gap
        height = rect.height + gap
        choices = []
        for free_index, free in enumerate(free_rectangles):
            if width > free.width + _EPSILON or height > free.height + _EPSILON:
                continue
            horizontal = free.width - width
            vertical = free.height - height
            choices.append((
                min(horizontal, vertical),
                max(horizontal, vertical),
                free.y,
                free.x,
                free.width * free.height,
                free_index,
                free,
            ))
        if not choices:
            return None
        free = min(choices, key=lambda item: item[:-1])[-1]
        used = _FreeRect(free.x, free.y, width, height)
        placements[rect.key] = _Placement(
            free.x, free.y, rect.width, rect.height, quarter_turn=False
        )
        split = []
        for candidate in free_rectangles:
            split.extend(_split_free_rect(candidate, used))
        free_rectangles = _prune_free_rectangles(split)

    packed_width = max(
        placement.x + placement.width for placement in placements.values()
    )
    packed_height = max(
        placement.y + placement.height for placement in placements.values()
    )
    return placements, packed_width, packed_height


def _maxrects_candidate_widths(
    rectangles: Sequence[_Rect],
    gap: float,
    baseline_width: float,
) -> Tuple[float, ...]:
    maximum = max(rect.width for rect in rectangles)
    total = sum(rect.width for rect in rectangles) + gap * max(
        len(rectangles) - 1, 0
    )
    padded_area = sum(
        (rect.width + gap) * (rect.height + gap) for rect in rectangles
    )
    root = math.sqrt(max(padded_area, _EPSILON))
    values = {
        maximum,
        total,
        max(float(baseline_width), maximum),
        max(root, maximum),
    }
    if total > maximum + _EPSILON:
        for index in range(65):
            values.add(maximum + (total - maximum) * index / 64.0)
    for factor in (0.75, 0.85, 0.90, 0.95, 1.05, 1.10, 1.20, 1.35):
        values.add(maximum * 1.0 if root * factor < maximum else root * factor)
    running = 0.0
    for rect in sorted(rectangles, key=lambda item: (-item.width, item.key)):
        if running > 0.0:
            running += gap
        running += rect.width
        values.add(running)
    return tuple(sorted(
        value for value in values
        if maximum - _EPSILON <= value <= total + _EPSILON
    ))


def _best_rigid_maxrects_pack(
    rectangles: Sequence[_Rect],
    gap: float,
    baseline_width: float,
) -> Optional[Tuple[Dict[int, _Placement], float, float]]:
    """Return the best deterministic, non-rotating MaxRects candidate."""

    if not rectangles:
        return {}, 0.0, 0.0
    candidates = []
    for order_index, ordered in enumerate(
        _shelf_rectangle_orders(rectangles, enumerate_small_orders=False)
    ):
        for target_width in _maxrects_candidate_widths(
            rectangles, gap, baseline_width
        ):
            packed = _maxrects_pack_once(ordered, target_width, gap)
            if packed is None:
                continue
            placements, width, height = packed
            candidates.append((
                max(width, height),
                width * height,
                abs(width - height),
                order_index,
                target_width,
                placements,
                width,
                height,
            ))
    if not candidates:
        return None
    best = min(candidates, key=lambda item: item[:5])
    return best[5], best[6], best[7]


def _rigid_pack_is_valid(
    rectangles: Sequence[_Rect],
    placements: Mapping[int, _Placement],
    width: float,
    height: float,
    gap: float,
) -> bool:
    """Validate a complete, translation-only, gap-preserving block pack."""

    rectangle_by_id = {rect.key: rect for rect in rectangles}
    if len(rectangle_by_id) != len(rectangles):
        return False
    if set(placements) != set(rectangle_by_id):
        return False
    tolerance = max(abs(width), abs(height), 1.0) * 1.0e-10
    for key, placement in placements.items():
        rect = rectangle_by_id[key]
        if (
            placement.quarter_turn
            or not all(math.isfinite(value) for value in (
                placement.x,
                placement.y,
                placement.width,
                placement.height,
            ))
            or placement.x < -tolerance
            or placement.y < -tolerance
            or abs(placement.width - rect.width) > tolerance
            or abs(placement.height - rect.height) > tolerance
            or placement.x + placement.width > width + tolerance
            or placement.y + placement.height > height + tolerance
        ):
            return False
    ordered = [placements[key] for key in sorted(placements)]
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            separated = bool(
                left.x + left.width + gap <= right.x + tolerance
                or right.x + right.width + gap <= left.x + tolerance
                or left.y + left.height + gap <= right.y + tolerance
                or right.y + right.height + gap <= left.y + tolerance
            )
            if not separated:
                return False
    return True


def _prefer_rigid_maxrects_pack(
    rectangles: Sequence[_Rect],
    gap: float,
    shelf: Tuple[Dict[int, _Placement], float, float],
    preserve_order: bool = False,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Accept MaxRects only when it cannot reduce the fitted UV scale."""

    shelf_placements, shelf_width, shelf_height = shelf
    if preserve_order:
        # Continuity racks use model-space order as a visual contract.  The
        # MaxRects heuristic intentionally reorders rectangles, so accepting
        # it here would undo the centroid-aware shelf arrangement even when
        # the footprint is marginally smaller.
        return shelf_placements, shelf_width, shelf_height
    candidate = _best_rigid_maxrects_pack(rectangles, gap, shelf_width)
    if candidate is None:
        return shelf
    placements, width, height = candidate
    baseline_longest = max(shelf_width, shelf_height)
    tolerance = max(baseline_longest, 1.0) * 1.0e-10
    if (
        max(width, height) > baseline_longest + tolerance
        or not _rigid_pack_is_valid(
            rectangles, placements, width, height, gap
        )
    ):
        return shelf
    return placements, width, height


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
    affinity_pairs: Sequence[Sequence[int]] = (),
    square_pack_bias: float = 0.35,
    square_pack_max_edge_relaxation: float = 0.02,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack owner cells while preserving repeat and mesh affinities."""

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
    # Structure constraints are deliberately pairwise.  Treating a chain of
    # adjacent hard-surface panels as one clique over-constrains the pack and
    # often recreates the scattered result this stage is meant to fix.
    for pair in affinity_pairs:
        members = tuple(dict.fromkeys(
            int(cell_id) for cell_id in pair if int(cell_id) in rectangles
        ))
        if len(members) != 2:
            continue
        canonical = tuple(sorted(members))
        if canonical not in normalized_cohorts:
            normalized_cohorts.append(canonical)
        left_id, right_id = canonical
        adjacency[left_id].add(right_id)
        adjacency[right_id].add(left_id)

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
        square_pack_bias=square_pack_bias,
        square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
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


def _owner_cell_extent(
    anchor_width: float,
    anchor_height: float,
    rack_width: float,
    rack_height: float,
    gap: float,
) -> Tuple[float, float, float, int]:
    """Return the best non-overlapping side placement for one rack."""

    candidates = []
    for side_rank, (offset_x, offset_y) in enumerate((
        (
            anchor_width + gap,
            (anchor_height - rack_height) * 0.5,
        ),
        (
            (anchor_width - rack_width) * 0.5,
            anchor_height + gap,
        ),
    )):
        minimum_x = min(0.0, offset_x)
        minimum_y = min(0.0, offset_y)
        maximum_x = max(anchor_width, offset_x + rack_width)
        maximum_y = max(anchor_height, offset_y + rack_height)
        cell_width = maximum_x - minimum_x
        cell_height = maximum_y - minimum_y
        candidates.append((
            max(cell_width, cell_height),
            cell_width * cell_height,
            abs(cell_width - cell_height),
            side_rank,
        ))
    return min(candidates)


def _rack_boundary_alignment(
    placements: Mapping[int, _Placement],
    tolerance: float,
) -> float:
    """Measure shared x/y boundaries between rack members."""

    ids = tuple(sorted(placements))
    if len(ids) < 2:
        return 0.0
    aligned = 0
    comparisons = 0
    tolerance = max(float(tolerance), 1.0e-9)
    for position, left_id in enumerate(ids):
        left = placements[left_id]
        left_edges = (
            float(left.x),
            float(left.x + left.width),
            float(left.y),
            float(left.y + left.height),
        )
        for right_id in ids[position + 1:]:
            right = placements[right_id]
            right_edges = (
                float(right.x),
                float(right.x + right.width),
                float(right.y),
                float(right.y + right.height),
            )
            aligned += sum(
                abs(left_edge - right_edge) <= tolerance
                for left_edge, right_edge in zip(left_edges, right_edges)
            )
            comparisons += 4
    return float(aligned) / max(float(comparisons), 1.0)


def _regular_grid_rack(
    rectangles: Sequence[_Rect],
    gap: float,
    columns: Optional[int] = None,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Build an order-preserving row/column-aligned rack candidate.

    Rectangles are inserted row-major in their supplied order, which is the
    deterministic model-space centroid order.  The candidate is rigid: no
    chart is rotated or scaled here.
    """

    if not rectangles:
        return {}, 0.0, 0.0
    count = len(rectangles)
    if columns is None:
        column_values = range(1, count + 1)
    else:
        column_values = (max(1, min(int(columns), count)),)
    candidates = []
    for column_count in column_values:
        rows = (count + column_count - 1) // column_count
        column_widths = [0.0] * column_count
        row_heights = [0.0] * rows
        for index, rectangle in enumerate(rectangles):
            column = index % column_count
            row = index // column_count
            column_widths[column] = max(
                column_widths[column], float(rectangle.width)
            )
            row_heights[row] = max(
                row_heights[row], float(rectangle.height)
            )

        x_offsets = []
        cursor = 0.0
        for width in column_widths:
            x_offsets.append(cursor)
            cursor += width + gap
        y_offsets = []
        cursor = 0.0
        for height in row_heights:
            y_offsets.append(cursor)
            cursor += height + gap

        placements = {}
        for index, rectangle in enumerate(rectangles):
            column = index % column_count
            row = index // column_count
            placements[rectangle.key] = _Placement(
                x=x_offsets[column],
                y=y_offsets[row],
                width=float(rectangle.width),
                height=float(rectangle.height),
                quarter_turn=False,
            )
        width = sum(column_widths) + gap * max(column_count - 1, 0)
        height = sum(row_heights) + gap * max(rows - 1, 0)
        candidates.append((placements, width, height, column_count))

    # Return a compact deterministic candidate.  The owner-cell selector
    # below compares this against the historical shelf and may reject it.
    best = min(
        candidates,
        key=lambda item: (
            max(item[1], item[2]),
            item[1] * item[2],
            abs(item[1] - item[2]),
            item[3],
        ),
    )
    return best[:3]


def _select_owner_attachment_rack(
    anchor_width: float,
    anchor_height: float,
    rectangles: Sequence[_Rect],
    gap: float,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Choose a compact rack with an optional regular-grid improvement.

    The historical shelf remains the density baseline.  A grid is accepted
    only when it does not increase the owner-cell longest edge or footprint,
    while gaining at least 0.10 in shared row/column boundaries.  The strict
    non-expansion guard is important because owner cells participate in a
    higher-level affinity pack: even a small local expansion can move a
    repeated part farther from its corresponding owner.
    """

    shelf, shelf_width, shelf_height = _best_shelf_pack(
        rectangles,
        gap,
        allow_rotate=False,
        preserve_order=True,
    )
    baseline = _owner_cell_extent(
        anchor_width,
        anchor_height,
        shelf_width,
        shelf_height,
        gap,
    )
    tolerance = max(float(gap) * 1.0e-4, 1.0e-8)
    baseline_alignment = _rack_boundary_alignment(shelf, tolerance)
    baseline_maximum = max(float(baseline[0]), _EPSILON)
    baseline_area = max(float(baseline[1]), _EPSILON)

    grid_candidates = []
    for columns in range(1, len(rectangles) + 1):
        grid, grid_width, grid_height = _regular_grid_rack(
            rectangles,
            gap,
            columns=columns,
        )
        grid_extent = _owner_cell_extent(
            anchor_width,
            anchor_height,
            grid_width,
            grid_height,
            gap,
        )
        grid_alignment = _rack_boundary_alignment(grid, tolerance)
        if (
            float(grid_extent[0]) <= baseline_maximum + _EPSILON
            and float(grid_extent[1]) <= baseline_area + _EPSILON
            and grid_alignment >= baseline_alignment + 0.10
        ):
            grid_candidates.append((
                -grid_alignment,
                float(grid_extent[0]),
                float(grid_extent[1]),
                columns,
                grid,
                grid_width,
                grid_height,
            ))
    if grid_candidates:
        _alignment, _maximum, _area, _columns, grid, grid_width, grid_height = min(
            grid_candidates
        )
        return grid, grid_width, grid_height
    return shelf, shelf_width, shelf_height


def _pack_owner_cell(
    anchor_id: int,
    member_ids: Sequence[int],
    island_local: Mapping[int, Mapping[int, Vector]],
    island_sizes: Mapping[int, Tuple[float, float]],
    gap: float,
) -> Tuple[Dict[int, Dict[int, Vector]], float, float]:
    """Pack attached fragments as one compact rack beside their owner.

    ``build_layout_groups`` supplies ``member_ids`` in stable model-centroid
    order.  Replaying that order in a non-rotating shelf keeps nearby model
    fragments visually continuous instead of scattering them around all four
    sides of the owner.
    """

    anchor_width, anchor_height = island_sizes[anchor_id]
    attachments = [
        member_id for member_id in member_ids if member_id != anchor_id
    ]
    ordered_members = [anchor_id] + attachments

    placements: Dict[int, _Placement] = {
        anchor_id: _Placement(0.0, 0.0, anchor_width, anchor_height)
    }
    if attachments:
        rectangles = [
            _Rect(
                key=member_id,
                width=float(island_sizes[member_id][0]),
                height=float(island_sizes[member_id][1]),
                sort_rank=position,
            )
            for position, member_id in enumerate(attachments)
        ]
        rack, rack_width, rack_height = _select_owner_attachment_rack(
            anchor_width,
            anchor_height,
            rectangles,
            gap,
        )
        if any(item.quarter_turn for item in rack.values()):
            raise RuntimeError("Owner attachment rack rotated an island")

        side_candidates = []
        for side_rank, (offset_x, offset_y) in enumerate((
            (
                anchor_width + gap,
                (anchor_height - rack_height) * 0.5,
            ),
            (
                (anchor_width - rack_width) * 0.5,
                anchor_height + gap,
            ),
        )):
            minimum_x = min(0.0, offset_x)
            minimum_y = min(0.0, offset_y)
            maximum_x = max(anchor_width, offset_x + rack_width)
            maximum_y = max(anchor_height, offset_y + rack_height)
            cell_width = maximum_x - minimum_x
            cell_height = maximum_y - minimum_y
            side_candidates.append((
                max(cell_width, cell_height),
                cell_width * cell_height,
                abs(cell_width - cell_height),
                side_rank,
                offset_x,
                offset_y,
            ))
        _maximum, _area, _delta, _side, offset_x, offset_y = min(
            side_candidates
        )
        for member_id in attachments:
            item = rack[member_id]
            placements[member_id] = _Placement(
                item.x + offset_x,
                item.y + offset_y,
                item.width,
                item.height,
                quarter_turn=False,
            )

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


def _continuity_centroid_sort_key(
    group_id: int,
    centroids: Optional[Mapping[int, Sequence[float]]],
) -> Tuple[float, float, float, int]:
    """Return a finite, deterministic model-space order key for a group."""

    value = None if centroids is None else centroids.get(int(group_id))
    try:
        values = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        values = ()
    if len(values) < 3 or not all(math.isfinite(component) for component in values[:3]):
        values = (0.0, 0.0, 0.0)
    # Rounding only affects the tie-break key, never the UV coordinates.  It
    # avoids platform-dependent ordering when Blender reports nearly equal
    # float32 centroids for mirrored panels.
    return (
        round(values[0], 10),
        round(values[1], 10),
        round(values[2], 10),
        int(group_id),
    )


def _source_uv_centroid_sort_key(
    group_id: int,
    centroids: Optional[Mapping[int, Sequence[float]]],
) -> Tuple[float, float, int]:
    """Return a stable row/column key for a pre-pack UV centroid.

    Source UVs are the artist's existing layout contract.  A plain floating
    point lexicographic sort is too sensitive to tiny row drift, so quantize
    the coordinates only for ordering.  The actual UV positions are never
    quantized or modified.
    """

    value = None if centroids is None else centroids.get(int(group_id))
    try:
        values = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        values = ()
    if len(values) < 2 or not all(math.isfinite(component) for component in values[:2]):
        values = (0.0, 0.0)
    # UV rows generally differ by much more than float32 writeback noise.  A
    # small fixed quantum keeps equivalent rows together while preserving
    # meaningful columns.  The sign puts the visually upper UV row first.
    return (
        round(-values[1], 5),
        round(values[0], 5),
        int(group_id),
    )


def _source_uv_order(
    members: Sequence[int],
    centroids: Optional[Mapping[int, Sequence[float]]],
) -> Tuple[int, ...]:
    """Return deterministic source-UV row-major order for ``members``."""

    ids = tuple(dict.fromkeys(int(member) for member in members))
    if not ids or not centroids:
        return ids
    return tuple(sorted(
        ids,
        key=lambda member: _source_uv_centroid_sort_key(member, centroids),
    ))


def _layout_group_source_centroids(
    oriented: Mapping[int, Mapping[int, Vector]],
    groups: Sequence[LayoutGroup],
) -> Dict[int, Vector]:
    """Compute source-UV centroids before any top-level packing transform."""

    result: Dict[int, Vector] = {}
    for group in groups:
        points = [
            point
            for island_id in group.member_ids
            for point in oriented.get(int(island_id), {}).values()
            if point is not None
            and all(math.isfinite(float(component)) for component in point[:2])
        ]
        if points:
            result[int(group.group_id)] = sum(
                (Vector((float(point.x), float(point.y))) for point in points),
                Vector((0.0, 0.0)),
            ) / len(points)
        else:
            result[int(group.group_id)] = Vector((0.0, 0.0))
    return result


def _source_layout_island_sort_key(
    island: IslandRecord,
    row_quantum: float,
) -> Tuple[int, float, int]:
    """Return the source atlas' coarse row-major order for one island."""

    try:
        center_x = float(island.uv_centroid.x)
        center_y = float(island.uv_centroid.y)
    except (AttributeError, TypeError, ValueError):
        center_x = 0.0
        center_y = 0.0
    if not math.isfinite(center_x):
        center_x = 0.0
    if not math.isfinite(center_y):
        center_y = 0.0
    quantum = max(float(row_quantum), 1.0e-6)
    return (
        int(round(-center_y / quantum)),
        round(center_x, 9),
        int(island.island_id),
    )


def _source_layout_placement_cost(
    placement: _Placement,
    desired: _Placement,
    row_weight: float,
) -> Tuple[float, float, float, float, float]:
    """Score a collision-free placement against the source AABB center."""

    center_x = float(placement.x) + float(placement.width) * 0.5
    center_y = float(placement.y) + float(placement.height) * 0.5
    desired_x = float(desired.x) + float(desired.width) * 0.5
    desired_y = float(desired.y) + float(desired.height) * 0.5
    delta_x = center_x - desired_x
    delta_y = center_y - desired_y
    return (
        delta_x * delta_x
        + delta_y * delta_y * (1.0 + max(float(row_weight), 0.0)),
        abs(delta_y),
        abs(delta_x),
        float(placement.y),
        float(placement.x),
    )


def _resolve_source_layout_placement(
    desired: _Placement,
    placed: Sequence[_Placement],
    gap: float,
    row_weight: float,
) -> _Placement:
    """Keep ``desired`` unless its AABB collides, then move it minimally.

    Candidate positions touch an obstructing AABB on one side and preserve a
    source row/column edge or center on the other.  Four atlas-exterior escape
    positions guarantee a deterministic solution even in a dense cluster.
    Already placed rectangles never move, so the source row-major order is a
    stable priority contract rather than a global force simulation.
    """

    if not placed or all(
        _placements_clear(desired, blocker, gap) for blocker in placed
    ):
        return desired

    hits = [
        blocker for blocker in placed
        if not _placements_clear(desired, blocker, gap)
    ]
    candidates: Set[Tuple[float, float]] = set()
    width = float(desired.width)
    height = float(desired.height)

    def add(x: float, y: float) -> None:
        if math.isfinite(float(x)) and math.isfinite(float(y)):
            candidates.add((round(float(x), 14), round(float(y), 14)))

    for blocker in hits:
        blocker_right = float(blocker.x + blocker.width)
        blocker_top = float(blocker.y + blocker.height)
        horizontal_y = (
            float(desired.y),
            float(blocker.y),
            blocker_top - height,
            float(blocker.y) + (float(blocker.height) - height) * 0.5,
        )
        for y in horizontal_y:
            add(blocker_right + gap, y)
            add(float(blocker.x) - gap - width, y)
        vertical_x = (
            float(desired.x),
            float(blocker.x),
            blocker_right - width,
            float(blocker.x) + (float(blocker.width) - width) * 0.5,
        )
        for x in vertical_x:
            add(x, blocker_top + gap)
            add(x, float(blocker.y) - gap - height)

    minimum_x = min(float(item.x) for item in placed)
    minimum_y = min(float(item.y) for item in placed)
    maximum_x = max(float(item.x + item.width) for item in placed)
    maximum_y = max(float(item.y + item.height) for item in placed)
    add(minimum_x - gap - width, float(desired.y))
    add(maximum_x + gap, float(desired.y))
    add(float(desired.x), minimum_y - gap - height)
    add(float(desired.x), maximum_y + gap)

    valid = []
    for x, y in candidates:
        candidate = _Placement(x, y, width, height, False)
        if all(
            _placements_clear(candidate, blocker, gap)
            for blocker in placed
        ):
            valid.append(candidate)
    if not valid:
        # The four exterior candidates above are mathematically disjoint from
        # every existing rectangle.  Keep a clear error if future placement
        # semantics or non-finite input invalidate that guarantee.
        raise RuntimeError("Source-preserving UV collision resolver stalled")
    return min(
        valid,
        key=lambda item: _source_layout_placement_cost(
            item, desired, row_weight
        ),
    )


def _pack_source_preserving_plan(
    oriented: Mapping[int, Mapping[int, Vector]],
    islands: Sequence[IslandRecord],
    gap: float,
    settings: GroupLayoutOptions,
) -> _PackedPlan:
    """Translate oriented islands near their source UV positions.

    This path deliberately ignores broad semantic layout groups.  Those
    groups remain available in ``analysis`` for diagnostics and proximity
    scoring, but they cannot turn several distant source clusters into one
    atlas-wide rigid rectangle.
    """

    island_by_id = {int(item.island_id): item for item in islands}
    if set(map(int, oriented)) != set(island_by_id):
        raise RuntimeError(
            "Source-preserving layout received an incomplete island partition"
        )

    local_coordinates: Dict[int, Dict[int, Vector]] = {}
    desired: Dict[int, _Placement] = {}
    for island_id, coordinates in oriented.items():
        bounds = _uv_bounds(coordinates.values())
        width = float(bounds[2] - bounds[0])
        height = float(bounds[3] - bounds[1])
        if width <= _EPSILON or height <= _EPSILON:
            raise RuntimeError(
                "UV island {} has degenerate bounds".format(island_id)
            )
        local_coordinates[int(island_id)] = {
            int(loop_index): Vector((
                float(point.x) - float(bounds[0]),
                float(point.y) - float(bounds[1]),
            ))
            for loop_index, point in coordinates.items()
        }
        # Rotations are performed around the source UV centroid before this
        # call.  Reusing the resulting AABB keeps that exact rigid transform
        # as the preferred location instead of recentering an asymmetric chart.
        desired[int(island_id)] = _Placement(
            float(bounds[0]), float(bounds[1]), width, height, False
        )

    order = sorted(
        island_by_id,
        key=lambda island_id: _source_layout_island_sort_key(
            island_by_id[island_id], settings.source_layout_row_quantum
        ),
    )
    placements: Dict[int, _Placement] = {}
    placed: List[_Placement] = []
    for island_id in order:
        placement = _resolve_source_layout_placement(
            desired[island_id],
            placed,
            float(gap),
            settings.source_layout_row_weight,
        )
        placements[island_id] = placement
        placed.append(placement)

    minimum_x = min(float(item.x) for item in placements.values())
    minimum_y = min(float(item.y) for item in placements.values())
    maximum_x = max(
        float(item.x + item.width) for item in placements.values()
    )
    maximum_y = max(
        float(item.y + item.height) for item in placements.values()
    )
    normalized_placements = {
        island_id: _Placement(
            float(item.x) - minimum_x,
            float(item.y) - minimum_y,
            float(item.width),
            float(item.height),
            False,
        )
        for island_id, item in placements.items()
    }
    coordinates = {
        island_id: {
            loop_index: Vector((
                float(point.x) + normalized_placements[island_id].x,
                float(point.y) + normalized_placements[island_id].y,
            ))
            for loop_index, point in local_coordinates[island_id].items()
        }
        for island_id in order
    }
    return _PackedPlan(
        coordinates=coordinates,
        width=max(maximum_x - minimum_x, _EPSILON),
        height=max(maximum_y - minimum_y, _EPSILON),
        source_gap=float(gap),
        group_placements=normalized_placements,
    )


def _continuity_edge_pairs(
    members: Sequence[int],
    cohorts: Sequence[Sequence[int]],
) -> Tuple[Tuple[int, int], ...]:
    """Return the sparse, layout-only peer edges for one continuity block.

    A continuity component is a physical connected region, not a semantic
    repeat cohort.  Treating every member as a clique makes a regular rack
    reject perfectly valid layouts when a component contains one large chart
    and several small inserts.  Keeping the actual peer edges preserves the
    topology signal while allowing the packer to choose a compact grid.
    """

    member_set = {int(member) for member in members}
    edges = set()
    for cohort in cohorts:
        ids = tuple(dict.fromkeys(
            int(member) for member in cohort if int(member) in member_set
        ))
        if len(ids) < 2:
            continue
        # ``soft_cohorts`` currently contains pairs, but accepting a small
        # cohort here keeps this helper useful for callers with older
        # manifests and makes the intent explicit.
        for left, right in itertools.combinations(sorted(ids), 2):
            if left != right:
                edges.add((left, right))
    return tuple(sorted(edges))


def _continuity_graph_order(
    members: Sequence[int],
    edges: Sequence[Sequence[int]],
    rectangles: Mapping[int, _Rect],
    centroids: Optional[Mapping[int, Sequence[float]]],
) -> Tuple[int, ...]:
    """Order a continuity component by peer-graph breadth-first traversal.

    Centroid order is a useful deterministic fallback, but it can put the two
    ends of a physical chain in separate rows.  A breadth-first order keeps a
    parent and its children in nearby row-major cells while retaining model
    space as the tie-breaker.  Disconnected leftovers are started from the
    strongest remaining node, so malformed/old manifests remain deterministic.
    """

    ids = tuple(dict.fromkeys(int(member) for member in members))
    if len(ids) < 2:
        return ids
    graph: Dict[int, Set[int]] = {member: set() for member in ids}
    for edge in edges:
        if len(edge) != 2:
            continue
        left, right = (int(edge[0]), int(edge[1]))
        if left not in graph or right not in graph or left == right:
            continue
        graph[left].add(right)
        graph[right].add(left)

    # Keep the historical centroid order when no peer graph is available.
    # Besides preserving backwards-compatible manifests, this makes the
    # fallback easy to audit: only an actual topology edge can change order.
    if not any(graph.values()):
        return tuple(sorted(
            ids,
            key=lambda member: _continuity_centroid_sort_key(member, centroids),
        ))

    def node_key(member: int) -> Tuple[Any, ...]:
        rectangle = rectangles.get(member)
        area = 0.0 if rectangle is None else float(rectangle.width) * float(
            rectangle.height
        )
        return (
            -len(graph.get(member, ())),
            -max(
                0.0 if rectangle is None else float(rectangle.width),
                0.0 if rectangle is None else float(rectangle.height),
            ),
            -area,
            _continuity_centroid_sort_key(member, centroids),
        )

    remaining = set(ids)
    ordered: List[int] = []
    while remaining:
        root = min(remaining, key=node_key)
        queue = deque([root])
        remaining.remove(root)
        while queue:
            current = queue.popleft()
            ordered.append(current)
            neighbors = sorted(
                graph.get(current, ()) & remaining,
                key=lambda member: (
                    -len(graph.get(member, ())),
                    _continuity_centroid_sort_key(member, centroids),
                ),
            )
            for neighbor in neighbors:
                remaining.remove(neighbor)
                queue.append(neighbor)
    return tuple(ordered)


def _continuity_rack_candidate(
    rectangles: Sequence[_Rect],
    members: Sequence[int],
    peer_edges: Sequence[Sequence[int]],
    gap: float,
    centroids: Optional[Mapping[int, Sequence[float]]],
) -> Tuple[Dict[int, _Placement], float, float]:
    """Build a compact, unrotated rack while honoring sparse peer contacts.

    The candidate set is deliberately small: graph/centroid/reverse orders
    crossed with every possible column count.  Compactness is the primary
    objective.  Weak topology links are only tie-breakers, so a long shell
    chain cannot turn a local rack into a tall strip just to satisfy one peer
    edge.  This is deterministic and cheap for the bounded continuity blocks
    used by the add-on.
    """

    if not rectangles:
        return {}, 0.0, 0.0
    by_id = {int(rectangle.key): rectangle for rectangle in rectangles}
    ordered_graph = _continuity_graph_order(
        members, peer_edges, by_id, centroids
    )
    ordered_centroid = tuple(sorted(
        by_id,
        key=lambda member: _continuity_centroid_sort_key(member, centroids),
    ))
    orders: List[Tuple[int, ...]] = []
    seen_orders = set()
    for order in (
        ordered_graph,
        ordered_graph[::-1],
        ordered_centroid,
        ordered_centroid[::-1],
    ):
        signature = tuple(int(member) for member in order)
        if len(signature) != len(by_id) or signature in seen_orders:
            continue
        seen_orders.add(signature)
        orders.append(signature)
    if not orders:
        orders.append(tuple(by_id))

    normalized_edges = _continuity_edge_pairs(tuple(by_id), peer_edges)
    candidates = []
    for order_index, order in enumerate(orders):
        ordered_rectangles = tuple(by_id[member] for member in order)
        for columns in range(1, len(ordered_rectangles) + 1):
            local, width, height = _regular_grid_rack(
                ordered_rectangles, gap, columns=columns
            )
            if not _rigid_pack_is_valid(
                ordered_rectangles, local, width, height, gap
            ):
                continue
            broken = 0
            excess = 0.0
            edge_distance = 0.0
            near_nodes = set()
            for left_id, right_id in normalized_edges:
                left = local[left_id]
                right = local[right_id]
                distance = _placement_distance(left, right)
                limit = max(
                    math.hypot(left.width, left.height),
                    math.hypot(right.width, right.height),
                ) + 2.0 * gap
                ratio = distance / max(limit, _EPSILON)
                edge_distance += ratio
                if ratio > 1.0 + _EPSILON:
                    broken += 1
                    excess += ratio - 1.0
                else:
                    near_nodes.add(left_id)
                    near_nodes.add(right_id)
            alignment = _rack_boundary_alignment(
                local, max(float(gap) * 1.0e-4, 1.0e-8)
            )
            isolated = len(set(by_id) - near_nodes) if normalized_edges else 0
            longest = max(width, height)
            shortest = max(min(width, height), _EPSILON)
            aspect = longest / shortest
            # Penalize a strip gently, while keeping the longest edge and
            # footprint ahead of peer coverage.  The alignment term rewards
            # shared row/column boundaries when dimensions are equivalent.
            compact_score = longest * (
                1.0 + 0.08 * min(max(aspect - 1.0, 0.0), 3.0)
            )
            candidates.append((
                compact_score,
                longest,
                aspect,
                width * height,
                -alignment,
                isolated,
                broken,
                round(excess, 12),
                round(edge_distance, 12),
                order_index,
                columns,
                local,
                width,
                height,
            ))
    if not candidates:
        # ``_regular_grid_rack`` should always produce a valid candidate, but
        # keep a clear failure mode if a future rectangle implementation
        # violates the non-overlap contract.
        raise RuntimeError("Continuity rack produced no valid candidates")
    best = min(candidates, key=lambda item: item[:11])
    return best[11], best[12], best[13]


def _pack_continuity_rigid_blocks(
    rectangles: Sequence[_Rect],
    repeat_cohorts: Sequence[Sequence[int]],
    soft_cohorts: Sequence[Sequence[int]],
    gap: float,
    allow_rotate: bool,
    square_pack_bias: float,
    square_pack_max_edge_relaxation: float,
    continuity_components: Mapping[int, int],
    continuity_centroids: Optional[Mapping[int, Sequence[float]]],
    continuity_group_member_counts: Optional[Mapping[int, int]] = None,
    continuity_target_members: Optional[int] = None,
    continuity_max_members: Optional[int] = None,
    group_source_centroids: Optional[Mapping[int, Sequence[float]]] = None,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack continuity components as rigid units, then solve soft links.

    The component grids are translation-only transforms.  Large physical
    components are split into bounded sub-blocks so one long chain cannot
    inflate the atlas bounding box.  Repeat cohorts remain hard constraints;
    continuity peer edges are used to seed proximity and are deliberately
    excluded from the final hard validation.
    """

    rectangle_by_id = {int(rectangle.key): rectangle for rectangle in rectangles}
    if len(rectangle_by_id) != len(rectangles):
        raise RuntimeError("Layout group rectangles contain duplicate ids")
    group_order = tuple(int(rectangle.key) for rectangle in rectangles)

    continuity_by_group: Dict[int, int] = {}
    for group_id in group_order:
        value = continuity_components.get(group_id)
        if value is None:
            continue
        try:
            continuity_by_group[group_id] = int(value)
        except (TypeError, ValueError):
            continue

    continuity_members: Dict[int, List[int]] = defaultdict(list)
    for group_id in group_order:
        component = continuity_by_group.get(group_id)
        if component is not None:
            continuity_members[component].append(group_id)

    # Source UV centers are used only as a deterministic top-level ordering
    # contract.  Keep a finite fallback for old callers/manifests that do not
    # provide them.
    source_centroids = {
        int(group_id): value
        for group_id, value in (group_source_centroids or {}).items()
        if value is not None
    }

    # Unit-local placements map a unit id to its constituent group rectangles.
    # A unit is rigid only when its continuity component has at least two
    # groups.  Singleton continuity records retain the historical freedom to
    # rotate at the unrelated top-level pack.
    unit_group_placements: Dict[int, Dict[int, _Placement]] = {}
    unit_sizes: Dict[int, Tuple[float, float]] = {}
    unit_is_rigid: Dict[int, bool] = {}
    unit_direction_locked: Dict[int, bool] = {}
    group_to_unit: Dict[int, int] = {}
    unit_centroids: Dict[int, Tuple[float, float, float, int]] = {}
    next_unit = 0

    # Keep a local rack readable without allowing a connected mesh region to
    # become one enormous rectangle.  The unit budget follows the configured
    # island-member budget; the square-root cap is only a solver guard for
    # callers that omit per-group member counts.
    target_members = max(
        2, int(continuity_target_members or 32)
    )
    max_members = max(
        target_members, int(continuity_max_members or 48)
    )
    member_counts = {
        int(group_id): max(
            1, int(value)
        )
        for group_id, value in (continuity_group_member_counts or {}).items()
    }
    max_rigid_unit_groups = max(2, min(8, int(round(math.sqrt(max_members)))))
    for component, members in sorted(continuity_members.items()):
        if len(members) < 2:
            continue
        component_edges = _continuity_edge_pairs(members, soft_cohorts)
        ordered_component = _continuity_graph_order(
            members,
            component_edges,
            rectangle_by_id,
            continuity_centroids,
        )
        chunks: List[Tuple[int, ...]] = []
        current: List[int] = []
        current_members = 0
        for group_id in ordered_component:
            group_members = member_counts.get(int(group_id), 1)
            should_split = bool(
                current
                and (
                    current_members >= target_members
                    or current_members + group_members > max_members
                    or len(current) >= max_rigid_unit_groups
                )
            )
            if should_split:
                chunks.append(tuple(current))
                current = []
                current_members = 0
            current.append(int(group_id))
            current_members += group_members
        if current:
            chunks.append(tuple(current))

        for ordered_ids in chunks:
            subblock_edges = _continuity_edge_pairs(
                ordered_ids, component_edges
            )
            rigid_rectangles = tuple(
                rectangle_by_id[group_id] for group_id in ordered_ids
            )
            local, width, height = _continuity_rack_candidate(
                rigid_rectangles,
                ordered_ids,
                subblock_edges,
                gap,
                continuity_centroids,
            )
            if any(item.quarter_turn for item in local.values()) or not _rigid_pack_is_valid(
                rigid_rectangles, local, width, height, gap
            ):
                raise RuntimeError("Continuity grid produced an invalid rigid block")
            unit_id = next_unit
            next_unit += 1
            unit_group_placements[unit_id] = dict(local)
            unit_sizes[unit_id] = (width, height)
            unit_is_rigid[unit_id] = len(ordered_ids) > 1
            unit_direction_locked[unit_id] = True
            for group_id in ordered_ids:
                group_to_unit[group_id] = unit_id
            first_key = _continuity_centroid_sort_key(
                ordered_ids[0], continuity_centroids
            )
            unit_centroids[unit_id] = first_key[:3] + (unit_id,)

    # Materialize all remaining groups as singleton units in source order.
    for group_id in group_order:
        if group_id in group_to_unit:
            continue
        rectangle = rectangle_by_id[group_id]
        unit_id = next_unit
        next_unit += 1
        unit_group_placements[unit_id] = {
            group_id: _Placement(0.0, 0.0, rectangle.width, rectangle.height)
        }
        unit_sizes[unit_id] = (rectangle.width, rectangle.height)
        unit_is_rigid[unit_id] = False
        unit_direction_locked[unit_id] = group_id in continuity_by_group
        group_to_unit[group_id] = unit_id
        unit_centroids[unit_id] = _continuity_centroid_sort_key(
            group_id, continuity_centroids
        )

    unit_order = tuple(sorted(unit_group_placements))
    unit_adjacency: Dict[int, set] = {unit_id: set() for unit_id in unit_order}
    hard_unit_cohorts: List[Tuple[int, ...]] = []
    soft_unit_cohorts: List[Tuple[int, ...]] = []

    def add_unit_cohorts(
        source: Sequence[Sequence[int]],
        target: List[Tuple[int, ...]],
    ) -> None:
        seen = {frozenset(item) for item in target}
        for cohort in source:
            units = tuple(dict.fromkeys(
                group_to_unit[int(group_id)]
                for group_id in cohort
                if int(group_id) in group_to_unit
            ))
            if len(units) < 2 or frozenset(units) in seen:
                continue
            target.append(units)
            seen.add(frozenset(units))
            for unit_id in units:
                unit_adjacency[unit_id].update(
                    peer_id for peer_id in units if peer_id != unit_id
                )

    add_unit_cohorts(repeat_cohorts, hard_unit_cohorts)

    # Continuity peers are deliberately not promoted to a complete unit
    # affinity graph.  A sparse peer forest can still span many chunks when a
    # mesh has a long shell boundary, turning every chunk into one expensive
    # search component.  Preserve local order with a centroid-sorted path per
    # bounded continuity component instead; semantic repeat cohorts remain the
    # only hard cross-component constraint.
    units_by_continuity: Dict[int, set] = defaultdict(set)
    for group_id, component in continuity_by_group.items():
        unit_id = group_to_unit.get(int(group_id))
        if unit_id is not None:
            units_by_continuity[int(component)].add(int(unit_id))
    for _component, unit_ids_set in sorted(units_by_continuity.items()):
        ordered_units = sorted(
            unit_ids_set,
            key=lambda unit_id: unit_centroids.get(
                unit_id, (0.0, 0.0, 0.0, unit_id)
            ),
        )
        for left_unit, right_unit in zip(ordered_units, ordered_units[1:]):
            unit_adjacency[left_unit].add(right_unit)
            unit_adjacency[right_unit].add(left_unit)

    # Consume actual cross-component continuity peers as a sparse unit-level
    # graph.  These links are deliberately softer than repeat cohorts: they
    # only bias the local affinity solver toward nearby placement and never
    # change island identity.  A bounded union forest prevents a long mesh
    # shell from connecting every unit into one expensive backtracking
    # component, while still recovering the common component-to-singleton
    # panel/bevel contact that was previously lost at the block boundary.
    soft_peer_edges: List[Tuple[int, int, int]] = []
    seen_soft_peer_edges: Set[Tuple[int, int]] = set()
    for rank, cohort in enumerate(soft_cohorts):
        ids = tuple(dict.fromkeys(
            int(group_id)
            for group_id in cohort
            if int(group_id) in group_to_unit
        ))
        if len(ids) < 2:
            continue
        for left_group, right_group in itertools.combinations(sorted(ids), 2):
            left_unit = group_to_unit[int(left_group)]
            right_unit = group_to_unit[int(right_group)]
            if left_unit == right_unit:
                continue
            pair = tuple(sorted((int(left_unit), int(right_unit))))
            if pair in seen_soft_peer_edges:
                continue
            seen_soft_peer_edges.add(pair)
            soft_peer_edges.append((rank, pair[0], pair[1]))

    # Union the hard/internal unit graph first so cross peers are evaluated
    # against the component size that the solver will actually explore.
    soft_parent = {int(unit_id): int(unit_id) for unit_id in unit_order}
    soft_component_size = {int(unit_id): 1 for unit_id in unit_order}

    def soft_find(unit_id: int) -> int:
        parent = soft_parent[int(unit_id)]
        while parent != soft_parent[parent]:
            soft_parent[parent] = soft_parent[soft_parent[parent]]
            parent = soft_parent[parent]
        soft_parent[int(unit_id)] = parent
        return parent

    def soft_union(left_unit: int, right_unit: int) -> None:
        left_root = soft_find(left_unit)
        right_root = soft_find(right_unit)
        if left_root == right_root:
            return
        if left_root > right_root:
            left_root, right_root = right_root, left_root
        soft_parent[right_root] = left_root
        soft_component_size[left_root] += soft_component_size[right_root]
        soft_component_size[right_root] = 0

    for left_unit in unit_order:
        for right_unit in unit_adjacency[left_unit]:
            if int(right_unit) > int(left_unit):
                soft_union(int(left_unit), int(right_unit))

    soft_peer_degree: Dict[int, int] = defaultdict(int)
    # Keep the same solver guard used for rigid chunks.  Existing hard repeat
    # cohorts may already be larger; they are left intact and simply reject
    # additional soft links at their current root.
    soft_component_limit = max(2, min(8, int(round(math.sqrt(max_members)))))
    for _rank, left_unit, right_unit in sorted(soft_peer_edges):
        left_root = soft_find(left_unit)
        right_root = soft_find(right_unit)
        if left_root == right_root:
            continue
        if (
            soft_peer_degree[left_unit] >= 2
            or soft_peer_degree[right_unit] >= 2
            or soft_component_size[left_root]
            + soft_component_size[right_root]
            > soft_component_limit
        ):
            continue
        soft_union(left_unit, right_unit)
        unit_adjacency[left_unit].add(right_unit)
        unit_adjacency[right_unit].add(left_unit)
        soft_peer_degree[left_unit] += 1
        soft_peer_degree[right_unit] += 1

    # Connected unit components are solved independently.  This keeps a
    # cross-block soft peer from turning the whole atlas into one giant search
    # graph while still allowing bounded continuity chains to stay nearby.
    components: List[Tuple[int, ...]] = []
    remaining = set(unit_order)
    unit_rank = {unit_id: index for index, unit_id in enumerate(unit_order)}
    while remaining:
        seed = min(remaining, key=lambda unit_id: unit_rank[unit_id])
        component: List[int] = []
        stack = [seed]
        while stack:
            unit_id = stack.pop()
            if unit_id not in remaining:
                continue
            remaining.remove(unit_id)
            component.append(unit_id)
            neighbors = sorted(
                unit_adjacency[unit_id] & remaining,
                key=lambda value: unit_rank[value],
                reverse=True,
            )
            stack.extend(neighbors)
        components.append(tuple(component))

    component_placements: Dict[int, Dict[int, _Placement]] = {}
    component_sizes: Dict[int, Tuple[float, float]] = {}
    for component_id, component in enumerate(components):
        if len(component) == 1:
            unit_id = component[0]
            width, height = unit_sizes[unit_id]
            local = {
                unit_id: _Placement(0.0, 0.0, width, height)
            }
        else:
            unit_rectangles = {
                unit_id: _Rect(
                    key=unit_id,
                    width=unit_sizes[unit_id][0],
                    height=unit_sizes[unit_id][1],
                    sort_rank=unit_rank[unit_id],
                )
                for unit_id in component
            }
            component_set = set(component)
            component_cohorts = [
                cohort for cohort in hard_unit_cohorts
                if set(cohort) <= component_set
            ]
            try:
                local, width, height = _pack_affinity_component(
                    unit_rectangles,
                    unit_adjacency,
                    component_cohorts,
                    gap,
                )
            except RuntimeError:
                # A bounded soft chain may exceed the historical backtracking
                # budget.  A row-major rigid fallback is deterministic and
                # still honors hard cohorts through the final unit audit.
                ordered_units = tuple(sorted(
                    component,
                    key=lambda unit_id: unit_centroids[unit_id],
                ))
                ordered_rectangles = tuple(
                    unit_rectangles[unit_id] for unit_id in ordered_units
                )
                local, width, height = _regular_grid_rack(
                    ordered_rectangles, gap, columns=len(ordered_rectangles)
                )
            if not _affinity_cohorts_valid(
                local,
                [cohort for cohort in hard_unit_cohorts if set(cohort) <= component_set],
                gap,
                True,
            ):
                raise RuntimeError("Repeat-owner affinity component was not preserved")
        component_placements[component_id] = local
        component_sizes[component_id] = (width, height)

    component_centroids = {}
    for component_id, component in enumerate(components):
        weighted = [
            unit_centroids.get(unit_id, (0.0, 0.0, 0.0, unit_id))
            for unit_id in component
        ]
        if weighted:
            component_centroids[component_id] = tuple(
                sum(float(item[index]) for item in weighted) / len(weighted)
                for index in range(3)
            )
        else:
            component_centroids[component_id] = (0.0, 0.0, 0.0)
    component_order = tuple(sorted(
        range(len(components)),
        key=lambda component_id: (
            component_centroids[component_id], component_id
        ),
    ))
    component_rank = {
        component_id: rank
        for rank, component_id in enumerate(component_order)
    }
    component_rectangles = tuple(
        _Rect(
            key=component_id,
            width=component_sizes[component_id][0],
            height=component_sizes[component_id][1],
            sort_rank=component_rank[component_id],
        )
        for component_id in component_order
    )
    # Any component containing a rigid continuity unit must remain unrotated;
    # otherwise a top-level quarter-turn would silently change checkerboard
    # direction for every chart inside that unit.
    component_rotation_locks = {
        component_id: False
        for component_id, component in enumerate(components)
        if len(component) >= 2
        or any(
            unit_is_rigid[unit_id] or unit_direction_locked[unit_id]
            for unit_id in component
        )
    }
    shelf_pack = _best_shelf_pack(
        component_rectangles,
        gap,
        allow_rotate=allow_rotate,
        preserve_order=True,
        fixed_quarter_turns=component_rotation_locks,
        square_pack_bias=square_pack_bias,
        square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
    )
    block_placements, packed_width, packed_height = _prefer_rigid_maxrects_pack(
        component_rectangles, gap, shelf_pack, preserve_order=True
    )

    placements: Dict[int, _Placement] = {}
    unit_to_component = {
        unit_id: component_id
        for component_id, component in enumerate(components)
        for unit_id in component
    }
    for component_id, unit_local_placements in component_placements.items():
        block = block_placements[component_id]
        if block.quarter_turn:
            # The lock map above should make this unreachable.  Failing loudly
            # is preferable to exporting a silently mirrored checker pattern.
            if any(
                unit_is_rigid[unit_id] or unit_direction_locked[unit_id]
                for unit_id in components[component_id]
            ):
                raise RuntimeError("Continuity block was quarter-turned")
        block_width, block_height = component_sizes[component_id]
        for unit_id, unit_local in unit_local_placements.items():
            unit_block = block
            # Keep the unit's pre-rotation dimensions for transforming its
            # nested groups.  ``unit_height`` after a quarter-turn is the
            # original width, but the local y coordinate must be reflected
            # against the original height; mixing the two creates overlaps.
            unit_original_height = float(unit_local.height)
            if unit_block.quarter_turn:
                unit_x = block.x + block_height - unit_local.y - unit_local.height
                unit_y = block.y + unit_local.x
                unit_quarter_turn = True
            else:
                unit_x = block.x + unit_local.x
                unit_y = block.y + unit_local.y
                unit_quarter_turn = False
            if unit_quarter_turn and (
                unit_is_rigid[unit_id] or unit_direction_locked[unit_id]
            ):
                raise RuntimeError("Continuity unit was quarter-turned")
            for group_id, group_local in unit_group_placements[unit_id].items():
                if unit_quarter_turn:
                    placements[group_id] = _Placement(
                        x=unit_x + unit_original_height - group_local.y - group_local.height,
                        y=unit_y + group_local.x,
                        width=group_local.height,
                        height=group_local.width,
                        quarter_turn=True,
                    )
                else:
                    placements[group_id] = _Placement(
                        x=unit_x + group_local.x,
                        y=unit_y + group_local.y,
                        width=group_local.width,
                        height=group_local.height,
                        quarter_turn=False,
                    )

    if not _affinity_cohorts_valid(
        {
            unit_id: _Placement(
                placement.x,
                placement.y,
                placement.width,
                placement.height,
                placement.quarter_turn,
            )
            for component_id, local in component_placements.items()
            for unit_id, placement in local.items()
        },
        hard_unit_cohorts,
        gap,
        True,
    ):
        raise RuntimeError("Repeat-owner affinity component was not preserved")
    return placements, packed_width, packed_height


def _pack_layout_group_rectangles(
    rectangles: Sequence[_Rect],
    repeat_cohorts: Sequence[Sequence[int]],
    gap: float,
    allow_rotate: bool,
    square_pack_bias: float = 0.35,
    square_pack_max_edge_relaxation: float = 0.02,
    continuity_components: Optional[Mapping[int, int]] = None,
    continuity_centroids: Optional[Mapping[int, Sequence[float]]] = None,
    soft_cohorts: Sequence[Sequence[int]] = (),
    continuity_group_member_counts: Optional[Mapping[int, int]] = None,
    continuity_target_members: Optional[int] = None,
    continuity_max_members: Optional[int] = None,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack layout groups as nearby blocks without merging their owners.

    A geometry-continuity component is replayed as one translation-only
    regular grid.  This keeps model-neighboring charts together and gives
    every member the same orientation contract.  The optional metadata keeps
    the historical call signature valid for external callers and older
    manifests that do not contain continuity records.
    """

    continuity_by_group = {
        int(group_id): int(component)
        for group_id, component in (continuity_components or {}).items()
        if component is not None
    }
    continuity_counts: Dict[int, int] = defaultdict(int)
    for component in continuity_by_group.values():
        continuity_counts[component] += 1
    if any(count >= 2 for count in continuity_counts.values()):
        return _pack_continuity_rigid_blocks(
            rectangles,
            repeat_cohorts,
            soft_cohorts=soft_cohorts,
            gap=gap,
            allow_rotate=allow_rotate,
            square_pack_bias=square_pack_bias,
            square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
            continuity_components=continuity_by_group,
            continuity_centroids=continuity_centroids,
            continuity_group_member_counts=continuity_group_member_counts,
            continuity_target_members=continuity_target_members,
            continuity_max_members=continuity_max_members,
        )

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

    # Materialize complete continuity cohorts before finding graph
    # components.  ``continuity_peers`` is intentionally sparse; consuming
    # only those edges leaves the remaining charts scattered across shelves.
    # A full cohort is layout-only and never welds UV vertices or changes
    # seam/topology data.
    continuity_by_group: Dict[int, int] = {}
    for group_id in group_order:
        if continuity_components is None:
            continue
        component = continuity_components.get(int(group_id))
        if component is None:
            continue
        try:
            continuity_by_group[int(group_id)] = int(component)
        except (TypeError, ValueError):
            continue
    continuity_members: Dict[int, List[int]] = defaultdict(list)
    for group_id in group_order:
        component = continuity_by_group.get(int(group_id))
        if component is not None:
            continuity_members[component].append(int(group_id))
    existing_cohort_sets = {frozenset(cohort) for cohort in normalized_cohorts}
    for component, members in sorted(continuity_members.items()):
        if len(members) < 2:
            continue
        ordered = tuple(sorted(
            members,
            key=lambda group_id: _continuity_centroid_sort_key(
                group_id, continuity_centroids
            ),
        ))
        signature = frozenset(ordered)
        if signature in existing_cohort_sets:
            continue
        normalized_cohorts.append(ordered)
        existing_cohort_sets.add(signature)
        for group_id in ordered:
            adjacency[group_id].update(
                peer_id for peer_id in ordered if peer_id != group_id
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
        component_continuity = {
            continuity_by_group.get(int(group_id))
            for group_id in component
            if continuity_by_group.get(int(group_id)) is not None
        }
        rigid_continuity = (
            len(component) >= 2
            and len(component_continuity) == 1
            and all(int(group_id) in continuity_by_group for group_id in component)
        )
        if rigid_continuity:
            ordered_ids = sorted(
                component,
                key=lambda group_id: _continuity_centroid_sort_key(
                    group_id, continuity_centroids
                ),
            )
            rigid_rectangles = tuple(
                rectangle_by_id[group_id] for group_id in ordered_ids
            )
            placements, width, height = _regular_grid_rack(
                rigid_rectangles, gap
            )
            if any(item.quarter_turn for item in placements.values()):
                raise RuntimeError("Continuity grid rotated a layout group")
            if not _rigid_pack_is_valid(
                rigid_rectangles, placements, width, height, gap
            ):
                raise RuntimeError("Continuity grid produced overlapping groups")
        elif len(component) == 1:
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
    shelf_pack = _best_shelf_pack(
        component_rectangles,
        gap,
        allow_rotate=allow_rotate,
        preserve_order=False,
        fixed_quarter_turns=component_rotation_locks,
        square_pack_bias=square_pack_bias,
        square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
    )
    # Cross-repeat components are already internally solved rigid blocks.
    # A translation-only MaxRects pass may fill shelf holes, but it is accepted
    # only when it preserves every block dimension/gap and cannot reduce the
    # fitted UV scale.  The shelf remains the deterministic fallback.
    block_placements, packed_width, packed_height = _prefer_rigid_maxrects_pack(
        component_rectangles,
        gap,
        shelf_pack,
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
    square_pack_bias: float = 0.35,
    square_pack_max_edge_relaxation: float = 0.02,
    group_model_centroids: Optional[Mapping[int, Sequence[float]]] = None,
    continuity_target_members: Optional[int] = None,
    continuity_max_members: Optional[int] = None,
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
        if any(
            len(pair) != 2
            or pair[0] == pair[1]
            or pair[0] not in anchor_set
            or pair[1] not in anchor_set
            for pair in group.affinity_pairs
        ):
            raise RuntimeError(
                "Layout group {} has invalid structure-affinity metadata".format(
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
            affinity_pairs=group.affinity_pairs,
            square_pack_bias=square_pack_bias,
            square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
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
    continuity_group_member_counts = {
        int(group.group_id): len(group.member_ids)
        for group in groups
    }
    repeat_cohorts = list(_cross_group_repeat_cohorts(groups, repeat_groups))
    continuity_components = {
        int(group.group_id): int(group.continuity_component)
        for group in groups
        if group.continuity_component is not None
    }
    continuity_counts: Dict[int, int] = defaultdict(int)
    for component in continuity_components.values():
        continuity_counts[component] += 1
    has_continuity_block = any(
        count >= 2 for count in continuity_counts.values()
    )
    # Geometry continuity blocks expose a sparse peer forest. A raw forest
    # can still become one large affinity component because pairwise edges
    # form a connected chain. Keep semantic repeat cohorts first, then admit
    # a deterministic bounded subset of continuity edges. This limits the
    # backtracking search while retaining nearby placement for each physical
    # component; the complete peer forest remains in the analysis metadata.
    seen_cohorts = {
        tuple(dict.fromkeys(int(group_id) for group_id in cohort))
        for cohort in repeat_cohorts
    }
    group_ids = tuple(int(group.group_id) for group in groups)
    peer_parent = {group_id: group_id for group_id in group_ids}
    peer_members = {group_id: {group_id} for group_id in group_ids}

    def peer_find(group_id: int) -> int:
        parent = peer_parent[group_id]
        while parent != peer_parent[parent]:
            peer_parent[parent] = peer_parent[peer_parent[parent]]
            parent = peer_parent[parent]
        peer_parent[group_id] = parent
        return parent

    def peer_union(left_id: int, right_id: int) -> int:
        left_root = peer_find(left_id)
        right_root = peer_find(right_id)
        if left_root == right_root:
            return left_root
        if left_root > right_root:
            left_root, right_root = right_root, left_root
        peer_parent[right_root] = left_root
        peer_members[left_root].update(peer_members[right_root])
        peer_members[right_root].clear()
        return left_root

    # Seed the component tracker with semantic repeat cohorts. Their
    # relationship is an existing hard contract and is never split here.
    for cohort in repeat_cohorts:
        cohort_ids = tuple(dict.fromkeys(
            int(group_id) for group_id in cohort
            if int(group_id) in peer_parent
        ))
        if len(cohort_ids) < 2:
            continue
        first = cohort_ids[0]
        for group_id in cohort_ids[1:]:
            peer_union(first, group_id)

    continuity_pairs: List[Tuple[int, int]] = []
    seen_continuity_pairs = set()
    # ``continuity_peers`` is ordered by the topology score produced by the
    # block pass. Preserve that order when selecting the bounded subset so a
    # degree budget keeps the strongest contacts instead of favoring low ids.
    for group in sorted(groups, key=lambda item: int(item.group_id)):
        left_id = int(group.group_id)
        for peer_id in group.continuity_peers:
            right_id = int(peer_id)
            if left_id == right_id or right_id not in peer_parent:
                continue
            pair = tuple(sorted((left_id, right_id)))
            if pair not in seen_continuity_pairs:
                seen_continuity_pairs.add(pair)
                continuity_pairs.append(pair)

    continuity_degree: Dict[int, int] = defaultdict(int)
    bounded_continuity_pairs = []
    for pair in continuity_pairs:
        left_id, right_id = pair
        if pair in seen_cohorts:
            continue
        # Two continuity edges per node keep the selected topology graph
        # readable while avoiding a star-shaped affinity component.
        if (
            continuity_degree[left_id] >= 2
            or continuity_degree[right_id] >= 2
        ):
            continue
        left_root = peer_find(left_id)
        right_root = peer_find(right_id)
        if left_root == right_root:
            continue
        # Eight rectangles is the largest component that the existing
        # bounded solver can explore reliably on Blender 3.3-era hardware.
        if len(peer_members[left_root]) + len(peer_members[right_root]) > 8:
            continue
        peer_union(left_id, right_id)
        continuity_degree[left_id] += 1
        continuity_degree[right_id] += 1
        bounded_continuity_pairs.append(pair)

    # Continuity peers are soft when rigid blocks are available.  Passing them
    # separately lets the hierarchical packer bias cross-block placement
    # without making a failed proximity check roll back the whole UV layout.
    soft_continuity_pairs = list(bounded_continuity_pairs) if has_continuity_block else []
    if not has_continuity_block:
        for pair in bounded_continuity_pairs:
            repeat_cohorts.append(pair)
            seen_cohorts.add(pair)
    placements, packed_width, packed_height = _pack_layout_group_rectangles(
        group_rectangles,
        repeat_cohorts,
        gap,
        allow_rotate=allow_group_quarter_turn,
        square_pack_bias=square_pack_bias,
        square_pack_max_edge_relaxation=square_pack_max_edge_relaxation,
        continuity_components=continuity_components,
        continuity_centroids=group_model_centroids,
        soft_cohorts=soft_continuity_pairs,
        continuity_group_member_counts=continuity_group_member_counts,
        continuity_target_members=continuity_target_members,
        continuity_max_members=continuity_max_members,
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


def _source_layout_bounds_overlap(
    left: Sequence[float],
    right: Sequence[float],
    gap: float,
) -> bool:
    """Return whether two source-layout AABBs violate ``gap``."""

    return not (
        float(left[2]) + gap <= float(right[0]) + _EPSILON
        or float(right[2]) + gap <= float(left[0]) + _EPSILON
        or float(left[3]) + gap <= float(right[1]) + _EPSILON
        or float(right[3]) + gap <= float(left[1]) + _EPSILON
    )


def _source_layout_candidate_deltas(
    current: Sequence[float],
    placed: Mapping[int, Sequence[float]],
    gap: float,
    max_rings: int = 12,
) -> Tuple[Tuple[float, float], ...]:
    """Return deterministic nearby translations around obstacle edges."""

    deltas = {(0.0, 0.0)}
    for other in placed.values():
        left = float(other[0]) - float(current[2]) - gap
        right = float(other[2]) - float(current[0]) + gap
        down = float(other[1]) - float(current[3]) - gap
        up = float(other[3]) - float(current[1]) + gap
        deltas.update({
            (left, 0.0),
            (right, 0.0),
            (0.0, down),
            (0.0, up),
            (left, down),
            (left, up),
            (right, down),
            (right, up),
        })

    width = max(float(current[2]) - float(current[0]), gap * 2.0, 1.0e-4)
    height = max(float(current[3]) - float(current[1]), gap * 2.0, 1.0e-4)
    step = max(width, height) * 1.35
    for radius in range(1, max(int(max_rings), 0) + 1):
        distance = float(radius) * step
        deltas.update({
            (distance, 0.0),
            (-distance, 0.0),
            (0.0, distance),
            (0.0, -distance),
            (distance, distance),
            (distance, -distance),
            (-distance, distance),
            (-distance, -distance),
        })
    return tuple(deltas)


def _source_layout_links(
    analysis: UVLayoutAnalysis,
    source_centers: Mapping[int, Vector],
) -> Dict[int, Tuple[int, ...]]:
    """Build sparse translation-coherence links without merging UV islands."""

    valid = set(int(island_id) for island_id in source_centers)
    links: Dict[int, set] = defaultdict(set)

    def add(left: int, right: int) -> None:
        left = int(left)
        right = int(right)
        if left == right or left not in valid or right not in valid:
            return
        links[left].add(right)
        links[right].add(left)

    for edge in analysis.adjacency:
        add(edge.left_id, edge.right_id)
    for group in analysis.layout_groups:
        for pair in group.affinity_pairs:
            if len(pair) == 2:
                add(pair[0], pair[1])
        anchors = [
            int(island_id) for island_id in group.anchor_ids
            if int(island_id) in valid
        ]
        for island_id in group.small_member_ids:
            island_id = int(island_id)
            if island_id not in valid or not anchors:
                continue
            owner = min(
                anchors,
                key=lambda anchor_id: (
                    (source_centers[anchor_id] - source_centers[island_id]).length_squared,
                    anchor_id,
                ),
            )
            add(island_id, owner)
        for cohort in group.owner_cohorts:
            ordered = _source_uv_order(cohort, source_centers)
            for left, right in zip(ordered, ordered[1:]):
                add(left, right)
    for repeat in analysis.repeat_groups:
        ordered = _source_uv_order(repeat.member_ids, source_centers)
        for left, right in zip(ordered, ordered[1:]):
            add(left, right)
    return {
        island_id: tuple(sorted(peers))
        for island_id, peers in links.items()
    }


def _pack_source_layout_once_legacy(
    oriented: Mapping[int, Mapping[int, Vector]],
    analysis: UVLayoutAnalysis,
    gap: float,
    settings: GroupLayoutOptions,
) -> _PackedPlan:
    """Legacy island-at-a-time source layout retained as a fallback."""

    coordinates = {
        int(island_id): {
            int(loop_index): point.copy()
            for loop_index, point in values.items()
        }
        for island_id, values in oriented.items()
        if values
    }
    if not coordinates:
        raise RuntimeError("Source-preserving layout has no UV coordinates")

    source_centers = {
        island_id: sum(values.values(), Vector((0.0, 0.0))) / len(values)
        for island_id, values in coordinates.items()
    }
    links = _source_layout_links(analysis, source_centers)
    by_id = {int(island.island_id): island for island in analysis.islands}
    row_quantum = max(float(settings.source_layout_row_quantum), 1.0e-6)
    source_order = str(getattr(settings, "source_layout_order", "ROW_MAJOR")).upper()
    if source_order == "AREA":
        # Legacy behavior: large charts claim their source positions first.
        # Keep it available for callers that explicitly depend on that policy.
        order = sorted(
            coordinates,
            key=lambda island_id: (
                -abs(float(getattr(by_id.get(island_id), "uv_area", 0.0))),
                round(float(source_centers[island_id].y), 10),
                round(float(source_centers[island_id].x), 10),
                island_id,
            ),
        )
    else:
        # The source atlas is already a useful artist-authored scaffold.  A
        # row-major insertion order means a collision displaces only the
        # later chart in that row instead of cascading from area-first holes.
        order = sorted(
            coordinates,
            key=lambda island_id: (
                int(round(-float(source_centers[island_id].y) /
                          max(row_quantum, 1.0e-6))),
                round(float(source_centers[island_id].x), 10),
                -abs(float(getattr(by_id.get(island_id), "uv_area", 0.0))),
                island_id,
            ),
        )

    placed: Dict[int, Dict[int, Vector]] = {}
    placed_bounds: Dict[int, Tuple[float, float, float, float]] = {}
    translations: Dict[int, Vector] = {}
    row_weight = max(float(settings.source_layout_row_weight), 0.0)
    # ``getattr`` keeps manifests/scripts created against pre-v38 option
    # objects readable while the dataclass field supplies the normal default.
    affinity_weight = max(
        float(getattr(settings, "source_layout_affinity_weight", 0.0)), 0.0
    )
    moved_islands = 0
    total_translation = 0.0
    max_translation = 0.0

    for island_id in order:
        source = coordinates[island_id]
        current_bounds = _uv_bounds(source.values())
        candidates = _source_layout_candidate_deltas(
            current_bounds, placed_bounds, gap
        )
        placed_peers = [
            peer_id for peer_id in links.get(island_id, ())
            if peer_id in translations
        ]

        def candidate_key(delta: Tuple[float, float]) -> Tuple[float, ...]:
            dx, dy = map(float, delta)
            movement = (dx * dx + dy * dy) / (row_quantum * row_quantum)
            row_steps = abs(dy) / row_quantum
            affinity = 0.0
            if placed_peers:
                affinity = sum(
                    (Vector((dx, dy)) - translations[peer_id]).length_squared
                    for peer_id in placed_peers
                ) / (len(placed_peers) * row_quantum * row_quantum)
            return (
                movement + row_weight * row_steps * row_steps
                + affinity_weight * affinity,
                abs(dy),
                abs(dx),
                dy,
                dx,
            )

        selected = None
        for dx, dy in sorted(candidates, key=candidate_key):
            moved = {
                loop_index: Vector((float(point.x) + dx, float(point.y) + dy))
                for loop_index, point in source.items()
            }
            moved_bounds = _uv_bounds(moved.values())
            if any(
                _source_layout_bounds_overlap(moved_bounds, other, gap)
                for other in placed_bounds.values()
            ):
                continue
            selected = (moved, moved_bounds, Vector((float(dx), float(dy))))
            break
        if selected is None:
            # The right edge of the complete placed set is always a finite,
            # deterministic escape hatch.  It should be unreachable because
            # the same position is included in the obstacle-edge candidates.
            dx = (
                max((item[2] for item in placed_bounds.values()), default=0.0)
                - float(current_bounds[0]) + gap
            )
            moved = {
                loop_index: Vector((float(point.x) + dx, float(point.y)))
                for loop_index, point in source.items()
            }
            selected = (moved, _uv_bounds(moved.values()), Vector((dx, 0.0)))

        moved, moved_bounds, translation = selected
        placed[island_id] = moved
        placed_bounds[island_id] = moved_bounds
        translations[island_id] = translation
        distance = float(translation.length)
        total_translation += distance
        max_translation = max(max_translation, distance)
        if distance > 1.0e-8:
            moved_islands += 1

    atlas_bounds = _uv_bounds(
        point for values in placed.values() for point in values.values()
    )
    minimum = Vector((float(atlas_bounds[0]), float(atlas_bounds[1])))
    normalized = {
        island_id: {
            loop_index: point - minimum
            for loop_index, point in values.items()
        }
        for island_id, values in placed.items()
    }
    width = max(float(atlas_bounds[2]) - float(atlas_bounds[0]), _EPSILON)
    height = max(float(atlas_bounds[3]) - float(atlas_bounds[1]), _EPSILON)
    group_placements: Dict[int, _Placement] = {}
    for group in analysis.layout_groups:
        points = [
            point
            for island_id in group.member_ids
            for point in normalized.get(int(island_id), {}).values()
        ]
        if not points:
            continue
        bounds = _uv_bounds(points)
        group_placements[int(group.group_id)] = _Placement(
            x=float(bounds[0]),
            y=float(bounds[1]),
            width=max(float(bounds[2]) - float(bounds[0]), 0.0),
            height=max(float(bounds[3]) - float(bounds[1]), 0.0),
            quarter_turn=False,
        )
    return _PackedPlan(
        coordinates=normalized,
        width=width,
        height=height,
        source_gap=float(gap),
        group_placements=group_placements,
        strategy="source_local_repair",
        moved_islands=moved_islands,
        total_translation=total_translation,
        max_translation=max_translation,
    )


def _source_cell_bounds(
    member_ids: Iterable[int],
    source_bounds: Mapping[int, Sequence[float]],
) -> Tuple[float, float, float, float]:
    """Return the source-space envelope for a prospective local cell."""

    boxes = [
        source_bounds[int(member)]
        for member in member_ids
        if int(member) in source_bounds
    ]
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    )


def _source_cell_edge_records(
    analysis: UVLayoutAnalysis,
    source_centers: Mapping[int, Vector],
    row_quantum: float,
    link_radius: float,
) -> Tuple[Tuple[int, float, int, int], ...]:
    """Build a sparse, source-local structural edge stream.

    Lower ``priority`` values are authoritative topology/repeat links;
    higher values are only nearest-neighbour hints.  The union pass applies
    the cell diameter/member limits, so even a long chain cannot become an
    atlas-wide rigid block.
    """

    valid = {int(value) for value in source_centers}
    records: Dict[Tuple[int, int], Tuple[int, float, int, int]] = {}

    def add(left: int, right: int, priority: int) -> None:
        left = int(left)
        right = int(right)
        if left == right or left not in valid or right not in valid:
            return
        pair = tuple(sorted((left, right)))
        distance = float((source_centers[left] - source_centers[right]).length)
        if not math.isfinite(distance):
            return
        # Proximity hints are intentionally local.  Explicit topology/repeat
        # links may be farther apart, but the cell envelope gate still limits
        # the resulting component.
        if int(priority) >= 3 and distance > float(link_radius) + _EPSILON:
            return
        candidate = (int(priority), distance, pair[0], pair[1])
        previous = records.get(pair)
        if previous is None or candidate < previous:
            records[pair] = candidate

    # Real mesh boundaries are the strongest continuity signal.
    for edge in analysis.adjacency:
        add(edge.left_id, edge.right_id, 0)

    # Explicit owner/structure affinities are next.  These are already
    # bounded by ``build_layout_groups`` and are safe to replay as local
    # rigid relationships.
    for group in analysis.layout_groups:
        members = tuple(dict.fromkeys(
            int(member) for member in group.member_ids if int(member) in valid
        ))
        ordered = tuple(sorted(
            members,
            key=lambda member: (
                int(round(-float(source_centers[member].y) /
                          max(float(row_quantum), 1.0e-6))),
                round(float(source_centers[member].x), 10),
                member,
            ),
        ))
        # A source-ordered path conveys the group relationship without making
        # every member a clique (which would over-constrain a large group).
        for left, right in zip(ordered, ordered[1:]):
            add(left, right, 1)
        for pair in group.affinity_pairs:
            if len(pair) == 2:
                add(pair[0], pair[1], 0)
        for left, right in zip(
            group.small_member_ids, group.small_anchor_ids
        ):
            add(left, right, 1)
        for cohort in group.owner_cohorts:
            cohort_order = tuple(sorted(
                (int(member) for member in cohort if int(member) in valid),
                key=lambda member: (
                    round(-float(source_centers[member].y), 10),
                    round(float(source_centers[member].x), 10),
                    member,
                ),
            ))
            for left, right in zip(cohort_order, cohort_order[1:]):
                add(left, right, 1)

    # Repeated parts should share a local rack when their source positions are
    # within the configured envelope.  The union diameter gate below keeps
    # intentionally distant mirrored sets as separate cells.
    for repeat in analysis.repeat_groups:
        ordered = tuple(sorted(
            (int(member) for member in repeat.member_ids if int(member) in valid),
            key=lambda member: (
                round(-float(source_centers[member].y), 10),
                round(float(source_centers[member].x), 10),
                member,
            ),
        ))
        for left, right in zip(ordered, ordered[1:]):
            add(left, right, 1)

    # Finally add at most two source-nearest hints per island.  These make a
    # group of unannotated hard-surface strips readable without inventing a
    # semantic relationship in the analysis metadata.
    ids = tuple(sorted(valid))
    for left in ids:
        neighbours = sorted(
            (
                float((source_centers[left] - source_centers[right]).length),
                int(right),
            )
            for right in ids
            if right != left
        )
        for distance, right in neighbours[:2]:
            if distance <= float(link_radius) + _EPSILON:
                add(left, right, 3)

    return tuple(sorted(records.values(), key=lambda item: item))


def _source_cell_partition(
    source_centers: Mapping[int, Vector],
    source_bounds: Mapping[int, Sequence[float]],
    edge_records: Sequence[Sequence[float]],
    max_members: int,
    max_diameter: float,
) -> Tuple[Tuple[int, ...], ...]:
    """Union source-local edges while enforcing hard cell bounds."""

    ids = tuple(sorted(int(value) for value in source_centers))
    parent = {value: value for value in ids}
    members = {value: {value} for value in ids}

    def find(value: int) -> int:
        value = int(value)
        root = value
        while parent[root] != root:
            root = parent[root]
        while parent[value] != value:
            next_value = parent[value]
            parent[value] = root
            value = next_value
        return root

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        # Stable root selection keeps cell ids deterministic across Blender
        # versions and independent of adjacency iteration order.
        if left_root > right_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        members[left_root].update(members[right_root])
        members[right_root].clear()

    def envelope(ids_to_measure: Iterable[int]) -> Tuple[float, float, float, float]:
        return _source_cell_bounds(ids_to_measure, source_bounds)

    for raw in sorted(
        edge_records,
        key=lambda item: (
            int(item[0]), float(item[1]), int(item[2]), int(item[3])
        ),
    ):
        if len(raw) < 4:
            continue
        left = int(raw[2])
        right = int(raw[3])
        if left not in parent or right not in parent:
            continue
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            continue
        combined = members[left_root] | members[right_root]
        if len(combined) > max(int(max_members), 2):
            continue
        bounds = envelope(combined)
        diameter = math.hypot(
            max(float(bounds[2]) - float(bounds[0]), 0.0),
            max(float(bounds[3]) - float(bounds[1]), 0.0),
        )
        if diameter > float(max_diameter) + _EPSILON:
            continue
        union(left, right)

    components: Dict[int, List[int]] = defaultdict(list)
    for value in ids:
        components[find(value)].append(value)
    return tuple(
        tuple(sorted(values))
        for _root, values in sorted(
            components.items(), key=lambda item: min(item[1])
        )
    )


def _source_cell_rack(
    member_ids: Sequence[int],
    local_coordinates: Mapping[int, Mapping[int, Vector]],
    island_sizes: Mapping[int, Sequence[float]],
    source_centers: Mapping[int, Vector],
    edge_records: Sequence[Sequence[float]],
    gap: float,
    row_quantum: float,
) -> Tuple[Dict[int, _Placement], float, float]:
    """Pack one bounded cell as a source-ordered, non-rotating rack."""

    ordered = tuple(sorted(
        (int(member) for member in member_ids),
        key=lambda member: (
            int(round(-float(source_centers[member].y) /
                      max(float(row_quantum), 1.0e-6))),
            round(float(source_centers[member].x), 10),
            member,
        ),
    ))
    rectangles = tuple(
        _Rect(
            key=member,
            width=float(island_sizes[member][0]),
            height=float(island_sizes[member][1]),
            sort_rank=index,
        )
        for index, member in enumerate(ordered)
    )
    if len(rectangles) == 1:
        item = rectangles[0]
        return {
            item.key: _Placement(0.0, 0.0, item.width, item.height, False)
        }, item.width, item.height

    # Evaluate every bounded column count.  The compactness term is primary;
    # sparse structural links break ties in favour of neighbouring cells.
    by_id = {int(item.key): item for item in rectangles}
    edges = [
        (int(item[2]), int(item[3]))
        for item in edge_records
        if len(item) >= 4
        and int(item[2]) in by_id
        and int(item[3]) in by_id
    ]
    candidates = []
    for columns in range(1, len(rectangles) + 1):
        placement, width, height = _regular_grid_rack(
            rectangles, gap, columns=columns
        )
        longest = max(float(width), float(height))
        shortest = max(min(float(width), float(height)), _EPSILON)
        aspect = longest / shortest
        edge_distance = 0.0
        for left, right in edges:
            first = placement[left]
            second = placement[right]
            edge_distance += _placement_distance(first, second)
        # Prefer a square-ish local block only after its longest edge is
        # comparable.  This avoids turning a coherent row into a tall strip.
        score = longest * (
            1.0 + 0.06 * min(max(aspect - 1.0, 0.0), 3.0)
        ) + 0.04 * edge_distance
        candidates.append((
            score,
            longest,
            float(width) * float(height),
            aspect,
            edge_distance,
            columns,
            placement,
            float(width),
            float(height),
        ))
    chosen = min(candidates, key=lambda item: item[:6])
    return chosen[6], chosen[7], chosen[8]


def _pack_source_cell_layout_once(
    oriented: Mapping[int, Mapping[int, Vector]],
    analysis: UVLayoutAnalysis,
    gap: float,
    settings: GroupLayoutOptions,
) -> _PackedPlan:
    """Pack bounded source cells, then repair cell-level AABB collisions."""

    coordinates = {
        int(island_id): {
            int(loop_index): point.copy()
            for loop_index, point in values.items()
        }
        for island_id, values in oriented.items()
        if values
    }
    if not coordinates:
        raise RuntimeError("Source-preserving layout has no UV coordinates")

    source_centers = {
        island_id: sum(values.values(), Vector((0.0, 0.0))) / len(values)
        for island_id, values in coordinates.items()
    }
    source_bounds = {
        island_id: _uv_bounds(values.values())
        for island_id, values in coordinates.items()
    }
    atlas_bounds = _uv_bounds(
        point for values in coordinates.values() for point in values.values()
    )
    source_extent = max(
        float(atlas_bounds[2]) - float(atlas_bounds[0]),
        float(atlas_bounds[3]) - float(atlas_bounds[1]),
        _EPSILON,
    )
    row_quantum = max(float(settings.source_layout_row_quantum), 1.0e-6)
    max_diameter = max(
        source_extent * float(settings.source_layout_cell_diameter_ratio),
        float(gap) * 4.0,
        row_quantum,
    )
    link_radius = max(
        source_extent * float(settings.source_layout_cell_link_radius_ratio),
        float(gap) * 4.0,
        row_quantum,
    )
    edges = _source_cell_edge_records(
        analysis,
        source_centers,
        row_quantum,
        link_radius,
    )
    cells = _source_cell_partition(
        source_centers,
        source_bounds,
        edges,
        int(settings.source_layout_cell_max_members),
        max_diameter,
    )

    local_coordinates = {}
    island_sizes = {}
    for island_id, values in coordinates.items():
        local, width, height = _translate_to_origin(values)
        if width <= _EPSILON or height <= _EPSILON:
            raise RuntimeError(
                "UV island {} has degenerate bounds".format(island_id)
            )
        local_coordinates[island_id] = local
        island_sizes[island_id] = (width, height)

    # Map each island edge to its cell so local racks can use only the links
    # that actually exist inside the bounded source envelope.
    cell_by_island = {
        int(member): cell_index
        for cell_index, cell in enumerate(cells)
        for member in cell
    }
    edge_by_cell: Dict[int, List[Tuple[int, float, int, int]]] = defaultdict(list)
    for edge in edges:
        if len(edge) < 4:
            continue
        left_cell = cell_by_island.get(int(edge[2]))
        right_cell = cell_by_island.get(int(edge[3]))
        if left_cell is not None and left_cell == right_cell:
            edge_by_cell[left_cell].append(tuple(edge))

    cell_data = {}
    for cell_index, members in enumerate(cells):
        rack, width, height = _source_cell_rack(
            members,
            local_coordinates,
            island_sizes,
            source_centers,
            edge_by_cell.get(cell_index, ()),
            gap,
            row_quantum,
        )
        packed = {
            int(island_id): {
                int(loop_index): Vector((
                    float(point.x) + float(rack[island_id].x),
                    float(point.y) + float(rack[island_id].y),
                ))
                for loop_index, point in local_coordinates[island_id].items()
            }
            for island_id in members
        }
        packed_bounds = _uv_bounds(
            point for values in packed.values() for point in values.values()
        )
        center = sum(
            (source_centers[island_id] for island_id in members),
            Vector((0.0, 0.0)),
        ) / max(len(members), 1)
        cell_data[cell_index] = {
            "members": tuple(members),
            "coordinates": packed,
            "bounds": packed_bounds,
            "width": max(float(packed_bounds[2] - packed_bounds[0]), _EPSILON),
            "height": max(float(packed_bounds[3] - packed_bounds[1]), _EPSILON),
            "center": center,
        }

    cell_order = tuple(sorted(
        cell_data,
        key=lambda cell_index: (
            int(round(-float(cell_data[cell_index]["center"].y) /
                      row_quantum)),
            round(float(cell_data[cell_index]["center"].x), 10),
            min(cell_data[cell_index]["members"]),
        ),
    ))
    desired = {}
    for cell_index in cell_order:
        item = cell_data[cell_index]
        center = item["center"]
        desired[cell_index] = _Placement(
            float(center.x) - item["width"] * 0.5,
            float(center.y) - item["height"] * 0.5,
            item["width"],
            item["height"],
            False,
        )

    placements: Dict[int, _Placement] = {}
    placed: List[_Placement] = []
    for cell_index in cell_order:
        placement = _resolve_source_layout_placement(
            desired[cell_index],
            placed,
            float(gap),
            float(settings.source_layout_row_weight),
        )
        placements[cell_index] = placement
        placed.append(placement)

    # Materialize each rack at its resolved cell placement.  A cell remains a
    # rigid translation-only block after this point.
    placed_coordinates: Dict[int, Dict[int, Vector]] = {}
    translations: Dict[int, Vector] = {}
    moved_islands = 0
    total_translation = 0.0
    max_translation = 0.0
    for cell_index in cell_order:
        item = cell_data[cell_index]
        placement = placements[cell_index]
        local_left = float(item["bounds"][0])
        local_bottom = float(item["bounds"][1])
        translation = Vector((
            float(placement.x) - local_left,
            float(placement.y) - local_bottom,
        ))
        for island_id, values in item["coordinates"].items():
            placed_coordinates[island_id] = {
                loop_index: point + translation
                for loop_index, point in values.items()
            }
            original_center = source_centers[island_id]
            final_center = sum(
                placed_coordinates[island_id].values(), Vector((0.0, 0.0))
            ) / max(len(values), 1)
            island_translation = final_center - original_center
            translations[island_id] = island_translation
            distance = float(island_translation.length)
            total_translation += distance
            max_translation = max(max_translation, distance)
            if distance > 1.0e-8:
                moved_islands += 1

    final_bounds = _uv_bounds(
        point for values in placed_coordinates.values() for point in values.values()
    )
    minimum = Vector((float(final_bounds[0]), float(final_bounds[1])))
    normalized = {
        island_id: {
            loop_index: point - minimum
            for loop_index, point in values.items()
        }
        for island_id, values in placed_coordinates.items()
    }
    width = max(float(final_bounds[2] - final_bounds[0]), _EPSILON)
    height = max(float(final_bounds[3] - final_bounds[1]), _EPSILON)
    group_placements: Dict[int, _Placement] = {}
    for group in analysis.layout_groups:
        points = [
            point
            for island_id in group.member_ids
            for point in normalized.get(int(island_id), {}).values()
        ]
        if not points:
            continue
        bounds = _uv_bounds(points)
        group_placements[int(group.group_id)] = _Placement(
            x=float(bounds[0]),
            y=float(bounds[1]),
            width=max(float(bounds[2] - bounds[0]), 0.0),
            height=max(float(bounds[3] - bounds[1]), 0.0),
            quarter_turn=False,
        )
    return _PackedPlan(
        coordinates=normalized,
        width=width,
        height=height,
        source_gap=float(gap),
        group_placements=group_placements,
        strategy="source_cell_layout",
        moved_islands=moved_islands,
        total_translation=total_translation,
        max_translation=max_translation,
    )


def _pack_source_layout_once(
    oriented: Mapping[int, Mapping[int, Vector]],
    analysis: UVLayoutAnalysis,
    gap: float,
    settings: GroupLayoutOptions,
) -> _PackedPlan:
    """Select the bounded source-cell path or the legacy repair fallback."""

    if not bool(getattr(settings, "source_layout_cell_enabled", True)):
        return _pack_source_layout_once_legacy(oriented, analysis, gap, settings)
    try:
        return _pack_source_cell_layout_once(oriented, analysis, gap, settings)
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError):
        # Preserve the transactional behavior of the old source layout when a
        # malformed/lightweight test double cannot supply cell metadata.
        return _pack_source_layout_once_legacy(oriented, analysis, gap, settings)


def _plan_source_layout_with_margin(
    oriented: Mapping[int, Mapping[int, Vector]],
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> Tuple[_PackedPlan, Dict[int, Dict[int, Vector]], float]:
    """Fit a source-preserving plan while converging on the tile margin."""

    points = [point for values in oriented.values() for point in values.values()]
    if not points:
        raise RuntimeError("Source-preserving layout has no UV coordinates")
    bounds = _uv_bounds(points)
    usable = max(1.0 - 2.0 * float(settings.margin), _EPSILON)
    initial_scale = min(
        usable / max(float(bounds[2]) - float(bounds[0]), _EPSILON),
        usable / max(float(bounds[3]) - float(bounds[1]), _EPSILON),
    )
    gap = (
        0.0
        if settings.margin <= _EPSILON
        else float(settings.margin) / max(initial_scale, _EPSILON) * 1.01
    )
    attempts = []
    for _iteration in range(max(1, min(int(settings.packing_iterations), 4))):
        plan = _pack_source_layout_once(oriented, analysis, gap, settings)
        fitted, scale = _fit_plan_to_tile(plan, settings.margin)
        achieved = float(plan.source_gap) * float(scale)
        attempts.append((plan, fitted, scale, achieved))
        if settings.margin <= _EPSILON or achieved + 1.0e-9 >= settings.margin:
            break
        requested = float(settings.margin) / max(float(scale), _EPSILON) * 1.01
        if abs(requested - gap) <= max(abs(requested), 1.0) * 1.0e-7:
            break
        gap = requested
    feasible = [
        item for item in attempts
        if item[3] + 1.0e-9 >= float(settings.margin)
    ]
    candidates = feasible or attempts
    return max(
        candidates,
        key=lambda item: (
            float(item[2]),
            -float(item[0].max_translation),
            -float(item[0].total_translation),
        ),
    )[:3]


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


def _group_quarter_turn_allowed(settings: GroupLayoutOptions) -> bool:
    """Return whether packing may rotate groups after direction alignment."""

    return bool(
        settings.allow_group_quarter_turn
        and not settings.align_geometry_direction
    )


def _layout_group_model_centroids(
    groups: Sequence[LayoutGroup],
    islands: Sequence[IslandRecord],
) -> Dict[int, Vector]:
    """Return area-weighted model centroids for continuity ordering."""

    by_id = {int(island.island_id): island for island in islands}
    result: Dict[int, Vector] = {}
    for group in groups:
        weighted = Vector((0.0, 0.0, 0.0))
        total_weight = 0.0
        fallback = []
        for island_id in group.member_ids:
            island = by_id.get(int(island_id))
            if island is None:
                continue
            centroid = _island_model_centroid(island)
            fallback.append(centroid)
            try:
                weight = float(island.area_3d)
            except (TypeError, ValueError, AttributeError):
                weight = 0.0
            if not math.isfinite(weight) or weight <= _EPSILON:
                continue
            weighted += centroid * weight
            total_weight += weight
        if total_weight > _EPSILON:
            result[int(group.group_id)] = weighted / total_weight
        elif fallback:
            result[int(group.group_id)] = sum(fallback, Vector((0.0, 0.0, 0.0))) / len(
                fallback
            )
        else:
            result[int(group.group_id)] = Vector((0.0, 0.0, 0.0))
    return result


def _plan_with_margin(
    oriented: Mapping[int, Mapping[int, Vector]],
    groups: Sequence[LayoutGroup],
    settings: GroupLayoutOptions,
    repeat_groups: Sequence[RepeatGroup] = (),
    group_model_centroids: Optional[Mapping[int, Sequence[float]]] = None,
    islands: Sequence[IslandRecord] = (),
    analysis: Optional[UVLayoutAnalysis] = None,
) -> Tuple[_PackedPlan, Dict[int, Dict[int, Vector]], float]:
    def build_plan(source_gap: float) -> _PackedPlan:
        if settings.preserve_source_layout:
            if not islands:
                raise RuntimeError(
                    "Source-preserving layout requires island records"
                )
            # Keep the artist-authored atlas as the scaffold, but resolve
            # collisions through the topology/repeat/owner links.  The local
            # resolver moves only the conflicting chart and keeps linked
            # charts on the same translation whenever possible; the legacy
            # _pack_source_preserving_plan path ignored those links and could
            # leave a structurally related family scattered across the tile.
            if analysis is None:
                raise RuntimeError(
                    "Source-preserving layout requires UV analysis"
                )
            return _pack_source_layout_once(
                oriented, analysis, source_gap, settings
            )
        return _pack_plan(
            oriented,
            groups,
            source_gap,
            _group_quarter_turn_allowed(settings),
            repeat_groups,
            settings.square_pack_bias,
            settings.square_pack_max_edge_relaxation,
            group_model_centroids,
            settings.continuity_block_target_members,
            settings.continuity_block_max_members,
        )

    gap = 0.0
    attempts = []
    seen_states = set()
    for _iteration in range(settings.packing_iterations):
        plan = build_plan(gap)
        fitted, scale = _fit_plan_to_tile(plan, settings.margin)
        achieved = gap * scale
        state = (
            round(float(gap), 10),
            round(float(plan.width), 9),
            round(float(plan.height), 9),
        )
        if state in seen_states:
            # Shelf row breaks can make the fixed-point map oscillate between
            # two layouts.  Keep the deterministic history and choose the
            # best feasible candidate below instead of returning the last,
            # potentially long-strip, iteration.
            break
        seen_states.add(state)
        attempts.append((gap, plan, fitted, scale, achieved))
        requested_source_gap = settings.margin / max(scale, _EPSILON)
        if abs(requested_source_gap - gap) <= max(requested_source_gap, 1.0) * 1.0e-6:
            break
        gap = requested_source_gap

    if not attempts:
        raise RuntimeError("UV group layout produced no packing candidate")

    feasible = [
        item for item in attempts
        if item[4] + 1.0e-9 >= settings.margin
    ]
    candidates = feasible or attempts

    def candidate_key(item):
        _candidate_gap, candidate_plan, _fitted, candidate_scale, _achieved = item
        minimum = max(
            min(candidate_plan.width, candidate_plan.height), _EPSILON
        )
        aspect = max(candidate_plan.width, candidate_plan.height) / minimum
        # Preserve texel density first; use square utilization as a stable
        # tie-breaker so a nearly-equivalent scale does not leave a strip.
        return (
            -candidate_scale,
            aspect,
            candidate_plan.width * candidate_plan.height,
        )

    gap, plan, fitted, scale, achieved = min(candidates, key=candidate_key)

    # One conservative correction avoids ending below the requested gap when
    # the fixed-point iteration stops on a shelf-layout discontinuity.
    if achieved + 1.0e-9 < settings.margin:
        # ``gap`` can still be zero when ``packing_iterations`` is one or
        # when every sampled candidate is infeasible.  Derive a non-zero
        # source-space gap from the selected scale before applying the small
        # safety factor.
        gap = max(
            gap,
            settings.margin / max(scale, _EPSILON),
        ) * 1.002
        plan = build_plan(gap)
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


def _triangle_uv_area(points: Sequence[Vector]) -> float:
    """Return the unsigned area of one UV triangle.

    Blender's tessellated loop triangles are used for quality measurements so
    concave n-gons are measured by their actual surface area instead of by a
    potentially misleading bounding rectangle.  The helper intentionally
    accepts any sequence with three vector-like points for small synthetic
    regression fixtures as well.
    """

    if len(points) != 3:
        return 0.0
    first, second, third = points
    determinant = (
        (second.x - first.x) * (third.y - first.y)
        - (second.y - first.y) * (third.x - first.x)
    )
    value = abs(float(determinant)) * 0.5
    return value if math.isfinite(value) else 0.0


def _uv_face_quality_data(
    mesh: Any,
    uv_layer: Any,
) -> Tuple[Dict[int, float], Dict[int, List[float]]]:
    """Collect tessellated UV area and boundary-edge lengths per face."""

    face_areas: Dict[int, float] = defaultdict(float)
    edge_lengths: Dict[int, List[float]] = defaultdict(list)
    # ``calc_loop_triangles`` exists in Blender 3.3 and 5.2.  Keep a
    # defensive fallback for light-weight test doubles and malformed meshes.
    try:
        mesh.calc_loop_triangles()
        triangles = list(mesh.loop_triangles)
    except (AttributeError, RuntimeError, TypeError):
        triangles = []
    for triangle in triangles:
        loop_indices = tuple(int(index) for index in triangle.loops)
        points = tuple(uv_layer.data[index].uv for index in loop_indices)
        area = _triangle_uv_area(points)
        face_index = int(triangle.polygon_index)
        face_areas[face_index] += area

    for polygon in mesh.polygons:
        face_index = int(polygon.index)
        loop_indices = tuple(int(index) for index in polygon.loop_indices)
        points = [uv_layer.data[index].uv for index in loop_indices]
        if face_index not in face_areas:
            # A triangle-less fallback still gives useful diagnostics for a
            # test double or a polygon Blender could not tessellate.
            face_areas[face_index] = abs(
                float(_polygon_signed_uv_area(mesh, uv_layer, face_index))
            )
        if len(points) >= 2:
            for offset, first in enumerate(points):
                second = points[(offset + 1) % len(points)]
                length = (second - first).length
                if math.isfinite(float(length)):
                    edge_lengths[face_index].append(max(float(length), 0.0))
    return dict(face_areas), dict(edge_lengths)


def _quality_validity(audit: Mapping[str, Any]) -> bool:
    """Return the single geometric validity predicate used by selection."""

    return bool(
        audit.get("finite", False)
        and audit.get("inside_tile", False)
        and int(audit.get("positive", 0)) > 0
        and int(audit.get("negative", 0)) == 0
        and int(audit.get("degenerate", 0)) == 0
        and not bool(audit.get("overlap", False))
    )


def _rebind_geometry_axis_contract_by_faces(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    expected_by_faces: Mapping[
        Tuple[int, ...], Tuple[Optional[str], str]
    ],
    settings: GroupLayoutOptions,
) -> UVLayoutAnalysis:
    """Keep selected model axes while replaying or auditing UV coordinates.

    The face-set keys include unresolved islands so a replay cannot silently
    change chart identity. A selected axis that later becomes unresolved
    remains recorded as required, allowing the final quality gate to reject
    the transaction instead of accepting a different AUTO axis.
    """

    if not settings.align_geometry_direction:
        return analysis
    replay_face_sets = {
        tuple(sorted(int(index) for index in island.face_indices))
        for island in analysis.islands
    }
    if replay_face_sets != set(expected_by_faces):
        raise RuntimeError(
            "Directed UV replay changed island identity"
        )
    rebound = 0
    for island in analysis.islands:
        key = tuple(sorted(int(index) for index in island.face_indices))
        source = expected_by_faces.get(key)
        if source is None:
            continue
        source_axis, source_space = source
        if source_axis not in _DIRECTION_AXES:
            continue
        expected_axis = str(source_axis).upper()
        expected_space = str(
            source_space or settings.direction_space
        ).upper()
        _resolved_axis, direction, rotation, confidence = (
            _geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                island.face_indices,
                space=expected_space,
                axis=settings.direction_axis,
                min_projection=settings.direction_axis_min_projection,
                fixed_axis_name=expected_axis,
                auto_priority=settings.direction_auto_priority,
            )
        )
        if _resolved_axis != expected_axis:
            raise RuntimeError(
                "Directed UV replay lost selected {} axis".format(expected_axis)
            )
        island.geometry_axis_name = expected_axis
        island.geometry_direction_space = expected_space
        island.geometry_direction_vector = direction
        island.geometry_direction_confidence = confidence
        island.geometry_rotation_angle = rotation
        island.geometry_final_residual = None
        _bind_geometry_frame_metadata(
            obj,
            mesh,
            uv_layer,
            island,
            settings,
            axis_name=expected_axis,
        )
        rebound += 1
    expected_resolved = sum(
        axis_name in _DIRECTION_AXES
        for axis_name, _space in expected_by_faces.values()
    )
    if rebound != expected_resolved:
        raise RuntimeError(
            "Directed UV replay changed island identity"
        )
    return _apply_orientation_policy(analysis, settings)


def _rebind_geometry_axis_contract(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    expected: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> UVLayoutAnalysis:
    """Keep the candidate's selected model axis during float32 replay."""

    expected_by_faces = {
        tuple(sorted(int(index) for index in island.face_indices)): (
            island.geometry_axis_name,
            str(island.geometry_direction_space),
        )
        for island in expected.islands
    }
    return _rebind_geometry_axis_contract_by_faces(
        obj,
        mesh,
        uv_layer,
        analysis,
        expected_by_faces,
        settings,
    )


def rebind_geometry_axis_contract(
    obj: Any,
    analysis: UVLayoutAnalysis,
    expected_by_faces: Mapping[
        Tuple[int, ...], Tuple[Optional[str], str]
    ],
    options: Optional[GroupLayoutOptions] = None,
) -> UVLayoutAnalysis:
    """Bind a captured face-set/axis contract to a fresh UV analysis.

    Optimizer paths without adaptive group layout use this to enforce the
    same strict island identity and fixed-axis replay gate.
    """

    settings = (options or GroupLayoutOptions()).validated()
    mesh, uv_layer = _require_object_mode_mesh(obj)
    normalized = {}
    for raw_faces, raw_value in expected_by_faces.items():
        key = tuple(sorted(int(index) for index in raw_faces))
        if not key or key in normalized:
            raise ValueError("direction contract contains an invalid face-set")
        try:
            raw_axis, raw_space = raw_value
        except (TypeError, ValueError):
            raise ValueError(
                "direction contract values must be (axis, space) pairs"
            )
        axis_name = None if raw_axis is None else str(raw_axis).upper()
        if axis_name is not None and axis_name not in _DIRECTION_AXES:
            raise ValueError("direction contract axis must be X, Y, Z, or None")
        direction_space = str(raw_space or settings.direction_space).upper()
        if direction_space not in {"OBJECT", "WORLD"}:
            raise ValueError("direction contract space must be OBJECT or WORLD")
        normalized[key] = (axis_name, direction_space)
    return _rebind_geometry_axis_contract_by_faces(
        obj,
        mesh,
        uv_layer,
        analysis,
        normalized,
        settings,
    )


def _directed_geometry_metrics(
    obj: Any,
    mesh: Any,
    uv_layer: Any,
    analysis: UVLayoutAnalysis,
    settings: GroupLayoutOptions,
) -> Dict[str, Any]:
    """Measure the final modulo-360 geometry-axis contract per island."""

    residuals = []
    misaligned_ids = []
    opposite_ids = []
    quarter_turn_ids = []
    unresolved_ids = []
    required_unresolved_ids = []
    frame_residuals = []
    frame_misaligned_ids = []
    # A frame may be numerically resolvable but unsuitable for rotation when
    # its perpendicular axis exceeds the strict direction tolerance.  Keep
    # this separate from ``frame_unresolved_ids`` for backwards-compatible
    # diagnostics: those islands still satisfy the single-axis +V contract.
    frame_fallback_ids = []
    frame_negative_parity_ids = []
    frame_unresolved_ids = []
    records = {}
    tolerance = float(settings.direction_residual_tolerance)
    for island in analysis.islands:
        island_id = int(island.island_id)
        persisted_reason = island.geometry_frame_downgrade_reason
        # A persisted AUTO contract can explicitly record that no axis was
        # available (or that the previously selected axis became unusable).
        # ``geometry_axis_name`` is intentionally None in both cases, so a
        # normal AUTO call here would silently select a different axis and
        # defeat replay determinism.  Treat these markers as terminal audit
        # states and keep the distinction between optional and required
        # unresolved contracts.
        if persisted_reason in {
            "persisted_axis_unresolved",
            "persisted_axis_unavailable",
        }:
            unresolved_ids.append(island_id)
            if persisted_reason == "persisted_axis_unavailable":
                required_unresolved_ids.append(island_id)
            frame_unresolved_ids.append(island_id)
            island.geometry_final_residual = None
            records[str(island_id)] = {
                "axis": None,
                "space": str(
                    island.geometry_direction_space or settings.direction_space
                ),
                "confidence": 0.0,
                "residual_degrees": None,
                "frame_confidence": 0.0,
                "frame_parity": 0,
                "frame_parity_confidence": 0.0,
                "frame_residual_degrees": None,
                "frame_axis_residual_degrees": None,
                "frame_reason": persisted_reason,
                "frame_selected": False,
                "frame_fallback": False,
            }
            continue
        required_axis_name = (
            island.geometry_axis_name
            if island.geometry_axis_name in _DIRECTION_AXES
            else None
        )
        axis_name, direction, rotation, confidence = (
            _geometry_direction_record(
                obj,
                mesh,
                uv_layer,
                island.face_indices,
                space=island.geometry_direction_space or settings.direction_space,
                axis=settings.direction_axis,
                min_projection=settings.direction_axis_min_projection,
                fixed_axis_name=island.geometry_axis_name,
                auto_priority=settings.direction_auto_priority,
            )
        )
        if (
            axis_name is None
            or direction.length_squared <= _EPSILON
            or confidence + _EPSILON
            < float(settings.direction_axis_min_projection)
        ):
            island.geometry_final_residual = None
            unresolved_ids.append(island_id)
            if required_axis_name is not None:
                required_unresolved_ids.append(island_id)
            frame_unresolved_ids.append(island_id)
            continue
        residual = abs(_angle_wrap(float(rotation)))
        island.geometry_final_residual = residual
        residuals.append(residual)
        if residual > tolerance + _EPSILON:
            misaligned_ids.append(island_id)
        opposite_residual = abs(_angle_wrap(float(rotation) - math.pi))
        if opposite_residual <= tolerance + _EPSILON:
            opposite_ids.append(island_id)
        quarter_residual = min(
            abs(_angle_wrap(float(rotation) - math.pi * 0.5)),
            abs(_angle_wrap(float(rotation) + math.pi * 0.5)),
        )
        if quarter_residual <= tolerance + _EPSILON:
            quarter_turn_ids.append(island_id)

        frame = _geometry_frame_record(
            obj,
            mesh,
            uv_layer,
            island.face_indices,
            space=island.geometry_direction_space or settings.direction_space,
            axis_name=axis_name,
            axis=settings.direction_axis,
            min_projection=settings.direction_axis_min_projection,
            auto_priority=settings.direction_auto_priority,
        )
        # Keep the analysis snapshot synchronized with the exact coordinates
        # being audited.  This is intentionally metadata-only; UVs remain
        # untouched by a quality evaluation.
        island.geometry_frame_u_vector = frame["u_vector"].copy()
        island.geometry_frame_v_vector = frame["v_vector"].copy()
        island.geometry_frame_parity = int(frame.get("parity", 0))
        island.geometry_frame_parity_confidence = float(
            frame.get("parity_confidence", 0.0)
        )
        island.geometry_frame_confidence = float(
            frame.get("confidence", 0.0)
        )
        island.geometry_frame_rotation_angle = float(
            frame.get("rotation", 0.0)
        )
        island.geometry_frame_residual = frame.get("residual")
        island.geometry_frame_downgrade_reason = frame.get("reason")
        frame_reason = frame.get("reason")
        frame_confidence = float(frame.get("confidence", 0.0))
        frame_parity = int(frame.get("parity", 0))
        frame_residual = frame.get("residual")
        frame_axis_residual = _geometry_frame_axis_residual(island)
        frame_fallback = False
        # Keep the historical distinction between an unresolved frame (no
        # usable signal) and a usable frame that must fall back to the strict
        # single-axis rotation.  The latter now includes a complete-frame
        # rotation that drifts from the selected +V axis.
        frame_signal_ready = bool(
            settings.align_geometry_frame
            and frame_parity != 0
            and frame_confidence + _EPSILON
            >= min(
                _GEOMETRY_FRAME_MIN_CONCENTRATION,
                _GEOMETRY_FRAME_MIN_EFFECTIVE_FRACTION,
                _GEOMETRY_FRAME_MIN_PARITY_CONFIDENCE,
            )
            and frame_residual is not None
            and math.isfinite(float(frame_residual))
        )
        if not settings.align_geometry_frame:
            frame_unresolved_ids.append(island_id)
        elif not frame_signal_ready:
            frame_unresolved_ids.append(island_id)
        else:
            frame_residual = abs(float(frame_residual))
            frame_residuals.append(frame_residual)
            if frame_parity < 0:
                frame_negative_parity_ids.append(island_id)
                island.geometry_frame_downgrade_reason = (
                    "negative_parity_no_reflection"
                )
                frame_reason = "negative_parity_no_reflection"
            axis_drift = (
                frame_axis_residual is None
                or frame_axis_residual > tolerance + _EPSILON
            )
            if frame_residual > tolerance + _EPSILON or axis_drift:
                frame_misaligned_ids.append(island_id)
                if frame_parity >= 0:
                    if frame_residual > tolerance + _EPSILON:
                        island.geometry_frame_downgrade_reason = (
                            "frame_residual_exceeds_tolerance"
                        )
                        frame_reason = "frame_residual_exceeds_tolerance"
                    else:
                        island.geometry_frame_downgrade_reason = (
                            "frame_axis_residual_exceeds_tolerance"
                        )
                        frame_reason = (
                            "frame_axis_residual_exceeds_tolerance"
                        )
                if frame_parity == 1:
                    frame_fallback_ids.append(island_id)
                    frame_fallback = True
        records[str(island.island_id)] = {
            "axis": axis_name,
            "space": str(island.geometry_direction_space),
            "confidence": round(float(confidence), 6),
            "residual_degrees": round(math.degrees(residual), 6),
            "frame_confidence": round(frame_confidence, 6),
            "frame_parity": frame_parity,
            "frame_parity_confidence": round(
                float(frame.get("parity_confidence", 0.0)), 6
            ),
            "frame_residual_degrees": (
                None
                if frame_residual is None
                else round(math.degrees(abs(float(frame_residual))), 6)
            ),
            "frame_axis_residual_degrees": (
                None
                if frame_axis_residual is None
                else round(math.degrees(abs(float(frame_axis_residual))), 6)
            ),
            "frame_reason": frame_reason,
            "frame_selected": bool(
                _geometry_frame_is_reliable(island, settings)
            ),
            "frame_fallback": frame_fallback,
        }

    return {
        "enabled": bool(settings.align_geometry_direction),
        "resolved_islands": len(residuals),
        "unresolved_islands": len(unresolved_ids),
        "required_unresolved_islands": len(required_unresolved_ids),
        "frame_resolved_islands": len(frame_residuals),
        "frame_unresolved_islands": len(frame_unresolved_ids),
        "frame_misaligned_islands": len(frame_misaligned_ids),
        "frame_fallback_islands": len(frame_fallback_ids),
        "frame_negative_parity_islands": len(frame_negative_parity_ids),
        "frame_residual_p95_degrees": math.degrees(
            _quantile(frame_residuals, 0.95)
        ),
        "frame_residual_max_degrees": math.degrees(
            max(frame_residuals, default=0.0)
        ),
        "frame_misaligned_ids": frame_misaligned_ids,
        "frame_fallback_ids": frame_fallback_ids,
        "frame_negative_parity_ids": frame_negative_parity_ids,
        "frame_unresolved_ids": frame_unresolved_ids,
        "misaligned_islands": len(misaligned_ids),
        "opposite_islands": len(opposite_ids),
        "quarter_turn_islands": len(quarter_turn_ids),
        "residual_p95_degrees": math.degrees(_quantile(residuals, 0.95)),
        "residual_max_degrees": math.degrees(max(residuals, default=0.0)),
        "tolerance_degrees": math.degrees(tolerance),
        "misaligned_ids": misaligned_ids,
        "opposite_ids": opposite_ids,
        "quarter_turn_ids": quarter_turn_ids,
        "unresolved_ids": unresolved_ids,
        "required_unresolved_ids": required_unresolved_ids,
        "records": records,
    }


def evaluate_layout_quality(
    obj: Any,
    analysis: Optional[UVLayoutAnalysis] = None,
    face_to_island: Optional[Sequence[int]] = None,
    audit: Optional[Mapping[str, Any]] = None,
    epsilon: float = 1.0e-7,
    options: Optional[GroupLayoutOptions] = None,
) -> Dict[str, Any]:
    """Measure post-layout quality without changing mesh or UV state.

    The returned values deliberately distinguish *polygon* coverage from the
    fitted AABB occupancy exposed by older releases.  Area quantiles are in
    tile-space UV units, while ``short_edge`` is the smallest positive UV
    boundary edge of each island.  Flat aliases are included alongside the
    nested summaries to keep JSON consumers and Blender panel code simple.
    """

    settings = (options or GroupLayoutOptions(
        uv_epsilon=max(float(epsilon), 1.0e-9)
    )).validated()
    mesh, uv_layer = _require_object_mode_mesh(obj)
    if analysis is None:
        analysis = analyze_active_uv(obj, settings)
    if face_to_island is None:
        face_to_island = tuple(analysis.face_to_island)
    else:
        face_to_island = tuple(int(value) for value in face_to_island)
    if len(face_to_island) != len(mesh.polygons):
        # A stale analysis should never make scoring crash.  Reconstructing is
        # deterministic and leaves the caller's UV coordinates untouched.
        _islands, face_to_island, _adjacency, _diagonal = (
            compute_active_uv_islands(
                obj, settings,
            )
        )
        if analysis is None or len(analysis.islands) == 0:
            analysis = UVLayoutAnalysis(
                object_name=str(obj.name),
                mesh_name=str(mesh.name),
                uv_layer_name=str(uv_layer.name),
                object_diagonal=1.0,
                islands=list(_islands),
                adjacency=[],
                repeat_candidates=[],
                repeat_groups=[],
                layout_groups=[],
                face_to_island=tuple(face_to_island),
            )

    if audit is None:
        audit = audit_active_uv(obj, face_to_island, epsilon=epsilon)
    audit_data = dict(audit)
    face_areas, face_edges = _uv_face_quality_data(mesh, uv_layer)
    by_id = {int(island.island_id): island for island in analysis.islands}
    island_areas: Dict[int, float] = {
        island_id: 0.0 for island_id in by_id
    }
    island_edges: Dict[int, List[float]] = {
        island_id: [] for island_id in by_id
    }
    island_points: Dict[int, List[Vector]] = {
        island_id: [] for island_id in by_id
    }
    for face_index, island_id in enumerate(face_to_island):
        island_id = int(island_id)
        if island_id not in island_areas:
            island_areas[island_id] = 0.0
            island_edges[island_id] = []
            island_points[island_id] = []
        island_areas[island_id] += max(
            float(face_areas.get(face_index, 0.0)), 0.0
        )
        island_edges[island_id].extend(
            max(float(value), 0.0)
            for value in face_edges.get(face_index, ())
            if math.isfinite(float(value))
        )
        try:
            island_points[island_id].extend(
                uv_layer.data[int(loop_index)].uv.copy()
                for loop_index in mesh.polygons[face_index].loop_indices
            )
        except (AttributeError, IndexError, TypeError):
            pass

    # Include islands from the analysis even if a malformed face map omitted
    # them, keeping quantile cardinality deterministic.
    island_ids = sorted(set(by_id) | set(island_areas))
    area_values = [
        max(float(island_areas.get(island_id, 0.0)), 0.0)
        for island_id in island_ids
    ]
    edge_values = []
    for island_id in island_ids:
        positive_edges = [
            value for value in island_edges.get(island_id, ())
            if value > max(float(epsilon), 1.0e-12)
        ]
        edge_values.append(min(positive_edges) if positive_edges else 0.0)
    small_ids = {
        island_id for island_id, island in by_id.items() if bool(island.is_small)
    }
    small_area_values = [
        max(float(island_areas.get(island_id, 0.0)), 0.0)
        for island_id in island_ids if island_id in small_ids
    ]
    small_edge_values = [
        edge_values[position]
        for position, island_id in enumerate(island_ids)
        if island_id in small_ids
    ]

    all_points = [item.uv for item in uv_layer.data]
    finite_points = all(
        math.isfinite(float(point.x)) and math.isfinite(float(point.y))
        for point in all_points
    )
    bounds = _uv_bounds(all_points) if all_points and finite_points else (
        0.0, 0.0, 0.0, 0.0
    )
    width = max(float(bounds[2] - bounds[0]), 0.0)
    height = max(float(bounds[3] - bounds[1]), 0.0)
    tile_aabb_area = width * height if finite_points else 0.0
    polygon_area = sum(area_values) if finite_points else 0.0
    if not math.isfinite(polygon_area):
        polygon_area = 0.0
    tile_polygon_coverage = _clamp(polygon_area, 0.0, 1.0)
    tile_aabb_coverage = _clamp(tile_aabb_area, 0.0, 1.0)
    aabb_fill_raw = (
        polygon_area / tile_aabb_area
        if tile_aabb_area > max(float(epsilon) ** 2, _EPSILON)
        else 0.0
    )
    aabb_fill = _clamp(aabb_fill_raw, 0.0, 1.0)

    # Orientation is deliberately only a tie-breaker.  A candidate with a
    # slightly better axis alignment must not displace one with materially
    # better lower-tail area or polygon coverage.
    residuals = []
    for island in analysis.islands:
        reference = _orientation_reference_angle(island)
        if reference is None or not math.isfinite(float(reference)):
            continue
        residuals.append(abs(_nearest_cardinal_delta(float(reference))))
    residual_p95 = math.degrees(_quantile(residuals, 0.95))

    direction_metrics = _directed_geometry_metrics(
        obj, mesh, uv_layer, analysis, settings
    )
    require_all_directions = bool(
        settings.align_geometry_direction
        and str(settings.direction_axis).upper() == "AUTO"
    )
    frame_contract_enabled = bool(
        settings.align_geometry_direction and settings.align_geometry_frame
    )
    # A frame cannot be repaired by a rotation when its parity is negative.
    # Keep that case visible to callers.  Residual U-axis skew is diagnostic
    # for curved/sheared charts and is downgraded rather than making an entire
    # asset un-packable; the existing strict +V contract remains the commit
    # gate.
    frame_contract_valid = bool(
        not frame_contract_enabled
        or (
            int(
                direction_metrics.get("frame_negative_parity_islands", 0)
            ) == 0
        )
    )
    direction_valid = bool(
        not settings.align_geometry_direction
        or (
            int(direction_metrics["misaligned_islands"]) == 0
            and int(direction_metrics["required_unresolved_islands"]) == 0
            and frame_contract_valid
            and (
                not require_all_directions
                or int(direction_metrics["unresolved_islands"]) == 0
            )
        )
    )
    direction_metrics["require_all_resolved"] = require_all_directions
    direction_metrics["frame_contract_enabled"] = frame_contract_enabled
    direction_metrics["frame_contract_valid"] = frame_contract_valid
    long_edge_metrics = dict(
        getattr(analysis, "geometry_long_edge_metrics", {}) or {}
    )
    valid = _quality_validity(audit_data) and direction_valid
    metrics: Dict[str, Any] = {
        "islands": len(island_ids),
        "small_islands": len(small_area_values),
        "polygon_area": float(polygon_area),
        "tile_polygon_coverage": float(tile_polygon_coverage),
        "tile_aabb_area": float(tile_aabb_area),
        "tile_aabb_coverage": float(tile_aabb_coverage),
        "aabb_fill_raw": float(aabb_fill_raw),
        "aabb_fill": float(aabb_fill),
        "tile_bounds": _bounds_to_list(bounds),
        "island_area_p05": _quantile(area_values, 0.05),
        "island_area_p10": _quantile(area_values, 0.10),
        "island_area_p50": _quantile(area_values, 0.50),
        "small_island_area_p05": _quantile(small_area_values, 0.05),
        "small_island_area_p10": _quantile(small_area_values, 0.10),
        "short_edge_p05": _quantile(edge_values, 0.05),
        "short_edge_p10": _quantile(edge_values, 0.10),
        "short_edge_p50": _quantile(edge_values, 0.50),
        "small_short_edge_p05": _quantile(small_edge_values, 0.05),
        "small_short_edge_p10": _quantile(small_edge_values, 0.10),
        "orientation_residual_p95_degrees": residual_p95,
        "geometry_long_edge_enabled": bool(
            long_edge_metrics.get(
                "enabled",
                settings.cohere_geometry_long_edge
                and settings.align_geometry_direction,
            )
        ),
        "geometry_long_edge_cohorts": int(
            long_edge_metrics.get("cohorts_considered", 0)
        ),
        "geometry_long_edge_attempted_islands": int(
            long_edge_metrics.get("attempted_islands", 0)
        ),
        "geometry_long_edge_aligned_islands": int(
            long_edge_metrics.get("aligned_islands", 0)
        ),
        "geometry_long_edge_downgraded_islands": int(
            long_edge_metrics.get("downgraded_islands", 0)
        ),
        "geometry_long_edge_max_spread_degrees": float(
            long_edge_metrics.get(
                "max_final_spread_degrees",
                math.degrees(_GEOMETRY_LONG_EDGE_MAX_SPREAD),
            )
        ),
        "geometry_long_edge_max_source_spread_degrees": float(
            long_edge_metrics.get("max_source_spread_degrees", 0.0)
        ),
        "geometry_long_edge_downgraded_ids": list(
            long_edge_metrics.get("downgraded_ids", ())
        ),
        "geometry_long_edge": long_edge_metrics,
        "directed_geometry_residual_p95_degrees": float(
            direction_metrics["residual_p95_degrees"]
        ),
        "directed_geometry_residual_max_degrees": float(
            direction_metrics["residual_max_degrees"]
        ),
        "directed_geometry_resolved_islands": int(
            direction_metrics["resolved_islands"]
        ),
        "directed_geometry_unresolved_islands": int(
            direction_metrics["unresolved_islands"]
        ),
        "directed_geometry_required_unresolved_islands": int(
            direction_metrics["required_unresolved_islands"]
        ),
        "directed_geometry_misaligned_islands": int(
            direction_metrics["misaligned_islands"]
        ),
        "directed_geometry_opposite_islands": int(
            direction_metrics["opposite_islands"]
        ),
        "directed_geometry_quarter_turn_islands": int(
            direction_metrics["quarter_turn_islands"]
        ),
        "directed_geometry_frame_resolved_islands": int(
            direction_metrics.get("frame_resolved_islands", 0)
        ),
        "directed_geometry_frame_unresolved_islands": int(
            direction_metrics.get("frame_unresolved_islands", 0)
        ),
        "directed_geometry_frame_misaligned_islands": int(
            direction_metrics.get("frame_misaligned_islands", 0)
        ),
        "directed_geometry_frame_fallback_islands": int(
            direction_metrics.get("frame_fallback_islands", 0)
        ),
        "directed_geometry_frame_fallback_ids": list(
            direction_metrics.get("frame_fallback_ids", ())
        ),
        "directed_geometry_frame_negative_parity_islands": int(
            direction_metrics.get("frame_negative_parity_islands", 0)
        ),
        "directed_geometry_frame_residual_p95_degrees": float(
            direction_metrics.get("frame_residual_p95_degrees", 0.0)
        ),
        "directed_geometry_frame_residual_max_degrees": float(
            direction_metrics.get("frame_residual_max_degrees", 0.0)
        ),
        "directed_geometry_valid": direction_valid,
        "directed_geometry": direction_metrics,
        "valid": bool(valid),
        "finite": bool(audit_data.get("finite", False)),
        "inside_tile": bool(audit_data.get("inside_tile", False)),
        "positive": int(audit_data.get("positive", 0)),
        "negative": int(audit_data.get("negative", 0)),
        "degenerate": int(audit_data.get("degenerate", 0)),
        "overlap": bool(audit_data.get("overlap", False)),
        "triangles": int(audit_data.get("triangles", 0)),
        "audit": audit_data,
    }
    # Concise aliases used by older report scripts and by UI integrations.
    metrics.update({
        "polygon_coverage": metrics["tile_polygon_coverage"],
        "aabb_coverage": metrics["tile_aabb_coverage"],
        "island_p05": metrics["island_area_p05"],
        "island_p10": metrics["island_area_p10"],
        "island_p50": metrics["island_area_p50"],
        "small_p05": metrics["small_island_area_p05"],
        "small_p10": metrics["small_island_area_p10"],
    })
    return metrics


# ``measure_layout_quality`` reads naturally in external integrations and is
# kept as a stable alias for the more explicit public name.
measure_layout_quality = evaluate_layout_quality


def _metric_value(metrics: Mapping[str, Any], *names: str) -> float:
    for name in names:
        value = metrics.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return 0.0


def score_layout_candidate(
    candidate: Mapping[str, Any],
    reference: Optional[Mapping[str, Any]] = None,
    options: Optional[GroupLayoutOptions] = None,
    reference_scale: Optional[float] = None,
    candidate_scale: Optional[float] = None,
) -> float:
    """Return a deterministic multi-objective score for one layout.

    Lower-tail island area is the primary objective.  Polygon coverage,
    polygon-vs-AABB fill/tile coverage, and short-edge readability are bounded
    secondary terms.  Values are normalized to a reference candidate when
    supplied, so the score remains meaningful across adaptive variants with
    different uniform scales.
    """

    settings = (options or GroupLayoutOptions()).validated()
    # Accept either the raw metric mapping or a GroupLayoutResult-like object;
    # this keeps the public helper convenient for add-on and test callers.
    if not isinstance(candidate, Mapping):
        candidate = getattr(candidate, "quality_metrics", {}) or {}
    if reference is not None and not isinstance(reference, Mapping):
        reference = getattr(reference, "quality_metrics", {}) or {}
    candidate = candidate or {}
    reference = reference or candidate
    valid = bool(candidate.get("valid", candidate.get("candidate_valid", True)))
    if not valid:
        return float("-inf")

    def ratio(names: Sequence[str], floor: float = _EPSILON) -> float:
        value = _metric_value(candidate, *names)
        baseline = _metric_value(reference, *names)
        if baseline <= floor:
            return 1.0 if value > floor else 0.0
        return _clamp(value / baseline, 0.0, 4.0)

    # The optimization target is the readability of detached mechanical
    # details, so prefer the lower tail of *small* islands whenever both
    # candidates describe a non-empty small-island population.  Falling back
    # to all-island quantiles keeps the helper useful for meshes without any
    # islands classified as small.
    candidate_small_count = _metric_value(candidate, "small_islands")
    reference_small_count = _metric_value(reference, "small_islands")
    use_small_tail = candidate_small_count > 0.0 and reference_small_count > 0.0
    if use_small_tail:
        area_names = (
            ("small_island_area_p05", "small_p05"),
            ("small_island_area_p10", "small_p10"),
        )
        edge_names = (
            ("small_short_edge_p05", "short_edge_p05"),
            ("small_short_edge_p10", "small_short_edge_p10", "short_edge_p10"),
        )
    else:
        area_names = (
            ("island_area_p05", "island_p05"),
            ("island_area_p10", "island_p10"),
        )
        edge_names = (
            ("short_edge_p05",),
            ("short_edge_p10",),
        )
    area_gain = min(
        ratio(area_names[0]),
        ratio(area_names[1]),
    )
    polygon_gain = ratio(("tile_polygon_coverage", "polygon_coverage"))
    # A high polygon/AABB fill can still be a poor atlas when the whole
    # bounding box occupies only a narrow strip of the 0-1 tile.  Combine
    # compactness and tile coverage symmetrically so a candidate cannot win by
    # trading away substantial occupied area for a tighter rectangle.
    aabb_fill_gain = ratio(("aabb_fill",), floor=1.0e-9)
    occupancy_names = ("tile_aabb_coverage", "aabb_coverage")
    if any(name in candidate for name in occupancy_names) and any(
        name in reference for name in occupancy_names
    ):
        aabb_coverage_gain = ratio(occupancy_names, floor=1.0e-9)
        fill_gain = math.sqrt(
            max(aabb_fill_gain * aabb_coverage_gain, 0.0)
        )
    else:
        # Keep the public scorer compatible with pre-0.5.5 metric mappings.
        fill_gain = aabb_fill_gain
    edge_gain = min(
        ratio(edge_names[0]),
        ratio(edge_names[1]),
    )
    weights = [
        max(float(settings.area_score_weight), 0.0),
        max(float(settings.polygon_coverage_score_weight), 0.0),
        max(float(settings.aabb_fill_score_weight), 0.0),
        max(float(settings.short_edge_score_weight), 0.0),
    ]
    total = sum(weights)
    if total <= _EPSILON:
        weights = [1.0, 0.0, 0.0, 0.0]
        total = 1.0
    score = (
        weights[0] * area_gain
        + weights[1] * polygon_gain
        + weights[2] * fill_gain
        + weights[3] * edge_gain
    ) / total
    # Scale is a soft tie-breaker only.  Density eligibility is enforced by
    # the adaptive selector, so a direct caller can still compare metrics.
    if candidate_scale is not None and reference_scale is not None:
        try:
            scale_ratio = _clamp(
                float(candidate_scale) / max(float(reference_scale), _EPSILON),
                0.0,
                1.0,
            )
            score += 0.02 * scale_ratio
        except (TypeError, ValueError):
            pass
    return float(score)


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
    result = {
        "triangles": len(triangle_records),
        "positive": positive,
        "negative": negative,
        "degenerate": degenerate,
        "finite": finite,
        "inside_tile": inside_tile,
        "overlap": finite and _has_triangle_overlap(triangle_records, epsilon),
    }
    # Keep the validity predicate available to callers that only request the
    # lightweight audit.  Detailed area/coverage metrics live in
    # ``evaluate_layout_quality`` so this audit remains cheap and deterministic.
    result["valid"] = _quality_validity(result)
    return result


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
    return sorted({
        int(record[1])
        for record in _nonpositive_winding_records(
            mesh, uv_layer, face_to_island)
    })


def _nonpositive_winding_records(
    mesh: Any,
    uv_layer: Any,
    face_to_island: Sequence[int],
    island_id: Optional[int] = None,
) -> List[Tuple[int, int, Tuple[int, int, int], Tuple[Vector, Vector, Vector], float, float]]:
    """Return tessellated triangles that are not safely positive."""

    mesh.calc_loop_triangles()
    records = []
    for triangle in mesh.loop_triangles:
        loop_indices = tuple(int(index) for index in triangle.loops)
        face_index = int(triangle.polygon_index)
        triangle_island = int(face_to_island[face_index])
        if island_id is not None and triangle_island != int(island_id):
            continue
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
            records.append((
                face_index,
                triangle_island,
                loop_indices,
                points,
                float(determinant),
                float(threshold),
            ))
    return records


def _source_welded_loop_groups(
    mesh: Any,
    source_uvs: Sequence[Vector],
    face_to_island: Sequence[int],
    uv_epsilon: float,
) -> Tuple[Dict[int, Tuple[int, ...]], List[int]]:
    """Build source UV vertex fans without crossing a seam or source island."""

    if len(source_uvs) != len(mesh.loops):
        raise ValueError("source_uvs must contain one coordinate per mesh loop")
    if len(face_to_island) != len(mesh.polygons):
        raise ValueError("face_to_island must contain one id per polygon")
    epsilon_squared = max(float(uv_epsilon), 0.0) ** 2
    parent = list(range(len(mesh.loops)))
    loop_faces = [-1] * len(mesh.loops)

    def find(loop_index: int) -> int:
        while parent[loop_index] != loop_index:
            parent[loop_index] = parent[parent[loop_index]]
            loop_index = parent[loop_index]
        return loop_index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root

    edge_corners: Dict[int, List[Tuple[int, Dict[int, int]]]] = defaultdict(list)
    for polygon in mesh.polygons:
        loop_indices = tuple(int(index) for index in polygon.loop_indices)
        for loop_index in loop_indices:
            loop_faces[loop_index] = int(polygon.index)
        for offset, loop_index in enumerate(loop_indices):
            next_loop = loop_indices[(offset + 1) % len(loop_indices)]
            edge_index = int(mesh.loops[loop_index].edge_index)
            edge_corners[edge_index].append((
                int(polygon.index),
                {
                    int(mesh.loops[loop_index].vertex_index): loop_index,
                    int(mesh.loops[next_loop].vertex_index): next_loop,
                },
            ))

    for edge_index, edge_records in edge_corners.items():
        if len(edge_records) != 2 or bool(mesh.edges[edge_index].use_seam):
            continue
        (left_face, left), (right_face, right) = edge_records
        left_island = int(face_to_island[left_face])
        if left_island != int(face_to_island[right_face]):
            continue
        shared_vertices = sorted(set(left) & set(right))
        if len(shared_vertices) != 2:
            continue
        if not all(
            (source_uvs[left[vertex]] - source_uvs[right[vertex]]).length_squared
            <= epsilon_squared
            for vertex in shared_vertices
        ):
            continue
        for vertex in shared_vertices:
            union(left[vertex], right[vertex])

    grouped: Dict[int, List[int]] = defaultdict(list)
    for loop_index in range(len(mesh.loops)):
        grouped[find(loop_index)].append(loop_index)
    groups = {
        root: tuple(sorted(members))
        for root, members in sorted(grouped.items())
    }
    return groups, loop_faces


def _snap_source_continuous_loops(
    mesh: Any,
    source_uvs: Sequence[Vector],
    fitted: Dict[int, Dict[int, Vector]],
    face_to_island: Sequence[int],
    uv_epsilon: float,
) -> int:
    """Make source-continuous manifold corners exactly equal after packing.

    UV corners are stored independently per polygon.  Two corners that were
    within the source continuity tolerance can quantize to opposite sides of
    that tolerance after a rigid transform, splitting one source island on
    replay.  Union only endpoint loops across an unseamed edge with exactly
    two incident faces in the same source island, then assign their fitted
    coordinates one deterministic average.  Source seams, non-manifold edges,
    and different source islands are never crossed.
    """

    groups, loop_faces = _source_welded_loop_groups(
        mesh, source_uvs, face_to_island, uv_epsilon)
    snapped = 0
    for loop_indices in groups.values():
        if len(loop_indices) < 2:
            continue
        island_ids = {
            int(face_to_island[loop_faces[index]])
            for index in loop_indices
        }
        if len(island_ids) != 1:
            raise RuntimeError("UV continuity snap crossed a source island")
        island_id = next(iter(island_ids))
        coordinates = fitted.get(island_id)
        if coordinates is None or any(index not in coordinates for index in loop_indices):
            raise RuntimeError("UV continuity snap could not resolve fitted loops")
        target = sum(
            (coordinates[index] for index in loop_indices),
            Vector((0.0, 0.0)),
        ) / len(loop_indices)
        for loop_index in loop_indices:
            coordinates[loop_index] = target.copy()
        snapped += len(loop_indices) - 1
    return snapped


def _validate_source_island_partition(
    mesh: Any,
    uv_layer: Any,
    source_face_to_island: Sequence[int],
    uv_epsilon: float,
) -> None:
    """Reject a writeback that splits or joins any source UV island."""

    _islands, replay_face_to_island, _records = _reconstruct_islands(
        mesh, uv_layer, uv_epsilon
    )
    source_members: Dict[int, Set[int]] = defaultdict(set)
    replay_members: Dict[int, Set[int]] = defaultdict(set)
    for face_index, source_id in enumerate(source_face_to_island):
        source_members[int(source_id)].add(face_index)
    for face_index, replay_id in enumerate(replay_face_to_island):
        replay_members[int(replay_id)].add(face_index)
    source_partition = sorted(tuple(sorted(value)) for value in source_members.values())
    replay_partition = sorted(tuple(sorted(value)) for value in replay_members.values())
    if replay_partition != source_partition:
        raise RuntimeError(
            "UV group layout changed source island continuity: {} -> {}".format(
                len(source_partition), len(replay_partition)
            )
        )


def _try_welded_quantized_winding_repair(
    mesh: Any,
    uv_layer: Any,
    island_id: int,
    island_coordinates: Mapping[int, Vector],
    face_to_island: Sequence[int],
    source_uvs: Sequence[Vector],
    welded_by_loop: Mapping[int, Sequence[int]],
    uv_epsilon: float,
    direction_validator: Optional[Callable[[], bool]] = None,
) -> Optional[Dict[int, Vector]]:
    """Repair float32-only skinny ears with a welded vertex micro-nudge.

    The source geometry and source UV triangle must both be positive and
    near-collinear.  Only one source-welded vertex fan moves; the caller-built
    fan never crosses a seam, non-manifold edge, UV split, or source island.
    Every accepted step strictly reduces the set of non-positive triangles
    and revalidates overlap, bounds, direction, and the complete face
    partition.  A rejected island is restored in full.
    """

    island_id = int(island_id)
    original = {
        int(loop_index): uv_layer.data[int(loop_index)].uv.copy()
        for loop_index in island_coordinates
    }
    working = {index: point.copy() for index, point in original.items()}
    target_relative_heights = (
        1.0e-6,
        2.0e-6,
        5.0e-6,
        1.0e-5,
        2.0e-5,
    )
    maximum_displacement = 4.0e-7

    def restore(coordinates: Mapping[int, Vector]) -> None:
        for loop_index, point in coordinates.items():
            uv_layer.data[loop_index].uv = point
        mesh.update()

    def problem_keys(records):
        return {
            (int(record[0]), tuple(int(value) for value in record[2]))
            for record in records
        }

    maximum_steps = max(
        len(_nonpositive_winding_records(
            mesh, uv_layer, face_to_island, island_id=island_id)),
        1,
    )
    for _step in range(maximum_steps):
        island_problems = _nonpositive_winding_records(
            mesh, uv_layer, face_to_island, island_id=island_id)
        if not island_problems:
            return working
        global_before = problem_keys(_nonpositive_winding_records(
            mesh, uv_layer, face_to_island))
        accepted_step = False

        for record in island_problems:
            _face_index, _record_island, loop_indices, points, determinant, _threshold = record
            source_points = tuple(source_uvs[index].copy() for index in loop_indices)
            source_edge_a = source_points[1] - source_points[0]
            source_edge_b = source_points[2] - source_points[0]
            source_determinant = (
                source_edge_a.x * source_edge_b.y
                - source_edge_a.y * source_edge_b.x
            )
            source_scale = max(
                source_edge_a.length_squared,
                source_edge_b.length_squared,
                (source_points[2] - source_points[1]).length_squared,
                _EPSILON,
            )
            source_threshold = max(source_scale * 1.0e-12, 1.0e-24)
            if (
                    source_determinant <= source_threshold
                    or abs(source_determinant) / source_scale > 1.0e-4):
                continue

            geometry = tuple(
                mesh.vertices[
                    int(mesh.loops[loop_index].vertex_index)
                ].co.copy()
                for loop_index in loop_indices
            )
            geometry_edges = (
                geometry[1] - geometry[0],
                geometry[2] - geometry[1],
                geometry[0] - geometry[2],
            )
            geometry_scale = max(
                (edge.length_squared for edge in geometry_edges),
                default=0.0,
            )
            geometry_area_2 = (
                geometry[1] - geometry[0]
            ).cross(geometry[2] - geometry[0]).length
            current_edges = (
                points[1] - points[0],
                points[2] - points[1],
                points[0] - points[2],
            )
            current_scale = max(
                (edge.length_squared for edge in current_edges),
                default=0.0,
            )
            if (
                    geometry_scale <= 1.0e-24
                    or geometry_area_2 / geometry_scale > 1.0e-4
                    or current_scale <= 1.0e-24
                    or abs(float(determinant)) / current_scale > 1.0e-4):
                continue

            base_a, base_b, apex = max(
                ((0, 1, 2), (1, 2, 0), (2, 0, 1)),
                key=lambda item: (
                    geometry[item[1]] - geometry[item[0]]
                ).length_squared,
            )
            apex_loop = int(loop_indices[apex])
            welded = tuple(int(value) for value in welded_by_loop.get(
                apex_loop, (apex_loop,)))
            if not welded or any(index not in working for index in welded):
                continue

            uv_base = points[base_b] - points[base_a]
            uv_base_squared = uv_base.length_squared
            if uv_base_squared <= 1.0e-24:
                continue
            base_length = math.sqrt(uv_base_squared)
            direction = uv_base / base_length
            perpendicular = Vector((-direction.y, direction.x))
            projection = (
                points[base_a]
                + direction * (points[apex] - points[base_a]).dot(direction)
            )

            candidates = []
            for relative_height in target_relative_heights:
                target_height = base_length * relative_height
                for candidate in (
                        projection + perpendicular * target_height,
                        projection - perpendicular * target_height):
                    candidate_points = list(points)
                    candidate_points[apex] = candidate
                    edge_a = candidate_points[1] - candidate_points[0]
                    edge_b = candidate_points[2] - candidate_points[0]
                    candidate_determinant = (
                        edge_a.x * edge_b.y - edge_a.y * edge_b.x)
                    candidate_scale = max(
                        edge_a.length_squared,
                        edge_b.length_squared,
                        (candidate_points[2] - candidate_points[1]).length_squared,
                        _EPSILON,
                    )
                    if candidate_determinant <= max(
                            candidate_scale * 1.0e-12, 1.0e-24):
                        continue
                    delta = candidate - points[apex]
                    if delta.length > maximum_displacement:
                        continue
                    candidates.append((delta.length_squared, delta))

            for _distance, delta in sorted(
                    candidates, key=lambda item: item[0]):
                candidate_coordinates = {
                    index: point.copy() for index, point in working.items()
                }
                for loop_index in welded:
                    candidate_coordinates[loop_index] = (
                        candidate_coordinates[loop_index] + delta)
                restore(candidate_coordinates)

                global_after = problem_keys(_nonpositive_winding_records(
                    mesh, uv_layer, face_to_island))
                if not global_after < global_before:
                    restore(working)
                    continue
                points_after = [item.uv for item in uv_layer.data]
                finite_and_bounded = all(
                    math.isfinite(float(point.x))
                    and math.isfinite(float(point.y))
                    and -float(uv_epsilon) <= float(point.x) <= 1.0 + float(uv_epsilon)
                    and -float(uv_epsilon) <= float(point.y) <= 1.0 + float(uv_epsilon)
                    for point in points_after
                )
                if not finite_and_bounded or _has_triangle_overlap(
                        _triangle_records(mesh, uv_layer, face_to_island),
                        float(uv_epsilon)):
                    restore(working)
                    continue
                try:
                    _validate_source_island_partition(
                        mesh,
                        uv_layer,
                        face_to_island,
                        float(uv_epsilon),
                    )
                except RuntimeError:
                    restore(working)
                    continue
                if direction_validator is not None:
                    try:
                        direction_valid = bool(direction_validator())
                    except Exception:
                        direction_valid = False
                    if not direction_valid:
                        restore(working)
                        continue

                working = {
                    index: uv_layer.data[index].uv.copy()
                    for index in working
                }
                accepted_step = True
                break
            if accepted_step:
                break

        if not accepted_step:
            restore(original)
            return None

    if _nonpositive_winding_records(
            mesh, uv_layer, face_to_island, island_id=island_id):
        restore(original)
        return None
    return working


def _stabilize_quantized_winding(
    mesh: Any,
    uv_layer: Any,
    fitted: Dict[int, Dict[int, Vector]],
    face_to_island: Sequence[int],
    source_uvs: Optional[Sequence[Vector]] = None,
    uv_epsilon: float = 1.0e-6,
    direction_validator: Optional[Callable[[], bool]] = None,
) -> Dict[int, float]:
    """Resolve float32 winding flips with bounded quantization repairs.

    Near-collinear source triangles can have positive double-precision area
    that changes sign only when the packed coordinates are stored by Blender
    as float32. Rotating or translating the complete island changes the
    quantization phase without changing shape, scale, or topology. Rotation
    stays below 0.001 radians and translation preserves direction exactly. If
    no rigid quantization phase works, a source-verified near-collinear ear may
    move one welded vertex fan by at most 4e-7 UV units. The full layout audit,
    face-partition gate, similarity check, and direction gate remain
    authoritative.
    """

    problem_ids = _nonpositive_winding_islands(
        mesh, uv_layer, face_to_island
    )
    if not problem_ids:
        return {}
    welded_by_loop: Dict[int, Tuple[int, ...]] = {}
    if source_uvs is not None:
        groups, _loop_faces = _source_welded_loop_groups(
            mesh, source_uvs, face_to_island, uv_epsilon)
        for members in groups.values():
            for loop_index in members:
                welded_by_loop[int(loop_index)] = members
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
        if not accepted and source_uvs is not None:
            for loop_index, point in base.items():
                uv_layer.data[loop_index].uv = point
            mesh.update()
            repaired = _try_welded_quantized_winding_repair(
                mesh,
                uv_layer,
                island_id,
                base,
                face_to_island,
                source_uvs,
                welded_by_loop,
                uv_epsilon,
                direction_validator=direction_validator,
            )
            if repaired is not None:
                fitted[island_id] = repaired
                resolved[island_id] = 0.0
                accepted = True
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
    island_scale_factors: Optional[Mapping[int, float]] = None,
) -> None:
    island_scale_factors = island_scale_factors or {}
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
        expected_scale = uniform_scale * max(
            float(island_scale_factors.get(island.island_id, 1.0)),
            _EPSILON,
        )
        tolerance = max(expected_scale, 1.0) * 5.0e-7
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
        if _relative_error(measured_scale, expected_scale) > scale_tolerance:
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
        obj, analysis.face_to_island, epsilon=settings.uv_epsilon
    )
    _validate_source_audit(before_audit, settings)

    angles = _orientation_angles(analysis, settings)
    oriented = _oriented_coordinates(uv_layer, analysis, angles)
    oriented, island_scale_factors, scaled_small_count = (
        _boost_small_island_coordinates(
            oriented,
            analysis,
            settings.small_island_scale_boost,
        )
    )
    plan, fitted, uniform_scale = _plan_with_margin(
        oriented,
        analysis.layout_groups,
        settings,
        analysis.repeat_groups,
        group_model_centroids=_layout_group_model_centroids(
            analysis.layout_groups, analysis.islands
        ),
        islands=analysis.islands,
        analysis=analysis,
    )

    def validate_direction_contract() -> bool:
        if not settings.align_geometry_direction:
            return True
        replay_analysis = analyze_active_uv(obj, settings)
        replay_analysis = _rebind_geometry_axis_contract(
            obj,
            mesh,
            uv_layer,
            replay_analysis,
            analysis,
            settings,
        )
        metrics = _directed_geometry_metrics(
            obj, mesh, uv_layer, replay_analysis, settings
        )
        require_all_resolved = str(settings.direction_axis).upper() == "AUTO"
        return bool(
            int(metrics["misaligned_islands"]) == 0
            and int(metrics["required_unresolved_islands"]) == 0
            and (
                not require_all_resolved
                or int(metrics["unresolved_islands"]) == 0
            )
        )

    try:
        _snap_source_continuous_loops(
            mesh,
            snapshot,
            fitted,
            analysis.face_to_island,
            settings.uv_epsilon,
        )
        for island_coordinates in fitted.values():
            for loop_index, coordinate in island_coordinates.items():
                uv_layer.data[loop_index].uv = coordinate
        mesh.update()
        after_audit = audit_active_uv(
            obj, analysis.face_to_island, epsilon=settings.uv_epsilon
        )
        quantization_rotations = {}
        if after_audit["negative"] or after_audit["degenerate"]:
            quantization_rotations = _stabilize_quantized_winding(
                mesh,
                uv_layer,
                fitted,
                analysis.face_to_island,
                source_uvs=snapshot,
                uv_epsilon=settings.uv_epsilon,
                direction_validator=validate_direction_contract,
            )
            after_audit = audit_active_uv(
                obj,
                analysis.face_to_island,
                epsilon=settings.uv_epsilon,
            )
        _validate_source_island_partition(
            mesh,
            uv_layer,
            analysis.face_to_island,
            settings.uv_epsilon,
        )
        _validate_result_audit(after_audit)
        _validate_island_similarity(
            snapshot,
            uv_layer,
            analysis.islands,
            uniform_scale,
            island_scale_factors=island_scale_factors,
        )
    except Exception:
        for item, coordinate in zip(uv_layer.data, snapshot):
            item.uv = coordinate
        mesh.update()
        raise

    fitted_gap = _minimum_aabb_gap(fitted)
    fitted_bounds = _uv_bounds(
        point
        for coordinates in fitted.values()
        for point in coordinates.values()
    )
    fitted_width = max(fitted_bounds[2] - fitted_bounds[0], 0.0)
    fitted_height = max(fitted_bounds[3] - fitted_bounds[1], 0.0)
    grouped_small = len({
        island_id
        for group in analysis.layout_groups
        for island_id in group.small_member_ids
    })
    rotated_islands = sum(
        abs(_angle_wrap(angle)) > 1.0e-7 for angle in angles.values()
    )
    try:
        quality_metrics = evaluate_layout_quality(
            obj,
            analysis=analysis,
            face_to_island=analysis.face_to_island,
            audit=after_audit,
            epsilon=settings.uv_epsilon,
            options=settings,
        )
        candidate_valid = bool(quality_metrics.get("valid", False))
        if not candidate_valid:
            raise RuntimeError("UV group layout quality gate failed")
    except Exception:
        # The quality gate is part of the transaction.  Never leave a caller
        # with a geometrically invalid candidate merely because the low-level
        # audit completed without raising.
        for item, coordinate in zip(uv_layer.data, snapshot):
            item.uv = coordinate
        mesh.update()
        raise
    # Persist only after every geometric, partition, and quality gate passes.
    # The face-set/topology key makes this contract safe across Blender
    # callbacks and later .blend reloads.
    _persist_geometry_axis_contract(mesh, uv_layer, analysis, settings)
    return GroupLayoutResult(
        analysis=analysis,
        uniform_scale=uniform_scale,
        requested_margin=settings.margin,
        achieved_aabb_gap=fitted_gap,
        rotated_islands=rotated_islands,
        grouped_small_islands=grouped_small,
        before_audit=before_audit,
        after_audit=after_audit,
        group_quarter_turn=_group_quarter_turn_allowed(settings),
        quantization_adjusted_islands=len(quantization_rotations),
        max_quantization_rotation_degrees=max(
            (abs(math.degrees(value)) for value in quantization_rotations.values()),
            default=0.0,
        ),
        packed_width=plan.width,
        packed_height=plan.height,
        tile_occupancy=_clamp(fitted_width * fitted_height, 0.0, 1.0),
        small_island_scale_boost=settings.small_island_scale_boost,
        small_islands_scaled=scaled_small_count,
        square_pack_max_edge_relaxation=(
            settings.square_pack_max_edge_relaxation
        ),
        quality_metrics=quality_metrics,
        candidate_score=score_layout_candidate(
            quality_metrics,
            quality_metrics,
            settings,
        ),
        candidate_valid=candidate_valid,
    )


def layout_active_uv_adaptive(
    obj: Any,
    options: Optional[GroupLayoutOptions] = None,
) -> GroupLayoutResult:
    """Try safe layout variants and commit the best valid result.

    A perceptual small-island boost is deliberately best-effort.  If the
    enlarged charts cannot satisfy the strict no-overlap contract, the
    alternate group rotation and finally the boost-disabled variants remain
    available.  Every variant starts from the same source coordinates.  This
    matters because the first valid variant is not necessarily the most useful
    atlas: a different quarter-turn can have a much squarer footprint, while
    a boost-disabled variant can retain more global texel density.  Selection
    is based on the resulting uniform scale and tile coverage before aspect
    ratio, and only a fully audited candidate can be committed.
    """

    primary = (options or GroupLayoutOptions()).validated()
    attempts = [primary]
    if not primary.align_geometry_direction:
        attempts.append(replace(
            primary,
            allow_group_quarter_turn=not primary.allow_group_quarter_turn,
        ))
    if primary.small_island_scale_boost > 1.0 + 1.0e-9:
        attempts.append(replace(primary, small_island_scale_boost=1.0))
        if not primary.align_geometry_direction:
            attempts.append(replace(
                primary,
                allow_group_quarter_turn=not primary.allow_group_quarter_turn,
                small_island_scale_boost=1.0,
            ))

    mesh, uv_layer = _require_object_mode_mesh(obj)
    source_uv = [item.uv.copy() for item in uv_layer.data]
    # ``layout_active_uv`` persists a contract on success.  Adaptive trials
    # must remain independent, otherwise a failed/alternate candidate can
    # silently steer the next trial's AUTO resolver.  Snapshot the one ID
    # property and restore it together with the UV coordinates.
    contract_key = _geometry_axis_contract_key(str(uv_layer.name))
    try:
        contract_present = contract_key in mesh.keys()
        contract_value = mesh.get(contract_key) if contract_present else None
    except (AttributeError, KeyError, RuntimeError, TypeError):
        contract_present = False
        contract_value = None

    def restore_source() -> None:
        for item, coordinate in zip(uv_layer.data, source_uv):
            item.uv = coordinate.copy()
        try:
            if contract_present:
                mesh[contract_key] = contract_value
            elif contract_key in mesh.keys():
                del mesh[contract_key]
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            pass
        mesh.update()

    errors = []
    successful = []
    for index, settings in enumerate(attempts):
        # ``layout_active_uv`` is transactional on its own, but explicitly
        # restoring here also isolates successful candidates from one another.
        restore_source()
        try:
            result = layout_active_uv(obj, settings)
            # Re-run the read-only quality gate explicitly at the adaptive
            # boundary.  This protects selection from a future layout backend
            # that returns a result without propagating its audit fields.
            quality_metrics = evaluate_layout_quality(
                obj,
                analysis=result.analysis,
                face_to_island=result.analysis.face_to_island,
                audit=result.after_audit,
                epsilon=settings.uv_epsilon,
                options=settings,
            )
            result.quality_metrics = quality_metrics
            result.candidate_valid = bool(quality_metrics.get("valid", False))
            direction_ok = bool(
                not settings.align_geometry_direction
                or result.quality_metrics.get("directed_geometry_valid", False)
            )
            if not result.candidate_valid or not direction_ok:
                errors.append(
                    "{}: invalid quality audit".format(index)
                )
                restore_source()
                continue
            successful.append((
                index,
                result,
                [item.uv.copy() for item in uv_layer.data],
            ))
        except Exception as exc:
            # ``layout_active_uv`` restores its own snapshot, but keep the
            # adaptive loop defensive: third-party Blender callbacks can
            # raise non-RuntimeError exceptions after touching the layer.
            restore_source()
            errors.append("{}: {}".format(type(exc).__name__, exc))

    if not successful:
        restore_source()
        raise RuntimeError(
            "Adaptive UV group layout failed: " + " | ".join(errors)
        )

    # A requested small-island boost is a presentation contract.  Do not let
    # a boost-disabled candidate win merely because it preserves a little
    # more global texel density.  It remains a true fallback only when every
    # boosted variant fails (or would require an unreasonable density loss).
    requested_boost = primary.small_island_scale_boost > 1.0 + 1.0e-9
    boosted_successful = [
        record for record in successful
        if record[1].small_island_scale_boost > 1.0 + 1.0e-9
    ]
    selection_pool = list(successful)
    if requested_boost and boosted_successful:
        unboosted_scales = [
            max(float(record[1].uniform_scale), _EPSILON)
            for record in successful
            if record[1].small_island_scale_boost <= 1.0 + 1.0e-9
        ]
        reference_scale = max(
            unboosted_scales,
            default=max(
                max(float(record[1].uniform_scale), _EPSILON)
                for record in boosted_successful
            ),
        )
        # The configurable density floor prevents a square/tall candidate from
        # winning solely on coverage while making the trade-off explicit.
        density_floor = reference_scale * float(primary.candidate_density_floor)
        density_eligible = [
            record for record in boosted_successful
            if float(record[1].uniform_scale) + 1.0e-9 >= density_floor
        ]
        if density_eligible:
            selection_pool = density_eligible
        else:
            # A requested boost is subordinate to the density contract.  If
            # every boosted layout falls below the floor, retain any valid
            # candidate that meets it (typically an unboosted layout) before
            # considering the absolute best valid fallback.
            all_density_eligible = [
                record for record in successful
                if float(record[1].uniform_scale) + 1.0e-9 >= density_floor
            ]
            selection_pool = all_density_eligible or list(successful)
    elif requested_boost:
        # No boosted candidate survived the strict geometric audit; use the
        # unboosted candidates as the documented last-resort fallback.
        selection_pool = list(successful)

    # Apply the same density guard when no boost was requested.  The fallback
    # to all geometrically valid records keeps the API usable for unusually
    # small meshes where the shelf packer cannot meet a strict floor.
    if not requested_boost:
        reference_scale = max(
            (max(float(record[1].uniform_scale), _EPSILON) for record in successful),
            default=_EPSILON,
        )
        density_floor = reference_scale * float(primary.candidate_density_floor)
        density_eligible = [
            record for record in successful
            if float(record[1].uniform_scale) + 1.0e-9 >= density_floor
        ]
        selection_pool = density_eligible or list(successful)

    # Use the highest-density unboosted layout as the normalization reference;
    # this makes lower-tail gains attributable to the small-island treatment,
    # while still rewarding a genuinely fuller polygon atlas.
    reference_candidates = [
        record for record in successful
        if record[1].small_island_scale_boost <= 1.0 + 1.0e-9
    ]
    reference_record = max(
        reference_candidates or successful,
        key=lambda record: (float(record[1].uniform_scale), -record[0]),
    )
    reference_metrics = reference_record[1].quality_metrics
    reference_scale = max(float(reference_record[1].uniform_scale), _EPSILON)

    if primary.area_aware_scoring:
        for index, result, _coordinates in successful:
            result.candidate_score = score_layout_candidate(
                result.quality_metrics,
                reference_metrics,
                primary,
                reference_scale=reference_scale,
                candidate_scale=result.uniform_scale,
            )
        def candidate_key(record):
            index, result, _coordinates = record
            metrics = result.quality_metrics
            # Orientation is a final tie-breaker.  Its contribution is kept
            # below the score precision so it cannot override area/coverage.
            residual = _metric_value(
                metrics, "orientation_residual_p95_degrees"
            )
            return (
                float(result.candidate_score),
                _metric_value(metrics, "tile_polygon_coverage", "polygon_coverage"),
                _metric_value(metrics, "aabb_fill"),
                -residual,
                float(result.uniform_scale),
                -index,
            )
    else:
        # Historical ordering remains available for callers that need exact
        # pre-area-aware behavior.
        def candidate_key(record):
            index, result, _coordinates = record
            scale = max(float(result.uniform_scale), _EPSILON)
            occupancy = _clamp(float(result.tile_occupancy), 0.0, 1.0)
            minimum = max(
                min(float(result.packed_width), float(result.packed_height)),
                _EPSILON,
            )
            aspect = max(
                float(result.packed_width), float(result.packed_height)
            ) / minimum
            return (
                -occupancy,
                aspect,
                -scale,
                index,
            )

    selector = max if primary.area_aware_scoring else min
    best_index, best_result, best_coordinates = selector(
        selection_pool, key=candidate_key
    )
    restore_source()
    for item, coordinate in zip(uv_layer.data, best_coordinates):
        item.uv = coordinate.copy()
    mesh.update()
    # Stored candidate reports describe the trial write, not necessarily the
    # float32 coordinates replayed above.  Reconstruct and re-audit the exact
    # committed state so direction lock, overlap, and winding cannot pass on a
    # stale analysis object.
    selected_settings = attempts[best_index]
    try:
        _validate_source_island_partition(
            mesh,
            uv_layer,
            best_result.analysis.face_to_island,
            selected_settings.uv_epsilon,
        )
        replay_analysis = analyze_active_uv(obj, selected_settings)
        replay_analysis = _rebind_geometry_axis_contract(
            obj,
            mesh,
            uv_layer,
            replay_analysis,
            best_result.analysis,
            selected_settings,
        )
        replay_audit = audit_active_uv(
            obj,
            replay_analysis.face_to_island,
            epsilon=selected_settings.uv_epsilon,
        )
        replay_quality = evaluate_layout_quality(
            obj,
            analysis=replay_analysis,
            face_to_island=replay_analysis.face_to_island,
            audit=replay_audit,
            epsilon=selected_settings.uv_epsilon,
            options=selected_settings,
        )
        replay_direction_ok = bool(
            not selected_settings.align_geometry_direction
            or replay_quality.get("directed_geometry_valid", False)
        )
        if not bool(replay_quality.get("valid", False)) or not replay_direction_ok:
            raise RuntimeError(
                "Adaptive UV group layout replay failed final quality audit"
            )
    except Exception:
        restore_source()
        raise
    best_result.analysis = replay_analysis
    best_result.after_audit = replay_audit
    best_result.quality_metrics = replay_quality
    best_result.candidate_valid = True
    _persist_geometry_axis_contract(mesh, uv_layer, replay_analysis, selected_settings)
    best_result.fallback_used = best_index > 0
    best_result.fallback_errors = tuple(errors)
    return best_result


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
    "evaluate_layout_quality",
    "measure_layout_quality",
    "score_layout_candidate",
    "build_layout_groups",
    "calculate_island_records",
    "compute_active_uv_islands",
    "detect_repeat_groups",
    "layout_active_uv",
    "layout_active_uv_adaptive",
    "post_layout_active_uv",
    "rebind_geometry_axis_contract",
]
