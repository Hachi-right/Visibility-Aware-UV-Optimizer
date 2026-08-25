# Visibility Aware UV Optimizer

Blender 3.3 add-on for camera-based face visibility analysis and conservative
UV island merging.

Version 0.5.2 removes the obsolete `Hidden UV Scale` and `Hidden Corner Size`
controls. Red/hidden faces now have one fixed rule: every UV loop collapses to
the `(0, 0)` origin. The low-visibility packing floor remains an internal
implementation detail for yellow UV charts.

Version 0.5.1 collapses every red/hidden face loop to the lower-left UV origin
`(0, 0)` after packing. This matches snapping the selected red-face UVs to a
cursor placed at the origin, while remaining independent of the UV Editor
context. Visible and yellow UVs are no longer resized to reserve a hidden-face
corner.

Version 0.5.0 adds texture-direction probe evidence import, read-only UV
boundary candidate reports, probe-guided chart scoring, and conservative
rejection of boundaries marked as flipped or non-connectable.

Version 0.4.2 fixes the all-red heatmap regression in Blender 3.3 Edit Mode.
Blender exposes face-attribute metadata there but reports an empty
`MeshAttribute.data` collection; reading it previously replaced every face
score with the default zero. Heatmap geometry and visibility selection now read
the authoritative BMesh face layers in Edit Mode, retain the object-mode path
elsewhere, skip hidden faces in the custom overlay, and clear stale vertex and
edge selection before selecting Low or Hidden faces.

Version 0.4.1 hardens operator state restoration and edge cases found during a
full code audit. Failed analysis and UV operations now restore Edit Mode and
the prior object selection. UV optimization without visibility data uses normal
visible texel density instead of collapsing the whole object as hidden. Manual
Important/Hidden/Auto overrides update immediately and Auto restores the saved
automatic analysis score from `vuv_visibility_auto`. The audit also adds safe
handling for reserved-name collisions, linked mesh-data analysis, concave
n-gon distortion checks, fully flipped UVs, missing overlay data, non-finite
mapping exports, and responsive viewer resizing.

Version 0.4.0 adds a standalone interactive mapping export for selected mesh
objects. The single HTML file shows UVs without vertex dots beside an orbitable
3D model. Exact polygon hit testing replaces nearest-vertex selection; clicking
a UV face or island highlights the corresponding model surface. Overlapping UV
faces can be cycled with repeated clicks at the same position. The viewer also
supports UV pan/zoom, 3D orbit/pan/zoom, selection focus, x-ray highlighting,
and neutral, material, or visibility colors. It has no external dependencies.

Version 0.3.1 keeps the viewport heatmap compatible with Blender 3.3 through
5.x by falling back from the legacy `3D_SMOOTH_COLOR` shader name to the newer
`SMOOTH_COLOR` name.

Version 0.3.2 keeps red/hidden faces in the mesh and UV data, but scales them
to the configured linear `Hidden UV Scale` (default 0.01) and moves them after
packing into a reserved top-right UV square. Visible and yellow faces are
normalized into the complementary lower-left region. This intentionally
overlaps hidden faces with one another; it is suitable for faces that do not
need unique texture coverage.

Version 0.3.3 makes Face Display a strictly non-destructive view filter. The
per-mesh hide-state layer is now the authoritative filter state, visibility
values are preserved across every toggle, and Show All restores the original
hide state in one update. Switching Green/Yellow/Red does not require another
visibility analysis.

Version 0.4.3 exports UV mapping data directly from BMesh while an object is in
Edit Mode. This avoids Blender 3.3's empty `MeshUVLoopLayer.data` edit-mode
snapshot, supports multi-object Edit Mode, and preserves the user's mode after
the interactive viewer is exported.

## Current scope

- Six generated orthographic cameras, selected cameras, or hybrid analysis.
- Reflection-symmetric sphere sampling with adjustable radius, coverage grid,
  and multi-layer hits.
- Exterior-air voxel flood fill that distinguishes outside surfaces, open
  cavities, and sealed internal surfaces.
- Combined occlusion across all selected mesh objects.
- Persistent face visibility and manual priority override attributes.
- Reversible automatic visibility stored separately as `vuv_visibility_auto`.
- Non-destructive viewport heatmap.
- Independent green, yellow, and red face display filters with hide-state restore.
- Smart UV seed generation followed by adjacent-island merge tests.
- Merge rejection for non-disk topology, UV stretch, flips, and overlap.
- Reduced texel density for low-visibility islands.
- Red/hidden faces collapsed to one overlapping UV point at the `(0, 0)` origin.
- Developable-band preference for planar, cylindrical, conical, and bevel regions.
- Visibility-aware seam scoring that favors hidden, hard, and concave boundaries.
- Detail-aware texel density using local normal variation, stored as
  `vuv_detail_score` on mesh faces.
- Standalone HTML export for interactive UV-to-model face and island mapping.
- Direction-probe evidence import for texture-flow-aware chart merging.
- UV optimization currently runs on the active mesh object.

## Install

Install the ZIP through Edit > Preferences > Add-ons > Install, then enable
"Visibility Aware UV Optimizer". The panel is in View3D > Sidebar > UV Optimizer.

Select one or more mesh objects with active UV maps, then use `Interactive
Mapping > Export Viewer`. The export opens in the default browser unless `Open
After Export` is disabled in Blender's file browser. In the viewer, left-click
selects an exact UV face, repeated clicks cycle overlapping faces, middle- or
right-drag pans, and the mouse wheel zooms. The 3D view uses left-drag to orbit,
middle- or right-drag to pan, and the mouse wheel to zoom.

## Notes

The optimizer rewrites the active UV map and seam flags. Use it on a duplicate
asset or rely on Blender Undo while tuning thresholds. Concave n-gons are
evaluated with a fan triangulation in version 0.2; triangulate unusually complex
n-gons before optimization.

Sphere mode places an Empty sphere around the selected mesh bounding box. The
direction set is mirrored across the three world axes, so bilateral geometry is
sampled symmetrically. `Coverage Grid` controls how many parallel rays are cast
for each direction: 1x1 is the original center ray, 3x3 is the recommended
setting, and 5x5 is useful for small or tangential exterior faces. `Hit Layers`
records the first N front-facing surfaces along each ray; deeper layers are
multiplied by `Layer Falloff`, so occluded intermediate geometry retains lower
priority rather than being classified as completely invisible. Visibility scores
are accumulated per face, then normalized; repeated hits do not make a face
exceed the visible range. This is useful for concentric shells, where the outer
shell should remain green and the inner shell should normally become yellow
rather than green.

For UV optimization, `Developable Bands` lowers the merge cost of coherent
low-angle face bands. `Seam Visibility Weight`, `Hard Edge Seam Bonus`, and
`Concave Seam Bonus` influence which existing boundaries are removed first.
`Detail Density Strength` gives high normal-variation areas more UV space, with
`Detail Density Cap` limiting the scale increase. These are conservative scoring
terms; final merges still require the stretch, winding, overlap, and topology
checks.

The final UV pass treats a chart containing red and visible faces as mixed:
the visible part is not reduced by the red face, and the red face loops are
split into UV discontinuities before being collapsed to the UV origin. This
prevents a hidden face from forcing an entire visible chart to become tiny.

Use `Sphere Radius` and `Update Sphere` for repeatable sizing. The Empty can also
be moved or uniformly scaled in the viewport; analysis reads its world-space
position and scale.

## Direction probe evidence

The optimizer can consume evidence generated by a separate surface-probe or
texture-bake pipeline. The probe is expected to be independent of the current
UV layout, then baked back into that layout so each currently split 3D edge can
be evaluated for texture continuity.

Import evidence from the `Texture Direction Probe` panel. The JSON schema is:

```json
{
  "schema": "vuv-probe-evidence-v1",
  "object": "UV_Test_Model",
  "source": "openSHIBIE",
  "edges": [
    {
      "vertices": [12, 18],
      "merge_score": 0.92,
      "confidence": 0.95,
      "direction_delta_deg": 4.5,
      "scale_ratio": 1.02,
      "phase_error": 0.03,
      "flipped": false,
      "can_merge": true
    }
  ]
}
```

The edge key is the sorted pair of mesh vertex indices, so it remains usable
while chart membership changes during the merge pass. `merge_score` may be
provided directly; otherwise the optimizer derives a conservative score from
direction, scale, and phase metrics. Evidence marked `flipped: true` or
`can_merge: false` is kept as a seam when `Reject Flipped Probe` is enabled.
`Export Candidate Report` is read-only and writes the current UV boundary,
chart pair, probe score, and rejection reason for external review.

Evidence is optional. With `Use Direction Probe` disabled or no evidence
loaded, the existing visibility, topology, stretch, overlap, and developable
band scoring remains unchanged.

Exterior Flood mode builds a conservative voxel surface from the selected
meshes, floods empty cells from the grid boundary, and marks faces that touch
the outside-air region. `Exterior Resolution` is the largest voxel axis count;
96 is a practical starting point for a real asset. `Water Depth Falloff`
reduces the score for surfaces reached through a deep open cavity, while
`Exterior Face Samples` controls how much of each face is tested. The mode
writes `vuv_exterior_ratio` and `vuv_exterior_depth` face attributes in
addition to the regular `vuv_visibility` score. It is a topology/exposure
classification, not a replacement for direct camera visibility: an open
barrel interior can be outside-air connected while still receiving lower
priority because of its flood distance.
