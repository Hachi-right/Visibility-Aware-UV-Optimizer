# Visibility Aware UV Optimizer 0.5.5

Blender 3.3 through 5.2 add-on for visibility analysis and conservative UV
generation. Version 0.5.5 adds topology-safe small-island cleanup and
structure-aware post-layout for hard-surface weapons and props while retaining
the 0.5.3 Legacy Smart mode.

## Install

Install the release ZIP through `Edit > Preferences > Add-ons > Install`, then
enable `Visibility Aware UV Optimizer`. The panel is in
`View3D > Sidebar > UV Optimizer`.

The optimizer operates on the active mesh object. Duplicate production assets
or keep a source-control copy before replacing an approved UV layout.

## Recommended hard-surface workflow

1. Apply or verify object scale and clean zero-area faces, duplicate vertices,
   non-manifold edges, and invalid n-gons.
2. Mark artist seams in Edit Mode with `Edge > Mark Seam` where a continuous
   texture must stop.
3. Set `Initial UV Mode` to `Auto`, `UV Usage` to `Auto`, and use the
   `Weapon / Prop` profile.
4. Keep `Preserve Existing Seams`, `Respect Material Borders`, and
   `Align Long Islands` enabled.
5. Keep `Cut Sharp Edges` disabled for imported production assets unless Sharp
   edges were deliberately authored as UV cuts.
6. Keep `Stitch Small Islands / 缝合小岛` and
   `Group Related Islands / 关联岛分组` enabled for the 0.5.5 hard-surface pass.
   The default `Small Island Boost / 小岛放大` value of `1.25` gives detached
   micro-mechanical parts a readable minimum presence without stretching them;
   the layout automatically retries with the boost disabled if the strict
   no-overlap gate cannot be satisfied.
   `Square Pack Bias / 方形装箱偏好` defaults to `0.35`, balancing maximum
   usable scale with a compact, square-like atlas. `Cardinal Edge Confidence /
   直角边方向置信度` defaults to `0.15`; clear boundary edges then provide a
   stable horizontal/vertical cue for nearly square hard-surface charts.
   `Square Pack Edge Relax / 方形装箱最长边容差` defaults to `0.02` (2%):
   the square-biased shelf search may use a candidate whose longest packed edge
   is at most 2% larger than the compact baseline, which limits any texel
   density trade-off while closing avoidable blank strips.
   `Directed Cardinal Tolerance / 有向直角容差` defaults to `3` degrees, so
   hard-surface repeats are kept visibly upright while genuinely collinear
   directional landmarks still use the 360-degree contract.
7. Run `Optimize Active Object UV`. A Unique result is committed only when the
   final quality gate passes.

### Refine an existing UV layout

Choose `Initial UV Mode: Refine Layout` when the active UV map already has
useful large charts and the remaining problem is fragmented small faces or
scattered repeated parts. Use it with a Unique / Bake contract. Trim Sheet,
Info Atlas, and LED/VFX layouts should normally remain on `Auto` so their
deliberate stacking and out-of-tile coordinates are preserved.

Refine Layout derives chart boundaries from the active UV map and skips the
initial global `Unwrap`, `Smart UV Project`, and general chart-growth merge
pass. Valid large charts are therefore not globally re-cut. This is not the
same as `Preserve Layout`: accepted small-island stitches may locally update an
anchor chart, and island scale, cardinal direction, and position can change
during the final global packing and structure-aware layout.

The Refine Layout pipeline is:

1. read the active UV charts and derive matching seam boundaries;
2. classify hard-surface constraints without rebuilding the initial charts;
3. locally repair only charts that already fail the Unique winding or
   degeneracy checks, if a safe repair is possible;
4. stitch a small island only across an eligible shared mesh edge;
5. normalize island scale, align directions, and globally pack all islands;
6. assign unstitched fragments to an owner by shared topology first,
   model-space distance second, and material compatibility third; pack each
   owner and its attachments as one atomic cell; then place repeated, bilateral,
   and rotational owner cohorts nearby with a common direction; and
7. run the strict Unique gate again after the structure layout. Any unresolved
   overlap or cohort-proximity failure restores the complete pre-operation UV
   and seam state.

Recommended 0.5.5 starting values are:

| Setting | Value |
| --- | ---: |
| Stitch Small Islands | On |
| Group Related Islands | On |
| Auto Hard Edge Angle | 70 degrees |
| Panel Flatness | 5 degrees |
| Initial Angle | 70 degrees |
| Merge Search Angle | 130 degrees |
| P95 Stretch | 1.50 |
| Max Stretch | 3.0 |
| Merge Tests | 500 |
| Small Island Faces | 12 |
| Small Mesh Area Ratio | 0.001 |
| Small UV Area Ratio | 0.001 |
| Small Area Logic | Mesh AND UV |
| Minimum Shared Boundary | 0.25 |
| Model Proximity Radius | 0.025 of bounding-box diagonal |
| Small Island Boost | 1.25x uniform scale, bounded and rollback-safe |
| Square Pack Bias | 0.35 |
| Square Pack Longest-Edge Relaxation | 0.02 (2%, bounded) |
| Cardinal Edge Confidence | 0.15 |
| Directed Cardinal Tolerance | 3 degrees |
| Small-Stitch P95 Stretch | 1.35 |
| Small-Stitch Max Stretch | 2.0 |
| Developable Angle | 55 degrees |
| Band Merge Bonus | 0.40 |
| Island Margin | 0.002 |

## Structure-aware layout

Isomorphic or exact-topology repeated parts, bilateral counterparts, rotational
repeats, and other duplicated mechanical structures are resolved into owner
cohorts. Cohort owners receive a common UV orientation and their
owner cells are kept near one another during packing. An owner cell contains
the owner chart and all fragments assigned to it and is moved as one atomic
unit. This is a rigid layout operation:

- UV coordinates may be translated or rotated to a common cardinal direction;
- islands remain disjoint and retain the configured margin;
- no UV island is reflected or mirrored;
- no repeated islands are stacked or intentionally overlapped; and
- disconnected mesh parts are never welded into one UV island.

For a hard-surface repeat, a directed landmark is used for the strict
modulo-360 sign only when its line agrees with the island's PCA axis (or the
longest reliable boundary edge) within `Directed Cardinal Tolerance` (3
degrees by default). If any member of a connected repeat/owner-cohort
component exceeds that residual, the entire component is explicitly downgraded
to `center_symmetric_modulo_180` and aligned from geometric axes, keeping
mirrored panels upright instead of leaving diagonal outliers. The analysis
manifest records `orientation_policy`, `orientation_downgrades`, each affected
member's `direction_mode`, and the measured residuals so an independent audit
uses the same policy.

Mirror and rotational relationships are evidence for grouping and ordering,
not permission to mirror the UV coordinates. Winding and the Unique
no-overlap contract remain unchanged. When `Small Island Boost` is above
`1.0`, only classified micro-islands receive the explicitly requested uniform
area weighting; all other charts retain their relative texel density.

Orientation groups and concrete layout groups are separate. Repeat detection
resolves a shared direction and ordering, while owner cohorts impose a concrete
nearby-cell constraint. Repeated tiny fragments keep the orientation evidence,
but owner assignment still follows shared mesh topology first, model-space
distance second, and material compatibility third. Distant identical slivers
are therefore not pulled into a global fragment block.

The cohort constraint is mandatory, not a best-effort hint. The deterministic
packer uses bounded backtracking and progressively wider candidate searches,
but every accepted result must keep cohort owner cells near one another,
preserve the configured margin, and remain free of positive-area overlap. If
those conditions cannot all be met, structure layout fails and the complete UV
operation is rolled back.

## Topology-safe small-island cleanup

An island is a small-layout candidate only when it has at most 12 faces, its
mesh-area ratio is at most 0.001, and its UV-area ratio is at most 0.001. Both
area tests must pass (`AND`). This prevents a chart with few faces and little
3D area but substantial UV coverage from being misclassified as a disposable
fragment.

Actual stitching is more restrictive. Two islands must share real mesh
topology, and the eligible shared edge length must cover at least 0.25 of the
small island's perimeter. The stitch is rejected if it crosses a preserved
artist Seam, a respected material boundary, a protected Sharp edge, a locked
structural cut, or non-manifold topology. The merged chart must then pass
connected-disk topology, finite-coordinate, positive-winding, non-degeneracy,
P95/Max stretch
`1.35 / 2.0`, and positive-area overlap checks.

Model-space proximity alone never authorizes a stitch. Small islands without an
eligible shared topology edge remain separate UV islands. For layout, their
owner is selected by shared topology first, model-space distance second, and
material compatibility third. Each fragment is packed atomically with that
owner while retaining the 0.002 island margin; model-space distance beyond
0.025 of the object's bounding-box diagonal is not treated as a nearby match.

## Local residual-chart repair

If the strict Unique checks still find folded or degenerate charts, repair is
limited to those residual invalid charts. The optimizer retains a spanning tree
of each chart's face adjacency, adds local seam cuts around the remaining
cycles, and reruns a local unwrap. A per-face projection is available as a last
local fallback. Unrelated valid charts are not globally re-cut, and a repair is
kept only after local winding and degeneracy checks pass.

Near-collinear ear triangles produced when an n-gon is triangulated can sit at
the numerical winding threshold. For source triangles that are genuinely
near-collinear, the optimizer may apply a bounded UV adjustment to establish a
stable positive winding after packing transforms. The adjustment is reverted
unless the complete chart revalidates; true zero-area source geometry is still
rejected. These repair paths reduce false failures but do not guarantee that
every invalid chart can be repaired.

## UV contracts

`UV Usage: Auto` infers the contract from underscore-delimited object-name
tokens:

| Contract | Recognized name token | Auto behavior |
| --- | --- | --- |
| Unique / Bake | default | Generate, pack to 0-1, and run the strict gate |
| Trim Sheet | `TrimSheet` or `Trim_Sheet` | Preserve the existing layout |
| Info Atlas | `InfoAtlas` or `Info_Atlas` | Preserve the existing layout |
| LED / VFX | `LED`, `LED1`, `VFX`, or `VFX1` | Preserve the existing layout |

Auto preservation applies only when `Initial UV Mode` is also `Auto`.
Selecting `Hard Surface`, `Marked Seams`, or `Legacy Smart` explicitly requests
a rebuild and can reproject or pack a non-Unique layout. `Refine Layout` skips
the initial projection but still repacks and can therefore also damage a
non-Unique layout.

## Hard-surface boundary rules

The seed pass classifies panels, bevel bands, cylinder sides, radial caps, and
general geometry. Boundaries are prioritized as follows:

- Mesh boundaries and non-manifold edges are structural cuts.
- Preserved artist seams are locked and never merged.
- Material borders are cuts when `Respect Material Borders` is enabled.
- Cylinder cap boundaries and one stable longitudinal cylinder opening are
  cuts. A regular capped cylinder is expected to produce one side island and
  two cap islands.
- Explicit Sharp edges are cuts only when `Cut Sharp Edges` is enabled. It is
  disabled by default because production shading data is often much denser
  than the intended UV seam set.
- Geometric hard angles, bevels, concave edges, visibility, and developable
  bands influence merge preference but still pass topology, stretch, winding,
  and overlap checks.

`Panel Flatness` controls whether a grown region is treated as a planar panel.
Long accepted islands are aligned to a horizontal or vertical axis when
`Align Long Islands` is enabled.

## Unique quality gate

Unique / Bake output must satisfy every final condition:

- all UV coordinates are finite and inside the 0-1 tile;
- source triangles and UV triangles are non-degenerate;
- every triangle has consistent positive winding;
- no positive-area overlap exists within an island or between islands;
- every real UV discontinuity has a matching seam flag.

Blender's pack operator is run with all UVs explicitly selected and the active
UDIM target. If the concave pack still overlaps, an AABB safety pack is tried.
A rejected stitch candidate restores that candidate and processing may continue.
Any unresolved final-gate, local-repair, or structure-layout failure instead
cancels the entire operation and restores all UV layers and coordinates,
pin/selection data, active/render layer identity, seam flags, mesh selection,
hidden state, and the prior `vuv_detail_score` attribute.

The final Unique gate remains strict even if `Reject UV Overlap` is disabled;
that setting controls candidate merge rejection, not the final contract.

Meshes containing zero-area faces, collapsed triangles, duplicate surfaces,
or severe non-manifold topology may be rejected. Clean the source geometry
instead of loosening the final gate.

## Multiple UV layers and non-Unique layouts

Rebuild and Refine Layout modes rewrite only the active UV map. Other UV layers
and the active/render layer identities are preserved. Auto Preserve Layout
keeps the active UV coordinates and seam flags byte-for-byte stable, including
deliberate stacking and coordinates outside 0-1; it may refresh the non-UV
`vuv_detail_score` analysis attribute.

`Collapse Hidden Overrides` is disabled by default. Keep it disabled for a
Unique contract because collapsed textured faces are intentionally degenerate.
Use it only for an explicitly rebuilt non-Unique layout where shared hidden
coverage is acceptable.

## Visibility and mapping viewer

The add-on also provides six-camera, selected-camera, hybrid, sphere-sampling,
and exterior-flood visibility analysis. Face priorities are stored as mesh
attributes and can be overridden as Important, Hidden, or Auto.

`Interactive Mapping > Export Viewer` writes a standalone HTML file containing
the selected mesh and UV data. Click a UV face or island to locate its 3D
surface; repeated clicks cycle overlapping faces. The viewer has no external
web dependency.

## Compatibility and validation scope

Version 0.5.5 supports Blender 3.3 through 5.2. Release regression targets are
Blender 3.3.5 and Blender 5.2.0 LTS. The structure-layout stage uses rigid UV
transforms and deterministic disjoint-rectangle packing available to both
versions; it does not rely on 5.x-only mirroring or overlap behavior.

Synthetic coverage includes marked/material/Sharp cuts, a capped cylinder, a
long rail, hidden geometry, multiple UV layers, non-Unique preservation,
topology-safe small-island acceptance and rejection, repeated-structure
grouping, operator RNA differences, selection state, rollback, and injected
overlap/winding/degeneracy failures.

In the historical 0.5.4 Blender 5.2 weapon validation,
`SM_CDO_ChopSword_1_LOD1` passes the Unique gate with 220 islands and 4,246
positive triangles. Four other sampled
Unique assets are rejected because their source geometry or projected charts
remain degenerate; their original state is restored. A rejection is an expected
safe result, not a successful UV rebuild.
