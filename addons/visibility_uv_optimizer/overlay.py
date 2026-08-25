# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from .visibility import VISIBILITY_ATTRIBUTE
from .visibility import effective_object_visibility
from .visibility import has_face_attribute


_HANDLE = None
_BATCHES = []
_SHADER = None


def _color(score, high, opacity):
    if score <= 0.0:
        return (0.75, 0.04, 0.02, opacity * 0.65)
    if score < high:
        t = score / max(high, 1e-8)
        return (1.0, 0.22 + 0.58 * t, 0.02, opacity)
    t = min((score - high) / max(1.0 - high, 1e-8), 1.0)
    return (0.08, 0.75 + 0.2 * t, 0.12, opacity)


def _category_enabled(score, settings):
    if score <= 0.0:
        return settings.show_red_faces
    if score < settings.visibility_high:
        return settings.show_yellow_faces
    return settings.show_green_faces


def _create_color_shader(gpu):
    """Use the 3.x name first, then the 4.x/5.x compatible name."""
    for shader_name in ('3D_SMOOTH_COLOR', 'SMOOTH_COLOR'):
        try:
            return gpu.shader.from_builtin(shader_name)
        except (TypeError, ValueError):
            continue
    return None


def _object_geometry(obj, settings):
    mesh = obj.data
    if not has_face_attribute(mesh, VISIBILITY_ATTRIBUTE, 'FLOAT'):
        return [], []

    scores = effective_object_visibility(obj)
    positions = []
    colors = []
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.index_update()
        triangles = bm.calc_loop_triangles()
        for triangle in triangles:
            face = triangle[0].face
            if face.hide or face.index < 0 or face.index >= len(scores):
                continue
            score = scores[face.index]
            if not _category_enabled(score, settings):
                continue
            color = _color(
                score, settings.visibility_high, settings.overlay_opacity)
            for loop in triangle:
                positions.append(obj.matrix_world @ loop.vert.co)
                colors.append(color)
        return positions, colors

    mesh.calc_loop_triangles()
    for triangle in mesh.loop_triangles:
        polygon_index = triangle.polygon_index
        if polygon_index < 0 or polygon_index >= len(scores):
            continue
        if mesh.polygons[polygon_index].hide:
            continue
        if any(index < 0 or index >= len(mesh.vertices)
               for index in triangle.vertices):
            continue
        score = scores[polygon_index]
        if not _category_enabled(score, settings):
            continue
        color = _color(
            score, settings.visibility_high, settings.overlay_opacity)
        for vertex_index in triangle.vertices:
            positions.append(
                obj.matrix_world @ mesh.vertices[vertex_index].co)
            colors.append(color)
    return positions, colors


def rebuild(objects, settings):
    global _BATCHES, _SHADER
    _BATCHES = []
    if bpy.app.background:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    _SHADER = _create_color_shader(gpu)
    if _SHADER is None:
        return
    for obj in objects:
        positions, colors = _object_geometry(obj, settings)
        if positions:
            batch = batch_for_shader(
                _SHADER, 'TRIS', {"pos": positions, "color": colors})
            _BATCHES.append(batch)


def _draw():
    if not _BATCHES or _SHADER is None:
        return
    import gpu
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    try:
        _SHADER.bind()
        for batch in _BATCHES:
            batch.draw(_SHADER)
    finally:
        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')


def enable():
    global _HANDLE
    if bpy.app.background or _HANDLE is not None:
        return
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (), 'WINDOW', 'POST_VIEW')
    redraw()


def disable():
    global _HANDLE
    if _HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLE, 'WINDOW')
        _HANDLE = None
    redraw()


def redraw():
    if bpy.app.background:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def shutdown():
    global _BATCHES, _SHADER
    disable()
    _BATCHES = []
    _SHADER = None
