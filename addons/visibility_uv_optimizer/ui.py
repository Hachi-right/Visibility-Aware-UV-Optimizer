# SPDX-License-Identifier: GPL-2.0-or-later

import bpy


class VUV_PT_MainPanel(bpy.types.Panel):
    bl_label = "Visibility UV Optimizer"
    bl_idname = "VUV_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "UV Optimizer"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.vuv_settings

        box = layout.box()
        box.label(text="Cameras", icon='CAMERA_DATA')
        box.prop(settings, "camera_source")
        box.prop(settings, "axis_basis")
        box.prop(settings, "camera_margin")
        row = box.row(align=True)
        row.operator("vuv.create_auto_cameras", text="Create / Update", icon='ADD')
        row.operator("vuv.remove_auto_cameras", text="", icon='TRASH')

        box = layout.box()
        box.label(text="Visibility", icon='HIDE_OFF')
        box.prop(settings, "ray_source")
        if settings.ray_source in {'CAMERA', 'HYBRID'}:
            box.prop(settings, "camera_source")
            box.prop(settings, "sampling_resolution")
        if settings.ray_source in {'SPHERE', 'HYBRID'}:
            box.prop(settings, "sphere_samples")
            box.prop(settings, "sphere_coverage_grid")
            row = box.row(align=True)
            row.prop(settings, "sphere_radius_factor")
            row.prop(settings, "sphere_visible")
            row = box.row(align=True)
            row.prop(settings, "sphere_depth_layers")
            row.prop(settings, "sphere_layer_falloff")
            box.prop(settings, "sphere_weight")
            row = box.row(align=True)
            row.operator("vuv.create_sphere_sampler", text="Update Sphere", icon='ADD')
            row.operator("vuv.remove_sphere_sampler", text="", icon='TRASH')
        if settings.ray_source == 'EXTERIOR':
            box.prop(settings, "exterior_resolution")
            box.prop(settings, "exterior_depth_falloff")
            box.prop(settings, "exterior_surface_samples")
        if settings.ray_source != 'EXTERIOR':
            box.prop(settings, "ignore_backfaces")
        box.prop(settings, "visibility_high")
        box.prop(settings, "overlay_opacity")
        box.operator("vuv.analyze_visibility", icon='VIEWZOOM')
        row = box.row(align=True)
        row.operator("vuv.toggle_overlay", text="Heatmap", icon='OVERLAY')
        select_hidden = row.operator("vuv.select_by_visibility", text="Hidden")
        select_hidden.mode = 'HIDDEN'
        select_low = row.operator("vuv.select_by_visibility", text="Low")
        select_low.mode = 'LOW'
        box.label(text="Face Display")
        row = box.row(align=True)
        row.prop(settings, "show_green_faces", toggle=True)
        row.prop(settings, "show_yellow_faces", toggle=True)
        row.prop(settings, "show_red_faces", toggle=True)
        box.operator("vuv.show_all_visibility", text="Show All", icon='HIDE_OFF')
        if settings.last_analysis:
            box.label(text=settings.last_analysis)

        box = layout.box()
        box.label(text="Face Override", icon='RESTRICT_SELECT_OFF')
        row = box.row(align=True)
        important = row.operator("vuv.set_face_override", text="Important")
        important.value = 'IMPORTANT'
        automatic = row.operator("vuv.set_face_override", text="Auto")
        automatic.value = 'AUTO'
        hidden = row.operator("vuv.set_face_override", text="Hidden")
        hidden.value = 'HIDDEN'

        box = layout.box()
        box.label(text="UV Optimization", icon='UV')
        box.prop(settings, "initial_uv_mode")
        if settings.initial_uv_mode in {
                'AUTO', 'HARD_SURFACE', 'MARKED_SEAMS', 'REFINE_LAYOUT'}:
            box.prop(settings, "hard_surface_profile")
        box.prop(settings, "uv_usage")
        if settings.initial_uv_mode in {
                'AUTO', 'HARD_SURFACE', 'MARKED_SEAMS', 'REFINE_LAYOUT'}:
            row = box.row(align=True)
            row.prop(settings, "hard_surface_sharp_angle")
            row.prop(settings, "hard_surface_planar_angle")
            box.prop(settings, "hard_surface_respect_sharp")
            box.prop(settings, "hard_surface_align_cardinal")
            box.prop(settings, "hard_surface_direction_lock")
            box.prop(settings, "reference_uv_layers")
            box.prop(settings, "reference_uv_boundary_mode")
            box.prop(settings, "reference_uv_boundary_bias")
            direction = box.column(align=True)
            direction.enabled = settings.hard_surface_direction_lock
            row = direction.row(align=True)
            row.prop(settings, "uv_direction_space")
            row.prop(settings, "uv_direction_axis")
            direction.prop(settings, "uv_direction_auto_priority")
            direction.prop(settings, "uv_direction_auto_cardinal_bias")
            direction.prop(settings, "uv_direction_auto_cardinal_min_confidence")
            direction.prop(settings, "uv_repeat_local_frame")
            direction.prop(settings, "uv_align_geometry_frame")
            frame = direction.column(align=True)
            frame.enabled = settings.uv_align_geometry_frame
            frame.prop(settings, "uv_use_geometry_frame_rotation")
            frame.prop(settings, "uv_cohere_geometry_angle_groups")
            frame.prop(settings, "uv_strict_geometry_frame_quality")
            strict_frame = frame.column(align=True)
            strict_frame.enabled = settings.uv_strict_geometry_frame_quality
            strict_frame.prop(settings, "uv_strict_geometry_frame_planar_only")
            row = strict_frame.row(align=True)
            row.prop(settings, "uv_strict_geometry_frame_planar_tolerance")
            row.prop(settings, "uv_strict_geometry_frame_residual_tolerance")
            box.prop(settings, "hard_surface_hidden_collapse")
        if settings.initial_uv_mode != 'PRESERVE_LAYOUT':
            box.prop(settings, "smart_angle")
            box.prop(settings, "merge_angle")
            box.label(text="Chart Growth")
            box.prop(settings, "developable_enabled")
            row = box.row(align=True)
            row.prop(settings, "developable_angle")
            row.prop(settings, "developable_bonus")
            row = box.row(align=True)
            row.prop(settings, "seam_visibility_weight")
            row.prop(settings, "seam_hard_edge_bonus")
            box.prop(settings, "seam_concave_bonus")
            box.label(text="Texel Importance")
            row = box.row(align=True)
            row.prop(settings, "detail_strength")
            row.prop(settings, "detail_scale_cap")
            row = box.row(align=True)
            row.prop(settings, "max_p95_stretch")
            row.prop(settings, "max_stretch")
            box.prop(settings, "max_merge_tests")

            if settings.initial_uv_mode in {
                    'AUTO', 'HARD_SURFACE', 'MARKED_SEAMS', 'REFINE_LAYOUT'}:
                box.separator()
                box.label(text="Small Island Cleanup / 小岛清理")
                box.prop(settings, "small_cleanup_enabled")
                cleanup = box.column(align=True)
                cleanup.enabled = settings.small_cleanup_enabled
                row = cleanup.row(align=True)
                row.prop(settings, "small_cleanup_tests")
                row.prop(settings, "small_cleanup_max_merges")
                row = cleanup.row(align=True)
                row.prop(settings, "small_island_faces")
                row.prop(settings, "small_boundary_ratio")
                row = cleanup.row(align=True)
                row.prop(settings, "small_island_area_ratio")
                row.prop(settings, "small_uv_area_ratio")
                row = cleanup.row(align=True)
                row.prop(settings, "small_cleanup_p95")
                row.prop(settings, "small_cleanup_max_stretch")
                cleanup.prop(settings, "small_target_area_multiplier")
                cleanup.prop(settings, "small_structural_cleanup_enabled")
                structural = cleanup.column(align=True)
                structural.enabled = settings.small_structural_cleanup_enabled
                row = structural.row(align=True)
                row.prop(settings, "small_structural_boundary_ratio")
                row.prop(settings, "small_structural_angle")
                structural.prop(settings, "small_structural_max_merges")
                row = structural.row(align=True)
                row.prop(settings, "small_chain_max_faces")
                row.prop(settings, "small_chain_max_absorptions")
                structural.prop(settings, "small_chain_max_diameter_ratio")

                box.separator()
                box.label(text="Structure Layout / 结构排布")
                box.prop(settings, "uv_group_layout_enabled")
                grouping = box.column(align=True)
                grouping.enabled = settings.uv_group_layout_enabled
                grouping.prop(settings, "proximity_radius_ratio")
                grouping.prop(settings, "uv_small_island_scale_boost")
                grouping.prop(settings, "uv_square_pack_bias")
                grouping.prop(settings, "uv_square_pack_max_edge_relaxation")
                grouping.prop(settings, "uv_cardinal_edge_confidence")
                grouping.prop(settings, "uv_directed_cardinal_tolerance")
                grouping.prop(settings, "uv_prefer_geometry_axis_cardinal")
                cardinal = grouping.column(align=True)
                cardinal.enabled = settings.uv_prefer_geometry_axis_cardinal
                cardinal.prop(settings, "uv_geometry_axis_cardinal_min_gain")
                cardinal.prop(settings, "uv_geometry_axis_cardinal_max_quality_loss")
                grouping.prop(settings, "uv_cohere_auto_geometry_axis")
                domain = grouping.column(align=True)
                domain.enabled = settings.uv_cohere_auto_geometry_axis
                domain.prop(settings, "uv_geometry_axis_consensus_min_tangent")
                domain.prop(settings, "uv_geometry_axis_consensus_min_confidence")
                grouping.prop(settings, "uv_preserve_source_layout")
                source = grouping.column(align=True)
                source.enabled = settings.uv_preserve_source_layout
                source.prop(settings, "uv_source_layout_cell_enabled")
                cell = source.column(align=True)
                cell.enabled = settings.uv_source_layout_cell_enabled
                cell.prop(settings, "uv_source_layout_compact_cells")
                row = cell.row(align=True)
                row.prop(settings, "uv_source_layout_cell_max_members")
                row.prop(settings, "uv_source_layout_cell_diameter_ratio")
                cell.prop(settings, "uv_source_layout_cell_link_radius_ratio")
            box.prop(settings, "preserve_seams")
            box.prop(settings, "respect_materials")
            box.prop(settings, "respect_sharp")
            box.prop(settings, "reject_overlap")
            box.prop(settings, "island_margin")

        probe_box = layout.box()
        probe_box.label(text="Texture Direction Probe", icon='TEXTURE')
        probe_box.prop(settings, "probe_enabled")
        row = probe_box.row(align=True)
        row.operator("vuv.import_probe_evidence", text="Import", icon='IMPORT')
        row.operator("vuv.clear_probe_evidence", text="", icon='TRASH')
        probe_box.prop(settings, "probe_merge_bonus")
        probe_box.prop(settings, "probe_min_confidence")
        probe_box.prop(settings, "probe_reject_flipped")
        probe_box.operator(
            "vuv.export_probe_report",
            text="Export Candidate Report",
            icon='EXPORT',
        )
        if settings.last_probe:
            probe_box.label(text=settings.last_probe)

        box.operator("vuv.optimize_uv", icon='MOD_UVPROJECT')
        if settings.last_optimize:
            for start in range(0, len(settings.last_optimize), 52):
                box.label(text=settings.last_optimize[start:start + 52])

        box = layout.box()
        box.label(text="Interactive Mapping", icon='UV')
        box.operator("vuv.export_mapping_viewer", text="Export Viewer", icon='EXPORT')
