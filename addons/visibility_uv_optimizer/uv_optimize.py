# SPDX-License-Identifier: GPL-2.0-or-later

import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import probe_analysis
from . import hard_surface
from . import small_island_cleanup
from . import uv_group_layout
from .visibility import effective_face_visibility
from .visibility import read_face_int
from .visibility import write_face_float


_LOW_VISIBILITY_SCALE = 0.01
_PLANAR_FRAME_REPAIR_NORMAL_TOLERANCE = math.radians(0.1)
_PLANAR_FRAME_REPAIR_DISTANCE_RATIO = 1.0e-5
_UV_SELECTION_ATTRIBUTES = (
    '.uv_select_vert',
    '.uv_select_edge',
    '.uv_select_face',
)


def _snapshot_face_float_attribute(mesh, name):
    attributes = getattr(mesh, 'attributes', None)
    if attributes is None:
        return None
    attribute = attributes.get(name)
    if (
            attribute is None
            or getattr(attribute, 'data_type', None) != 'FLOAT'
            or str(getattr(attribute, 'domain', '')) != 'FACE'):
        return None
    return [float(item.value) for item in attribute.data]


def _restore_face_float_attribute(mesh, name, values):
    attributes = getattr(mesh, 'attributes', None)
    if attributes is None:
        return
    attribute = attributes.get(name)
    if values is None:
        if attribute is not None:
            try:
                attributes.remove(attribute)
            except (ReferenceError, RuntimeError):
                pass
        return
    if (
            attribute is None
            or getattr(attribute, 'data_type', None) != 'FLOAT'
            or str(getattr(attribute, 'domain', '')) != 'FACE'):
        if attribute is not None:
            try:
                attributes.remove(attribute)
            except (ReferenceError, RuntimeError):
                attribute = None
        try:
            attribute = attributes.new(
                name=name, type='FLOAT', domain='FACE')
        except (RuntimeError, TypeError, ValueError):
            attribute = attributes.get(name)
    if attribute is None or len(attribute.data) != len(values):
        return
    for item, value in zip(attribute.data, values):
        item.value = float(value)


def _snapshot_uv_selection_attributes(mesh):
    attributes = getattr(mesh, 'attributes', None)
    if attributes is None:
        return {}
    result = {}
    for name in _UV_SELECTION_ATTRIBUTES:
        attribute = attributes.get(name)
        if attribute is None or getattr(attribute, 'data_type', None) != 'BOOLEAN':
            result[name] = None
            continue
        result[name] = {
            'domain': str(attribute.domain),
            'values': [bool(item.value) for item in attribute.data],
        }
    return result


def _restore_uv_selection_attributes(mesh, states):
    attributes = getattr(mesh, 'attributes', None)
    if attributes is None:
        return
    for name, state in (states or {}).items():
        attribute = attributes.get(name)
        if state is None:
            if attribute is not None:
                try:
                    attributes.remove(attribute)
                except (ReferenceError, RuntimeError):
                    pass
            continue
        domain = state.get('domain', 'CORNER')
        if (
                attribute is None
                or getattr(attribute, 'data_type', None) != 'BOOLEAN'
                or str(getattr(attribute, 'domain', '')) != domain):
            if attribute is not None:
                try:
                    attributes.remove(attribute)
                except (ReferenceError, RuntimeError):
                    attribute = None
            try:
                attribute = attributes.new(
                    name=name, type='BOOLEAN', domain=domain)
            except (RuntimeError, TypeError, ValueError):
                attribute = attributes.get(name)
        values = state.get('values', ())
        if attribute is None or len(attribute.data) != len(values):
            continue
        for item, value in zip(attribute.data, values):
            item.value = bool(value)


def _filter_operator_kwargs(operator, kwargs, operator_name=None):
    """Return kwargs shared by the installed Blender operator RNA.

    Blender 3.3 and 5.2 expose different optional UV operator properties.  The
    RNA probe keeps the shared code path conservative and is also useful to
    callers that need to report which options were accepted.
    """
    try:
        properties = operator.get_rna_type().properties
        supported = {item.identifier for item in properties}
    except (AttributeError, RuntimeError, TypeError):
        if operator_name and tuple(bpy.app.version) < (4, 0, 0):
            blender_33 = {
                'smart_project': {
                    'angle_limit', 'island_margin', 'area_weight',
                    'correct_aspect', 'scale_to_bounds',
                },
                'unwrap': {
                    'method', 'fill_holes', 'correct_aspect',
                    'use_subsurf_data', 'margin',
                },
                'average_islands_scale': {'scale_uv', 'shear'},
                'pack_islands': {'udim_source', 'rotate', 'margin'},
            }
            supported = blender_33.get(operator_name)
            if supported is not None:
                return {
                    key: value for key, value in kwargs.items()
                    if key in supported
                }
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in supported}


def _call_uv_operator(operator, _operator_name=None, **kwargs):
    return operator(**_filter_operator_kwargs(
        operator, kwargs, operator_name=_operator_name))


def _resolve_uv_usage(obj, settings):
    value = getattr(settings, "uv_usage", 'AUTO')
    if value != 'AUTO':
        return value
    name = (getattr(obj, "name", "") or "").upper()
    if 'TRIMSHEET' in name or 'TRIM_SHEET' in name:
        return 'TRIM_SHEET'
    if 'INFOATLAS' in name or 'INFO_ATLAS' in name:
        return 'INFO_ATLAS'
    tokens = [token for token in name.split('_') if token]
    if any(
            token in {'LED', 'VFX'}
            or (token.startswith('LED') and token[3:].isdigit())
            or (token.startswith('VFX') and token[3:].isdigit())
            for token in tokens):
        return 'LED'
    return 'UNIQUE'


def _resolve_initial_uv_mode(settings, original_seams, uv_usage='UNIQUE'):
    value = getattr(settings, "initial_uv_mode", 'LEGACY_SMART')
    if value != 'AUTO':
        return value
    # Non-unique texture contracts commonly contain deliberate stacking and
    # carefully placed atlas coordinates.  Re-projecting or packing them is
    # destructive, so Auto is a true no-op for these layouts.  Users can still
    # select Hard Surface or Legacy Smart explicitly when a rebuild is wanted.
    if uv_usage != 'UNIQUE':
        return 'PRESERVE_LAYOUT'
    return 'MARKED_SEAMS' if original_seams else 'HARD_SURFACE'


def _inspect_existing_layout(mesh):
    """Return island/detail data without changing the mesh or its UV layers."""
    active_layer = mesh.uv_layers.active
    if active_layer is None or len(active_layer.data) != len(mesh.loops):
        raise ValueError(
            "Preserve Layout needs a valid active UV map; choose an explicit "
            "rebuild mode to create one"
        )
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.index_update()
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.get(active_layer.name)
        if uv_layer is None:
            raise ValueError("Active UV map is unavailable in BMesh")
        # Derive chart boundaries only on this detached BMesh.  Existing Mesh
        # seam flags and every UV coordinate remain untouched.
        _derive_seams_from_uv(bm, uv_layer, set())
        _, charts = _charts(bm)
        return len(charts), _compute_face_detail(bm)
    finally:
        bm.free()


def _snapshot_mesh_uv_state(mesh):
    """Capture UV layers and seam flags without assuming every layer is valid."""
    layers = []
    loop_count = len(mesh.loops)
    for layer in mesh.uv_layers:
        try:
            if len(layer.data) != loop_count:
                continue
            layers.append((layer.name, [
                {
                    'uv': item.uv.copy(),
                    'select': bool(getattr(item, 'select', False)),
                    'select_edge': bool(getattr(item, 'select_edge', False)),
                    'pin_uv': bool(getattr(item, 'pin_uv', False)),
                }
                for item in layer.data
            ]))
        except (IndexError, ReferenceError, RuntimeError):
            continue
    active_layer = mesh.uv_layers.active if mesh.uv_layers.active else None
    try:
        render_layer = mesh.uv_layers.active_render
    except AttributeError:
        # Blender 3.3 has no active_render accessor on UV layers; the active
        # layer is the only stable layer identity available there.
        render_layer = active_layer
    return {
        'layers': layers,
        'layer_names': [name for name, _ in layers],
        'active_index': int(getattr(mesh.uv_layers, 'active_index', -1)),
        'active': active_layer.name if active_layer else None,
        'render': render_layer.name if render_layer else None,
        'seams': [bool(edge.use_seam) for edge in mesh.edges],
        'vertex_selection': [bool(vertex.select) for vertex in mesh.vertices],
        'edge_selection': [bool(edge.select) for edge in mesh.edges],
        'face_selection': [bool(face.select) for face in mesh.polygons],
        'vertex_hidden': [bool(vertex.hide) for vertex in mesh.vertices],
        'edge_hidden': [bool(edge.hide) for edge in mesh.edges],
        'face_hidden': [bool(face.hide) for face in mesh.polygons],
        'uv_selection_attributes': _snapshot_uv_selection_attributes(mesh),
        'detail_score': _snapshot_face_float_attribute(
            mesh, 'vuv_detail_score'),
        'active_face': int(getattr(mesh.polygons, 'active', -1)),
    }


def _restore_mesh_interaction_state(mesh, snapshot):
    for name, values in snapshot.get('layers', ()):
        layer = mesh.uv_layers.get(name)
        if layer is None or len(layer.data) != len(values):
            continue
        for item, state in zip(layer.data, values):
            for key in ('select', 'select_edge', 'pin_uv'):
                if hasattr(item, key):
                    setattr(item, key, bool(state.get(key, False)))
    for vertex, state in zip(
            mesh.vertices, snapshot.get('vertex_selection', ())):
        vertex.select = bool(state)
    for edge, state in zip(
            mesh.edges, snapshot.get('edge_selection', ())):
        edge.select = bool(state)
    for face, state in zip(
            mesh.polygons, snapshot.get('face_selection', ())):
        face.select = bool(state)
    for vertex, state in zip(
            mesh.vertices, snapshot.get('vertex_hidden', ())):
        vertex.hide = bool(state)
    for edge, state in zip(
            mesh.edges, snapshot.get('edge_hidden', ())):
        edge.hide = bool(state)
    for face, state in zip(
            mesh.polygons, snapshot.get('face_hidden', ())):
        face.hide = bool(state)
    active_face = int(snapshot.get('active_face', -1))
    if hasattr(mesh.polygons, 'active'):
        try:
            mesh.polygons.active = active_face
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    _restore_uv_selection_attributes(
        mesh, snapshot.get('uv_selection_attributes', {}))
    mesh.update()


def _restore_mesh_uv_state(mesh, snapshot):
    if not snapshot:
        return
    original_names = set(snapshot.get('layer_names', ()))
    # Failed Smart/Unwrap calls may have created a UV layer on an object that
    # started without one.  Remove only layers introduced after the snapshot.
    for layer in list(mesh.uv_layers):
        if layer.name not in original_names:
            try:
                mesh.uv_layers.remove(layer)
            except (ReferenceError, RuntimeError):
                pass
    for name, values in snapshot.get('layers', ()):
        layer = mesh.uv_layers.get(name)
        if layer is None or len(layer.data) != len(values):
            continue
        for item, state in zip(layer.data, values):
            item.uv = state['uv']
            for key in ('select', 'select_edge', 'pin_uv'):
                if hasattr(item, key):
                    setattr(item, key, bool(state.get(key, False)))
    for edge, state in zip(mesh.edges, snapshot.get('seams', ())):
        edge.use_seam = bool(state)
    _restore_face_float_attribute(
        mesh, 'vuv_detail_score', snapshot.get('detail_score'))
    _restore_mesh_interaction_state(mesh, snapshot)
    active = snapshot.get('active')
    render = snapshot.get('render')
    if active and mesh.uv_layers.get(active):
        mesh.uv_layers.active = mesh.uv_layers.get(active)
    active_index = int(snapshot.get('active_index', -1))
    if 0 <= active_index < len(mesh.uv_layers):
        mesh.uv_layers.active_index = active_index
    if render and mesh.uv_layers.get(render):
        try:
            mesh.uv_layers.active_render = mesh.uv_layers.get(render)
        except AttributeError:
            pass
    mesh.update()


def _restore_refine_source_state(mesh, snapshot, target_layer_name):
    """Keep copied-source UV layers and mesh seams outside a refine result."""
    if not snapshot:
        return
    has_source_layer = any(
        name != target_layer_name for name, _values in snapshot.get('layers', ())
    )
    if not has_source_layer:
        return
    for name, values in snapshot.get('layers', ()):
        if name == target_layer_name:
            continue
        layer = mesh.uv_layers.get(name)
        if layer is None or len(layer.data) != len(values):
            continue
        for item, state in zip(layer.data, values):
            item.uv = state['uv']
            for key in ('select', 'select_edge', 'pin_uv'):
                if hasattr(item, key):
                    setattr(item, key, bool(state.get(key, False)))
    for edge, state in zip(mesh.edges, snapshot.get('seams', ())):
        edge.use_seam = bool(state)
    active = mesh.uv_layers.get(target_layer_name) if target_layer_name else None
    if active is not None:
        mesh.uv_layers.active = active
    mesh.update()


@dataclass
class OptimizeResult:
    initial_islands: int
    final_islands: int
    merge_tests: int
    accepted_merges: int
    rejected_stretch: int
    rejected_overlap: int
    rejected_topology: int
    detail_values: list
    rejected_probe: int = 0
    probe_guided_merges: int = 0
    initial_uv_mode: str = 'LEGACY_SMART'
    uv_usage: str = 'UNIQUE'
    forced_cuts: int = 0
    protected_cuts: int = 0
    repaired_charts: int = 0
    mirrored_charts: int = 0
    safe_pack_retry: bool = False
    local_repair_attempted: int = 0
    local_repair_accepted: int = 0
    local_repair_rejected: int = 0
    smart_repair_input_charts: int = 0
    smart_repair_output_charts: int = 0
    small_cleanup_tests: int = 0
    small_cleanup_merges: int = 0
    small_cleanup_initial_charts: int = 0
    small_cleanup_final_charts: int = 0
    small_cleanup_summary: dict = field(default_factory=dict)
    planar_frame_repair_attempted: int = 0
    planar_frame_repair_accepted: int = 0
    planar_frame_repair_rejected: int = 0
    planar_frame_repair_summary: dict = field(default_factory=dict)
    group_layout_applied: bool = False
    group_layout_repeat_groups: int = 0
    group_layout_repeat_members: int = 0
    group_layout_small_grouped: int = 0
    group_layout_rotated_islands: int = 0
    group_layout_uniform_scale: float = 1.0
    group_layout_fallback_used: bool = False
    group_layout_summary: dict = field(default_factory=dict)
    # Semantic grouping is computed before the optional small-island display
    # boost.  Keep that exact partition available to transaction/reporting
    # callers; re-analyzing the boosted UV can otherwise reclassify borderline
    # charts and make the recorded owner mapping disagree with the layout that
    # was actually executed.
    group_layout_analysis: dict = field(default_factory=dict)


def _set_uv_selection(bm, uv_layer, selected_faces):
    selected = set(selected_faces)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        state = face.index in selected
        face.select_set(state)
        if state:
            for vertex in face.verts:
                vertex.select_set(True)
            for edge in face.edges:
                edge.select_set(True)
        for loop in face.loops:
            if hasattr(loop, "uv_select_vert_set") and hasattr(loop, "uv_select_edge_set"):
                loop.uv_select_vert_set(state)
                loop.uv_select_edge_set(state)
            else:
                loop[uv_layer].select = state
                loop[uv_layer].select_edge = state
    if hasattr(bm, "uv_select_flush_mode"):
        bm.uv_select_flush_mode()


def _select_all_uvs_for_operator(mesh, bm, uv_layer):
    """Synchronize whole-layout selection before a global UV operator.

    In Blender 5.x, setting every BMLoop UV selection flag is not sufficient
    in every UV selection mode.  Pack Islands can report FINISHED while leaving
    some islands untouched.  Running the UV selection operator after flushing
    the BMesh state makes the intended whole-layout scope explicit in both 3.3
    and 5.x.  The public optimizer isolates one active mesh before entering
    Edit Mode; callers using this helper directly must provide the same scope.
    """
    _set_uv_selection(bm, uv_layer, (face.index for face in bm.faces))
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    result = bpy.ops.uv.select_all(action='SELECT')
    if 'FINISHED' not in result:
        raise RuntimeError("Select All UVs did not finish")
    return _refresh_edit_bmesh(mesh)


def _refresh_edit_bmesh(mesh):
    """Return current edit BMesh handles after a UV operator call."""
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    return bm, bm.loops.layers.uv.verify()


def _set_edit_mesh_hidden(bm, hidden=False):
    """Set element hide flags without changing topology or UV data."""
    for vertex in bm.verts:
        vertex.hide = bool(hidden)
    for edge in bm.edges:
        edge.hide = bool(hidden)
    for face in bm.faces:
        face.hide = bool(hidden)


def _restore_edit_mesh_hidden(bm, snapshot):
    """Restore element visibility captured before entering Edit Mode."""
    for vertex, state in zip(
            bm.verts, snapshot.get('vertex_hidden', ())):
        vertex.hide = bool(state)
    for edge, state in zip(
            bm.edges, snapshot.get('edge_hidden', ())):
        edge.hide = bool(state)
    for face, state in zip(
            bm.faces, snapshot.get('face_hidden', ())):
        face.hide = bool(state)


def _uv_at_vertex(face, vertex, uv_layer):
    for loop in face.loops:
        if loop.vert == vertex:
            return loop[uv_layer].uv
    raise RuntimeError("Face does not contain edge vertex")


def _derive_seams_from_uv(
        bm, uv_layer, locked_seams, forced_seams=None, preserve_boundaries=False):
    forced_seams = set(forced_seams or ())
    for edge in bm.edges:
        if edge.index in locked_seams or edge.index in forced_seams:
            edge.seam = True
            continue
        if len(edge.link_faces) != 2:
            # Boundary edges are real chart boundaries even though there is no
            # pair of UV loops to compare.  Legacy mode keeps the historical
            # flag behavior; hard-surface mode records them explicitly.
            edge.seam = bool(preserve_boundaries)
            continue
        face_a, face_b = edge.link_faces
        discontinuous = False
        for vertex in edge.verts:
            uv_a = _uv_at_vertex(face_a, vertex, uv_layer)
            uv_b = _uv_at_vertex(face_b, vertex, uv_layer)
            if (uv_a - uv_b).length_squared > 1e-12:
                discontinuous = True
                break
        edge.seam = discontinuous


def _reference_uv_boundary_edges(
        mesh, layer_names, mode='UNION', epsilon=1.0e-12):
    """Return discontinuity edges from one or more UV layers.

    The returned set is only guidance.  Callers may seed these edges as
    temporary seams before unwrap, while keeping them mergeable afterward.
    Missing/blank layer names are ignored so the normal optimizer remains
    usable on meshes without reference maps.
    """
    names = []
    for value in layer_names or ():
        name = str(value).strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return set()
    layers = [mesh.uv_layers.get(name) for name in names]
    if any(layer is None for layer in layers):
        return set()
    edge_faces = {int(edge.index): [] for edge in mesh.edges}
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            edge_faces[int(mesh.loops[loop_index].edge_index)].append(
                (int(polygon.index), int(loop_index)))
    per_layer = [set() for _layer in layers]
    for edge_index, records in edge_faces.items():
        if len(records) != 2:
            continue
        face_a, _ = records[0]
        face_b, _ = records[1]
        vertices = set(mesh.edges[edge_index].vertices)
        for layer_index, layer in enumerate(layers):
            values_a = {}
            values_b = {}
            for loop_index in mesh.polygons[face_a].loop_indices:
                vertex = mesh.loops[loop_index].vertex_index
                if vertex in vertices:
                    values_a[vertex] = layer.data[loop_index].uv
            for loop_index in mesh.polygons[face_b].loop_indices:
                vertex = mesh.loops[loop_index].vertex_index
                if vertex in vertices:
                    values_b[vertex] = layer.data[loop_index].uv
            if any(
                vertex not in values_a or vertex not in values_b or
                (values_a[vertex] - values_b[vertex]).length_squared > epsilon
                for vertex in vertices
            ):
                per_layer[layer_index].add(edge_index)
                break
    if not per_layer:
        return set()
    if str(mode or 'UNION').upper() == 'INTERSECTION':
        return set.intersection(*per_layer)
    return set.union(*per_layer)


def _reference_uv_layer_names(settings):
    """Parse the comma/semicolon separated reference layer setting."""
    value = getattr(settings, 'reference_uv_layers', '')
    if not value:
        return ()
    return tuple(
        name.strip() for name in str(value).replace(';', ',').split(',')
        if name.strip()
    )


def _charts(bm):
    chart_ids = [-1] * len(bm.faces)
    charts = []
    for face in bm.faces:
        if chart_ids[face.index] >= 0:
            continue
        chart_index = len(charts)
        pending = [face]
        chart = []
        chart_ids[face.index] = chart_index
        while pending:
            current = pending.pop()
            chart.append(current)
            for edge in current.edges:
                if edge.seam:
                    continue
                for neighbor in edge.link_faces:
                    if chart_ids[neighbor.index] < 0:
                        chart_ids[neighbor.index] = chart_index
                        pending.append(neighbor)
        charts.append(chart)
    return chart_ids, charts


def _uv_charts(bm, uv_layer):
    """Build charts from actual UV continuity, independent of seam flags."""
    chart_ids = [-1] * len(bm.faces)
    charts = []
    for face in bm.faces:
        if chart_ids[face.index] >= 0:
            continue
        chart_index = len(charts)
        chart_ids[face.index] = chart_index
        pending = [face]
        chart = []
        while pending:
            current = pending.pop()
            chart.append(current)
            for edge in current.edges:
                for neighbor in edge.link_faces:
                    if neighbor == current or chart_ids[neighbor.index] >= 0:
                        continue
                    continuous = all(
                        (_uv_at_vertex(current, vertex, uv_layer)
                         - _uv_at_vertex(neighbor, vertex, uv_layer)).length_squared
                        <= 1.0e-12
                        for vertex in edge.verts
                    )
                    if continuous:
                        chart_ids[neighbor.index] = chart_index
                        pending.append(neighbor)
        charts.append(chart)
    return chart_ids, charts


def _chart_area(chart):
    return sum(face.calc_area() for face in chart)


def _compute_face_detail(bm):
    """Estimate local signal/detail complexity from weighted normal variation."""
    values = [0.0] * len(bm.faces)
    bm.faces.index_update()
    for face in bm.faces:
        total_length = 0.0
        variation = 0.0
        for edge in face.edges:
            length = max(edge.calc_length(), 1e-12)
            neighbors = [neighbor for neighbor in edge.link_faces if neighbor != face]
            if not neighbors:
                continue
            angle = edge.calc_face_angle(0.0)
            variation += angle * length
            total_length += length
        if total_length > 1e-12:
            values[face.index] = min(variation / total_length / math.pi, 1.0)
    maximum = max(values, default=0.0)
    if maximum > 1e-12:
        values = [value / maximum for value in values]
    return values


def _boundary_alignment(boundary):
    directions = []
    for edge in boundary:
        direction = edge.verts[1].co - edge.verts[0].co
        if direction.length_squared > 1e-12:
            directions.append(direction.normalized())
    if len(directions) < 2:
        return 0.0
    reference = directions[0]
    return sum(abs(reference.dot(direction)) for direction in directions[1:]) / (len(directions) - 1)


def _developable_band_bonus(chart_left, chart_right, boundary, settings):
    if not settings.developable_enabled:
        return 0.0
    total_length = sum(max(edge.calc_length(), 1e-12) for edge in boundary)
    if total_length <= 1e-12:
        return 0.0
    average_angle = sum(
        edge.calc_face_angle(0.0) * max(edge.calc_length(), 1e-12)
        for edge in boundary
    ) / total_length
    if average_angle > settings.developable_angle:
        return 0.0
    alignment = _boundary_alignment(boundary)
    if alignment < 0.72:
        return 0.0
    combined = chart_left + chart_right
    normal_sum = sum((face.normal for face in combined), Vector((0.0, 0.0, 0.0)))
    if normal_sum.length_squared <= 1e-12:
        return 0.0
    average_normal = normal_sum.normalized()
    spread = sum(
        face.normal.angle(average_normal) * max(face.calc_area(), 1e-12)
        for face in combined
    ) / max(sum(face.calc_area() for face in combined), 1e-12)
    # A long, directionally coherent band with moderate normal spread is
    # characteristic of a cylinder, cone, or bevel strip.
    spread_factor = max(0.0, 1.0 - spread / math.pi)
    angle_factor = max(0.0, 1.0 - average_angle / max(settings.developable_angle, 1e-8))
    return settings.developable_bonus * alignment * spread_factor * angle_factor


def _boundary_seam_cost(boundary, visibility, settings):
    """Estimate how objectionable it is to leave this boundary as a seam."""
    total_length = sum(max(edge.calc_length(), 1e-12) for edge in boundary)
    if total_length <= 1e-12:
        return 0.0
    cost = 0.0
    for edge in boundary:
        length = max(edge.calc_length(), 1e-12)
        faces = list(edge.link_faces)
        face_visibility = max(
            (visibility[face.index] for face in faces if face.index < len(visibility)),
            default=0.0,
        )
        edge_cost = face_visibility * settings.seam_visibility_weight
        if not edge.smooth:
            edge_cost -= settings.seam_hard_edge_bonus
        if len(faces) == 2 and not edge.is_convex:
            edge_cost -= settings.seam_concave_bonus
        cost += edge_cost * length
    return cost / total_length


def _is_disk(faces):
    """Check connected disk topology, including a single boundary loop.

    Euler characteristic alone accepts disconnected patches and some
    multi-boundary/non-manifold configurations.  Merge candidates must be a
    connected orientable disk so an unwrap failure cannot silently weld a hole
    or a second shell into a chart.
    """
    faces = list(faces)
    if not faces:
        return False
    selected = {face.index: face for face in faces}
    pending = [faces[0]]
    visited = {faces[0].index}
    while pending:
        face = pending.pop()
        for edge in face.edges:
            linked = [neighbor for neighbor in edge.link_faces if neighbor.index in selected]
            if len(linked) > 2:
                return False
            for neighbor in linked:
                if neighbor.index not in visited:
                    visited.add(neighbor.index)
                    pending.append(neighbor)
    if len(visited) != len(selected):
        return False
    vertices = {vertex.index for face in faces for vertex in face.verts}
    edges = {edge.index: edge for face in faces for edge in face.edges}
    boundary = {
        index: edge for index, edge in edges.items()
        if sum(1 for linked in edge.link_faces if linked.index in selected) == 1
    }
    if not boundary:
        return False
    boundary_degree = {}
    for edge in boundary.values():
        for vertex in edge.verts:
            boundary_degree[vertex.index] = boundary_degree.get(vertex.index, 0) + 1
    if any(value != 2 for value in boundary_degree.values()):
        return False
    pending_edges = [next(iter(boundary.values()))]
    boundary_seen = {pending_edges[0].index}
    while pending_edges:
        edge = pending_edges.pop()
        edge_vertices = {vertex.index for vertex in edge.verts}
        for other in boundary.values():
            if other.index in boundary_seen:
                continue
            if edge_vertices.intersection(vertex.index for vertex in other.verts):
                boundary_seen.add(other.index)
                pending_edges.append(other)
    if len(boundary_seen) != len(boundary):
        return False
    return len(vertices) - len(edges) + len(faces) == 1


def _triangle_data(faces, uv_layer, bm=None):
    faces = list(faces)
    if bm is not None:
        selected = {face.index for face in faces}
        triangles = []
        for tri_loops in bm.calc_loop_triangles():
            if not tri_loops:
                continue
            face = tri_loops[0].face
            if face.index not in selected:
                continue
            points = tuple(loop.vert.co.copy() for loop in tri_loops)
            uvs = tuple(loop[uv_layer].uv.copy() for loop in tri_loops)
            triangles.append((face.index, points, uvs))
        return triangles

    triangles = []
    for face in faces:
        loops = list(face.loops)
        if len(loops) < 3:
            continue
        coordinates = [loop.vert.co.copy() for loop in loops]
        tessellated = tessellate_polygon([coordinates])
        if not tessellated:
            tessellated = [
                (0, index, index + 1)
                for index in range(1, len(loops) - 1)
            ]
        for triangle in tessellated:
            if isinstance(triangle[0], int):
                indices = triangle
            else:
                indices = tuple(min(
                    range(len(coordinates)),
                    key=lambda index: (coordinates[index] - point).length_squared,
                ) for point in triangle)
            tri_loops = tuple(loops[index] for index in indices)
            points = tuple(loop.vert.co.copy() for loop in tri_loops)
            uvs = tuple(loop[uv_layer].uv.copy() for loop in tri_loops)
            triangles.append((face.index, points, uvs))
    return triangles


def _triangle_stretch(points, uvs):
    p0, p1, p2 = points
    e1 = p1 - p0
    e2 = p2 - p0
    x = e1.length
    if x <= 1e-12:
        return math.inf, 0.0, 0.0
    axis = e1 / x
    sx = e2.dot(axis)
    sy_squared = max(e2.length_squared - sx * sx, 0.0)
    sy = math.sqrt(sy_squared)
    if sy <= 1e-12:
        return math.inf, 0.0, 0.0

    du1 = uvs[1] - uvs[0]
    du2 = uvs[2] - uvs[0]
    j00 = du1.x / x
    j01 = (du2.x - du1.x * sx / x) / sy
    j10 = du1.y / x
    j11 = (du2.y - du1.y * sx / x) / sy
    det = j00 * j11 - j01 * j10

    a = j00 * j00 + j10 * j10
    b = j00 * j01 + j10 * j11
    d = j01 * j01 + j11 * j11
    trace = a + d
    root = math.sqrt(max((a - d) * (a - d) + 4.0 * b * b, 0.0))
    lambda_max = max((trace + root) * 0.5, 0.0)
    lambda_min = max((trace - root) * 0.5, 0.0)
    if lambda_min <= 1e-18:
        stretch = math.inf
    else:
        stretch = math.sqrt(lambda_max / lambda_min)
    area = e1.cross(e2).length * 0.5
    return stretch, area, det


def _weighted_percentile(values, percentile):
    if not values:
        return math.inf
    ordered = sorted(values, key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    if total <= 1e-18:
        return math.inf
    target = total * percentile
    accumulated = 0.0
    for value, weight in ordered:
        accumulated += weight
        if accumulated >= target:
            return value
    return ordered[-1][0]


def _measure_distortion(triangles):
    values = []
    maximum = 1.0
    has_positive = False
    has_negative = False
    degenerate = False
    for _, points, uvs in triangles:
        stretch, area, determinant = _triangle_stretch(points, uvs)
        values.append((stretch, max(area, 1e-18)))
        maximum = max(maximum, stretch)
        if determinant > 1e-12:
            has_positive = True
        elif determinant < -1e-12:
            has_negative = True
        else:
            degenerate = True
    invalid_winding = degenerate or has_negative or not has_positive
    return _weighted_percentile(values, 0.95), maximum, invalid_winding


def _positive_triangle_overlap(uv_a, uv_b, epsilon=1e-7):
    for triangle in (uv_a, uv_b):
        for index in range(3):
            edge = triangle[(index + 1) % 3] - triangle[index]
            axis = Vector((-edge.y, edge.x))
            if axis.length_squared <= epsilon * epsilon:
                continue
            values_a = [point.dot(axis) for point in uv_a]
            values_b = [point.dot(axis) for point in uv_b]
            overlap = min(max(values_a), max(values_b)) - max(min(values_a), min(values_b))
            if overlap <= epsilon * axis.length:
                return False
    return True


def _has_overlap(triangles, ignore_same_face=True):
    epsilon = 1e-7
    records = []
    for face_index, _, uvs in triangles:
        minimum_x = min(uv.x for uv in uvs)
        maximum_x = max(uv.x for uv in uvs)
        minimum_y = min(uv.y for uv in uvs)
        maximum_y = max(uv.y for uv in uvs)
        records.append((minimum_x, maximum_x, minimum_y, maximum_y, face_index, uvs))
    records.sort(key=lambda item: item[0])
    active = []
    for record in records:
        minimum_x, maximum_x, minimum_y, maximum_y, face_index, uvs = record
        active = [item for item in active if item[1] > minimum_x + epsilon]
        for other in active:
            if ignore_same_face and face_index == other[4]:
                continue
            if min(maximum_y, other[3]) - max(minimum_y, other[2]) <= epsilon:
                continue
            if _positive_triangle_overlap(uvs, other[5]):
                return True
        active.append(record)
    return False


def _triangle_orientation(points, uvs):
    edge_1 = points[1] - points[0]
    edge_2 = points[2] - points[0]
    edge_3 = points[2] - points[1]
    geometry_scale = max(
        edge_1.length_squared,
        edge_2.length_squared,
        edge_3.length_squared,
        1.0e-30,
    )
    geometry_cross = edge_1.cross(edge_2).length
    if geometry_cross <= max(geometry_scale * 1.0e-12, 1.0e-24):
        return 'source_degenerate'

    uv_edge_1 = uvs[1] - uvs[0]
    uv_edge_2 = uvs[2] - uvs[0]
    uv_edge_3 = uvs[2] - uvs[1]
    uv_scale = max(
        uv_edge_1.length_squared,
        uv_edge_2.length_squared,
        uv_edge_3.length_squared,
        1.0e-30,
    )
    signed_uv_area_2 = (
        uv_edge_1.x * uv_edge_2.y - uv_edge_1.y * uv_edge_2.x)
    if abs(signed_uv_area_2) <= max(uv_scale * 1.0e-12, 1.0e-24):
        return 'uv_degenerate'
    return 'positive' if signed_uv_area_2 > 0.0 else 'negative'


def _winding_counts(triangles):
    counts = {
        'positive': 0,
        'negative': 0,
        'degenerate': 0,
        'source_degenerate': 0,
        'uv_degenerate': 0,
    }
    for _, points, uvs in triangles:
        orientation = _triangle_orientation(points, uvs)
        if orientation in {'positive', 'negative'}:
            counts[orientation] += 1
        else:
            counts['degenerate'] += 1
            counts[orientation] += 1
    return counts


def _classify_problem_charts(charts, uv_layer, bm=None):
    """Return charts needing re-projection and consistently mirrored charts."""
    triangles_by_face = defaultdict(list)
    if bm is not None:
        all_faces = [face for chart in charts for face in chart]
        for triangle in _triangle_data(all_faces, uv_layer, bm=bm):
            triangles_by_face[triangle[0]].append(triangle)
    problem = []
    mirrored = []
    for chart in charts:
        if bm is None:
            triangles = _triangle_data(chart, uv_layer)
        else:
            triangles = [
                triangle
                for face in chart
                for triangle in triangles_by_face.get(face.index, ())
            ]
        finite = all(
            math.isfinite(value)
            for _, _, uvs in triangles
            for uv in uvs for value in (uv.x, uv.y)
        )
        counts = _winding_counts(triangles)
        positive = counts['positive']
        negative = counts['negative']
        invalid = (
            not triangles
            or not finite
            or counts['degenerate'] > 0
            or (positive > 0 and negative > 0)
            or _has_overlap(triangles, ignore_same_face=False)
        )
        if invalid:
            problem.append(chart)
        elif negative > 0 and positive == 0:
            mirrored.append(chart)
    return problem, mirrored


def _mirror_charts_u(charts, uv_layer):
    for chart in charts:
        loops = [loop for face in chart for loop in face.loops]
        if not loops:
            continue
        minimum_u = min(loop[uv_layer].uv.x for loop in loops)
        maximum_u = max(loop[uv_layer].uv.x for loop in loops)
        center_u = (minimum_u + maximum_u) * 0.5
        for loop in loops:
            loop[uv_layer].uv.x = 2.0 * center_u - loop[uv_layer].uv.x


def _audit_unique_layout(bm, uv_layer, epsilon=1.0e-6):
    points = [loop[uv_layer].uv for face in bm.faces for loop in face.loops]
    finite = bool(points) and all(
        math.isfinite(point.x) and math.isfinite(point.y) for point in points
    )
    triangles = _triangle_data(list(bm.faces), uv_layer, bm=bm) if finite else []
    counts = _winding_counts(triangles)
    inside_tile = finite and all(
        -epsilon <= value <= 1.0 + epsilon
        for point in points for value in (point.x, point.y)
    )
    return {
        'triangles': len(triangles),
        'positive': counts['positive'],
        'negative': counts['negative'],
        'degenerate': counts['degenerate'],
        'source_degenerate': counts['source_degenerate'],
        'uv_degenerate': counts['uv_degenerate'],
        'finite': finite,
        'inside_tile': inside_tile,
        'overlap': finite and (
            _has_overlap(triangles, ignore_same_face=False)
            if triangles else False
        ),
    }


def _unique_layout_error(audit):
    if not audit['finite']:
        return "Unique UV quality gate: non-finite coordinate"
    if not audit['inside_tile']:
        return "Unique UV quality gate: coordinates outside the 0-1 tile"
    if audit['source_degenerate']:
        return (
            "Unique UV quality gate: source mesh has {} degenerate triangles; "
            "clean the geometry before unwrapping"
        ).format(audit['source_degenerate'])
    if audit['uv_degenerate']:
        return "Unique UV quality gate: {} UV-degenerate triangles".format(
            audit['uv_degenerate'])
    if audit['negative']:
        return "Unique UV quality gate: {} flipped triangles".format(
            audit['negative'])
    if not audit['positive']:
        return "Unique UV quality gate: no positive-area triangles"
    if audit['overlap']:
        return "Unique UV quality gate: positive-area overlap"
    return None


def _save_uv(faces, uv_layer):
    return {
        face.index: [loop[uv_layer].uv.copy() for loop in face.loops]
        for face in faces
    }


def _restore_uv(faces, uv_layer, saved):
    for face in faces:
        for loop, uv in zip(face.loops, saved[face.index]):
            loop[uv_layer].uv = uv


def _uv_charts_for_faces(faces, uv_layer):
    """Build UV-continuous charts without traversing outside ``faces``."""

    selected = {face.index: face for face in faces}
    visited = set()
    charts = []
    for start in sorted(selected.values(), key=lambda face: face.index):
        if start.index in visited:
            continue
        visited.add(start.index)
        pending = [start]
        chart = []
        while pending:
            current = pending.pop()
            chart.append(current)
            for edge in current.edges:
                for neighbor in edge.link_faces:
                    if (
                            neighbor.index not in selected
                            or neighbor.index in visited):
                        continue
                    continuous = all(
                        (_uv_at_vertex(current, vertex, uv_layer)
                         - _uv_at_vertex(neighbor, vertex, uv_layer)).length_squared
                        <= 1.0e-12
                        for vertex in edge.verts
                    )
                    if continuous:
                        visited.add(neighbor.index)
                        pending.append(neighbor)
        charts.append(chart)
    return charts


def _validate_local_repair(bm, uv_layer, faces):
    charts = _uv_charts_for_faces(faces, uv_layer)
    problem, mirrored = _classify_problem_charts(
        charts, uv_layer, bm=bm)
    if problem:
        return False, 0
    mirrored_count = len(mirrored)
    if mirrored:
        _mirror_charts_u(mirrored, uv_layer)
        problem, mirrored = _classify_problem_charts(
            charts, uv_layer, bm=bm)
    return not problem and not mirrored, mirrored_count


def _first_overlapping_face_pair(triangles, epsilon=1.0e-7):
    """Return one positive-area overlap pair from a local UV chart."""

    records = []
    for face_index, _, uvs in triangles:
        records.append((
            min(uv.x for uv in uvs),
            max(uv.x for uv in uvs),
            min(uv.y for uv in uvs),
            max(uv.y for uv in uvs),
            face_index,
            uvs,
        ))
    records.sort(key=lambda item: item[0])
    active = []
    for record in records:
        minimum_x, maximum_x, minimum_y, maximum_y, face_index, uvs = record
        active = [item for item in active if item[1] > minimum_x + epsilon]
        for other in active:
            if min(maximum_y, other[3]) - max(minimum_y, other[2]) <= epsilon:
                continue
            if _positive_triangle_overlap(uvs, other[5], epsilon=epsilon):
                return face_index, other[4]
        active.append(record)
    return None


def _chart_dual_path_edges(faces, start_index, end_index):
    """Find the uncut dual-graph path between two faces in one chart."""

    selected = {face.index: face for face in faces}
    pending = [start_index]
    previous = {start_index: (None, None)}
    while pending:
        current_index = pending.pop(0)
        if current_index == end_index:
            break
        for edge in selected[current_index].edges:
            if edge.seam:
                continue
            for neighbor in edge.link_faces:
                if (
                        neighbor.index not in selected
                        or neighbor.index in previous
                        or neighbor.index == current_index):
                    continue
                previous[neighbor.index] = (current_index, edge.index)
                pending.append(neighbor.index)
    if end_index not in previous:
        return []
    path = []
    current_index = end_index
    while previous[current_index][0] is not None:
        parent_index, edge_index = previous[current_index]
        path.append(edge_index)
        current_index = parent_index
    path.reverse()
    return path


def _restore_chart_trial(
        mesh, face_indices, saved_uv, saved_seams, forced_cuts, saved_forced):
    bm, uv_layer = _refresh_edit_bmesh(mesh)
    faces = [bm.faces[index] for index in face_indices]
    _restore_uv(faces, uv_layer, saved_uv)
    for edge_index, seam in saved_seams.items():
        bm.edges[edge_index].seam = seam
    forced_cuts.clear()
    forced_cuts.update(saved_forced)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    return bm, uv_layer


def _capture_chart_candidate(
        bm, uv_layer, faces, forced_cuts, kind, angle=None):
    valid, mirrored_count = _validate_local_repair(
        bm, uv_layer, faces)
    if not valid:
        return None
    local_charts = _uv_charts_for_faces(faces, uv_layer)
    triangles = _triangle_data(faces, uv_layer, bm=bm)
    p95, maximum, _flipped = _measure_distortion(triangles)
    chart_edges = {
        edge.index: edge for face in faces for edge in face.edges
    }
    return {
        'kind': kind,
        'angle': angle,
        'chart_count': len(local_charts),
        'p95': p95,
        'maximum': maximum,
        'mirrored_count': mirrored_count,
        'uv': _save_uv(faces, uv_layer),
        'seams': {
            index: bool(edge.seam) for index, edge in chart_edges.items()
        },
        'forced': set(forced_cuts),
    }


def _bounded_problem_chart_repairs(
        mesh, bm, uv_layer, problem_charts, settings, forced_cuts,
        chart_budget):
    """Repair invalid charts independently and keep the least fragmented result."""

    face_groups = [
        tuple(sorted(face.index for face in chart))
        for chart in problem_charts
    ]
    mirrored_total = 0
    output_charts = 0
    fallback_angles = []
    for angle in (
            float(settings.smart_angle),
            math.radians(80.0),
            math.radians(85.0),
            math.radians(89.0)):
        angle = min(max(angle, math.radians(1.0)), math.radians(89.0))
        if not any(abs(angle - current) <= 1.0e-9
                   for current in fallback_angles):
            fallback_angles.append(angle)

    for face_indices in face_groups:
        bm, uv_layer = _refresh_edit_bmesh(mesh)
        faces = [bm.faces[index] for index in face_indices]
        saved_uv = _save_uv(faces, uv_layer)
        chart_edges = {
            edge.index: edge for face in faces for edge in face.edges
        }
        saved_seams = {
            index: bool(edge.seam) for index, edge in chart_edges.items()
        }
        saved_forced = set(forced_cuts)
        candidates = []

        for angle in fallback_angles:
            bm, uv_layer = _restore_chart_trial(
                mesh,
                face_indices,
                saved_uv,
                saved_seams,
                forced_cuts,
                saved_forced,
            )
            _set_uv_selection(bm, uv_layer, face_indices)
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
            try:
                result = _call_uv_operator(
                    bpy.ops.uv.smart_project,
                    _operator_name='smart_project',
                    angle_limit=angle,
                    island_margin=settings.island_margin,
                    area_weight=0.0,
                    correct_aspect=True,
                    scale_to_bounds=False,
                    preserve_seams=True,
                )
            except (ReferenceError, RuntimeError, TypeError, ValueError):
                continue
            if 'FINISHED' not in result:
                continue
            bm, uv_layer = _refresh_edit_bmesh(mesh)
            faces = [bm.faces[index] for index in face_indices]
            candidate = _capture_chart_candidate(
                bm, uv_layer, faces, forced_cuts, 'SMART', angle=angle)
            if candidate is not None and candidate['chart_count'] <= chart_budget:
                candidates.append(candidate)

        # Blender can reject a small selected region when inherited seam
        # flags describe a non-manifold local boundary.  Try one relaxed
        # projection only after all seam-preserving candidates were tested;
        # the candidate still has to pass the same strict local audit.
        if not candidates:
            bm, uv_layer = _restore_chart_trial(
                mesh,
                face_indices,
                saved_uv,
                saved_seams,
                forced_cuts,
                saved_forced,
            )
            for edge in bm.edges:
                if edge.index in chart_edges and edge.index not in saved_forced:
                    edge.seam = False
            _set_uv_selection(bm, uv_layer, face_indices)
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
            try:
                result = _call_uv_operator(
                    bpy.ops.uv.smart_project,
                    _operator_name='smart_project_relaxed',
                    angle_limit=min(max(float(settings.smart_angle), math.radians(1.0)), math.radians(89.0)),
                    island_margin=settings.island_margin,
                    area_weight=0.0,
                    correct_aspect=True,
                    scale_to_bounds=False,
                    preserve_seams=False,
                )
            except (ReferenceError, RuntimeError, TypeError, ValueError):
                result = ()
            if 'FINISHED' in result:
                bm, uv_layer = _refresh_edit_bmesh(mesh)
                faces = [bm.faces[index] for index in face_indices]
                candidate = _capture_chart_candidate(
                    bm, uv_layer, faces, forced_cuts, 'SMART_RELAXED',
                    angle=float(settings.smart_angle))
                if candidate is not None and candidate['chart_count'] <= chart_budget:
                    candidates.append(candidate)

        best_smart_count = min(
            (candidate['chart_count'] for candidate in candidates),
            default=chart_budget,
        )
        tree_limit = min(max(int(best_smart_count), 1), int(chart_budget))
        bm, uv_layer = _restore_chart_trial(
            mesh,
            face_indices,
            saved_uv,
            saved_seams,
            forced_cuts,
            saved_forced,
        )
        faces = [bm.faces[index] for index in face_indices]
        forced_cuts.update(_add_chart_tree_cuts(faces))
        for _attempt in range(tree_limit):
            bm, uv_layer = _refresh_edit_bmesh(mesh)
            _set_uv_selection(bm, uv_layer, face_indices)
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
            try:
                result = _call_uv_operator(
                    bpy.ops.uv.unwrap,
                    _operator_name='unwrap',
                    method='ANGLE_BASED',
                    fill_holes=True,
                    correct_aspect=True,
                    use_subsurf_data=False,
                    margin=settings.island_margin,
                )
            except (ReferenceError, RuntimeError, TypeError, ValueError):
                break
            bm, uv_layer = _refresh_edit_bmesh(mesh)
            faces = [bm.faces[index] for index in face_indices]
            local_charts = _uv_charts_for_faces(faces, uv_layer)
            if 'FINISHED' in result:
                candidate = _capture_chart_candidate(
                    bm, uv_layer, faces, forced_cuts, 'TREE_SPLIT')
                if candidate is not None:
                    candidates.append(candidate)
                    break
            if len(local_charts) >= tree_limit:
                break
            cut_edge_index = None
            for local_chart in local_charts:
                local_problem, _local_mirrored = _classify_problem_charts(
                    [local_chart], uv_layer, bm=bm)
                if not local_problem:
                    continue
                overlap_pair = _first_overlapping_face_pair(
                    _triangle_data(local_chart, uv_layer, bm=bm))
                if (
                        overlap_pair is None
                        or overlap_pair[0] == overlap_pair[1]):
                    continue
                path = _chart_dual_path_edges(
                    local_chart, overlap_pair[0], overlap_pair[1])
                if path:
                    cut_edge_index = path[len(path) // 2]
                    break
            if cut_edge_index is None:
                break
            bm.edges[cut_edge_index].seam = True
            forced_cuts.add(cut_edge_index)
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)

        if not candidates:
            _restore_chart_trial(
                mesh,
                face_indices,
                saved_uv,
                saved_seams,
                forced_cuts,
                saved_forced,
            )
            raise RuntimeError(
                "Unique UV bounded repair could not repair chart with {} faces".format(
                    len(face_indices)))

        best = min(candidates, key=lambda candidate: (
            candidate['chart_count'],
            candidate['p95'],
            candidate['maximum'],
            candidate['kind'] != 'TREE_SPLIT',
        ))
        bm, uv_layer = _restore_chart_trial(
            mesh,
            face_indices,
            best['uv'],
            best['seams'],
            forced_cuts,
            best['forced'],
        )
        mirrored_total += best['mirrored_count']
        output_charts += best['chart_count']

    return bm, uv_layer, mirrored_total, output_charts


def _add_chart_tree_cuts(faces):
    """Cut dual-graph cycles while retaining a connected face-tree unwrap."""

    selected = {face.index: face for face in faces}
    retained_edges = set()
    visited = set()
    for start in sorted(selected.values(), key=lambda face: face.index):
        if start.index in visited:
            continue
        visited.add(start.index)
        pending = [start]
        while pending:
            current = pending.pop()
            for edge in sorted(current.edges, key=lambda item: item.index):
                if edge.seam or len(edge.link_faces) != 2:
                    continue
                neighbors = [
                    face for face in edge.link_faces
                    if face.index in selected and face.index != current.index
                ]
                if len(neighbors) != 1:
                    continue
                neighbor = neighbors[0]
                if neighbor.index in visited:
                    continue
                retained_edges.add(edge.index)
                visited.add(neighbor.index)
                pending.append(neighbor)

    edges = {
        edge.index: edge
        for face in selected.values()
        for edge in face.edges
    }
    forced = set()
    for edge in edges.values():
        linked_inside = sum(
            face.index in selected for face in edge.link_faces)
        if (
                len(edge.link_faces) == 2
                and linked_inside == 2
                and edge.index in retained_edges):
            continue
        edge.seam = True
        if len(edge.link_faces) >= 2:
            forced.add(edge.index)
    return forced


def _signed_uv_area_2(points):
    edge_a = points[1] - points[0]
    edge_b = points[2] - points[0]
    return edge_a.x * edge_b.y - edge_a.y * edge_b.x


def _welded_chart_vertex_loops(chart, start_loop, uv_layer):
    """Return one UV-welded vertex fan without crossing a chart cut."""

    selected = {face.index: face for face in chart}
    vertex = start_loop.vert
    visited = {start_loop.face.index}
    pending = [start_loop.face]
    while pending:
        face = pending.pop()
        for edge in face.edges:
            if (
                    vertex not in edge.verts
                    or edge.seam
                    or len(edge.link_faces) != 2):
                continue
            neighbors = [
                linked for linked in edge.link_faces
                if linked.index in selected and linked.index != face.index
            ]
            if len(neighbors) != 1:
                continue
            neighbor = neighbors[0]
            if neighbor.index in visited:
                continue
            continuous = all(
                (_uv_at_vertex(face, edge_vertex, uv_layer)
                 - _uv_at_vertex(neighbor, edge_vertex, uv_layer)).length_squared
                <= 1.0e-12
                for edge_vertex in edge.verts
            )
            if not continuous:
                continue
            visited.add(neighbor.index)
            pending.append(neighbor)

    return [
        next(loop for loop in selected[index].loops if loop.vert == vertex)
        for index in sorted(visited)
    ]


def _stabilize_near_collinear_uv_ears(
        bm, uv_layer, charts, target_relative_height=1.0e-4):
    """Nudge only numerically unstable skinny ears to positive UV winding."""

    stabilized = 0
    loop_triangles = list(bm.calc_loop_triangles())
    for chart in charts:
        face_ids = {face.index for face in chart}
        saved_uv = _save_uv(chart, uv_layer)
        partition_before = tuple(sorted(
            tuple(sorted(face.index for face in component))
            for component in _uv_charts_for_faces(chart, uv_layer)
        ))
        changed = False
        for _iteration in range(3):
            adjusted = False
            for tri_loops in loop_triangles:
                if not tri_loops or tri_loops[0].face.index not in face_ids:
                    continue
                points = [loop.vert.co.copy() for loop in tri_loops]
                uvs = [loop[uv_layer].uv.copy() for loop in tri_loops]
                orientation = _triangle_orientation(points, uvs)
                if orientation not in {'negative', 'uv_degenerate'}:
                    continue

                base_a, base_b, apex = max(
                    ((0, 1, 2), (1, 2, 0), (2, 0, 1)),
                    key=lambda item: (
                        points[item[1]] - points[item[0]]).length_squared,
                )
                source_base = points[base_b] - points[base_a]
                source_base_squared = source_base.length_squared
                if source_base_squared <= 1.0e-24:
                    continue
                source_relative_height = (
                    source_base.cross(points[apex] - points[base_a]).length
                    / source_base_squared
                )
                if source_relative_height > 1.0e-4:
                    continue

                uv_base = uvs[base_b] - uvs[base_a]
                uv_base_squared = uv_base.length_squared
                if uv_base_squared <= 1.0e-24:
                    continue
                current_relative_height = abs(
                    _signed_uv_area_2(uvs)) / uv_base_squared
                if current_relative_height > 1.0e-3:
                    continue
                base_length = math.sqrt(uv_base_squared)
                direction = uv_base / base_length
                perpendicular = Vector((-direction.y, direction.x))
                projection = (
                    uvs[base_a]
                    + direction * (uvs[apex] - uvs[base_a]).dot(direction)
                )
                target_height = max(
                    base_length * target_relative_height,
                    1.0e-12,
                )
                candidates = [
                    projection + perpendicular * target_height,
                    projection - perpendicular * target_height,
                ]
                positive = []
                for candidate in candidates:
                    candidate_uvs = list(uvs)
                    candidate_uvs[apex] = candidate
                    determinant = _signed_uv_area_2(candidate_uvs)
                    if determinant > uv_base_squared * 1.0e-12:
                        positive.append((
                            (candidate - uvs[apex]).length_squared,
                            candidate,
                        ))
                if not positive:
                    continue
                target_loop = tri_loops[apex]
                candidate = min(
                    positive, key=lambda item: item[0])[1]
                delta = candidate - target_loop[uv_layer].uv
                for welded_loop in _welded_chart_vertex_loops(
                        chart, target_loop, uv_layer):
                    welded_loop[uv_layer].uv = (
                        welded_loop[uv_layer].uv + delta)
                adjusted = True
                changed = True
            if not adjusted:
                break
            problem, mirrored = _classify_problem_charts(
                [chart], uv_layer, bm=bm)
            if not problem:
                if mirrored:
                    _mirror_charts_u(mirrored, uv_layer)
                break

        problem, mirrored = _classify_problem_charts(
            [chart], uv_layer, bm=bm)
        if changed and not problem:
            if mirrored:
                _mirror_charts_u(mirrored, uv_layer)
            problem, mirrored = _classify_problem_charts(
                [chart], uv_layer, bm=bm)
        partition_after = tuple(sorted(
            tuple(sorted(face.index for face in component))
            for component in _uv_charts_for_faces(chart, uv_layer)
        ))
        if (
                changed
                and not problem
                and not mirrored
                and partition_after == partition_before):
            stabilized += 1
        else:
            _restore_uv(chart, uv_layer, saved_uv)
    return stabilized


def _project_face_planar_positive(bm, face, uv_layer, offset_u):
    loops = list(face.loops)
    if len(loops) < 3 or face.normal.length_squared <= 1.0e-24:
        return None
    normal = face.normal.normalized()
    directions = [
        loop.link_loop_next.vert.co - loop.vert.co
        for loop in loops
    ]
    axis_u = max(directions, key=lambda value: value.length_squared).copy()
    axis_u -= normal * axis_u.dot(normal)
    if axis_u.length_squared <= 1.0e-24:
        return None
    axis_u.normalize()
    axis_v = normal.cross(axis_u)
    if axis_v.length_squared <= 1.0e-24:
        return None
    axis_v.normalize()

    origin = loops[0].vert.co
    projected = [
        Vector(((loop.vert.co - origin).dot(axis_u),
                (loop.vert.co - origin).dot(axis_v)))
        for loop in loops
    ]
    minimum_u = min(point.x for point in projected)
    minimum_v = min(point.y for point in projected)
    maximum_u = max(point.x for point in projected)
    maximum_v = max(point.y for point in projected)
    width = maximum_u - minimum_u
    height = maximum_v - minimum_v
    local_scale = max(width, height, 1.0e-30)
    if min(width, height) <= max(local_scale * 1.0e-12, 1.0e-24):
        return None
    saved_uv = _save_uv([face], uv_layer)
    for loop, point in zip(loops, projected):
        loop[uv_layer].uv = Vector((
            point.x - minimum_u,
            point.y - minimum_v,
        ))

    problem, mirrored = _classify_problem_charts(
        [[face]], uv_layer, bm=bm)
    if problem:
        _stabilize_near_collinear_uv_ears(
            bm, uv_layer, [[face]])
        problem, mirrored = _classify_problem_charts(
            [[face]], uv_layer, bm=bm)
    if problem:
        _restore_uv([face], uv_layer, saved_uv)
        return None
    if mirrored:
        _mirror_charts_u(mirrored, uv_layer)
        problem, mirrored = _classify_problem_charts(
            [[face]], uv_layer, bm=bm)
        if problem or mirrored:
            _restore_uv([face], uv_layer, saved_uv)
            return None
    for loop in loops:
        loop[uv_layer].uv.x += offset_u
    return width, height


def _face_is_convex_for_projection_fallback(face):
    """Return whether one face is a non-degenerate convex polygon."""

    loops = list(face.loops)
    if len(loops) < 3 or face.normal.length_squared <= 1.0e-24:
        return False
    normal = face.normal.normalized()
    points = [loop.vert.co for loop in loops]
    edge_scale = max(
        (points[(index + 1) % len(points)] - point).length_squared
        for index, point in enumerate(points)
    )
    tolerance = max(edge_scale * 1.0e-12, 1.0e-24)
    has_positive_area = False
    for index, point in enumerate(points):
        edge = points[(index + 1) % len(points)] - point
        if edge.length_squared <= tolerance:
            return False
        for candidate in points:
            side = edge.cross(candidate - point).dot(normal)
            if side < -tolerance:
                return False
            if side > tolerance:
                has_positive_area = True
    return has_positive_area


def _project_face_convex_positive(bm, face, uv_layer, offset_u):
    """Give one valid face a deterministic convex UV when projection fails.

    Some legal n-gons have an unusable aggregate face normal even though
    Blender can tessellate them into non-degenerate source triangles.  Mapping
    the face-loop cycle to a convex polygon preserves the tessellation winding
    and cannot create positive-area overlap inside the face.  The existing
    local-repair validator remains authoritative and rejects genuinely
    degenerate source geometry.
    """

    loops = list(face.loops)
    if (
            len(loops) < 3
            or not _face_is_convex_for_projection_fallback(face)):
        return None
    saved_uv = _save_uv([face], uv_layer)
    angle_step = math.tau / len(loops)
    projected = [
        Vector((math.cos(index * angle_step), math.sin(index * angle_step)))
        for index in range(len(loops))
    ]
    minimum_u = min(point.x for point in projected)
    minimum_v = min(point.y for point in projected)
    maximum_u = max(point.x for point in projected)
    maximum_v = max(point.y for point in projected)
    width = maximum_u - minimum_u
    height = maximum_v - minimum_v
    for loop, point in zip(loops, projected):
        loop[uv_layer].uv = Vector((
            point.x - minimum_u + offset_u,
            point.y - minimum_v,
        ))

    valid, _mirrored_count = _validate_local_repair(
        bm, uv_layer, [face])
    if not valid:
        _restore_uv([face], uv_layer, saved_uv)
        return None
    return width, height


def _project_chart_faces_individually(
        bm, uv_layer, faces, forced_cuts):
    faces = tuple(faces)
    if (
            len(faces) != 1
            or not _face_is_convex_for_projection_fallback(faces[0])):
        return False, 0
    edges = {
        edge.index: edge
        for face in faces
        for edge in face.edges
    }
    for edge in edges.values():
        edge.seam = True
        if len(edge.link_faces) >= 2:
            forced_cuts.add(edge.index)

    cursor_u = 0.0
    for face in sorted(faces, key=lambda item: item.index):
        dimensions = _project_face_planar_positive(
            bm, face, uv_layer, cursor_u)
        if dimensions is None:
            dimensions = _project_face_convex_positive(
                bm, face, uv_layer, cursor_u)
        if dimensions is None:
            return False, 0
        width, height = dimensions
        cursor_u += width + max(width, height, 1.0) * 1.0e-4
    return _validate_local_repair(bm, uv_layer, faces)


def _repair_remaining_problem_charts(
        mesh, bm, uv_layer, problem_charts, settings, forced_cuts,
        force_face_projection=False):
    """Repair each remaining invalid chart without changing Mesh topology."""

    face_groups = [
        tuple(sorted(face.index for face in chart))
        for chart in problem_charts
    ]
    mirrored_total = 0
    for face_indices in face_groups:
        bm, uv_layer = _refresh_edit_bmesh(mesh)
        try:
            faces = [bm.faces[index] for index in face_indices]
        except IndexError as error:
            raise RuntimeError(
                "Unique UV repair unexpectedly changed Mesh topology"
            ) from error

        forced_cuts.update(_add_chart_tree_cuts(faces))
        _set_uv_selection(bm, uv_layer, face_indices)
        bmesh.update_edit_mesh(
            mesh, loop_triangles=False, destructive=False)
        unwrap_finished = False
        if not force_face_projection:
            try:
                result = _call_uv_operator(
                    bpy.ops.uv.unwrap,
                    _operator_name='unwrap',
                    method='ANGLE_BASED',
                    fill_holes=True,
                    correct_aspect=True,
                    use_subsurf_data=False,
                    margin=settings.island_margin,
                )
                unwrap_finished = 'FINISHED' in result
            except (ReferenceError, RuntimeError, TypeError, ValueError):
                unwrap_finished = False

        bm, uv_layer = _refresh_edit_bmesh(mesh)
        faces = [bm.faces[index] for index in face_indices]
        if unwrap_finished:
            valid, mirrored_count = _validate_local_repair(
                bm, uv_layer, faces)
            if valid:
                mirrored_total += mirrored_count
                continue

            # ANGLE_BASED can invert a numerically near-collinear tessellation
            # ear while the connected chart is otherwise valid and free of
            # overlap.  Repair only that bounded numerical case, and accept it
            # only when the exact UV face partition is unchanged and the full
            # local validator succeeds afterwards.
            saved_unwrap_uv = _save_uv(faces, uv_layer)
            local_charts = _uv_charts_for_faces(faces, uv_layer)
            partition_before = tuple(sorted(
                tuple(sorted(face.index for face in chart))
                for chart in local_charts
            ))
            stabilized = _stabilize_near_collinear_uv_ears(
                bm, uv_layer, local_charts,
                target_relative_height=1.0e-6)
            if stabilized:
                repaired_charts = _uv_charts_for_faces(faces, uv_layer)
                partition_after = tuple(sorted(
                    tuple(sorted(face.index for face in chart))
                    for chart in repaired_charts
                ))
                if partition_after == partition_before:
                    valid, mirrored_count = _validate_local_repair(
                        bm, uv_layer, faces)
                    if valid:
                        mirrored_total += mirrored_count
                        bmesh.update_edit_mesh(
                            mesh, loop_triangles=False, destructive=False)
                        continue
            _restore_uv(faces, uv_layer, saved_unwrap_uv)

        valid, mirrored_count = _project_chart_faces_individually(
            bm, uv_layer, faces, forced_cuts)
        if not valid:
            raise RuntimeError(
                "Unique UV local fallback could not repair chart with {} faces".format(
                    len(face_indices)))
        mirrored_total += mirrored_count
        bmesh.update_edit_mesh(
            mesh, loop_triangles=False, destructive=False)
    return bm, uv_layer, mirrored_total


def _try_local_problem_chart_repairs(
        mesh, bm, uv_layer, problem_charts, settings, forced_cuts):
    """Try connected chart repairs and restore every rejected attempt."""

    face_groups = [
        tuple(sorted(face.index for face in chart))
        for chart in problem_charts
    ]
    accepted = 0
    rejected = 0
    mirrored_total = 0
    for face_indices in face_groups:
        bm, uv_layer = _refresh_edit_bmesh(mesh)
        faces = [bm.faces[index] for index in face_indices]
        saved_uv = _save_uv(faces, uv_layer)
        chart_edges = {
            edge.index: edge
            for face in faces
            for edge in face.edges
        }
        saved_seams = {
            index: bool(edge.seam) for index, edge in chart_edges.items()
        }
        forced_before = set(forced_cuts)
        local_mirrored = 0
        valid = False

        try:
            forced_cuts.update(_add_chart_tree_cuts(faces))
            _set_uv_selection(bm, uv_layer, face_indices)
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
            result = _call_uv_operator(
                bpy.ops.uv.unwrap,
                _operator_name='unwrap',
                method='ANGLE_BASED',
                fill_holes=True,
                correct_aspect=True,
                use_subsurf_data=False,
                margin=settings.island_margin,
            )
            bm, uv_layer = _refresh_edit_bmesh(mesh)
            faces = [bm.faces[index] for index in face_indices]
            if 'FINISHED' in result:
                valid, local_mirrored = _validate_local_repair(
                    bm, uv_layer, faces)
                local_charts = _uv_charts_for_faces(faces, uv_layer)
                triangles = _triangle_data(faces, uv_layer, bm=bm)
                p95, maximum, flipped = _measure_distortion(triangles)
                valid = bool(
                    valid
                    and len(local_charts) == 1
                    and not flipped
                    and p95 <= settings.max_p95_stretch
                    and maximum <= settings.max_stretch
                    and not _has_overlap(
                        triangles, ignore_same_face=False)
                )
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            valid = False

        if valid:
            accepted += 1
            mirrored_total += local_mirrored
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
            continue

        bm, uv_layer = _refresh_edit_bmesh(mesh)
        faces = [bm.faces[index] for index in face_indices]
        _restore_uv(faces, uv_layer, saved_uv)
        for edge_index, seam in saved_seams.items():
            bm.edges[edge_index].seam = seam
        forced_cuts.clear()
        forced_cuts.update(forced_before)
        bmesh.update_edit_mesh(
            mesh, loop_triangles=False, destructive=False)
        rejected += 1

    return bm, uv_layer, accepted, rejected, mirrored_total


def _refresh_merge_candidate(mesh, face_indices, boundary_indices):
    """Rehydrate a merge candidate after an edit-mode UV operator call."""

    bm, uv_layer = _refresh_edit_bmesh(mesh)
    try:
        faces = [bm.faces[index] for index in face_indices]
        boundary = [bm.edges[index] for index in boundary_indices]
    except IndexError as error:
        raise RuntimeError(
            "UV unwrap unexpectedly changed merge-candidate topology"
        ) from error
    return bm, uv_layer, faces, boundary


def _candidate_groups(
        bm, uv_layer, chart_ids, charts, locked_seams, blocked, visibility, settings,
        probe_evidence=None, edge_policy=None, face_classes=None,
        hard_surface_mode=False, defer_small=False, reference_edges=None):
    edge_policy = edge_policy or {}
    face_classes = face_classes or {}
    reference_edges = set(reference_edges or ())
    groups = defaultdict(list)
    for edge in bm.edges:
        if not edge.seam or len(edge.link_faces) != 2 or edge.index in locked_seams:
            continue
        policy = edge_policy.get(edge.index, 'NEUTRAL')
        if policy in {'LOCKED_CUT', 'FORCE_CUT'}:
            continue
        face_a, face_b = edge.link_faces
        chart_a = chart_ids[face_a.index]
        chart_b = chart_ids[face_b.index]
        if chart_a == chart_b:
            continue
        if settings.respect_materials and face_a.material_index != face_b.material_index:
            continue
        if settings.respect_sharp and not edge.smooth:
            continue
        if hard_surface_mode:
            class_a = face_classes.get(face_a.index, 'GENERAL')
            class_b = face_classes.get(face_b.index, 'GENERAL')
            if not hard_surface.can_merge_classes(class_a, class_b):
                continue
        groups[tuple(sorted((chart_a, chart_b)))].append(edge)

    total_area = sum(_chart_area(chart) for chart in charts)
    total_uv_area = sum(
        small_island_cleanup._chart_uv_area(chart, uv_layer)
        for chart in charts
    )
    candidates = []
    for (chart_a, chart_b), boundary in groups.items():
        signature = frozenset(edge.index for edge in boundary)
        if signature in blocked:
            continue
        chart_left = charts[chart_a]
        chart_right = charts[chart_b]
        lengths = [max(edge.calc_length(), 1e-12) for edge in boundary]
        total_length = sum(lengths)
        average_angle = sum(
            edge.calc_face_angle(0.0) * length for edge, length in zip(boundary, lengths)
        ) / total_length
        area_left = _chart_area(chart_left)
        area_right = _chart_area(chart_right)
        if hard_surface_mode:
            left_small = small_island_cleanup.is_small_chart(
                chart_left, uv_layer, total_area, total_uv_area, settings
            )
            right_small = small_island_cleanup.is_small_chart(
                chart_right, uv_layer, total_area, total_uv_area, settings
            )
            small = left_small or right_small
            if defer_small and small:
                # UNIQUE hard-surface fragments are handled only by the later,
                # stricter cleanup stage.  This remains true when cleanup is
                # disabled, so the switch cannot expose the generic thresholds.
                continue
        else:
            small = (
                min(len(chart_left), len(chart_right)) <= settings.small_island_faces
                or min(area_left, area_right) <= total_area * settings.small_island_area_ratio
            )
        if average_angle > settings.merge_angle and not (small and not hard_surface_mode):
            continue
        seam_cost = _boundary_seam_cost(boundary, visibility, settings)
        score = average_angle / max(settings.merge_angle, 1e-8)
        # Removing a seam is most valuable where that seam would be visible.
        score -= seam_cost * 0.25
        score -= _developable_band_bonus(
            chart_left, chart_right, boundary, settings)
        if small and hard_surface_mode:
            # Small hardware gets only a modest ordering preference.  It must
            # still satisfy the normal angle gate and may never cross a hard
            # policy edge merely because it has few faces.
            score -= 0.15
        elif small:
            score -= 0.9
        score -= min(total_length / max(math.sqrt(total_area), 1e-8), 1.0) * 0.1
        if reference_edges and any(edge.index in reference_edges for edge in boundary):
            # Preserve the learned split when it remains valid, while still
            # allowing a strict repair pass to merge it if necessary.
            score += float(getattr(settings, 'reference_uv_boundary_bias', 0.35))
        probe = probe_analysis.summarize_boundary(
            boundary,
            probe_evidence,
            min_confidence=settings.probe_min_confidence,
            reject_flipped=settings.probe_reject_flipped,
        )
        if (
            settings.probe_enabled
            and probe["supported"]
            and not probe["hard_reject"]
        ):
            score -= (
                settings.probe_merge_bonus
                * probe["score"]
                * max(probe["confidence"], 0.0)
            )
        elif settings.probe_enabled and probe["hard_reject"]:
            # Keep rejected candidates in reports, but sort them after viable
            # candidates so an optimizer pass can explicitly count the reason.
            score += 1000.0
        candidates.append((score, chart_a, chart_b, boundary, signature, probe))
    candidates.sort(key=lambda item: item[0])
    return candidates


def build_probe_candidate_report(obj, settings):
    """Report current UV boundary candidates and their probe evidence.

    This is intentionally read-only. It does not run Smart UV, unwrap, or
    change the object's seams, UVs, selection, or mode.
    """
    evidence = probe_analysis.load_stored_evidence(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            raise ValueError("Active mesh has no UV layer")
        _derive_seams_from_uv(bm, uv_layer, set())
        chart_ids, charts = _charts(bm)
        visibility = effective_face_visibility(obj.data, default=1.0)
        candidates = _candidate_groups(
            bm, uv_layer, chart_ids, charts, set(), set(), visibility, settings,
            evidence)
        report = {
            "schema": probe_analysis.SCHEMA,
            "object": obj.name,
            "charts": len(charts),
            "evidence_edges": len(evidence["edges"]) if evidence else 0,
            "candidates": [],
        }
        for score, chart_a, chart_b, boundary, signature, probe in candidates:
            report["candidates"].append({
                "chart_a": chart_a,
                "chart_b": chart_b,
                "score": round(float(score), 6),
                "boundary_edges": sorted(int(value) for value in signature),
                "boundary_vertices": [
                    sorted(int(vertex.index) for vertex in edge.verts)
                    for edge in boundary
                ],
                "probe": probe,
            })
        return report
    finally:
        bm.free()


def _try_merge(
        obj, bm, uv_layer, faces, boundary, settings, reject_overlap=True):
    if not _is_disk(faces):
        return False, 'TOPOLOGY'

    face_indices = tuple(sorted(face.index for face in faces))
    boundary_indices = tuple(sorted(edge.index for edge in boundary))
    saved_uv = _save_uv(faces, uv_layer)
    old_seams = {edge.index: edge.seam for edge in boundary}
    accepted = False
    reason = 'STRETCH'
    refreshed = False
    for edge in boundary:
        edge.seam = False
    _set_uv_selection(bm, uv_layer, (face.index for face in faces))
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    try:
        result = _call_uv_operator(
            bpy.ops.uv.unwrap,
            _operator_name='unwrap',
            method='ANGLE_BASED',
            fill_holes=True,
            correct_aspect=True,
            use_subsurf_data=False,
            margin=settings.island_margin,
        )
        bm, uv_layer, faces, boundary = _refresh_merge_candidate(
            obj.data, face_indices, boundary_indices)
        refreshed = True
        if 'FINISHED' in result:
            # Use the refreshed edit BMesh tessellation and the same overlap
            # contract as the final unique-layout gate.  UV operators may
            # invalidate every loop wrapper even when topology is unchanged.
            triangles = _triangle_data(faces, uv_layer, bm=bm)
            p95, maximum, flipped = _measure_distortion(triangles)
            if (
                not flipped
                and p95 <= settings.max_p95_stretch
                and maximum <= settings.max_stretch
            ):
                if reject_overlap and _has_overlap(
                        triangles, ignore_same_face=False):
                    reason = 'OVERLAP'
                else:
                    accepted = True
                    reason = 'ACCEPT'
    except (ReferenceError, RuntimeError, ValueError, TypeError):
        reason = 'STRETCH'
    finally:
        if not refreshed:
            bm, uv_layer, faces, boundary = _refresh_merge_candidate(
                obj.data, face_indices, boundary_indices)
        if not accepted:
            _restore_uv(faces, uv_layer, saved_uv)
            for edge in boundary:
                edge.seam = old_seams[edge.index]
            bmesh.update_edit_mesh(
                obj.data, loop_triangles=False, destructive=False)
    return accepted, reason


def _scale_charts_by_visibility(
        bm, uv_layer, charts, visibility, detail_values, settings,
        hard_surface_mode=False):
    for chart in charts:
        all_hidden = all(
            face.index < len(visibility) and visibility[face.index] <= 0.0
            for face in chart
        )
        if all_hidden:
            # Legacy keeps red charts tiny and collapses them later.  A unique
            # hard-surface layout uses a non-zero floor for automatic zero-hit
            # faces; only explicit Hidden overrides are collapsed.
            scale = 0.25 if hard_surface_mode else _LOW_VISIBILITY_SCALE
        else:
            # A chart containing any visible face must not be reduced merely
            # because a neighboring face is hidden. Hidden faces are split
            # from mixed charts during the final origin-collapse pass.
            importance = max((visibility[face.index] for face in chart), default=0.0)
            normalized = min(importance / max(settings.visibility_high, 1e-8), 1.0)
            scale = (
                _LOW_VISIBILITY_SCALE
                + (1.0 - _LOW_VISIBILITY_SCALE) * normalized
            )
            detail = sum(
                detail_values[face.index] * max(face.calc_area(), 1e-12)
                for face in chart
            ) / max(_chart_area(chart), 1e-12)
            scale *= 1.0 + settings.detail_strength * detail
            scale = min(scale, settings.detail_scale_cap)
        if scale >= 0.999999:
            continue
        loops = [loop for face in chart for loop in face.loops]
        if not loops:
            continue
        center = sum((loop[uv_layer].uv for loop in loops), Vector((0.0, 0.0))) / len(loops)
        for loop in loops:
            loop[uv_layer].uv = center + (loop[uv_layer].uv - center) * scale


def _collapse_hidden_faces_to_origin(
        bm, uv_layer, visibility, overrides=None, explicit_only=False):
    """Collapse hidden faces, optionally restricting this to explicit overrides."""
    origin = Vector((0.0, 0.0))
    for face in bm.faces:
        if face.index >= len(visibility) or visibility[face.index] > 0.0:
            continue
        if explicit_only and (
                overrides is None or face.index >= len(overrides)
                or overrides[face.index] >= 0):
            continue
        for loop in face.loops:
            loop[uv_layer].uv = origin


def _normalize_uv_to_tile(bm, uv_layer, margin):
    """Fit an active unique UV layout into one tile after packing.

    Blender 3.3's pack operator has no ``scale`` keyword, and Blender 5.2's
    default can likewise preserve large pre-pack coordinates.  A deterministic
    final affine fit keeps the shared path inside the requested tile without
    changing island topology or relative texel density.
    """
    points = [loop[uv_layer].uv for face in bm.faces for loop in face.loops]
    if not points:
        return False
    minimum_x = min(point.x for point in points)
    maximum_x = max(point.x for point in points)
    minimum_y = min(point.y for point in points)
    maximum_y = max(point.y for point in points)
    width = maximum_x - minimum_x
    height = maximum_y - minimum_y
    usable = max(1.0 - 2.0 * max(float(margin), 0.0), 1.0e-6)
    if width <= 1.0e-12 and height <= 1.0e-12:
        return False
    scale = usable / max(width, height, 1.0e-12)
    offset = Vector((
        max(float(margin), 0.0) - minimum_x * scale,
        max(float(margin), 0.0) - minimum_y * scale,
    ))
    for point in points:
        point *= scale
        point += offset
    return True


def _pack_unique_aabb(
        mesh, bm, uv_layer, settings, preserve_direction=False):
    bm, uv_layer = _select_all_uvs_for_operator(mesh, bm, uv_layer)
    result = _call_uv_operator(
        bpy.ops.uv.pack_islands,
        _operator_name='pack_islands',
        udim_source='ACTIVE_UDIM',
        rotate=not bool(preserve_direction),
        rotate_method='CARDINAL',
        scale=True,
        merge_overlap=False,
        margin=settings.island_margin,
        margin_method='FRACTION',
        pin=False,
        shape_method='AABB',
    )
    if 'FINISHED' not in result:
        raise RuntimeError("Safe AABB Pack Islands did not finish")
    bm, uv_layer = _refresh_edit_bmesh(mesh)
    _normalize_uv_to_tile(bm, uv_layer, settings.island_margin)
    return bm, uv_layer


def _repair_and_pack_unique_charts(
        obj, mesh, bm, uv_layer, problem_charts, settings, forced_cuts,
        original_seams, hard_surface_mode, direction_contract,
        force_face_projection=False):
    if hard_surface_mode:
        chart_budget = max(
            len(problem_charts) * 8,
            len(problem_charts) + 32,
        )
        bm, uv_layer, mirrored_count, _output_charts = (
            _bounded_problem_chart_repairs(
                mesh,
                bm,
                uv_layer,
                problem_charts,
                settings,
                forced_cuts,
                chart_budget,
            )
        )
    else:
        bm, uv_layer, mirrored_count = _repair_remaining_problem_charts(
            mesh,
            bm,
            uv_layer,
            problem_charts,
            settings,
            forced_cuts,
            force_face_projection=force_face_projection,
        )
    if hard_surface_mode and bool(getattr(
            settings, 'hard_surface_direction_lock', True)):
        # Local repair can rebuild a chart from a projection with an arbitrary
        # 90/180-degree orientation.  Reapply the signed contract before the
        # direction-preserving AABB pack establishes final island positions.
        _, repaired_charts = _uv_charts(bm, uv_layer)
        direction_contract.clear()
        for chart in repaired_charts:
            _align_hard_surface_chart(
                obj,
                chart,
                uv_layer,
                settings,
                direction_contract=direction_contract,
            )
    _derive_seams_from_uv(
        bm,
        uv_layer,
        original_seams,
        forced_cuts,
        preserve_boundaries=hard_surface_mode,
    )
    bm, uv_layer = _select_all_uvs_for_operator(
        mesh, bm, uv_layer)
    scale_result = _call_uv_operator(
        bpy.ops.uv.average_islands_scale,
        _operator_name='average_islands_scale')
    if 'FINISHED' not in scale_result:
        raise RuntimeError(
            "Average Islands Scale after local UV repair did not finish")
    bm, uv_layer = _refresh_edit_bmesh(mesh)
    bm, uv_layer = _pack_unique_aabb(
        mesh,
        bm,
        uv_layer,
        settings,
        preserve_direction=bool(
            hard_surface_mode
            and getattr(settings, 'hard_surface_direction_lock', True)
        ),
    )
    _derive_seams_from_uv(
        bm,
        uv_layer,
        original_seams,
        forced_cuts,
        preserve_boundaries=hard_surface_mode,
    )
    _, final_charts = _uv_charts(bm, uv_layer)
    problem_charts, mirrored_charts = _classify_problem_charts(
        final_charts, uv_layer, bm=bm)
    mirrored_count += len(mirrored_charts)
    _mirror_charts_u(mirrored_charts, uv_layer)
    return (
        bm,
        uv_layer,
        final_charts,
        problem_charts,
        mirrored_count,
    )


def _repair_pack_until_valid(
        obj, mesh, bm, uv_layer, problem_charts, settings, forced_cuts,
        original_seams, hard_surface_mode, direction_contract):
    mirrored_total = 0
    final_charts = []
    _, source_charts = _uv_charts(bm, uv_layer)
    repair_chart_budget = max(
        len(problem_charts) * 8,
        len(problem_charts) + 32,
    )
    total_chart_budget = (
        len(source_charts) - len(problem_charts) + repair_chart_budget
    )
    repair_modes = (
        (False, False, False, False)
        if hard_surface_mode
        else (False, True)
    )
    for force_face_projection in repair_modes:
        (
            bm,
            uv_layer,
            final_charts,
            problem_charts,
            mirrored_count,
        ) = _repair_and_pack_unique_charts(
            obj,
            mesh,
            bm,
            uv_layer,
            problem_charts,
            settings,
            forced_cuts,
            original_seams,
            hard_surface_mode,
            direction_contract,
            force_face_projection=force_face_projection,
        )
        mirrored_total += mirrored_count
        if hard_surface_mode and len(final_charts) > total_chart_budget:
            raise RuntimeError(
                "Unique UV Pack repair produced {} total charts (budget {})".format(
                    len(final_charts), total_chart_budget))
        if problem_charts:
            stabilized_charts = _stabilize_near_collinear_uv_ears(
                bm, uv_layer, problem_charts)
            if stabilized_charts:
                _derive_seams_from_uv(
                    bm,
                    uv_layer,
                    original_seams,
                    forced_cuts,
                    preserve_boundaries=hard_surface_mode,
                )
            _, final_charts = _uv_charts(bm, uv_layer)
            problem_charts, mirrored_charts = _classify_problem_charts(
                final_charts, uv_layer, bm=bm)
            mirrored_total += len(mirrored_charts)
            _mirror_charts_u(mirrored_charts, uv_layer)
        if not problem_charts:
            break
    return (
        bm,
        uv_layer,
        final_charts,
        problem_charts,
        mirrored_total,
    )


def _audit_unique_object_mesh(mesh):
    """Run the existing Unique gates on an Object Mode mesh snapshot."""
    active_layer = mesh.uv_layers.active
    if active_layer is None or len(active_layer.data) != len(mesh.loops):
        raise RuntimeError(
            "Unique UV post-layout gate: active UV map is unavailable")
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.index_update()
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.get(active_layer.name)
        if uv_layer is None:
            raise RuntimeError(
                "Unique UV post-layout gate: active UV map is unavailable in BMesh")
        _, charts = _uv_charts(bm, uv_layer)
        problem_charts, mirrored_charts = _classify_problem_charts(
            charts, uv_layer, bm=bm)
        if problem_charts:
            raise RuntimeError(
                "Unique UV post-layout gate: {} folded or degenerate charts".format(
                    len(problem_charts)))
        if mirrored_charts:
            raise RuntimeError(
                "Unique UV post-layout gate: {} mirrored charts".format(
                    len(mirrored_charts)))
        audit = _audit_unique_layout(bm, uv_layer)
        quality_error = _unique_layout_error(audit)
        if quality_error is not None:
            raise RuntimeError(quality_error)
        return len(charts), audit
    finally:
        bm.free()


def _audit_directed_object_mesh(obj, settings, direction_contract):
    """Require the final signed direction contract without moving UVs."""

    options = _group_layout_options(settings)
    analysis = uv_group_layout.analyze_active_uv(obj, options)
    try:
        analysis = uv_group_layout.rebind_geometry_axis_contract(
            obj,
            analysis,
            direction_contract,
            options,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "Hard-surface UV direction gate failed: {}".format(exc)
        ) from exc
    audit = uv_group_layout.audit_active_uv(
        obj,
        analysis.face_to_island,
        epsilon=options.uv_epsilon,
    )
    metrics = uv_group_layout.evaluate_layout_quality(
        obj,
        analysis=analysis,
        face_to_island=analysis.face_to_island,
        audit=audit,
        epsilon=options.uv_epsilon,
        options=options,
    )
    if not bool(metrics.get('directed_geometry_valid', False)):
        directed = metrics.get('directed_geometry', {})
        raise RuntimeError(
            "Hard-surface UV direction gate failed: {} unresolved, {} "
            "misaligned".format(
                int(directed.get('unresolved_islands', 0)),
                int(directed.get('misaligned_islands', 0)),
            )
        )
    return metrics


def _group_layout_options(settings, strict_source_overlap=True):
    """Map established VUV settings to the experimental post-layout stage."""
    # The Blender PropertyGroup only exposes the small set of controls shown
    # in the UI.  Batch/hard-surface scripts can still provide the complete
    # ``GroupLayoutOptions`` contract either with the ``uv_*`` setting names
    # used by this add-on or with the unprefixed dataclass names.  Keep the
    # prefixed spelling first so an explicit UI value wins when both are
    # present, and retain the historical defaults when neither is available.
    def _option(name, default, *aliases):
        for key in (name,) + aliases:
            try:
                value = getattr(settings, key)
            except (AttributeError, TypeError):
                continue
            if value is not None:
                return value
        return default

    defaults = uv_group_layout.GroupLayoutOptions()

    def _bool(name, default, *aliases):
        return bool(_option(name, default, *aliases))

    def _int(name, default, minimum, *aliases):
        return max(int(_option(name, default, *aliases)), int(minimum))

    def _float(name, default, minimum=None, maximum=None, *aliases):
        value = float(_option(name, default, *aliases))
        if minimum is not None:
            value = max(value, float(minimum))
        if maximum is not None:
            value = min(value, float(maximum))
        return value

    def _property_was_set(*names):
        try:
            checker = getattr(settings, 'is_property_set')
        except (AttributeError, TypeError):
            return False
        if not callable(checker):
            return False
        for name in names:
            try:
                if checker(name):
                    return True
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        return False

    direction_contract_was_set = _property_was_set(
        'uv_direction_space', 'uv_direction_auto_priority')

    return uv_group_layout.GroupLayoutOptions(
        margin=max(float(settings.island_margin), 0.0),
        small_face_count=max(
            int(getattr(settings, 'small_island_faces', 12)), 1),
        small_area_ratio=max(
            float(getattr(settings, 'small_island_area_ratio', 0.001)), 0.0),
        small_uv_area_ratio=max(
            float(getattr(settings, 'small_uv_area_ratio', 0.001)), 0.0),
        proximity_radius_ratio=max(
            float(getattr(settings, 'proximity_radius_ratio', 0.025)), 0.0),
        align_non_repeat_cardinal=bool(getattr(
            settings, 'hard_surface_align_cardinal', True)),
        align_directed_cardinal=bool(getattr(
            settings, 'hard_surface_align_cardinal', True)),
        align_geometry_direction=bool(getattr(
            settings, 'hard_surface_direction_lock', True)),
        align_repeat_local_frame=_bool(
            'uv_repeat_local_frame', True,
            'align_repeat_local_frame'),
        direction_space=str(getattr(
            settings, 'uv_direction_space', 'OBJECT')).upper(),
        direction_axis=str(getattr(
            settings, 'uv_direction_axis', 'AUTO')).upper(),
        # Keep every directed stage on the same strict, configurable AUTO
        # resolver.  Only a numerically normal axis may trigger the next
        # fallback; the default remains the historical Z -> X -> Y order.
        direction_auto_priority=str(getattr(
            settings,
            'uv_direction_auto_priority',
            getattr(settings, 'direction_auto_priority', 'ZXY'),
        )).upper(),
        # A saved AUTO contract is authoritative after reopening a file until
        # the artist explicitly changes a contract-defining RNA control.  A
        # plain script settings proxy has no ``is_property_set`` method, so it
        # retains the backward-compatible restore behavior.
        restore_persisted_direction_contract=True,
        direction_contract_settings_explicit=direction_contract_was_set,
        direction_auto_cardinal_bias=max(
            min(float(getattr(
                settings, 'uv_direction_auto_cardinal_bias', 0.0
            )), 1.0),
            0.0,
        ),
        direction_auto_cardinal_min_confidence=max(
            min(float(getattr(
                settings, 'uv_direction_auto_cardinal_min_confidence', 0.15
            )), 1.0),
            0.0,
        ),
        direction_axis_min_projection=(
            hard_surface.GEOMETRY_AXIS_RELATIVE_EPSILON
        ),
        direction_residual_tolerance=math.radians(3.0),
        directed_cardinal_tolerance=max(
            min(float(getattr(
                settings, 'uv_directed_cardinal_tolerance', math.radians(3.0)
            )), math.pi * 0.5),
            0.0,
        ),
        square_pack_bias=max(
            min(float(getattr(settings, 'uv_square_pack_bias', 0.35)), 1.0),
            0.0,
        ),
        square_pack_max_edge_relaxation=max(
            min(float(getattr(
                settings, 'uv_square_pack_max_edge_relaxation', 0.02
            )), 0.25),
            0.0,
        ),
        min_cardinal_edge_confidence=max(
            min(float(getattr(
                settings, 'uv_cardinal_edge_confidence', 0.15
            )), 1.0),
            0.0,
        ),
        prefer_geometry_axis_cardinal=bool(getattr(
            settings, 'uv_prefer_geometry_axis_cardinal', False
        )),
        geometry_axis_cardinal_min_gain=max(
            min(float(getattr(
                settings, 'uv_geometry_axis_cardinal_min_gain', math.radians(5.0)
            )), math.pi * 0.25),
            0.0,
        ),
        geometry_axis_cardinal_max_quality_loss=max(
            min(float(getattr(
                settings, 'uv_geometry_axis_cardinal_max_quality_loss', 0.15
            )), 1.0),
            0.0,
        ),
        cohere_auto_geometry_axis=bool(getattr(
            settings, 'uv_cohere_auto_geometry_axis', False
        )),
        geometry_axis_consensus_min_tangent=max(
            min(float(getattr(
                settings, 'uv_geometry_axis_consensus_min_tangent', 0.70
            )), 1.0),
            0.0,
        ),
        geometry_axis_consensus_min_confidence=max(
            min(float(getattr(
                settings, 'uv_geometry_axis_consensus_min_confidence', 0.30
            )), 1.0),
            0.0,
        ),
        # The long-edge cohort pass is intentionally opt-in.  Existing
        # property groups do not expose it yet, but scripted callers can set
        # ``uv_cohere_geometry_long_edge`` without changing older defaults.
        cohere_geometry_long_edge=bool(getattr(
            settings, 'uv_cohere_geometry_long_edge', False
        )),
        # Complete signed-frame controls.  These are intentionally separate
        # from ``align_geometry_direction``: callers may request frame
        # diagnostics without enabling the write-back rotation, or enable the
        # stricter frame rotation only after their own compatibility check.
        align_geometry_frame=_bool(
            'uv_align_geometry_frame', defaults.align_geometry_frame,
            'align_geometry_frame', 'hard_surface_align_geometry_frame'),
        use_geometry_frame_rotation=_bool(
            'uv_use_geometry_frame_rotation',
            defaults.use_geometry_frame_rotation,
            'use_geometry_frame_rotation',
            'hard_surface_use_geometry_frame_rotation'),
        cohere_geometry_angle_groups=_bool(
            'uv_cohere_geometry_angle_groups',
            defaults.cohere_geometry_angle_groups,
            'cohere_geometry_angle_groups'),
        strict_geometry_frame_quality=_bool(
            'uv_strict_geometry_frame_quality',
            defaults.strict_geometry_frame_quality,
            'strict_geometry_frame_quality'),
        strict_geometry_frame_planar_only=_bool(
            'uv_strict_geometry_frame_planar_only',
            defaults.strict_geometry_frame_planar_only,
            'strict_geometry_frame_planar_only'),
        strict_geometry_frame_planar_tolerance=_float(
            'uv_strict_geometry_frame_planar_tolerance',
            defaults.strict_geometry_frame_planar_tolerance,
            0.0,
            math.pi * 0.5,
            'strict_geometry_frame_planar_tolerance'),
        strict_geometry_frame_residual_tolerance=_float(
            'uv_strict_geometry_frame_residual_tolerance',
            defaults.strict_geometry_frame_residual_tolerance,
            0.0,
            math.pi,
            'strict_geometry_frame_residual_tolerance'),
        preserve_source_layout=_bool(
            'uv_preserve_source_layout', defaults.preserve_source_layout,
            'preserve_source_layout'),
        source_layout_row_quantum=_float(
            'uv_source_layout_row_quantum',
            defaults.source_layout_row_quantum,
            1.0e-6,
            1.0,
            'source_layout_row_quantum'),
        source_layout_row_weight=_float(
            'uv_source_layout_row_weight',
            defaults.source_layout_row_weight,
            0.0,
            10.0,
            'source_layout_row_weight'),
        source_layout_order=str(_option(
            'uv_source_layout_order', defaults.source_layout_order,
            'source_layout_order')).upper(),
        source_layout_affinity_weight=_float(
            'uv_source_layout_affinity_weight',
            defaults.source_layout_affinity_weight,
            0.0,
            10.0,
            'source_layout_affinity_weight'),
        source_layout_cell_enabled=_bool(
            'uv_source_layout_cell_enabled',
            defaults.source_layout_cell_enabled,
            'source_layout_cell_enabled'),
        source_layout_compact_cells=_bool(
            'uv_source_layout_compact_cells',
            defaults.source_layout_compact_cells,
            'source_layout_compact_cells'),
        source_layout_cell_max_members=_int(
            'uv_source_layout_cell_max_members',
            defaults.source_layout_cell_max_members,
            2,
            'source_layout_cell_max_members'),
        source_layout_cell_diameter_ratio=_float(
            'uv_source_layout_cell_diameter_ratio',
            defaults.source_layout_cell_diameter_ratio,
            1.0e-6,
            1.0,
            'source_layout_cell_diameter_ratio'),
        source_layout_cell_link_radius_ratio=_float(
            'uv_source_layout_cell_link_radius_ratio',
            defaults.source_layout_cell_link_radius_ratio,
            1.0e-6,
            1.0,
            'source_layout_cell_link_radius_ratio'),
        structure_group_enabled=_bool(
            'uv_structure_group_enabled', defaults.structure_group_enabled,
            'structure_group_enabled'),
        structure_group_max_members=_int(
            'uv_structure_group_max_members',
            defaults.structure_group_max_members,
            2,
            'structure_group_max_members'),
        structure_group_max_degree=_int(
            'uv_structure_group_max_degree',
            defaults.structure_group_max_degree,
            1,
            'structure_group_max_degree'),
        structure_group_min_contact_ratio=_float(
            'uv_structure_group_min_contact_ratio',
            defaults.structure_group_min_contact_ratio,
            0.0,
            None,
            'structure_group_min_contact_ratio'),
        structure_group_max_diameter_ratio=_float(
            'uv_structure_group_max_diameter_ratio',
            defaults.structure_group_max_diameter_ratio,
            1.0e-6,
            1.0,
            'structure_group_max_diameter_ratio'),
        structure_group_max_normal_angle=_float(
            'uv_structure_group_max_normal_angle',
            defaults.structure_group_max_normal_angle,
            0.0,
            math.pi,
            'structure_group_max_normal_angle'),
        repeat_group_max_members=_int(
            'uv_repeat_group_max_members',
            defaults.repeat_group_max_members,
            2,
            'repeat_group_max_members'),
        repeat_group_max_diameter_ratio=_float(
            'uv_repeat_group_max_diameter_ratio',
            defaults.repeat_group_max_diameter_ratio,
            1.0e-6,
            1.0,
            'repeat_group_max_diameter_ratio'),
        # These two legacy limits are consumed alongside the newer
        # ``repeat_group_*`` envelope.  Mapping them here prevents scripts
        # that already expose the old names from silently losing their cap.
        max_repeat_group_size=_int(
            'uv_max_repeat_group_size', defaults.max_repeat_group_size, 2,
            'max_repeat_group_size', 'repeat_group_max_size'),
        max_small_members_per_group=_int(
            'uv_max_small_members_per_group',
            defaults.max_small_members_per_group,
            1,
            'max_small_members_per_group'),
        small_island_scale_boost=max(
            min(float(getattr(
                settings, 'uv_small_island_scale_boost', 1.25
            )), 3.0),
            1.0,
        ),
        strict_positive_winding=True,
        strict_source_overlap=bool(strict_source_overlap),
        continuity_block_enabled=bool(getattr(
            settings, 'uv_continuity_block_enabled', True)),
        continuity_block_target_members=max(int(getattr(
            settings, 'uv_continuity_block_target_members', 32)), 2),
        continuity_block_max_members=max(int(getattr(
            settings, 'uv_continuity_block_max_members', 48)), 2),
        continuity_component_max_groups=max(int(getattr(
            settings, 'uv_continuity_component_max_groups', 6)), 2),
        continuity_component_max_members=max(int(getattr(
            settings, 'uv_continuity_component_max_members', 96)), 2),
        continuity_block_max_diameter_ratio=max(min(float(getattr(
            settings, 'uv_continuity_block_max_diameter_ratio', 0.35)), 1.0), 1.0e-6),
    )


def _hard_surface_direction_axis_priority(settings):
    axis = str(getattr(settings, 'uv_direction_axis', 'AUTO')).upper()
    if axis == 'AUTO':
        configured = getattr(
            settings,
            'uv_direction_auto_priority',
            getattr(settings, 'direction_auto_priority', 'ZXY'),
        )
        try:
            # The group-layout resolver is the canonical normalizer.  Pass a
            # tuple to hard_surface so compact strings such as ``YZX`` are
            # interpreted as three axes rather than one invalid token.
            return uv_group_layout._direction_axis_order(
                'AUTO', configured
            )
        except (TypeError, ValueError):
            return hard_surface.DEFAULT_GEOMETRY_AXIS_PRIORITY
    if axis in {'X', 'Y', 'Z'}:
        return (axis,)
    return hard_surface.DEFAULT_GEOMETRY_AXIS_PRIORITY


def _align_hard_surface_chart(
        obj, chart, uv_layer, settings, direction_contract=None):
    """Apply the directed contract when local geometry axes are available."""

    face_key = tuple(sorted(int(face.index) for face in chart))
    direction_lock = bool(getattr(
        settings, 'hard_surface_direction_lock', True))
    direction_space = str(getattr(
        settings, 'uv_direction_space', 'OBJECT')).upper()
    if direction_lock and direction_space in {'OBJECT', 'WORLD'}:
        geometry_matrix = (
            obj.matrix_world.to_3x3()
            if direction_space == 'WORLD'
            else None
        )
        report = None
        try:
            report = hard_surface.align_island_geometry_report(
                chart,
                uv_layer,
                axis_priority=_hard_surface_direction_axis_priority(settings),
                geometry_matrix=geometry_matrix,
                write=False,
            )
        except Exception:
            pass
        aligned = hard_surface.align_island_geometry(
            chart,
            uv_layer,
            axis_priority=_hard_surface_direction_axis_priority(settings),
            geometry_matrix=geometry_matrix,
        )
        if direction_contract is not None:
            selected_axis = (
                report.selected_axis
                if (
                    aligned
                    and report is not None
                    and not report.degenerate
                )
                else None
            )
            direction_contract[face_key] = (
                selected_axis,
                direction_space,
            )
        if aligned:
            return True
    elif direction_contract is not None:
        direction_contract[face_key] = (None, direction_space)
    if bool(getattr(settings, 'hard_surface_align_cardinal', True)):
        return hard_surface.align_island_cardinal(chart, uv_layer)
    return False


def _frame_space_point(obj, point, space):
    if str(space).upper() == 'WORLD':
        return obj.matrix_world @ point
    return point.copy()


def _mesh_chart_triangles(
        obj, mesh, uv_layer, face_indices, space, coordinates=None):
    selected = {int(index) for index in face_indices}
    mesh.calc_loop_triangles()
    triangles = []
    for triangle in mesh.loop_triangles:
        if int(triangle.polygon_index) not in selected:
            continue
        loop_indices = tuple(int(index) for index in triangle.loops)
        points = tuple(
            _frame_space_point(
                obj,
                mesh.vertices[mesh.loops[index].vertex_index].co,
                space,
            )
            for index in loop_indices
        )
        if coordinates is None:
            uvs = tuple(
                uv_layer.data[index].uv.copy() for index in loop_indices
            )
        else:
            try:
                uvs = tuple(coordinates[index].copy() for index in loop_indices)
            except KeyError:
                raise RuntimeError(
                    "Planar frame candidate lost a chart loop")
        triangles.append((int(triangle.polygon_index), points, uvs))
    return triangles


def _strict_planar_frame_basis(
        obj, mesh, island, direction_space,
        normal_tolerance=_PLANAR_FRAME_REPAIR_NORMAL_TOLERANCE,
        distance_ratio=_PLANAR_FRAME_REPAIR_DISTANCE_RATIO):
    """Return a signed U/V projection basis only for a strict geometric plane."""

    triangles = _mesh_chart_triangles(
        obj,
        mesh,
        mesh.uv_layers.active,
        island.face_indices,
        direction_space,
    )
    weighted_normal = Vector((0.0, 0.0, 0.0))
    triangle_normals = []
    for _face_index, points, _uvs in triangles:
        cross = (points[1] - points[0]).cross(points[2] - points[0])
        if (
                cross.length_squared <= 1.0e-20
                or not all(math.isfinite(float(value)) for value in cross)):
            continue
        triangle_normals.append(cross.normalized())
        weighted_normal += cross
    if not triangle_normals or weighted_normal.length_squared <= 1.0e-20:
        return None, 'degenerate_geometry'
    normal = weighted_normal.normalized()
    cosine_limit = math.cos(max(min(float(normal_tolerance), math.pi), 0.0))
    if any(
            float(value.dot(normal)) + 1.0e-9 < cosine_limit
            for value in triangle_normals):
        return None, 'normal_spread'

    vertex_indices = sorted({
        int(vertex_index)
        for face_index in island.face_indices
        for vertex_index in mesh.polygons[int(face_index)].vertices
    })
    points = [
        _frame_space_point(
            obj, mesh.vertices[index].co, direction_space)
        for index in vertex_indices
    ]
    if len(points) < 3:
        return None, 'degenerate_geometry'
    origin = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
    minimum = Vector(tuple(
        min(point[axis] for point in points) for axis in range(3)
    ))
    maximum = Vector(tuple(
        max(point[axis] for point in points) for axis in range(3)
    ))
    diagonal = (maximum - minimum).length
    if not math.isfinite(diagonal) or diagonal <= 1.0e-10:
        return None, 'degenerate_geometry'
    plane_error = max(
        abs(float((point - origin).dot(normal))) for point in points
    )
    relative_error = plane_error / diagonal
    if (
            not math.isfinite(relative_error)
            or relative_error > float(distance_ratio) + 1.0e-12):
        return None, 'point_plane_error'

    axis = {
        'X': Vector((1.0, 0.0, 0.0)),
        'Y': Vector((0.0, 1.0, 0.0)),
        'Z': Vector((0.0, 0.0, 1.0)),
    }.get(str(island.geometry_axis_name).upper())
    if axis is None:
        return None, 'unresolved_axis'
    v_basis = axis - normal * axis.dot(normal)
    if v_basis.length_squared <= 1.0e-12:
        return None, 'axis_parallel_to_plane_normal'
    v_basis.normalize()
    # This signed construction maps the positive selected model axis to +V
    # while preserving mesh winding: (V x N) x V = N.
    u_basis = v_basis.cross(normal)
    if u_basis.length_squared <= 1.0e-12:
        return None, 'degenerate_basis'
    u_basis.normalize()
    if float(u_basis.cross(v_basis).dot(normal)) <= 0.0:
        return None, 'negative_basis'
    return {
        'origin': origin,
        'normal': normal,
        'u_basis': u_basis,
        'v_basis': v_basis,
        'normal_spread': max(
            math.acos(max(-1.0, min(1.0, float(value.dot(normal)))))
            for value in triangle_normals
        ),
        'plane_error_ratio': relative_error,
    }, None


def _triangle_uv_signed_area(triangles):
    return sum(
        0.5 * (
            (uvs[1].x - uvs[0].x) * (uvs[2].y - uvs[0].y)
            - (uvs[1].y - uvs[0].y) * (uvs[2].x - uvs[0].x)
        )
        for _face_index, _points, uvs in triangles
    )


def _planar_frame_candidate(obj, mesh, uv_layer, island, basis):
    loop_indices = tuple(int(index) for index in island.loop_indices)
    if not loop_indices:
        return None, 'missing_loops', {}
    old_center = sum(
        (uv_layer.data[index].uv.copy() for index in loop_indices),
        Vector((0.0, 0.0)),
    ) / len(loop_indices)
    direction_space = str(island.geometry_direction_space).upper()
    raw = {}
    for loop_index in loop_indices:
        point = _frame_space_point(
            obj,
            mesh.vertices[mesh.loops[loop_index].vertex_index].co,
            direction_space,
        )
        relative = point - basis['origin']
        raw[loop_index] = Vector((
            relative.dot(basis['u_basis']),
            relative.dot(basis['v_basis']),
        ))
    raw_center = sum(raw.values(), Vector((0.0, 0.0))) / len(raw)
    old_triangles = _mesh_chart_triangles(
        obj,
        mesh,
        uv_layer,
        island.face_indices,
        direction_space,
    )
    raw_triangles = _mesh_chart_triangles(
        obj,
        mesh,
        uv_layer,
        island.face_indices,
        direction_space,
        coordinates=raw,
    )
    old_area = abs(_triangle_uv_signed_area(old_triangles))
    raw_area = abs(_triangle_uv_signed_area(raw_triangles))
    if old_area <= 1.0e-15 or raw_area <= 1.0e-15:
        return None, 'degenerate_area', {}
    scale = math.sqrt(old_area / raw_area)
    candidate = {
        index: old_center + (point - raw_center) * scale
        for index, point in raw.items()
    }
    candidate_triangles = _mesh_chart_triangles(
        obj,
        mesh,
        uv_layer,
        island.face_indices,
        direction_space,
        coordinates=candidate,
    )
    old_p95, old_maximum, old_flipped = _measure_distortion(old_triangles)
    new_p95, new_maximum, new_flipped = _measure_distortion(
        candidate_triangles)
    candidate_area = abs(_triangle_uv_signed_area(candidate_triangles))
    metrics = {
        'old_p95': old_p95,
        'old_max': old_maximum,
        'new_p95': new_p95,
        'new_max': new_maximum,
        'area_ratio': candidate_area / max(old_area, 1.0e-15),
        'old_flipped': bool(old_flipped),
        'new_flipped': bool(new_flipped),
        'internal_overlap': bool(_has_overlap(
            candidate_triangles, ignore_same_face=False)),
    }
    return candidate, None, metrics


def _repair_refine_planar_frame_failures(obj, settings, layout_options):
    """Reparameterize strict planar frame failures without changing seams."""

    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    summary = {
        'enabled': True,
        'reason': 'completed',
        'source_islands': 0,
        'frame_failures': 0,
        'strict_planar_failures': 0,
        'attempted': 0,
        'accepted': 0,
        'rejected': 0,
        'rejected_reasons': {},
        'contract_entries': 0,
        'normal_tolerance_degrees': math.degrees(
            _PLANAR_FRAME_REPAIR_NORMAL_TOLERANCE),
        'distance_ratio_tolerance': _PLANAR_FRAME_REPAIR_DISTANCE_RATIO,
    }
    if uv_layer is None or len(uv_layer.data) != len(mesh.loops):
        raise RuntimeError(
            "Planar frame repair requires a valid active UV layer")
    source = uv_group_layout.analyze_active_uv(obj, layout_options)
    summary['source_islands'] = len(source.islands)
    direction_contract = {
        tuple(sorted(int(index) for index in island.face_indices)): (
            island.geometry_axis_name,
            str(island.geometry_direction_space or layout_options.direction_space),
        )
        for island in source.islands
    }
    summary['contract_entries'] = len(direction_contract)
    source_uv = [item.uv.copy() for item in uv_layer.data]
    accepted = {}

    def reject(reason):
        summary['rejected'] += 1
        reasons = summary['rejected_reasons']
        reasons[reason] = int(reasons.get(reason, 0)) + 1

    max_p95 = float(getattr(settings, 'max_p95_stretch', 1.35))
    max_stretch = float(getattr(settings, 'max_stretch', 2.0))
    for island in source.islands:
        if uv_group_layout._geometry_frame_is_reliable(
                island, layout_options):
            continue
        summary['frame_failures'] += 1
        basis, reason = _strict_planar_frame_basis(
            obj,
            mesh,
            island,
            str(island.geometry_direction_space or layout_options.direction_space),
        )
        if basis is None:
            # Non-planar failures are normal on curved shells and bands.  They
            # are not repair attempts and remain visible in frame diagnostics.
            continue
        summary['strict_planar_failures'] += 1
        summary['attempted'] += 1
        candidate, reason, metrics = _planar_frame_candidate(
            obj, mesh, uv_layer, island, basis)
        if candidate is None:
            reject(reason or 'candidate_failed')
            continue
        if metrics['old_flipped']:
            reject('source_winding')
            continue
        if metrics['new_flipped']:
            reject('candidate_winding')
            continue
        if metrics['internal_overlap']:
            reject('internal_overlap')
            continue
        if not 0.999 <= float(metrics['area_ratio']) <= 1.001:
            reject('area_change')
            continue
        if (
                float(metrics['new_p95']) > max_p95 + 1.0e-7
                or float(metrics['new_max']) > max_stretch + 1.0e-7):
            reject('stretch_limit')
            continue
        if (
                float(metrics['new_p95'])
                > max(1.01, float(metrics['old_p95']) * 1.01)
                or float(metrics['new_max'])
                > max(1.02, float(metrics['old_max']) * 1.01)):
            reject('stretch_regression')
            continue
        for loop_index, coordinate in candidate.items():
            uv_layer.data[loop_index].uv = coordinate
        accepted[tuple(sorted(int(index) for index in island.face_indices))] = (
            tuple(int(index) for index in island.loop_indices),
            metrics,
        )
    mesh.update()

    # Face sets and selected axes are one transaction contract.  This raises
    # instead of silently reselecting AUTO if a candidate changed identity.
    rebound = uv_group_layout.analyze_active_uv(obj, layout_options)
    rebound = uv_group_layout.rebind_geometry_axis_contract(
        obj,
        rebound,
        direction_contract,
        layout_options,
    )
    rebound_by_faces = {
        tuple(sorted(int(index) for index in island.face_indices)): island
        for island in rebound.islands
    }
    post_frame_rejected = []
    for face_key, (loop_indices, _metrics) in accepted.items():
        island = rebound_by_faces.get(face_key)
        if (
                island is None
                or not uv_group_layout._geometry_frame_is_reliable(
                    island, layout_options)):
            for loop_index in loop_indices:
                uv_layer.data[loop_index].uv = source_uv[loop_index]
            post_frame_rejected.append(face_key)
            reject('post_frame_gate')
    for face_key in post_frame_rejected:
        del accepted[face_key]
    if post_frame_rejected:
        mesh.update()
        rebound = uv_group_layout.analyze_active_uv(obj, layout_options)
        uv_group_layout.rebind_geometry_axis_contract(
            obj,
            rebound,
            direction_contract,
            layout_options,
        )
    summary['accepted'] = len(accepted)
    if not accepted and summary['attempted'] == 0:
        summary['reason'] = 'no_strict_planar_frame_failures'
    elif not accepted:
        summary['reason'] = 'all_candidates_rejected'
    return summary, direction_contract


def optimize_active_object(context, obj, settings):
    mesh = obj.data
    mesh.update()
    if not mesh.polygons:
        raise ValueError("Active mesh contains no faces")
    snapshot = _snapshot_mesh_uv_state(mesh)
    target_layer_name = snapshot.get('active')
    visibility = effective_face_visibility(mesh, default=1.0)
    overrides = read_face_int(mesh)
    probe_evidence = probe_analysis.load_stored_evidence(obj)
    original_seams = {
        edge.index for edge in mesh.edges if edge.use_seam
    } if settings.preserve_seams else set()
    reference_uv_layers = _reference_uv_layer_names(settings)
    reference_edges = _reference_uv_boundary_edges(
        mesh,
        reference_uv_layers,
        mode=getattr(settings, 'reference_uv_boundary_mode', 'UNION'),
    )
    uv_usage = _resolve_uv_usage(obj, settings)
    initial_uv_mode = _resolve_initial_uv_mode(settings, original_seams, uv_usage)
    refine_layout_mode = initial_uv_mode == 'REFINE_LAYOUT'
    hard_surface_mode = initial_uv_mode in {
        'HARD_SURFACE', 'MARKED_SEAMS', 'REFINE_LAYOUT'
    }
    direct_refine_group_pack = (
        refine_layout_mode
        and uv_usage == 'UNIQUE'
        and bool(getattr(settings, 'uv_group_layout_enabled', True))
    )
    edge_policy = {}
    face_classes = {}
    forced_cuts = set()
    protected_cuts = set()
    detail_values = []
    constraints = None
    small_cleanup_summary = {
        'enabled': False,
        'reason': 'not_hard_surface_unique',
        'initial_charts': 0,
        'final_charts': 0,
        'tests': 0,
        'accepted': 0,
    }
    planar_frame_repair_summary = {
        'enabled': False,
        'reason': 'not_refine_layout_group_pack',
        'attempted': 0,
        'accepted': 0,
        'rejected': 0,
    }
    frozen_group_direction_contract = None
    group_layout_result = None
    direction_contract = {}
    group_layout_disabled_reason = 'not_hard_surface_unique'
    refine_source_audit = None
    refine_cleanup_audit = None
    allow_refine_source_overlap = False

    if initial_uv_mode == 'PRESERVE_LAYOUT':
        island_count, detail_values = _inspect_existing_layout(mesh)
        try:
            write_face_float(mesh, detail_values, name='vuv_detail_score')
        except Exception:
            _restore_face_float_attribute(
                mesh, 'vuv_detail_score', snapshot.get('detail_score'))
            raise
        return OptimizeResult(
            initial_islands=island_count,
            final_islands=island_count,
            merge_tests=0,
            accepted_merges=0,
            rejected_stretch=0,
            rejected_overlap=0,
            rejected_topology=0,
            detail_values=detail_values,
            initial_uv_mode=initial_uv_mode,
            uv_usage=uv_usage,
        )

    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.index_update()
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        _set_edit_mesh_hidden(bm, hidden=False)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_all(action='SELECT')

        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.index_update()
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.verify()

        if refine_layout_mode:
            constraints = hard_surface.classify_edge_constraints(
                bm,
                settings,
                mode='HARD_SURFACE',
                original_seams=original_seams,
                preferred_cut_edges=reference_edges,
                preferred_cut_penalty=getattr(
                    settings, 'reference_uv_boundary_bias', 0.35),
            )
            edge_policy = dict(getattr(constraints, 'edge_policy', {}))
            face_classes = dict(getattr(constraints, 'face_classes', {}))
            forced_cuts = set(getattr(constraints, 'forced_cuts', set()))
            protected_cuts = set(getattr(constraints, 'protected_cuts', set()))
            _derive_seams_from_uv(
                bm,
                uv_layer,
                original_seams,
                forced_cuts,
                preserve_boundaries=True,
            )
            bmesh.update_edit_mesh(
                mesh, loop_triangles=False, destructive=False)
        elif hard_surface_mode:
            if not settings.preserve_seams:
                for edge in bm.edges:
                    edge.seam = False
            constraints = hard_surface.classify_edge_constraints(
                bm,
                settings,
                mode=initial_uv_mode,
                original_seams=original_seams,
                preferred_cut_edges=reference_edges,
                preferred_cut_penalty=getattr(
                    settings, 'reference_uv_boundary_bias', 0.35),
            )
            edge_policy = dict(getattr(constraints, 'edge_policy', {}))
            face_classes = dict(getattr(constraints, 'face_classes', {}))
            forced_cuts = set(getattr(constraints, 'forced_cuts', set()))
            protected_cuts = set(getattr(constraints, 'protected_cuts', set()))
            locked_cuts = set(getattr(constraints, 'locked_cuts', original_seams))
            for edge in bm.edges:
                edge.seam = (
                    edge.index in locked_cuts
                    or edge.index in forced_cuts
                    or edge.index in reference_edges
                )
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

            unwrap_result = _call_uv_operator(
                bpy.ops.uv.unwrap,
                _operator_name='unwrap',
                method='ANGLE_BASED',
                fill_holes=True,
                correct_aspect=True,
                use_subsurf_data=False,
                margin=settings.island_margin,
            )
            if 'FINISHED' not in unwrap_result:
                raise RuntimeError("Hard-surface UV unwrap did not finish")

            # Re-fetch the edit BMesh after an operator call; Blender may
            # invalidate loop wrappers while rebuilding the UV layer.
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.index_update()
            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.verts.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.verify()
            # Record the unwrap's real chart boundaries before optional
            # geometric cleanup.  A classifier region is not allowed to split
            # an already continuous chart merely because its normals form a
            # smaller panel or band.
            _derive_seams_from_uv(
                bm,
                uv_layer,
                locked_cuts,
                forced_cuts,
                preserve_boundaries=True,
            )
            regions = hard_surface.build_region_seed_records(bm, constraints)
            for record in regions:
                region = list(record.faces)
                classes = {face_classes.get(face.index, 'GENERAL') for face in region}
                chart_bounded = all(
                    bm.edges[index].seam
                    for index in record.boundary_edges
                    if 0 <= index < len(bm.edges)
                )
                planar_classes = {
                    'PLANAR_PANEL', 'PANEL', 'RADIAL_CAP', 'CAP'
                }
                if chart_bounded and classes and classes <= planar_classes:
                    saved_region_uv = _save_uv(region, uv_layer)
                    if hard_surface.project_planar_region(region, uv_layer):
                        triangles = _triangle_data(region, uv_layer, bm=bm)
                        p95, maximum, flipped = _measure_distortion(triangles)
                        if (
                            flipped
                            or p95 > settings.max_p95_stretch
                            or maximum > settings.max_stretch
                            or _has_overlap(
                                triangles, ignore_same_face=False)
                        ):
                            _restore_uv(region, uv_layer, saved_region_uv)
                band_classes = {
                    'BEVEL_STRIP', 'BEVEL', 'QUAD_STRIP', 'STRIP',
                    'CYLINDER_SIDE', 'CYLINDER'
                }
                if chart_bounded and classes and classes <= band_classes:
                    _align_hard_surface_chart(obj, region, uv_layer, settings)
            _derive_seams_from_uv(
                bm,
                uv_layer,
                locked_cuts,
                forced_cuts,
                preserve_boundaries=True,
            )
        else:
            smart_result = _call_uv_operator(
                bpy.ops.uv.smart_project,
                _operator_name='smart_project',
                angle_limit=settings.smart_angle,
                island_margin=settings.island_margin,
                area_weight=0.0,
                correct_aspect=True,
                scale_to_bounds=False,
            )
            if 'FINISHED' not in smart_result:
                raise RuntimeError("Smart UV Project did not finish")
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.index_update()
            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.verts.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.verify()
            _derive_seams_from_uv(bm, uv_layer, original_seams)

        detail_values = _compute_face_detail(bm)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        _, initial_charts = _charts(bm)
        initial_count = len(initial_charts)
        blocked = set()
        merge_tests = 0
        accepted = 0
        rejected_stretch = 0
        rejected_overlap = 0
        rejected_topology = 0
        rejected_probe = 0
        probe_guided_merges = 0
        repaired_charts = 0
        mirrored_chart_count = 0
        safe_pack_retry = False
        local_repair_attempted = 0
        local_repair_accepted = 0
        local_repair_rejected = 0
        smart_repair_input_charts = 0
        smart_repair_output_charts = 0
        smart_repair_total_budget = None
        smart_repair_faces = set()
        reject_overlap = bool(settings.reject_overlap and uv_usage == 'UNIQUE')

        while (
                not refine_layout_mode
                and merge_tests < settings.max_merge_tests):
            chart_ids, charts = _charts(bm)
            candidates = _candidate_groups(
                bm, uv_layer, chart_ids, charts, original_seams, blocked, visibility,
                settings, probe_evidence, edge_policy, face_classes,
                hard_surface_mode,
                defer_small=(hard_surface_mode and uv_usage == 'UNIQUE'),
                reference_edges=reference_edges)
            if not candidates:
                break

            merged_this_round = False
            attempted_merge = False
            for _, chart_a, chart_b, boundary, signature, probe in candidates:
                if merge_tests >= settings.max_merge_tests:
                    break
                merge_tests += 1
                if settings.probe_enabled and probe["hard_reject"]:
                    rejected_probe += 1
                    blocked.add(signature)
                    continue
                faces = charts[chart_a] + charts[chart_b]
                accepted_merge, reason = _try_merge(
                    obj, bm, uv_layer, faces, boundary, settings,
                    reject_overlap=reject_overlap)
                # Every operator attempt, including a rejected one, invalidates
                # the candidate list's BMesh wrappers.  Rehydrate here and build
                # a fresh candidate list on the next outer iteration.
                bm, uv_layer = _refresh_edit_bmesh(mesh)
                attempted_merge = True
                if accepted_merge:
                    accepted += 1
                    if (
                        settings.probe_enabled
                        and probe["supported"]
                        and probe["score"] >= 0.5
                    ):
                        probe_guided_merges += 1
                    merged_this_round = True
                    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
                    break
                blocked.add(signature)
                if reason == 'OVERLAP':
                    rejected_overlap += 1
                elif reason == 'TOPOLOGY':
                    rejected_topology += 1
                else:
                    rejected_stretch += 1
                break
            if not merged_this_round:
                if attempted_merge:
                    continue
                break

        _, final_charts = _uv_charts(bm, uv_layer)
        if uv_usage == 'UNIQUE':
            problem_charts, mirrored_charts = _classify_problem_charts(
                final_charts, uv_layer, bm=bm)
            mirrored_chart_count += len(mirrored_charts)
            _mirror_charts_u(mirrored_charts, uv_layer)
            if problem_charts:
                repaired_charts = len(problem_charts)
                smart_repair_faces = {
                    face.index for chart in problem_charts for face in chart
                }
                # Hard-surface repair should retain each failed structural
                # region whenever possible. Smart UV can split a small number
                # of failed large charts into hundreds of fragments, so try
                # the connected tree-cut unwrap before using it as a fallback.
                if hard_surface_mode:
                    local_repair_attempted = len(problem_charts)
                    (
                        bm,
                        uv_layer,
                        local_repair_accepted,
                        local_repair_rejected,
                        local_mirrored,
                    ) = _try_local_problem_chart_repairs(
                            mesh,
                            bm,
                            uv_layer,
                            problem_charts,
                            settings,
                            forced_cuts,
                    )
                    mirrored_chart_count += local_mirrored
                    _derive_seams_from_uv(
                        bm,
                        uv_layer,
                        original_seams,
                        forced_cuts,
                        preserve_boundaries=True,
                    )
                    _, final_charts = _uv_charts(bm, uv_layer)
                    problem_charts, mirrored_charts = _classify_problem_charts(
                        final_charts, uv_layer, bm=bm)
                    mirrored_chart_count += len(mirrored_charts)
                    _mirror_charts_u(mirrored_charts, uv_layer)

                if problem_charts:
                    smart_repair_input_charts = len(problem_charts)
                    smart_chart_budget = max(
                        smart_repair_input_charts * 8,
                        smart_repair_input_charts + 32,
                    )
                    smart_repair_total_budget = (
                        len(final_charts)
                        - smart_repair_input_charts
                        + smart_chart_budget
                    )
                    if hard_surface_mode:
                        (
                            bm,
                            uv_layer,
                            bounded_mirrored,
                            smart_repair_output_charts,
                        ) = _bounded_problem_chart_repairs(
                            mesh,
                            bm,
                            uv_layer,
                            problem_charts,
                            settings,
                            forced_cuts,
                            smart_chart_budget,
                        )
                        mirrored_chart_count += bounded_mirrored
                    else:
                        problem_face_groups = [
                            tuple(sorted(face.index for face in chart))
                            for chart in problem_charts
                        ]
                        repair_faces = {
                            face_index
                            for group in problem_face_groups
                            for face_index in group
                        }
                        saved_repair_uv = _save_uv(
                            [face for chart in problem_charts for face in chart],
                            uv_layer)
                        saved_repair_seams = {
                            edge.index: bool(edge.seam)
                            for chart in problem_charts
                            for face in chart
                            for edge in face.edges
                        }
                        _set_uv_selection(bm, uv_layer, repair_faces)
                        bmesh.update_edit_mesh(
                            mesh, loop_triangles=False, destructive=False)
                        repair_result = _call_uv_operator(
                            bpy.ops.uv.smart_project,
                            _operator_name='smart_project',
                            angle_limit=settings.smart_angle,
                            island_margin=settings.island_margin,
                            area_weight=0.0,
                            correct_aspect=True,
                            scale_to_bounds=False,
                            preserve_seams=True,
                        )
                        if 'FINISHED' not in repair_result:
                            raise RuntimeError(
                                "Unique UV repair projection did not finish")
                        bm, uv_layer = _refresh_edit_bmesh(mesh)
                        repaired_faces = [
                            bm.faces[index] for index in sorted(repair_faces)
                        ]
                        smart_repair_output_charts = len(
                            _uv_charts_for_faces(repaired_faces, uv_layer))
                    if smart_repair_output_charts > smart_chart_budget:
                        if hard_surface_mode:
                            raise RuntimeError(
                                "Unique UV Smart repair fragmented {} charts into {} "
                                "charts (budget {})".format(
                                    smart_repair_input_charts,
                                    smart_repair_output_charts,
                                    smart_chart_budget,
                                )
                            )
                        # A generic Smart Project can over-split a selected
                        # region. Restore the pre-repair state and ask the
                        # bounded solver for one candidate per original chart.
                        bm, uv_layer = _refresh_edit_bmesh(mesh)
                        restore_faces = [
                            bm.faces[index] for index in sorted(repair_faces)
                        ]
                        _restore_uv(restore_faces, uv_layer, saved_repair_uv)
                        for edge_index, seam in saved_repair_seams.items():
                            if 0 <= edge_index < len(bm.edges):
                                bm.edges[edge_index].seam = seam
                        bmesh.update_edit_mesh(
                            mesh, loop_triangles=False, destructive=False)
                        bounded_problem_charts = [
                            [bm.faces[index] for index in group]
                            for group in problem_face_groups
                        ]
                        (
                            bm,
                            uv_layer,
                            bounded_mirrored,
                            smart_repair_output_charts,
                        ) = _bounded_problem_chart_repairs(
                            mesh,
                            bm,
                            uv_layer,
                            bounded_problem_charts,
                            settings,
                            forced_cuts,
                            smart_chart_budget,
                        )
                        mirrored_chart_count += bounded_mirrored
                    _derive_seams_from_uv(
                        bm,
                        uv_layer,
                        original_seams,
                        forced_cuts,
                        preserve_boundaries=hard_surface_mode,
                    )
                    _, final_charts = _uv_charts(bm, uv_layer)
                    problem_charts, mirrored_charts = _classify_problem_charts(
                        final_charts, uv_layer, bm=bm)
                    mirrored_chart_count += len(mirrored_charts)
                    _mirror_charts_u(mirrored_charts, uv_layer)
                if problem_charts and hard_surface_mode:
                    smart_repair_faces.update(
                        face.index
                        for chart in problem_charts
                        for face in chart
                    )
                    (
                        bm,
                        uv_layer,
                        bounded_mirrored,
                        _retry_output_charts,
                    ) = _bounded_problem_chart_repairs(
                        mesh,
                        bm,
                        uv_layer,
                        problem_charts,
                        settings,
                        forced_cuts,
                        smart_chart_budget,
                    )
                    mirrored_chart_count += bounded_mirrored
                    _derive_seams_from_uv(
                        bm,
                        uv_layer,
                        original_seams,
                        forced_cuts,
                        preserve_boundaries=hard_surface_mode,
                    )
                    _, final_charts = _uv_charts(bm, uv_layer)
                    problem_charts, mirrored_charts = _classify_problem_charts(
                        final_charts, uv_layer, bm=bm)
                    mirrored_chart_count += len(mirrored_charts)
                    _mirror_charts_u(mirrored_charts, uv_layer)
                    if problem_charts:
                        raise RuntimeError(
                            "Unique UV bounded repair left {} folded or degenerate charts".format(
                                len(problem_charts)))
                    repaired_faces = [
                        bm.faces[index]
                        for index in sorted(smart_repair_faces)
                    ]
                    smart_repair_output_charts = len(
                        _uv_charts_for_faces(repaired_faces, uv_layer))
                    if (
                            smart_repair_total_budget is not None
                            and len(final_charts) > smart_repair_total_budget):
                        raise RuntimeError(
                            "Unique UV bounded repair produced {} total charts "
                            "(budget {})".format(
                                len(final_charts),
                                smart_repair_total_budget,
                            ))
                elif problem_charts:
                    bm, uv_layer, local_mirrored = (
                        _repair_remaining_problem_charts(
                            mesh,
                            bm,
                            uv_layer,
                            problem_charts,
                            settings,
                            forced_cuts,
                        )
                    )
                    mirrored_chart_count += local_mirrored
                    _derive_seams_from_uv(
                        bm,
                        uv_layer,
                        original_seams,
                        forced_cuts,
                        preserve_boundaries=False,
                    )
                    _, final_charts = _uv_charts(bm, uv_layer)
                    problem_charts, mirrored_charts = _classify_problem_charts(
                        final_charts, uv_layer, bm=bm)
                    mirrored_chart_count += len(mirrored_charts)
                    _mirror_charts_u(mirrored_charts, uv_layer)
                    if problem_charts:
                        raise RuntimeError(
                            "Unique UV repair left {} folded or degenerate charts".format(
                                len(problem_charts)))

        if uv_usage == 'UNIQUE':
            if direct_refine_group_pack:
                refine_source_audit = _audit_unique_layout(bm, uv_layer)
                if refine_source_audit['overlap']:
                    raise RuntimeError(
                        "Refine Layout source UV contains positive-area overlap")
            if hard_surface_mode and constraints is not None:
                small_cleanup_summary = small_island_cleanup.stitch_small_islands(
                    obj,
                    bm,
                    uv_layer,
                    settings,
                    constraints,
                    sys.modules[__name__],
                )
                if not small_cleanup_summary.get('enabled', False):
                    small_cleanup_summary.setdefault(
                        'reason', 'disabled_by_setting')
                bm, uv_layer = _refresh_edit_bmesh(mesh)
                _derive_seams_from_uv(
                    bm,
                    uv_layer,
                    original_seams,
                    forced_cuts,
                    preserve_boundaries=True,
                )
                _, final_charts = _uv_charts(bm, uv_layer)
                cleanup_problem, cleanup_mirrored = _classify_problem_charts(
                    final_charts, uv_layer, bm=bm)
                if cleanup_problem or cleanup_mirrored:
                    raise RuntimeError(
                        "Small-island cleanup left {} invalid charts".format(
                            len(cleanup_problem) + len(cleanup_mirrored)))
                if direct_refine_group_pack:
                    refine_cleanup_audit = _audit_unique_layout(bm, uv_layer)
                    audit_without_position = dict(refine_cleanup_audit)
                    audit_without_position['inside_tile'] = True
                    audit_without_position['overlap'] = False
                    cleanup_error = _unique_layout_error(audit_without_position)
                    if cleanup_error is not None:
                        raise RuntimeError(cleanup_error)
                    allow_refine_source_overlap = bool(
                        not refine_source_audit['overlap']
                        and int(small_cleanup_summary.get('accepted', 0)) > 0
                        and refine_cleanup_audit['overlap']
                    )
                    if (
                        refine_cleanup_audit['overlap']
                        and not allow_refine_source_overlap
                    ):
                        raise RuntimeError(
                            "Refine Layout cleanup introduced unexplained overlap")
            else:
                small_cleanup_summary = {
                    'enabled': False,
                    'reason': 'hard_surface_constraints_unavailable',
                    'initial_charts': len(final_charts),
                    'final_charts': len(final_charts),
                    'tests': 0,
                    'accepted': 0,
                }

        if not direct_refine_group_pack:
            bm, uv_layer = _select_all_uvs_for_operator(
                mesh, bm, uv_layer)
            scale_result = _call_uv_operator(
                bpy.ops.uv.average_islands_scale,
                _operator_name='average_islands_scale')
            if 'FINISHED' not in scale_result:
                raise RuntimeError("Average Islands Scale did not finish")
            bm, uv_layer = _refresh_edit_bmesh(mesh)
            _, final_charts = _uv_charts(bm, uv_layer)
            _scale_charts_by_visibility(
                bm, uv_layer, final_charts, visibility, detail_values, settings,
                hard_surface_mode=hard_surface_mode)
            if hard_surface_mode and (
                    getattr(settings, 'hard_surface_direction_lock', True)
                    or getattr(settings, 'hard_surface_align_cardinal', True)):
                direction_contract.clear()
                for chart in final_charts:
                    _align_hard_surface_chart(
                        obj,
                        chart,
                        uv_layer,
                        settings,
                        direction_contract=direction_contract,
                    )
            bm, uv_layer = _select_all_uvs_for_operator(
                mesh, bm, uv_layer)
            if hard_surface_mode:
                pack_result = _call_uv_operator(
                    bpy.ops.uv.pack_islands,
                    _operator_name='pack_islands',
                    udim_source='ACTIVE_UDIM',
                    rotate=False,
                    scale=True,
                    merge_overlap=False,
                    margin=settings.island_margin,
                    margin_method='FRACTION',
                    pin=False,
                    rotate_method='CARDINAL',
                    shape_method='CONCAVE',
                )
            else:
                pack_result = _call_uv_operator(
                    bpy.ops.uv.pack_islands,
                    _operator_name='pack_islands',
                    udim_source='ACTIVE_UDIM',
                    rotate=True,
                    scale=True,
                    merge_overlap=False,
                    margin=settings.island_margin,
                    margin_method='FRACTION',
                    pin=False,
                    shape_method='CONCAVE',
                )
            if 'FINISHED' not in pack_result:
                raise RuntimeError("Pack Islands did not finish")
        # UV packing is an edit-mesh operator.  Re-fetch the BMesh before any
        # final seam derivation so a stale wrapper cannot write pre-pack UVs
        # back over the packed coordinates (notably visible on cap/side
        # islands in Blender 3.3 and 5.x).
        bm, uv_layer = _refresh_edit_bmesh(mesh)
        if uv_usage == 'UNIQUE' and not direct_refine_group_pack:
            _normalize_uv_to_tile(bm, uv_layer, settings.island_margin)
        if hard_surface_mode and getattr(settings, 'hard_surface_hidden_collapse', False):
            _collapse_hidden_faces_to_origin(
                bm,
                uv_layer,
                visibility,
                overrides=overrides,
                explicit_only=True,
            )
        elif not hard_surface_mode:
            _collapse_hidden_faces_to_origin(bm, uv_layer, visibility)
        _derive_seams_from_uv(
            bm,
            uv_layer,
            original_seams,
            forced_cuts,
            preserve_boundaries=hard_surface_mode,
        )
        _, final_charts = _uv_charts(bm, uv_layer)
        if uv_usage == 'UNIQUE':
            problem_charts, mirrored_charts = _classify_problem_charts(
                final_charts, uv_layer, bm=bm)
            mirrored_chart_count += len(mirrored_charts)
            _mirror_charts_u(mirrored_charts, uv_layer)
            stabilized_charts = _stabilize_near_collinear_uv_ears(
                bm, uv_layer, problem_charts)
            if stabilized_charts:
                repaired_charts += stabilized_charts
                _derive_seams_from_uv(
                    bm,
                    uv_layer,
                    original_seams,
                    forced_cuts,
                    preserve_boundaries=hard_surface_mode,
                )
                _, final_charts = _uv_charts(bm, uv_layer)
                problem_charts, mirrored_charts = _classify_problem_charts(
                    final_charts, uv_layer, bm=bm)
                mirrored_chart_count += len(mirrored_charts)
                _mirror_charts_u(mirrored_charts, uv_layer)
            if problem_charts:
                safe_pack_retry = True
                repaired_charts += len(problem_charts)
                (
                    bm,
                    uv_layer,
                    final_charts,
                    problem_charts,
                    local_mirrored,
                ) = _repair_pack_until_valid(
                    obj,
                    mesh,
                    bm,
                    uv_layer,
                    problem_charts,
                    settings,
                    forced_cuts,
                    original_seams,
                    hard_surface_mode,
                    direction_contract,
                )
                mirrored_chart_count += local_mirrored
            if problem_charts:
                raise RuntimeError(
                    "Unique UV quality gate: {} folded or degenerate charts".format(
                        len(problem_charts)))
            audit = _audit_unique_layout(bm, uv_layer)
            if audit['overlap'] and not direct_refine_group_pack:
                safe_pack_retry = True
                # Exact concave packing can still leave numerical or cross-tile
                # collisions on dense imported layouts.  AABB is less compact,
                # but its disjoint rectangles provide a deterministic safety
                # fallback before the transaction is rejected.
                bm, uv_layer = _pack_unique_aabb(
                    mesh,
                    bm,
                    uv_layer,
                    settings,
                    preserve_direction=bool(
                        hard_surface_mode
                        and getattr(
                            settings, 'hard_surface_direction_lock', True
                        )
                    ),
                )
                _derive_seams_from_uv(
                    bm,
                    uv_layer,
                    original_seams,
                    forced_cuts,
                    preserve_boundaries=hard_surface_mode,
                )
                _, final_charts = _uv_charts(bm, uv_layer)
                problem_charts, mirrored_charts = _classify_problem_charts(
                    final_charts, uv_layer, bm=bm)
                mirrored_chart_count += len(mirrored_charts)
                _mirror_charts_u(mirrored_charts, uv_layer)
                stabilized_charts = _stabilize_near_collinear_uv_ears(
                    bm, uv_layer, problem_charts)
                if stabilized_charts:
                    repaired_charts += stabilized_charts
                    _derive_seams_from_uv(
                        bm,
                        uv_layer,
                        original_seams,
                        forced_cuts,
                        preserve_boundaries=hard_surface_mode,
                    )
                    _, final_charts = _uv_charts(bm, uv_layer)
                    problem_charts, mirrored_charts = _classify_problem_charts(
                        final_charts, uv_layer, bm=bm)
                    mirrored_chart_count += len(mirrored_charts)
                    _mirror_charts_u(mirrored_charts, uv_layer)
                if problem_charts:
                    repaired_charts += len(problem_charts)
                    (
                        bm,
                        uv_layer,
                        final_charts,
                        problem_charts,
                        local_mirrored,
                    ) = _repair_pack_until_valid(
                        obj,
                        mesh,
                        bm,
                        uv_layer,
                        problem_charts,
                        settings,
                        forced_cuts,
                        original_seams,
                        hard_surface_mode,
                        direction_contract,
                    )
                    mirrored_chart_count += local_mirrored
                if problem_charts:
                    raise RuntimeError(
                        "Unique UV quality gate: {} invalid charts after safe pack".format(
                            len(problem_charts)))
                audit = _audit_unique_layout(bm, uv_layer)
            audit_for_gate = dict(audit)
            if direct_refine_group_pack:
                # The rigid group layout below owns final tile placement. A
                # temporary cross-island collision is allowed only when the
                # conservative cleanup introduced it from a clean source.
                audit_for_gate['inside_tile'] = True
                if allow_refine_source_overlap:
                    audit_for_gate['overlap'] = False
            quality_error = _unique_layout_error(audit_for_gate)
            if quality_error is not None:
                raise RuntimeError(quality_error)
        else:
            for loop in (loop for face in bm.faces for loop in face.loops):
                uv = loop[uv_layer].uv
                if not (math.isfinite(uv.x) and math.isfinite(uv.y)):
                    raise RuntimeError(
                        "UV operation produced a non-finite coordinate")
        _restore_edit_mesh_hidden(bm, snapshot)
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        _restore_mesh_interaction_state(mesh, snapshot)
        post_layout_final_count = len(final_charts)
        if uv_usage == 'UNIQUE' and hard_surface_mode:
            if bool(getattr(settings, 'uv_group_layout_enabled', True)):
                layout_options = _group_layout_options(
                    settings,
                    strict_source_overlap=not allow_refine_source_overlap,
                )
                if direct_refine_group_pack:
                    if (
                            layout_options.align_geometry_direction
                            and layout_options.align_geometry_frame):
                        (
                            planar_frame_repair_summary,
                            frozen_group_direction_contract,
                        ) = _repair_refine_planar_frame_failures(
                            obj,
                            settings,
                            layout_options,
                        )
                        if int(planar_frame_repair_summary.get(
                                'accepted', 0)) > 0:
                            # A shape-corrected chart may temporarily overlap
                            # a neighbor around its preserved source center.
                            # The adaptive layout owns final disjoint placement.
                            allow_refine_source_overlap = True
                            layout_options = _group_layout_options(
                                settings,
                                strict_source_overlap=False,
                            )
                    else:
                        planar_frame_repair_summary = {
                            'enabled': False,
                            'reason': 'direction_or_frame_disabled',
                            'attempted': 0,
                            'accepted': 0,
                            'rejected': 0,
                        }
                if frozen_group_direction_contract is None:
                    group_layout_result = (
                        uv_group_layout.layout_active_uv_adaptive(
                            obj, layout_options))
                else:
                    group_layout_result = (
                        uv_group_layout.layout_active_uv_adaptive(
                            obj,
                            layout_options,
                            direction_contract=(
                                frozen_group_direction_contract),
                        ))
            else:
                group_layout_disabled_reason = 'disabled_by_setting'
        if uv_usage == 'UNIQUE':
            post_layout_final_count, _post_layout_audit = (
                _audit_unique_object_mesh(mesh))
            if (
                    hard_surface_mode
                    and bool(getattr(
                        settings, 'hard_surface_direction_lock', True))
                    and group_layout_result is None):
                _audit_directed_object_mesh(
                    obj,
                    settings,
                    direction_contract,
                )
        write_face_float(mesh, detail_values, name='vuv_detail_score')

        if refine_layout_mode:
            # A copied UV layer is a transaction boundary: Blender edit-mode
            # UV operations may rebuild loop custom data, and the derived seam
            # graph is only working state for stitching/layout.  Commit the
            # active target UV while restoring all source layers and the
            # object's artist seam flags exactly.
            _restore_refine_source_state(
                mesh, snapshot, target_layer_name)

        if group_layout_result is None:
            group_layout_summary = {
                'applied': False,
                'reason': group_layout_disabled_reason,
            }
            repeat_group_count = 0
            repeat_member_count = 0
            grouped_small_count = 0
            rotated_island_count = 0
            group_uniform_scale = 1.0
            group_fallback_used = False
        else:
            group_layout_summary = group_layout_result.to_dict(
                include_islands=False)
            arranged_repeat_groups = [
                group
                for group in group_layout_result.analysis.layout_groups
                if (
                    group.reason in {'MIRROR', 'ROTATIONAL', 'REPEATED'}
                    and len(group.anchor_ids) >= 2
                )
            ]
            repeat_group_count = len(arranged_repeat_groups)
            repeat_member_count = sum(
                len(group.anchor_ids) for group in arranged_repeat_groups)
            grouped_small_count = group_layout_result.grouped_small_islands
            rotated_island_count = group_layout_result.rotated_islands
            group_uniform_scale = group_layout_result.uniform_scale
            group_fallback_used = group_layout_result.fallback_used

        return OptimizeResult(
            initial_islands=initial_count,
            final_islands=post_layout_final_count,
            merge_tests=merge_tests,
            accepted_merges=accepted,
            rejected_stretch=rejected_stretch,
            rejected_overlap=rejected_overlap,
            rejected_topology=rejected_topology,
            detail_values=detail_values,
            rejected_probe=rejected_probe,
            probe_guided_merges=probe_guided_merges,
            initial_uv_mode=initial_uv_mode,
            uv_usage=uv_usage,
            forced_cuts=len(forced_cuts),
            protected_cuts=len(protected_cuts),
            repaired_charts=repaired_charts,
            mirrored_charts=mirrored_chart_count,
            safe_pack_retry=safe_pack_retry,
            local_repair_attempted=local_repair_attempted,
            local_repair_accepted=local_repair_accepted,
            local_repair_rejected=local_repair_rejected,
            smart_repair_input_charts=smart_repair_input_charts,
            smart_repair_output_charts=smart_repair_output_charts,
            small_cleanup_tests=int(
                small_cleanup_summary.get('tests', 0)),
            small_cleanup_merges=int(
                small_cleanup_summary.get('accepted', 0)),
            small_cleanup_initial_charts=int(
                small_cleanup_summary.get('initial_charts', 0)),
            small_cleanup_final_charts=int(
                small_cleanup_summary.get('final_charts', 0)),
            small_cleanup_summary=dict(small_cleanup_summary),
            planar_frame_repair_attempted=int(
                planar_frame_repair_summary.get('attempted', 0)),
            planar_frame_repair_accepted=int(
                planar_frame_repair_summary.get('accepted', 0)),
            planar_frame_repair_rejected=int(
                planar_frame_repair_summary.get('rejected', 0)),
            planar_frame_repair_summary=dict(
                planar_frame_repair_summary),
            group_layout_applied=group_layout_result is not None,
            group_layout_repeat_groups=repeat_group_count,
            group_layout_repeat_members=repeat_member_count,
            group_layout_small_grouped=grouped_small_count,
            group_layout_rotated_islands=rotated_island_count,
            group_layout_uniform_scale=group_uniform_scale,
            group_layout_fallback_used=group_fallback_used,
            group_layout_summary=group_layout_summary,
            group_layout_analysis=(
                group_layout_result.analysis.to_dict(include_islands=True)
                if group_layout_result is not None else {}
            ),
        )
    except Exception:
        # The operator is undoable, but a direct API call or an operator error
        # should also leave the user's original UVs and seams intact.
        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except (RuntimeError, ValueError):
            pass
        _restore_mesh_uv_state(mesh, snapshot)
        raise
