# SPDX-License-Identifier: GPL-2.0-or-later

import math

import bpy
from bpy.props import BoolProperty
from bpy.props import EnumProperty
from bpy.props import FloatProperty
from bpy.props import IntProperty
from bpy.props import StringProperty


def _update_sphere_visible(settings, context):
    from . import camera_utils
    sampler = camera_utils.find_sphere_sampler()
    if sampler is not None:
        sampler.hide_viewport = not settings.sphere_visible
        sampler.hide_render = True


def _update_face_filter(settings, context):
    if bpy.app.background or context is None or settings.filter_update_suspended:
        return
    try:
        from . import operators
        operators.apply_visibility_filter_state(context)
    except (AttributeError, RuntimeError, ValueError):
        pass


def _update_overlay(settings, context):
    if bpy.app.background or context is None or not settings.show_overlay:
        return
    try:
        from . import camera_utils
        from . import overlay
        targets = camera_utils.selected_meshes(context)
        overlay.rebuild(targets, settings)
        overlay.enable()
    except (AttributeError, RuntimeError, ValueError):
        pass


def _update_hard_surface_profile(settings, context):
    """Apply a profile threshold while leaving later manual edits possible."""
    angles = {
        'WEAPON': math.radians(70.0),
        'GENERAL': math.radians(80.0),
        'DEFAULT': math.radians(80.0),
    }
    angle = angles.get(settings.hard_surface_profile)
    if angle is not None:
        settings.hard_surface_sharp_angle = angle


class VUVSettings(bpy.types.PropertyGroup):
    ray_source: EnumProperty(
        name="Ray Source",
        items=(
            ('CAMERA', "Cameras", "Use camera-based parallel or perspective rays"),
            ('SPHERE', "Sphere", "Use rays from a sphere toward its center"),
            ('EXTERIOR', "Exterior Flood", "Classify faces connected to outside air"),
            ('HYBRID', "Camera + Sphere", "Combine camera rays and sphere rays"),
        ),
        default='CAMERA',
    )
    camera_source: EnumProperty(
        name="Camera Source",
        items=(
            ('AUTO', "Auto 6", "Use the six generated orthographic cameras"),
            ('SELECTED', "Selected", "Use selected camera objects"),
            ('HYBRID', "Hybrid", "Use generated and selected cameras"),
        ),
        default='AUTO',
    )
    axis_basis: EnumProperty(
        name="Axis Basis",
        items=(
            ('ACTIVE', "Active Object", "Use the active object's local axes"),
            ('WORLD', "World", "Use world axes"),
        ),
        default='ACTIVE',
    )
    sampling_resolution: EnumProperty(
        name="Sampling",
        items=(
            ('32', "32 Preview", "32 by 32 rays per camera"),
            ('64', "64 Fast", "64 by 64 rays per camera"),
            ('128', "128 Standard", "128 by 128 rays per camera"),
            ('256', "256 Final", "256 by 256 rays per camera"),
        ),
        default='128',
    )
    sphere_samples: EnumProperty(
        name="Sphere Samples",
        items=(
            ('64', "64 Fast", "64 evenly distributed sphere rays"),
            ('128', "128 Standard", "128 evenly distributed sphere rays"),
            ('256', "256 Detailed", "256 evenly distributed sphere rays"),
            ('512', "512 Final", "512 evenly distributed sphere rays"),
            ('1024', "1024 High", "1024 evenly distributed sphere rays"),
        ),
        default='256',
    )
    sphere_coverage_grid: EnumProperty(
        name="Coverage Grid",
        description=(
            "Cast a symmetric orthographic bundle for each sphere direction; "
            "higher values cover small and tangential exterior faces"
        ),
        items=(
            ('1', "1 x 1 Center", "One ray through the sphere center"),
            ('3', "3 x 3 Coverage", "Nine rays per sphere direction"),
            ('5', "5 x 5 Detailed", "Twenty-five rays per sphere direction"),
        ),
        default='3',
    )
    sphere_radius_factor: FloatProperty(
        name="Sphere Radius",
        description="Multiplier of the mesh bounding-sphere radius",
        default=1.15,
        min=1.0,
        max=5.0,
        precision=3,
    )
    sphere_depth_layers: IntProperty(
        name="Hit Layers",
        description="Number of surfaces recorded along each inward ray",
        default=3,
        min=1,
        max=8,
    )
    sphere_layer_falloff: FloatProperty(
        name="Layer Falloff",
        description="Weight multiplier for each deeper hit layer",
        default=0.35,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    sphere_weight: FloatProperty(
        name="Sphere Weight",
        description="Relative contribution of sphere rays in hybrid mode",
        default=1.0,
        min=0.0,
        max=10.0,
    )
    exterior_resolution: EnumProperty(
        name="Exterior Resolution",
        description="Largest axis voxel count used for outside-air flood fill",
        items=(
            ('48', "48 Fast", "Fast exterior connectivity preview"),
            ('64', "64 Standard", "Standard exterior connectivity analysis"),
            ('96', "96 Detailed", "Detailed exterior connectivity analysis"),
            ('128', "128 Final", "High-detail exterior connectivity analysis"),
            ('192', "192 High", "Very high detail; may be slow on dense meshes"),
        ),
        default='96',
    )
    exterior_depth_falloff: FloatProperty(
        name="Water Depth Falloff",
        description="Reduce priority as a connected cavity gets deeper from outside",
        default=0.12,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    exterior_surface_samples: IntProperty(
        name="Exterior Face Samples",
        description="Number of samples per face used to estimate outside contact",
        default=4,
        min=1,
        max=8,
    )
    sphere_visible: BoolProperty(
        name="Show Sphere",
        default=True,
        update=_update_sphere_visible,
    )
    camera_margin: FloatProperty(
        name="Frame Margin",
        default=0.08,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    ignore_backfaces: BoolProperty(
        name="Ignore Backfaces",
        default=True,
    )
    visibility_high: FloatProperty(
        name="High Threshold",
        default=0.05,
        min=0.0001,
        max=1.0,
        subtype='FACTOR',
        update=_update_face_filter,
    )
    show_overlay: BoolProperty(
        name="Heatmap",
        default=True,
    )
    overlay_opacity: FloatProperty(
        name="Opacity",
        default=0.55,
        min=0.05,
        max=1.0,
        subtype='FACTOR',
        update=_update_overlay,
    )
    show_green_faces: BoolProperty(
        name="Green",
        description="Show high-priority faces",
        default=True,
        update=_update_face_filter,
    )
    show_yellow_faces: BoolProperty(
        name="Yellow",
        description="Show low-priority faces",
        default=True,
        update=_update_face_filter,
    )
    show_red_faces: BoolProperty(
        name="Red",
        description="Show faces with zero visibility",
        default=True,
        update=_update_face_filter,
    )
    filter_active: BoolProperty(default=False, options={'HIDDEN'})
    filter_update_suspended: BoolProperty(default=False, options={'HIDDEN'})

    initial_uv_mode: EnumProperty(
        name="Initial UV Mode",
        description=(
            "Choose how the first UV charts are seeded. Auto uses marked seams "
            "when present and otherwise the hard-surface classifier"
        ),
        items=(
            (
                'AUTO',
                "Auto",
                "Use marked-seam mode when existing seams are present, otherwise hard surface",
            ),
            (
                'HARD_SURFACE',
                "Hard Surface",
                "Seed panels, strips, bevels, cylinders, and caps with explicit geometric cuts",
            ),
            (
                'MARKED_SEAMS',
                "Marked Seams",
                "Treat artist-marked seams as locked boundaries and add only necessary cuts",
            ),
            (
                'REFINE_LAYOUT',
                "Refine Layout",
                "Keep existing charts, then safely stitch small fragments and group related islands",
            ),
            (
                'PRESERVE_LAYOUT',
                "Preserve Layout",
                "Keep the active UV map and seam flags unchanged",
            ),
            (
                'LEGACY_SMART',
                "Legacy Smart",
                "Use the 0.5.3 Smart UV Project seed and merge behavior",
            ),
        ),
        default='AUTO',
    )
    hard_surface_profile: EnumProperty(
        name="Hard Surface Profile",
        description="Geometry policy preset used when a hard-surface seed mode is active",
        items=(
            ('WEAPON', "Weapon / Prop", "Aggressive panel, bevel, strip, and cylinder separation"),
            ('GENERAL', "General Hard Surface", "Use the same rules with less aggressive angle cuts"),
            ('DEFAULT', "Default", "Use the standard 0.5.5 hard-surface thresholds"),
        ),
        default='WEAPON',
        update=_update_hard_surface_profile,
    )
    uv_usage: EnumProperty(
        name="UV Usage",
        description=(
            "Controls whether overlap is forbidden. Auto detects Unique, TrimSheet, "
            "LED, and InfoAtlas from the object name"
        ),
        items=(
            ('AUTO', "Auto", "Infer the UV contract from the object name"),
            ('UNIQUE', "Unique / Bake", "Require non-overlapping texture coverage"),
            ('TRIM_SHEET', "Trim Sheet", "Allow intentional repeated trim UVs"),
            ('LED', "LED / VFX", "Allow intentional strip and repeated UVs"),
            ('INFO_ATLAS', "Info Atlas", "Allow intentional label/atlas overlap"),
        ),
        default='AUTO',
    )
    hard_surface_sharp_angle: FloatProperty(
        name="Auto Hard Edge Angle",
        description="Geometric turn angle at which a smooth edge becomes a hard-surface cut",
        default=math.radians(70.0),
        min=math.radians(30.0),
        max=math.radians(175.0),
        subtype='ANGLE',
    )
    hard_surface_planar_angle: FloatProperty(
        name="Panel Flatness",
        description="Maximum normal spread used to classify a planar panel",
        default=math.radians(5.0),
        min=math.radians(0.5),
        max=math.radians(20.0),
        subtype='ANGLE',
    )
    hard_surface_respect_sharp: BoolProperty(
        name="Cut Sharp Edges",
        description="Treat Blender Sharp edges as hard-surface UV boundaries in the new modes",
        default=False,
    )
    hard_surface_align_cardinal: BoolProperty(
        name="Align Long Islands",
        description="Rotate long UV islands to the nearest horizontal or vertical axis",
        default=True,
    )
    hard_surface_hidden_collapse: BoolProperty(
        name="Collapse Hidden Overrides",
        description="Collapse only explicitly Hidden-overridden faces to the UV origin",
        default=False,
    )
    small_cleanup_enabled: BoolProperty(
        name="Stitch Small Islands / 缝合小岛",
        description=(
            "Safely stitch topology-adjacent small UV islands while preserving "
            "locked seams, material boundaries, and the Unique UV quality gate; "
            "安全缝合拓扑相邻的小 UV 岛，并保留锁定接缝、材质边界和唯一 UV 门禁"
        ),
        default=True,
    )
    uv_group_layout_enabled: BoolProperty(
        name="Group Related Islands / 关联岛分组",
        description=(
            "Keep repeated, symmetric, and model-space-near UV islands together "
            "without overlapping them; 将重复、对称及模型空间邻近的 UV 岛靠近排布且不重叠"
        ),
        default=True,
    )

    smart_angle: FloatProperty(
        name="Initial Angle",
        default=math.radians(70.0),
        min=math.radians(1.0),
        max=math.radians(89.0),
        subtype='ANGLE',
    )
    merge_angle: FloatProperty(
        name="Merge Search Angle",
        default=math.radians(130.0),
        min=math.radians(1.0),
        max=math.radians(179.0),
        subtype='ANGLE',
    )
    developable_enabled: BoolProperty(
        name="Developable Bands",
        description="Favor continuous planar, cylindrical, conical, and bevel bands",
        default=True,
    )
    developable_angle: FloatProperty(
        name="Developable Angle",
        description="Maximum neighbor angle for developable-band preference",
        default=math.radians(55.0),
        min=math.radians(1.0),
        max=math.radians(89.0),
        subtype='ANGLE',
    )
    developable_bonus: FloatProperty(
        name="Band Merge Bonus",
        description="How strongly aligned face bands resist fragmentation",
        default=0.40,
        min=0.0,
        max=2.0,
    )
    seam_visibility_weight: FloatProperty(
        name="Seam Visibility Weight",
        description="Penalty for leaving a seam on highly visible smooth faces",
        default=1.5,
        min=0.0,
        max=10.0,
    )
    seam_hard_edge_bonus: FloatProperty(
        name="Hard Edge Seam Bonus",
        description="Prefer hard edges as seam locations",
        default=0.35,
        min=0.0,
        max=2.0,
    )
    seam_concave_bonus: FloatProperty(
        name="Concave Seam Bonus",
        description="Prefer concave edges as seam locations",
        default=0.25,
        min=0.0,
        max=2.0,
    )
    detail_strength: FloatProperty(
        name="Detail Density Strength",
        description="Extra texel density for high local normal variation",
        default=0.65,
        min=0.0,
        max=3.0,
    )
    detail_scale_cap: FloatProperty(
        name="Detail Density Cap",
        description="Maximum relative scale from local detail weighting",
        default=1.75,
        min=1.0,
        max=4.0,
    )
    max_p95_stretch: FloatProperty(
        name="P95 Stretch",
        default=1.50,
        min=1.001,
        max=10.0,
    )
    max_stretch: FloatProperty(
        name="Max Stretch",
        default=3.0,
        min=1.001,
        max=20.0,
    )
    max_merge_tests: IntProperty(
        name="Merge Tests",
        default=500,
        min=0,
        max=10000,
    )
    small_island_faces: IntProperty(
        name="Face Limit / 小岛面数",
        description=(
            "Maximum face count for a small-island candidate; "
            "小岛候选允许的最大面数"
        ),
        default=12,
        min=1,
        max=1000,
    )
    small_island_area_ratio: FloatProperty(
        name="Mesh Area Ratio / 模型面积比",
        description=(
            "Small-island mesh-area threshold relative to the object; both this and "
            "the UV-area threshold must qualify the island; 相对模型总面积的小岛阈值，"
            "模型和 UV 面积比必须同时满足"
        ),
        default=0.001,
        min=0.0,
        max=0.1,
        precision=4,
        subtype='FACTOR',
    )
    small_uv_area_ratio: FloatProperty(
        name="UV Area Ratio / UV 面积比",
        description=(
            "Small-island UV-area threshold relative to all UV coverage; both this "
            "and the mesh-area threshold must qualify the island; 相对 UV 总覆盖面积的"
            "小岛阈值，UV 和模型面积比必须同时满足"
        ),
        default=0.001,
        min=0.0,
        max=0.1,
        precision=4,
        subtype='FACTOR',
    )
    small_boundary_ratio: FloatProperty(
        name="Shared Boundary / 共享边比例",
        description=(
            "Minimum share of the small island perimeter that must touch its stitch "
            "target; 小岛周长中必须与缝合目标相接的最小比例"
        ),
        default=0.25,
        min=0.0,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    small_cleanup_p95: FloatProperty(
        name="Cleanup P95 / 清理 P95",
        description=(
            "P95 stretch limit for a small-island stitch; 小岛缝合的 P95 拉伸上限"
        ),
        default=1.35,
        min=1.001,
        max=10.0,
        precision=3,
    )
    small_cleanup_max_stretch: FloatProperty(
        name="Cleanup Max / 清理最大拉伸",
        description=(
            "Maximum stretch allowed for a small-island stitch; 小岛缝合允许的最大拉伸"
        ),
        default=2.0,
        min=1.001,
        max=20.0,
        precision=3,
    )
    proximity_radius_ratio: FloatProperty(
        name="Model Radius / 模型邻近半径",
        description=(
            "Maximum model-space grouping distance as a fraction of the object "
            "bounding-box diagonal; 按模型包围盒对角线比例计算的最大邻近分组距离"
        ),
        default=0.025,
        min=0.0001,
        max=1.0,
        precision=4,
        subtype='FACTOR',
    )
    preserve_seams: BoolProperty(
        name="Preserve Existing Seams",
        default=True,
    )
    respect_materials: BoolProperty(
        name="Respect Material Borders",
        default=True,
    )
    respect_sharp: BoolProperty(
        name="Respect Sharp Edges",
        default=False,
    )
    reject_overlap: BoolProperty(
        name="Reject UV Overlap",
        default=True,
    )
    island_margin: FloatProperty(
        name="Island Margin",
        default=0.002,
        min=0.0,
        max=0.1,
        precision=4,
    )
    probe_enabled: BoolProperty(
        name="Use Direction Probe",
        description="Use imported surface-texture evidence to guide chart merges",
        default=False,
    )
    probe_merge_bonus: FloatProperty(
        name="Probe Merge Bonus",
        description="How strongly supported probe boundaries are preferred",
        default=0.75,
        min=0.0,
        max=3.0,
    )
    probe_min_confidence: FloatProperty(
        name="Probe Confidence",
        description="Ignore probe edge evidence below this confidence",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    probe_reject_flipped: BoolProperty(
        name="Reject Flipped Probe",
        description="Keep a UV boundary when the probe reports a mirrored direction",
        default=True,
    )

    last_analysis: StringProperty(default="", options={'HIDDEN'})
    last_optimize: StringProperty(default="", options={'HIDDEN'})
    last_probe: StringProperty(default="", options={'HIDDEN'})
