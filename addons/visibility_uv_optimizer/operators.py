# SPDX-License-Identifier: GPL-2.0-or-later

import json

import bmesh
import bpy
from bpy.props import EnumProperty
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper
from bpy_extras.io_utils import ImportHelper

from . import camera_utils
from . import overlay
from . import probe_analysis
from . import uv_optimize
from . import visibility


FILTER_RESTORE_LAYER = "vuv_filter_original_hide"


def _active_mesh(context):
    obj = context.active_object
    return obj if obj and obj.type == 'MESH' else None


def _restore_object_selection(context, selected_names, active_name):
    for obj in context.view_layer.objects:
        obj.select_set(obj.name in selected_names)
    active = bpy.data.objects.get(active_name) if active_name else None
    if active and active.name in context.view_layer.objects:
        context.view_layer.objects.active = active


def _restore_edit_mode(context, obj, original_mode):
    if original_mode != 'EDIT' or obj is None:
        return
    if obj.name not in context.view_layer.objects:
        return
    context.view_layer.objects.active = obj
    obj.select_set(True)
    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')


class VUV_OT_CreateAutoCameras(bpy.types.Operator):
    bl_idname = "vuv.create_auto_cameras"
    bl_label = "Create Auto Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vuv_settings
        targets = camera_utils.selected_meshes(context)
        try:
            cameras = camera_utils.create_auto_cameras(context, settings, targets)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, "Created or updated {} cameras".format(len(cameras)))
        return {'FINISHED'}


class VUV_OT_RemoveAutoCameras(bpy.types.Operator):
    bl_idname = "vuv.remove_auto_cameras"
    bl_label = "Remove Auto Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        removed = camera_utils.remove_auto_cameras()
        self.report({'INFO'}, "Removed {} cameras".format(removed))
        return {'FINISHED'}


class VUV_OT_CreateSphereSampler(bpy.types.Operator):
    bl_idname = "vuv.create_sphere_sampler"
    bl_label = "Create / Update Sphere"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vuv_settings
        try:
            sampler = camera_utils.create_sphere_sampler(
                context, settings, camera_utils.selected_meshes(context))
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        settings.sphere_visible = True
        self.report({'INFO'}, "Sphere radius {:.3f}".format(sampler.empty_display_size))
        return {'FINISHED'}


class VUV_OT_RemoveSphereSampler(bpy.types.Operator):
    bl_idname = "vuv.remove_sphere_sampler"
    bl_label = "Remove Sphere"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        removed = camera_utils.remove_sphere_sampler()
        self.report({'INFO'}, "Removed {} sphere sampler".format(removed))
        return {'FINISHED'}


class VUV_OT_AnalyzeVisibility(bpy.types.Operator):
    bl_idname = "vuv.analyze_visibility"
    bl_label = "Analyze Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vuv_settings
        original_active = context.active_object
        original_mode = original_active.mode if original_active else 'OBJECT'
        try:
            if original_active and original_active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            targets = camera_utils.selected_meshes(context)
            if not targets:
                raise ValueError("Select at least one mesh object")
            uses_cameras = settings.ray_source in {'CAMERA', 'HYBRID'}
            uses_sphere = settings.ray_source in {'SPHERE', 'HYBRID'}
            uses_exterior = settings.ray_source == 'EXTERIOR'
            cameras = camera_utils.get_analysis_cameras(
                context, settings, auto_create=uses_cameras,
                targets=targets) if uses_cameras else []
            sampler = camera_utils.get_sphere_sampler(
                context, settings, auto_create=uses_sphere,
                targets=targets) if uses_sphere else None
            if uses_cameras and not cameras:
                raise ValueError("No analysis cameras found")
            if uses_sphere and sampler is None:
                raise ValueError("No sphere sampler found")
            sphere = None
            sphere_basis = None
            if sampler is not None:
                world_scale = sampler.matrix_world.to_scale()
                radius = sampler.empty_display_size * max(
                    abs(value) for value in world_scale)
                sphere = (sampler.matrix_world.translation.copy(), radius)
                sphere_basis = camera_utils._basis(context, settings)
            scores, _ = visibility.analyze(
                context.scene,
                targets,
                cameras,
                int(settings.sampling_resolution),
                settings.ignore_backfaces,
                sphere=sphere,
                sphere_samples=int(settings.sphere_samples),
                sphere_layers=settings.sphere_depth_layers,
                sphere_layer_falloff=settings.sphere_layer_falloff,
                sphere_weight=settings.sphere_weight,
                sphere_coverage_grid=int(settings.sphere_coverage_grid),
                sphere_basis=sphere_basis,
                exterior=uses_exterior,
                exterior_resolution=int(settings.exterior_resolution),
                exterior_depth_falloff=settings.exterior_depth_falloff,
                exterior_surface_samples=settings.exterior_surface_samples,
            )
            visible = sum(
                sum(1 for value in values if value > 0.0)
                for values in scores.values())
            total = sum(len(values) for values in scores.values())
            if settings.ray_source == 'SPHERE':
                ray_label = "{} sphere directions, {} rays".format(
                    int(settings.sphere_samples),
                    int(settings.sphere_samples) *
                    int(settings.sphere_coverage_grid) ** 2,
                )
            elif settings.ray_source == 'CAMERA':
                ray_label = "{} cameras".format(len(cameras))
            elif settings.ray_source == 'EXTERIOR':
                ray_label = "{}^3 exterior voxels, depth falloff {:.2f}".format(
                    int(settings.exterior_resolution),
                    settings.exterior_depth_falloff,
                )
            else:
                ray_label = "{} cameras + {} sphere directions".format(
                    len(cameras), int(settings.sphere_samples))
            settings.last_analysis = "{} / {} faces visible, {}".format(
                visible, total, ray_label)
            overlay.rebuild(targets, settings)
            if settings.show_overlay:
                overlay.enable()
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        finally:
            _restore_edit_mode(context, original_active, original_mode)
        if original_mode == 'EDIT' and original_active:
            bpy.ops.vuv.apply_visibility_filter()
        self.report({'INFO'}, settings.last_analysis)
        return {'FINISHED'}


class VUV_OT_ToggleOverlay(bpy.types.Operator):
    bl_idname = "vuv.toggle_overlay"
    bl_label = "Toggle Heatmap"

    def execute(self, context):
        settings = context.scene.vuv_settings
        settings.show_overlay = not settings.show_overlay
        if settings.show_overlay:
            targets = camera_utils.selected_meshes(context)
            overlay.rebuild(targets, settings)
            overlay.enable()
        else:
            overlay.disable()
        return {'FINISHED'}


def _face_category_visible(score, settings):
    if score <= 0.0:
        return settings.show_red_faces
    if score < settings.visibility_high:
        return settings.show_yellow_faces
    return settings.show_green_faces


def apply_visibility_filter_state(context):
    """Apply display categories without modifying stored visibility scores."""
    obj = _active_mesh(context)
    if obj is None:
        return False, "No active mesh object"
    if not visibility.has_face_attribute(
            obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT'):
        return False, "No visibility analysis found on the active mesh"

    settings = context.scene.vuv_settings
    all_enabled = (
        settings.show_green_faces
        and settings.show_yellow_faces
        and settings.show_red_faces
    )
    entered_edit_mode = obj.mode != 'EDIT'
    if entered_edit_mode:
        bpy.ops.object.mode_set(mode='EDIT')
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.index_update()
        bm.faces.ensure_lookup_table()
        visibility_layer = bm.faces.layers.float.get(
            visibility.VISIBILITY_ATTRIBUTE)
        if visibility_layer is None:
            return False, "No visibility analysis found on the active mesh"

        # The per-mesh restore layer is authoritative. A Scene-level flag cannot
        # represent filters on different active objects reliably.
        stored_scores = [float(face[visibility_layer]) for face in bm.faces]
        restore_layer = bm.faces.layers.int.get(FILTER_RESTORE_LAYER)
        if all_enabled:
            if restore_layer is not None:
                for face in bm.faces:
                    face.hide_set(bool(face[restore_layer]))
                bm.faces.layers.int.remove(restore_layer)
            settings.filter_active = False
        else:
            if restore_layer is None:
                restore_layer = bm.faces.layers.int.new(FILTER_RESTORE_LAYER)
                for face in bm.faces:
                    face[restore_layer] = 1 if face.hide else 0
            for face in bm.faces:
                score = stored_scores[face.index]
                original_hidden = bool(face[restore_layer])
                category_hidden = not _face_category_visible(score, settings)
                face.hide_set(original_hidden or category_hidden)
            settings.filter_active = True

        # Hiding faces must never mutate the persistent classification.
        for face, score in zip(bm.faces, stored_scores):
            face[visibility_layer] = score
        bmesh.update_edit_mesh(
            obj.data, loop_triangles=True, destructive=False)
    finally:
        if entered_edit_mode and obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
    targets = camera_utils.selected_meshes(context)
    overlay.rebuild(targets or [obj], settings)
    if settings.show_overlay:
        overlay.enable()
    return True, None


class VUV_OT_ApplyVisibilityFilter(bpy.types.Operator):
    bl_idname = "vuv.apply_visibility_filter"
    bl_label = "Apply Visibility Display Filter"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return obj is not None and visibility.has_face_attribute(
            obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT')

    def execute(self, context):
        applied, error = apply_visibility_filter_state(context)
        if not applied:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class VUV_OT_ShowAllVisibility(bpy.types.Operator):
    bl_idname = "vuv.show_all_visibility"
    bl_label = "Show All Visibility Classes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return obj is not None and visibility.has_face_attribute(
            obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT')

    def execute(self, context):
        settings = context.scene.vuv_settings
        settings.filter_update_suspended = True
        try:
            settings.show_green_faces = True
            settings.show_yellow_faces = True
            settings.show_red_faces = True
        finally:
            settings.filter_update_suspended = False
        applied, error = apply_visibility_filter_state(context)
        if not applied:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class VUV_OT_SelectByVisibility(bpy.types.Operator):
    bl_idname = "vuv.select_by_visibility"
    bl_label = "Select Faces by Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        items=(
            ('VISIBLE', "Visible", "Select faces hit by at least one ray"),
            ('LOW', "Low", "Select low coverage faces"),
            ('HIDDEN', "Hidden", "Select faces with no hits"),
        ),
        default='HIDDEN',
    )

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return obj is not None and visibility.has_face_attribute(
            obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT')

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.vuv_settings
        values = visibility.read_object_face_float(obj)

        def matches(value):
            if self.mode == 'HIDDEN':
                return value <= 0.0
            if self.mode == 'LOW':
                return 0.0 < value < settings.visibility_high
            return value >= settings.visibility_high

        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            for vertex in bm.verts:
                vertex.select_set(False)
            for edge in bm.edges:
                edge.select_set(False)
            for face in bm.faces:
                face.select_set(False)
            for face in bm.faces:
                face.select_set(matches(values[face.index]))
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            for polygon in obj.data.polygons:
                polygon.select = matches(values[polygon.index])
        return {'FINISHED'}


class VUV_OT_SetFaceOverride(bpy.types.Operator):
    bl_idname = "vuv.set_face_override"
    bl_label = "Set Visibility Override"
    bl_options = {'REGISTER', 'UNDO'}

    value: EnumProperty(
        items=(
            ('IMPORTANT', "Important", "Force selected faces to high priority"),
            ('AUTO', "Auto", "Clear overrides on selected faces"),
            ('HIDDEN', "Hidden", "Force selected faces to hidden priority"),
        ),
        default='IMPORTANT',
    )

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        mapping = {'IMPORTANT': 1, 'AUTO': 0, 'HIDDEN': -1}
        override_value = mapping[self.value]
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            layer = bm.faces.layers.int.get(visibility.OVERRIDE_ATTRIBUTE)
            if layer is None:
                layer = bm.faces.layers.int.new(visibility.OVERRIDE_ATTRIBUTE)
            automatic_layer = bm.faces.layers.float.get(
                visibility.AUTO_VISIBILITY_ATTRIBUTE)
            visibility_layer = bm.faces.layers.float.get(
                visibility.VISIBILITY_ATTRIBUTE)
            selected = [face for face in bm.faces if face.select]
            for face in selected:
                face[layer] = override_value
                if visibility_layer is not None:
                    automatic_score = (
                        float(face[automatic_layer])
                        if automatic_layer is not None
                        else float(face[visibility_layer])
                    )
                    face[visibility_layer] = (
                        1.0 if override_value > 0
                        else 0.0 if override_value < 0
                        else automatic_score
                    )
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            values = visibility.read_face_int(obj.data)
            selected = [polygon.index for polygon in obj.data.polygons if polygon.select]
            for face_index in selected:
                values[face_index] = override_value
            visibility.write_face_int(obj.data, values)
            visibility.refresh_effective_visibility(obj.data)
        if visibility.has_face_attribute(
                obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT'):
            apply_visibility_filter_state(context)
        self.report({'INFO'}, "Updated {} faces".format(len(selected)))
        return {'FINISHED'}


class VUV_OT_ImportProbeEvidence(ImportHelper, bpy.types.Operator):
    bl_idname = "vuv.import_probe_evidence"
    bl_label = "Import Probe Evidence"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: StringProperty(
        default="*.json",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.vuv_settings
        try:
            with open(self.filepath, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
            evidence = probe_analysis.normalize_evidence(payload)
            obj[probe_analysis.OBJECT_PROPERTY] = (
                probe_analysis.encode_evidence(evidence))
            settings.probe_enabled = True
            settings.last_probe = "Loaded {} edge evidences".format(
                len(evidence["edges"]))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({'ERROR'}, "Probe evidence import failed: {}".format(error))
            return {'CANCELLED'}

        object_name = evidence.get("object", "")
        if object_name and object_name != obj.name:
            self.report(
                {'WARNING'},
                "Evidence targets {}; active object is {}".format(
                    object_name, obj.name),
            )
        else:
            self.report({'INFO'}, settings.last_probe)
        return {'FINISHED'}


class VUV_OT_ClearProbeEvidence(bpy.types.Operator):
    bl_idname = "vuv.clear_probe_evidence"
    bl_label = "Clear Probe Evidence"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return obj is not None and probe_analysis.OBJECT_PROPERTY in obj

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.vuv_settings
        obj.pop(probe_analysis.OBJECT_PROPERTY, None)
        settings.probe_enabled = False
        settings.last_probe = "Probe evidence cleared"
        self.report({'INFO'}, settings.last_probe)
        return {'FINISHED'}


class VUV_OT_ExportProbeCandidateReport(ExportHelper, bpy.types.Operator):
    bl_idname = "vuv.export_probe_report"
    bl_label = "Export Probe Candidate Report"
    bl_options = {'REGISTER'}
    filename_ext = ".json"
    filter_glob: StringProperty(
        default="*.json",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return obj is not None and obj.mode == 'OBJECT'

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.vuv_settings
        try:
            report = uv_optimize.build_probe_candidate_report(obj, settings)
            with open(self.filepath, 'w', encoding='utf-8') as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except (OSError, RuntimeError, ValueError) as error:
            self.report({'ERROR'}, "Probe report export failed: {}".format(error))
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            "Exported {} probe candidates".format(len(report["candidates"])),
        )
        return {'FINISHED'}


def _optimization_summary(result, settings):
    """Build a compact summary while accepting pre-0.5.5 result objects."""
    summary = (
        "Islands {} -> {}, merged {}/{}; reject stretch {}, overlap {}, "
        "topology {}, probe {}; guided {}; mode {}; usage {}; cuts {}; "
        "repaired {}; mirrored {}; safe pack {}"
    ).format(
        result.initial_islands,
        result.final_islands,
        result.accepted_merges,
        result.merge_tests,
        result.rejected_stretch,
        result.rejected_overlap,
        result.rejected_topology,
        getattr(result, "rejected_probe", 0),
        getattr(result, "probe_guided_merges", 0),
        getattr(result, "initial_uv_mode", "LEGACY_SMART"),
        getattr(result, "uv_usage", "UNIQUE"),
        getattr(result, "forced_cuts", 0),
        getattr(result, "repaired_charts", 0),
        getattr(result, "mirrored_charts", 0),
        "yes" if getattr(result, "safe_pack_retry", False) else "no",
    )

    cleanup_enabled = bool(getattr(settings, "small_cleanup_enabled", False))
    cleanup_data = getattr(result, "small_cleanup_summary", {}) or {}
    cleanup_applied = bool(cleanup_data.get("enabled", False))
    if cleanup_applied:
        cleanup_text = "small/小岛 {}/{} ({} -> {})".format(
            getattr(result, "small_cleanup_merges", 0),
            getattr(result, "small_cleanup_tests", 0),
            getattr(result, "small_cleanup_initial_charts", 0),
            getattr(result, "small_cleanup_final_charts", 0),
        )
    elif cleanup_enabled:
        cleanup_text = "small/小岛 n/a"
    else:
        cleanup_text = "small/小岛 off"

    grouping_enabled = bool(getattr(settings, "uv_group_layout_enabled", False))
    grouping_applied = bool(getattr(result, "group_layout_applied", False))
    if grouping_applied:
        grouping_text = (
            "groups/分组 repeat {} ({} members), nearby {}, rotated {}, fallback {}"
        ).format(
            getattr(result, "group_layout_repeat_groups", 0),
            getattr(result, "group_layout_repeat_members", 0),
            getattr(result, "group_layout_small_grouped", 0),
            getattr(result, "group_layout_rotated_islands", 0),
            "yes" if getattr(result, "group_layout_fallback_used", False) else "no",
        )
    elif grouping_enabled:
        grouping_text = "groups/分组 n/a"
    else:
        grouping_text = "groups/分组 off"
    return "; ".join((summary, cleanup_text, grouping_text))


class VUV_OT_OptimizeUV(bpy.types.Operator):
    bl_idname = "vuv.optimize_uv"
    bl_label = "Optimize Active Object UV"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.vuv_settings
        original_mode = obj.mode
        selected_names = {item.name for item in context.selected_objects}
        active_name = obj.name
        result = None
        error = None
        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for item in context.selected_objects:
                item.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            result = uv_optimize.optimize_active_object(context, obj, settings)
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as caught_error:
            error = str(caught_error)
        finally:
            if obj.mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                except RuntimeError:
                    pass
            _restore_object_selection(context, selected_names, active_name)
            _restore_edit_mode(context, obj, original_mode)
        if error is not None:
            self.report({'ERROR'}, "UV optimization failed: {}".format(error))
            return {'CANCELLED'}
        settings.last_optimize = _optimization_summary(result, settings)
        self.report({'INFO'}, settings.last_optimize)
        return {'FINISHED'}
