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
            ('DEFAULT', "Default", "Use the current hard-surface thresholds"),
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
    hard_surface_direction_lock: BoolProperty(
        name="Lock Texture Direction / 锁定纹理方向",
        description=(
            "Map a positive model axis to UV +V so arrows, letters, and checker "
            "orientation stay upright; 将模型正轴映射到 UV +V，避免棋盘格、"
            "箭头和文字出现 90 度侧转或 180 度倒置"
        ),
        default=True,
    )
    uv_repeat_local_frame: BoolProperty(
        name="Repeat Local Frame / 重复件局部帧",
        description=(
            "Orient exact mirrored, rotational, and repeated mechanical parts "
            "from an intrinsic landmark before falling back to the object axis; "
            "优先按重复机械件的固有特征统一棋盘格方向，无可靠局部帧时回退到对象轴"
        ),
        default=True,
    )
    uv_direction_space: EnumProperty(
        name="Direction Space / 方向空间",
        description="Choose whether the texture-up rule follows object-local or world axes",
        items=(
            (
                'OBJECT',
                "Object / 对象",
                "Use object-local axes so UV orientation is stable when the object moves or rotates",
            ),
            (
                'WORLD',
                "World / 世界",
                "Use world axes so separate scene objects share one visual up direction",
            ),
        ),
        default='OBJECT',
    )
    uv_direction_axis: EnumProperty(
        name="Texture Up Axis / 纹理向上轴",
        description="Positive model axis that should point toward UV +V",
        items=(
            (
                'AUTO',
                "Auto / 自动",
                "Resolve a stable tangent axis using the configurable AUTO priority below",
            ),
            ('Z', "+Z", "Map positive Z to UV +V where Z has a tangent-plane component"),
            ('X', "+X", "Map positive X to UV +V where X has a tangent-plane component"),
            ('Y', "+Y", "Map positive Y to UV +V where Y has a tangent-plane component"),
        ),
        default='AUTO',
    )
    uv_direction_auto_priority: EnumProperty(
        name="AUTO Axis Priority / 自动轴优先级",
        description=(
            "Fallback order used when Texture Up Axis is Auto; the first axis "
            "with a stable tangent projection wins"
        ),
        items=(
            (
                'ZXY',
                "Z - X - Y",
                "Legacy default: prefer +Z, then +X, then +Y",
            ),
            (
                'ZYX',
                "Z - Y - X",
                "Prefer +Z, then +Y, then +X",
            ),
            (
                'XZY',
                "X - Z - Y",
                "Prefer +X, then +Z, then +Y",
            ),
            (
                'XYZ',
                "X - Y - Z",
                "Prefer +X, then +Y, then +Z",
            ),
            (
                'YZX',
                "Y - Z - X",
                "Weapon longitudinal default: prefer +Y, then +Z, then +X",
            ),
            (
                'YXZ',
                "Y - X - Z",
                "Prefer +Y, then +X, then +Z",
            ),
        ),
        default='ZXY',
    )
    uv_direction_auto_cardinal_bias: FloatProperty(
        name="AUTO Cardinal Bias / 自动直角偏好",
        description=(
            "Optional bounded bias toward an AUTO axis whose corrected long edge "
            "is cardinal; 仅在通过几何投影门槛时偏好长边更水平/垂直的自动轴"
        ),
        default=0.0,
        min=0.0,
        max=1.0,
        precision=2,
        subtype='FACTOR',
    )
    uv_direction_auto_cardinal_min_confidence: FloatProperty(
        name="AUTO Cardinal Cue Confidence / 自动直角线索置信度",
        description=(
            "Ignore boundary-edge cardinal cues below this confidence; 忽略低于该置信度的边界边直角线索"
        ),
        default=0.15,
        min=0.0,
        max=1.0,
        precision=2,
        subtype='FACTOR',
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
    uv_small_island_scale_boost: FloatProperty(
        name="Small Island Boost / 小岛放大",
        description=(
            "Uniformly enlarge classified micro-islands before packing, within a "
            "strict no-overlap fallback; 对判定为微小机械件的 UV 岛在装箱前等比放大，"
            "无法满足无重叠时自动回退"
        ),
        # 1.25 is the measured hard-surface compromise: it raises detached
        # micro-charts while leaving enough packing headroom for a dense atlas.
        default=1.25,
        min=1.0,
        max=3.0,
        precision=2,
    )
    uv_square_pack_bias: FloatProperty(
        name="Square Pack Bias / 方形装箱偏好",
        description=(
            "Prefer a compact, square-like 0-1 layout while preserving the best "
            "texel scale; 优先选择更紧凑的方形排布，同时保持可达到的最佳统一密度"
        ),
        default=0.35,
        min=0.0,
        max=1.0,
        precision=2,
    )
    uv_square_pack_max_edge_relaxation: FloatProperty(
        name="Square Pack Edge Relax / 方形装箱最长边容差",
        description=(
            "Allow a bounded relative increase of the packed longest edge when "
            "it materially improves tile utilization; 仅在能明显改善贴图利用率时，"
            "允许装箱最长边相对基准小幅增加"
        ),
        default=0.02,
        min=0.0,
        max=0.25,
        precision=3,
        subtype='FACTOR',
    )
    uv_cardinal_edge_confidence: FloatProperty(
        name="Cardinal Edge Confidence / 直角边方向置信度",
        description=(
            "Use a clear boundary edge as the horizontal or vertical orientation "
            "cue for round-ish hard-surface islands; 为近方形硬表面 UV 岛使用可靠"
            "边界边作为水平或垂直方向参考"
        ),
        default=0.15,
        min=0.0,
        max=1.0,
        precision=2,
    )
    uv_prefer_geometry_axis_cardinal: BoolProperty(
        name="Prefer Shared Cardinal Axes / 优先共享直角轴",
        description=(
            "For related hard-surface islands, allow a clearly straighter long-edge "
            "heading to choose a common AUTO geometry axis within the quality gates; "
            "仅在质量门槛允许时让结构相关硬表面岛共享更规整的几何轴"
        ),
        # Keep the historical AUTO resolver unchanged unless an artist opts in.
        default=False,
    )
    uv_geometry_axis_cardinal_min_gain: FloatProperty(
        name="Shared Axis Min Gain / 共享轴最小收益",
        description=(
            "Minimum long-edge cardinal improvement required before the common "
            "AUTO axis may change; 共享 AUTO 轴切换前要求的最小长边直角收益"
        ),
        default=math.radians(5.0),
        min=0.0,
        max=math.radians(45.0),
        precision=1,
        subtype='ANGLE',
    )
    uv_geometry_axis_cardinal_max_quality_loss: FloatProperty(
        name="Shared Axis Quality Loss / 共享轴质量损失",
        description=(
            "Maximum weakest-stability loss accepted for the cardinal improvement; "
            "为获得直角长边方向允许的最大最弱稳定性损失"
        ),
        default=0.15,
        min=0.0,
        max=1.0,
        precision=2,
        subtype='FACTOR',
    )
    uv_cohere_auto_geometry_axis: BoolProperty(
        name="Normal-Domain Axis Coherence / 法线域轴一致",
        description=(
            "For unconstrained hard-surface islands, choose the first stable "
            "configured model axis that lies in the island tangent plane; "
            "按法线域和配置优先级为无结构约束的硬表面岛选择稳定切向轴"
        ),
        default=False,
    )
    uv_geometry_axis_consensus_min_tangent: FloatProperty(
        name="Axis Tangent Threshold / 轴切向阈值",
        description=(
            "Minimum model-axis projection in the tangent plane before the "
            "normal-domain AUTO resolver may use that axis; "
            "轴在切平面内的最小投影"
        ),
        default=0.70,
        min=0.0,
        max=1.0,
        precision=2,
        subtype='FACTOR',
    )
    uv_geometry_axis_consensus_min_confidence: FloatProperty(
        name="Axis Confidence Threshold / 轴置信阈值",
        description=(
            "Quality threshold used to report a low-confidence member while "
            "retaining a valid shared axis; 用于标记低置信成员而不拆散有效共享轴"
        ),
        default=0.30,
        min=0.0,
        max=1.0,
        precision=2,
        subtype='FACTOR',
    )
    uv_align_geometry_frame: BoolProperty(
        name="Validate Full Geometry Frame / 校验完整几何帧",
        description=(
            "Validate both signed tangent axes instead of only texture-up; "
            "同时校验纹理向上轴及其横向轴，不允许用镜像修复负帧"
        ),
        default=True,
    )
    uv_use_geometry_frame_rotation: BoolProperty(
        name="Use Full Frame Rotation / 使用完整帧旋转",
        description=(
            "Use the complete positive geometry frame when it satisfies the "
            "direction contract; 仅在完整正向几何帧通过方向契约时用于旋转 UV"
        ),
        default=False,
    )
    uv_cohere_geometry_angle_groups: BoolProperty(
        name="Share Group Heading / 共享组朝向",
        description=(
            "Let compatible structure members share a visual heading; disabled "
            "by default to preserve exact per-island direction"
        ),
        default=False,
    )
    uv_strict_geometry_frame_quality: BoolProperty(
        name="Require Planar Frame Quality / 要求平面帧质量",
        description=(
            "Reject planar hard-surface UV results that lose the complete "
            "positive tangent frame or exceed the checker-direction tolerance"
        ),
        default=False,
    )
    uv_strict_geometry_frame_planar_only: BoolProperty(
        name="Planar Charts Only / 仅平面 UV 岛",
        description=(
            "Apply strict complete-frame acceptance only to planar charts; "
            "curved shells may retain the signed texture-up fallback"
        ),
        default=True,
    )
    uv_strict_geometry_frame_planar_tolerance: FloatProperty(
        name="Planar Normal Tolerance / 平面法线容差",
        description="Maximum normal spread for strict planar-frame validation",
        default=math.radians(5.0),
        min=0.0,
        max=math.radians(90.0),
        precision=1,
        subtype='ANGLE',
    )
    uv_strict_geometry_frame_residual_tolerance: FloatProperty(
        name="Frame Residual Tolerance / 几何帧残差容差",
        description="Maximum complete-frame residual accepted by the quality gate",
        default=math.radians(3.0),
        min=0.0,
        max=math.radians(180.0),
        precision=1,
        subtype='ANGLE',
    )
    uv_preserve_source_layout: BoolProperty(
        name="Preserve Source Layout / 保留原始排布",
        description=(
            "Use the existing UV atlas as the structural scaffold and repair "
            "local collisions without rebuilding broad semantic groups"
        ),
        default=False,
    )
    uv_source_layout_row_quantum: FloatProperty(
        name="Source Row Quantum / 原始行量化",
        description="UV-space row quantization used by source-layout packing",
        default=0.025,
        min=0.000001,
        max=1.0,
        precision=4,
    )
    uv_source_layout_row_weight: FloatProperty(
        name="Source Row Weight / 原始行权重",
        description="Preference for retaining source UV rows",
        default=0.15,
        min=0.0,
        max=10.0,
        precision=3,
    )
    uv_source_layout_order: EnumProperty(
        name="Source Layout Order / 原始排布顺序",
        description="Choose the primary placement order for source-atlas repair",
        items=(
            ('ROW_MAJOR', "Row Major / 按行", "Preserve source UV rows and columns"),
            ('AREA', "Area / 按面积", "Place larger charts first"),
        ),
        default='ROW_MAJOR',
    )
    uv_source_layout_affinity_weight: FloatProperty(
        name="Source Link Weight / 原始关联权重",
        description="Preference for applying one translation to linked charts",
        default=0.0,
        min=0.0,
        max=10.0,
        precision=3,
    )
    uv_source_layout_cell_enabled: BoolProperty(
        name="Local Source Cells / 原始局部单元",
        description=(
            "Pack nearby compatible structural charts into bounded local cells "
            "before resolving atlas-level collisions"
        ),
        default=True,
    )
    uv_source_layout_compact_cells: BoolProperty(
        name="Compact Local Cells / 紧凑排布局部单元",
        description=(
            "Pack local structure cells tightly while preserving their source "
            "row order and locked checker direction"
        ),
        default=True,
    )
    uv_source_layout_cell_max_members: IntProperty(
        name="Cell Member Limit / 单元成员上限",
        description="Maximum UV islands retained in one local source cell",
        default=12,
        min=2,
        max=256,
    )
    uv_source_layout_cell_diameter_ratio: FloatProperty(
        name="Cell Diameter / 单元直径",
        description="Maximum source-UV diameter relative to the source atlas",
        default=0.16,
        min=0.000001,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    uv_source_layout_cell_link_radius_ratio: FloatProperty(
        name="Cell Link Radius / 单元连接半径",
        description="Maximum source-UV distance for local-cell proximity links",
        default=0.12,
        min=0.000001,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    uv_structure_group_enabled: BoolProperty(
        name="Structure Groups / 结构分组",
        description="Keep bounded topology-adjacent mechanical charts together",
        default=True,
    )
    uv_structure_group_max_members: IntProperty(
        name="Structure Member Limit / 结构成员上限",
        description="Maximum UV islands in one structure group",
        default=12,
        min=2,
        max=256,
    )
    uv_structure_group_max_degree: IntProperty(
        name="Structure Link Degree / 结构连接度",
        description="Maximum accepted structural links at one group endpoint",
        default=2,
        min=1,
        max=32,
    )
    uv_structure_group_min_contact_ratio: FloatProperty(
        name="Structure Contact / 结构接触比",
        description="Minimum mesh contact ratio for a structural link",
        default=0.12,
        min=0.0,
        max=10.0,
        precision=3,
    )
    uv_structure_group_max_diameter_ratio: FloatProperty(
        name="Structure Diameter / 结构直径",
        description="Maximum model-space diameter relative to the object",
        default=0.20,
        min=0.000001,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    uv_structure_group_max_normal_angle: FloatProperty(
        name="Structure Normal Angle / 结构法线角",
        description="Maximum normal difference allowed in one structure group",
        default=math.radians(100.0),
        min=0.0,
        max=math.pi,
        precision=1,
        subtype='ANGLE',
    )
    uv_repeat_group_max_members: IntProperty(
        name="Repeat Member Limit / 重复件成员上限",
        description="Maximum UV islands in one bounded repeat layout group",
        default=48,
        min=2,
        max=512,
    )
    uv_repeat_group_max_diameter_ratio: FloatProperty(
        name="Repeat Diameter / 重复件直径",
        description="Maximum model-space diameter for one repeat layout group",
        default=0.20,
        min=0.000001,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    uv_max_repeat_group_size: IntProperty(
        name="Repeat Detection Limit / 重复检测上限",
        description="Legacy cap for repeat candidates retained for compatibility",
        default=24,
        min=2,
        max=512,
    )
    uv_max_small_members_per_group: IntProperty(
        name="Small Member Limit / 小岛成员上限",
        description="Maximum attached small islands in one layout group",
        default=16,
        min=1,
        max=512,
    )
    uv_directed_cardinal_tolerance: FloatProperty(
        name="Directed Cardinal Tolerance / 有向直角容差",
        description=(
            "Downgrade a repeat component to center-symmetric alignment when a "
            "landmark disagrees with its geometric axis by more than this angle; "
            "当 landmark 与几何主轴偏差超过该角度时，将重复组件降级为中心对称排布"
        ),
        # A tighter default keeps high-anisotropy repeat panels truly
        # horizontal/vertical; the setting remains user-adjustable for
        # directional props whose landmark intentionally points off-axis.
        default=math.radians(3.0),
        min=0.0,
        max=math.radians(90.0),
        precision=1,
        subtype='ANGLE',
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
    small_cleanup_tests: IntProperty(
        name="Cleanup Tests / 清理测试数",
        description=(
            "Maximum topology-adjacent stitch candidates tested per object"
        ),
        default=400,
        min=0,
        max=10000,
    )
    small_cleanup_max_merges: IntProperty(
        name="Cleanup Merge Limit / 清理合并上限",
        description="Maximum accepted small-island stitches per object",
        default=200,
        min=0,
        max=1000,
    )
    small_target_area_multiplier: FloatProperty(
        name="Target Area Multiplier / 目标面积倍率",
        description=(
            "Require the receiving chart to be at least this many times the "
            "candidate chart area"
        ),
        default=1.0,
        min=1.0,
        max=100.0,
        precision=2,
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
    small_structural_cleanup_enabled: BoolProperty(
        name="Structural Fallback / 结构补缝",
        description=(
            "When no strict candidate remains, conservatively stitch local "
            "panel, bevel, or general fragments without crossing hard cuts; "
            "严格候选为空时，保守缝合局部面板、倒角或普通碎片且不跨越强制切缝"
        ),
        default=True,
    )
    small_structural_boundary_ratio: FloatProperty(
        name="Fallback Boundary / 补缝共享边",
        description=(
            "Minimum shared-perimeter ratio for the structural fallback; "
            "结构补缝所需的最小共享周长比例"
        ),
        default=0.08,
        min=0.0,
        max=1.0,
        precision=3,
        subtype='FACTOR',
    )
    small_structural_angle: FloatProperty(
        name="Fallback Angle / 补缝角度",
        description=(
            "Maximum face angle for a structural fallback candidate; "
            "结构补缝候选允许的最大面夹角"
        ),
        default=math.radians(60.0),
        min=0.0,
        max=math.pi,
        subtype='ANGLE',
    )
    small_structural_max_merges: IntProperty(
        name="Fallback Limit / 补缝上限",
        description=(
            "Maximum accepted structural fallback stitches per object; "
            "每个对象最多接受的结构补缝次数"
        ),
        default=160,
        min=0,
        max=1000,
    )
    small_chain_max_faces: IntProperty(
        name="Chain Face Limit / 连续链面数上限",
        description=(
            "Maximum total faces in one iteratively stitched local chart; "
            "zero disables this limit"
        ),
        default=96,
        min=0,
        max=10000,
    )
    small_chain_max_absorptions: IntProperty(
        name="Chain Absorption Limit / 连续链吸收上限",
        description=(
            "Maximum cumulative fragment stitches in one local chart; zero "
            "disables this limit"
        ),
        default=8,
        min=0,
        max=1000,
    )
    small_chain_max_diameter_ratio: FloatProperty(
        name="Chain Diameter / 连续链直径",
        description=(
            "Maximum stitched-chart model diameter relative to the object; "
            "zero disables this limit"
        ),
        default=0.35,
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
