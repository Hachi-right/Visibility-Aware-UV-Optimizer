# SPDX-License-Identifier: GPL-2.0-or-later

import json
import math
import os
from pathlib import Path
import re
import webbrowser

import bmesh
import bpy
from bpy.props import BoolProperty
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from . import camera_utils
from . import visibility

TEMPLATE_NAME = "mapping_viewer.html"


class _UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first, second):
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root != second_root:
            self.parent[second_root] = first_root


def _round(value, digits=6):
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("Mesh or UV data contains a non-finite value")
    rounded = round(numeric, digits)
    return 0.0 if rounded == 0.0 else rounded


def _uv_close(first, second, epsilon=1.0e-6):
    return abs(first[0] - second[0]) <= epsilon and abs(first[1] - second[1]) <= epsilon


def _island_ids_from_edge_uses(face_count, edge_uses):
    union_find = _UnionFind(face_count)
    for uses in edge_uses.values():
        for first_offset in range(len(uses)):
            first_face, first_uvs = uses[first_offset]
            for second_offset in range(first_offset + 1, len(uses)):
                second_face, second_uvs = uses[second_offset]
                if all(
                        vertex in second_uvs and _uv_close(uv, second_uvs[vertex])
                        for vertex, uv in first_uvs.items()):
                    union_find.union(first_face, second_face)

    root_ids = {}
    island_ids = []
    for face_index in range(face_count):
        root = union_find.find(face_index)
        if root not in root_ids:
            root_ids[root] = len(root_ids)
        island_ids.append(root_ids[root])
    return island_ids, len(root_ids)


def _uv_island_ids(mesh, uv_layer):
    edge_uses = {}
    uv_data = uv_layer.data

    for polygon in mesh.polygons:
        loop_indices = list(polygon.loop_indices)
        for offset, loop_index in enumerate(loop_indices):
            next_loop_index = loop_indices[(offset + 1) % len(loop_indices)]
            first_vertex = mesh.loops[loop_index].vertex_index
            second_vertex = mesh.loops[next_loop_index].vertex_index
            key = tuple(sorted((first_vertex, second_vertex)))
            edge_uses.setdefault(key, []).append((
                polygon.index,
                {
                    first_vertex: tuple(uv_data[loop_index].uv),
                    second_vertex: tuple(uv_data[next_loop_index].uv),
                },
            ))

    return _island_ids_from_edge_uses(len(mesh.polygons), edge_uses)


def _edit_uv_island_ids(bm, uv_layer):
    bm.faces.index_update()
    bm.verts.index_update()
    edge_uses = {}
    for face in bm.faces:
        for loop in face.loops:
            next_loop = loop.link_loop_next
            first_vertex = loop.vert.index
            second_vertex = next_loop.vert.index
            key = tuple(sorted((first_vertex, second_vertex)))
            edge_uses.setdefault(key, []).append((
                face.index,
                {
                    first_vertex: tuple(loop[uv_layer].uv),
                    second_vertex: tuple(next_loop[uv_layer].uv),
                },
            ))
    return _island_ids_from_edge_uses(len(bm.faces), edge_uses)


def _material_color(obj, material_index):
    if 0 <= material_index < len(obj.material_slots):
        material = obj.material_slots[material_index].material
        if material is not None:
            color = material.diffuse_color
            return [_round(color[0], 4), _round(color[1], 4), _round(color[2], 4)]
    return [0.45, 0.5, 0.55]


def _visibility_values(obj, face_count):
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        has_visibility = (
            bm.faces.layers.float.get(visibility.VISIBILITY_ATTRIBUTE)
            is not None
        )
    else:
        has_visibility = visibility.has_face_attribute(
            obj.data, visibility.VISIBILITY_ATTRIBUTE, 'FLOAT')
    if not has_visibility:
        return [None] * face_count
    return [
        _round(value, 6)
        for value in visibility.effective_object_visibility(obj)
    ]


def _selected_meshes(context):
    return camera_utils.selected_meshes(context)


def _mesh_export_faces(obj):
    mesh = obj.data
    if not mesh.polygons:
        raise ValueError("Object '{}' has no mesh faces".format(obj.name))
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise ValueError("Object '{}' has no active UV map".format(obj.name))
    if len(uv_layer.data) != len(mesh.loops):
        raise ValueError("Object '{}' has unavailable UV loop data".format(obj.name))

    mesh.calc_loop_triangles()
    triangles_by_face = {polygon.index: [] for polygon in mesh.polygons}
    for triangle in mesh.loop_triangles:
        polygon = mesh.polygons[triangle.polygon_index]
        triangles_by_face[polygon.index].append([
            int(loop_index - polygon.loop_start) for loop_index in triangle.loops
        ])

    local_islands, island_count = _uv_island_ids(mesh, uv_layer)
    faces = []
    for polygon in mesh.polygons:
        positions = []
        uvs = []
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            world_position = obj.matrix_world @ mesh.vertices[vertex_index].co
            positions.append([_round(value) for value in world_position])
            uv = uv_layer.data[loop_index].uv
            uvs.append([_round(uv.x), _round(uv.y)])
        faces.append({
            "index": polygon.index,
            "local_island": local_islands[polygon.index],
            "positions": positions,
            "uvs": uvs,
            "triangles": triangles_by_face[polygon.index],
            "material_index": polygon.material_index,
        })
    return faces, island_count


def _edit_export_faces(obj):
    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    if not bm.faces:
        raise ValueError("Object '{}' has no mesh faces".format(obj.name))

    mesh_uv_layer = mesh.uv_layers.active
    uv_layer = (
        bm.loops.layers.uv.get(mesh_uv_layer.name)
        if mesh_uv_layer is not None else None
    )
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        raise ValueError("Object '{}' has no active UV map".format(obj.name))

    triangles_by_face = {face.index: [] for face in bm.faces}
    for triangle in bm.calc_loop_triangles():
        face = triangle[0].face
        face_loops = list(face.loops)
        triangles_by_face[face.index].append([
            face_loops.index(loop) for loop in triangle
        ])

    local_islands, island_count = _edit_uv_island_ids(bm, uv_layer)
    faces = []
    for face in bm.faces:
        positions = []
        uvs = []
        for loop in face.loops:
            world_position = obj.matrix_world @ loop.vert.co
            positions.append([_round(value) for value in world_position])
            uv = loop[uv_layer].uv
            uvs.append([_round(uv.x), _round(uv.y)])
        faces.append({
            "index": face.index,
            "local_island": local_islands[face.index],
            "positions": positions,
            "uvs": uvs,
            "triangles": triangles_by_face[face.index],
            "material_index": face.material_index,
        })
    return faces, island_count


def build_export_data(context, objects=None):
    objects = list(objects) if objects is not None else _selected_meshes(context)
    if not objects:
        raise ValueError("Select at least one mesh object")

    exported_objects = []
    next_face_id = 0
    next_island_id = 0
    total_triangles = 0

    for obj in objects:
        local_faces, island_count = (
            _edit_export_faces(obj)
            if obj.mode == 'EDIT' else _mesh_export_faces(obj)
        )
        visibility_values = _visibility_values(obj, len(local_faces))
        material_colors = {}
        faces = []
        for local_face in local_faces:
            face_index = local_face["index"]
            material_index = local_face["material_index"]
            if material_index not in material_colors:
                material_colors[material_index] = _material_color(
                    obj, material_index)
            triangles = local_face["triangles"]
            total_triangles += len(triangles)
            faces.append({
                "id": next_face_id,
                "index": face_index,
                "island": next_island_id + local_face["local_island"],
                "positions": local_face["positions"],
                "uvs": local_face["uvs"],
                "triangles": triangles,
                "visibility": visibility_values[face_index],
                "material": material_colors[material_index],
            })
            next_face_id += 1

        exported_objects.append({
            "name": obj.name,
            "faces": faces,
        })
        next_island_id += island_count

    return {
        "format": "Visibility Aware UV Mapping Viewer",
        "format_version": 1,
        "scene": context.scene.name,
        "visibility_high": _round(
            getattr(getattr(context.scene, "vuv_settings", None), "visibility_high", 0.05), 6),
        "objects": exported_objects,
        "stats": {
            "objects": len(exported_objects),
            "faces": next_face_id,
            "triangles": total_triangles,
            "islands": next_island_id,
        },
    }


def write_mapping_viewer(filepath, data):
    template_path = os.path.join(os.path.dirname(__file__), TEMPLATE_NAME)
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    marker = "__VUV_EXPORT_DATA__"
    if template.count(marker) != 1:
        raise ValueError("Mapping viewer template has an invalid data marker")
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    document = template.replace(marker, payload)
    with open(filepath, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)


class VUV_OT_ExportMappingViewer(bpy.types.Operator, ExportHelper):
    bl_idname = "vuv.export_mapping_viewer"
    bl_label = "Export Interactive Mapping"
    bl_description = "Export selected meshes and UVs to a standalone interactive HTML viewer"
    bl_options = {'REGISTER'}

    filename_ext = ".html"
    filter_glob: StringProperty(default="*.html", options={'HIDDEN'})
    open_after_export: BoolProperty(
        name="Open After Export",
        description="Open the interactive mapping viewer in the default browser",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(_selected_meshes(context))

    def invoke(self, context, event):
        if not self.filepath:
            selected = _selected_meshes(context)
            base_name = selected[0].name if len(selected) == 1 else context.scene.name
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
            safe_name = safe_name or "UV_Mapping"
            self.filepath = safe_name + "_UV_Mapping.html"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            data = build_export_data(context)
            write_mapping_viewer(self.filepath, data)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, "Mapping export failed: {}".format(error))
            return {'CANCELLED'}

        if self.open_after_export and not bpy.app.background:
            try:
                webbrowser.open(Path(self.filepath).resolve().as_uri())
            except (OSError, ValueError):
                pass

        stats = data["stats"]
        self.report({'INFO'}, "Exported {} objects, {} faces, {} UV islands".format(
            stats["objects"], stats["faces"], stats["islands"]))
        return {'FINISHED'}
