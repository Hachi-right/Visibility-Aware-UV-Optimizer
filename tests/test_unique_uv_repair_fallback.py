"""Blender 3.3/5.2 regression smoke for local Unique UV repair."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
import sys

import bmesh
import bpy
from mathutils import Vector


ADDON_PARENT = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDON_PARENT))

import visibility_uv_optimizer as addon  # noqa: E402
from visibility_uv_optimizer import uv_optimize  # noqa: E402


def _clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _activate(obj):
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _make_degenerate_cube(name):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = name
    mesh = obj.data
    mesh.name = name + "Mesh"
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for item in uv_layer.data:
        item.uv = Vector((0.0, 0.0))
    mesh.update()
    return obj


def _make_near_planar_ngon(name):
    vertices = []
    for index in range(7):
        angle = 2.0 * 3.141592653589793 * index / 7.0
        radius = 1.0 if index % 2 == 0 else 0.72
        vertices.append((
            math.cos(angle) * radius,
            math.sin(angle) * radius,
            (index % 3 - 1) * 1.0e-5,
        ))
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [tuple(range(7))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for item in uv_layer.data:
        item.uv = Vector((0.0, 0.0))
    mesh.update()
    return obj


def _make_convex_ngon(name, vertex_count=7):
    vertices = [
        (
            math.cos(2.0 * math.pi * index / vertex_count),
            math.sin(2.0 * math.pi * index / vertex_count),
            0.0,
        )
        for index in range(vertex_count)
    ]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [tuple(range(vertex_count))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for item in uv_layer.data:
        item.uv = Vector((0.0, 0.0))
    mesh.update()
    return obj


def _make_concave_ngon(name):
    vertices = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 2.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 2.0, 0.0),
    )
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for item in uv_layer.data:
        item.uv = Vector((0.0, 0.0))
    mesh.update()
    return obj


def _make_skinny_triangle(name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.5, 1.0e-5, 0.0),
            (1.0, 1.0, 0.0),
        ),
        [],
        ((0, 1, 2), (1, 3, 2)),
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    coordinates = (
        Vector((0.0, 0.0)),
        Vector((1.0, 0.0)),
        Vector((0.5, -1.0e-8)),
        Vector((1.0, 1.0)),
    )
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = coordinates[loop.vertex_index]
    mesh.update()
    return obj


def _make_connected_quad_strip(name, face_count):
    vertices = [
        (float(index), float(row), 0.0)
        for row in range(2)
        for index in range(face_count + 1)
    ]
    row_length = face_count + 1
    faces = [
        (
            index,
            index + 1,
            row_length + index + 1,
            row_length + index,
        )
        for index in range(face_count)
    ]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for item in uv_layer.data:
        item.uv = Vector((0.0, 0.0))
    mesh.update()
    return obj


def _make_small_fragment_pair(name, artist_seam):
    """Create two valid UV charts separated only at one shared planar edge."""

    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.05, 0.0, 0.0),
            (1.05, 1.0, 0.0),
        ),
        [],
        (
            (0, 1, 2, 3),
            (1, 4, 5, 2),
        ),
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    coordinates = (
        {
            0: Vector((0.05, 0.10)),
            1: Vector((0.65, 0.10)),
            2: Vector((0.65, 0.90)),
            3: Vector((0.05, 0.90)),
        },
        {
            1: Vector((0.75, 0.10)),
            4: Vector((0.79, 0.10)),
            5: Vector((0.79, 0.90)),
            2: Vector((0.75, 0.90)),
        },
    )
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = coordinates[polygon.index][vertex_index]
    shared_edge = next(
        edge for edge in mesh.edges
        if set(edge.vertices) == {1, 2}
    )
    shared_edge.use_seam = bool(artist_seam)
    mesh.update()
    return obj, shared_edge.index


def _make_small_fragment_chain(name, face_count=4):
    obj = _make_connected_quad_strip(name, face_count)
    uv_layer = obj.data.uv_layers.active
    for polygon in obj.data.polygons:
        offset = 0.05 + polygon.index * 0.22
        coordinates = (
            Vector((offset, 0.10)),
            Vector((offset + 0.16, 0.10)),
            Vector((offset + 0.16, 0.90)),
            Vector((offset, 0.90)),
        )
        for local_index, loop_index in enumerate(polygon.loop_indices):
            uv_layer.data[loop_index].uv = coordinates[local_index]
    obj.data.update()
    return obj


def _make_large_panel_fragment_chain(name, base_faces=100, fragments=2):
    obj = _make_connected_quad_strip(name, base_faces + fragments)
    uv_layer = obj.data.uv_layers.active
    base_width = 0.68
    for polygon in obj.data.polygons:
        if polygon.index < base_faces:
            left = 0.05 + base_width * polygon.index / base_faces
            right = 0.05 + base_width * (polygon.index + 1) / base_faces
        else:
            fragment_index = polygon.index - base_faces
            left = 0.77 + 0.04 * fragment_index
            right = left + 0.025
        coordinates = (
            Vector((left, 0.10)),
            Vector((right, 0.10)),
            Vector((right, 0.90)),
            Vector((left, 0.90)),
        )
        for local_index, loop_index in enumerate(polygon.loop_indices):
            uv_layer.data[loop_index].uv = coordinates[local_index]
    obj.data.update()
    return obj


def _make_distant_fragment_pair(name):
    x_values = (-1.0, 0.0, 10.0, 11.0)
    vertices = [
        (x_value, float(row), 0.0)
        for row in range(2)
        for x_value in x_values
    ]
    row_length = len(x_values)
    faces = [
        (index, index + 1, row_length + index + 1, row_length + index)
        for index in range(3)
    ]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_bounds = (
        (0.03, 0.11),
        (0.15, 0.85),
        (0.91, 0.97),
    )
    for polygon, (left, right) in zip(mesh.polygons, uv_bounds):
        coordinates = (
            Vector((left, 0.10)),
            Vector((right, 0.10)),
            Vector((right, 0.90)),
            Vector((left, 0.90)),
        )
        for local_index, loop_index in enumerate(polygon.loop_indices):
            uv_layer.data[loop_index].uv = coordinates[local_index]
    mesh.update()
    return obj


def _small_cleanup_fixture(
        name,
        *,
        face_classes=("PANEL", "BEVEL"),
        locked=False,
        forced=False,
        material_mismatch=False,
        reject_stretch=False):
    _clear_scene()
    obj, shared_edge_index = _make_small_fragment_pair(name, False)
    _activate(obj)
    bm, uv_layer = _enter_edit(obj)
    bm.edges[shared_edge_index].seam = True
    if material_mismatch:
        bm.faces[1].material_index = 1
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=False, destructive=False)
    bm, uv_layer = uv_optimize._refresh_edit_bmesh(obj.data)
    settings = SimpleNamespace(
        small_cleanup_enabled=True,
        small_island_faces=1,
        small_island_area_ratio=0.1,
        small_uv_area_ratio=0.1,
        small_boundary_ratio=0.25,
        small_structural_cleanup_enabled=True,
        small_structural_boundary_ratio=0.08,
        small_structural_angle=math.radians(60.0),
        small_structural_max_merges=4,
        small_cleanup_tests=8,
        small_cleanup_max_merges=4,
        small_cleanup_p95=1.35,
        small_cleanup_max_stretch=2.0,
        small_chain_max_faces=96,
        small_chain_max_absorptions=8,
        small_chain_max_diameter_ratio=0.0,
        max_p95_stretch=1.5,
        max_stretch=3.0,
        respect_sharp=False,
        island_margin=0.002,
    )
    constraints = SimpleNamespace(
        locked_cuts={shared_edge_index} if locked else set(),
        forced_cuts={shared_edge_index} if forced else set(),
        face_classes={
            index: face_classes[index]
            for index in range(len(face_classes))
        },
    )
    original_try_merge = uv_optimize._try_merge
    if reject_stretch:
        uv_optimize._try_merge = (
            lambda *_args, **_kwargs: (False, "STRETCH")
        )
    try:
        result = uv_optimize.small_island_cleanup.stitch_small_islands(
            obj,
            bm,
            uv_layer,
            settings,
            constraints,
            uv_optimize,
        )
    finally:
        uv_optimize._try_merge = original_try_merge
    return obj, shared_edge_index, result


def _small_cleanup_chain_fixture(name, **chain_limits):
    _clear_scene()
    obj = _make_small_fragment_chain(name)
    _activate(obj)
    bm, uv_layer = _enter_edit(obj)
    settings = SimpleNamespace(
        small_cleanup_enabled=True,
        small_island_faces=1,
        small_island_area_ratio=0.3,
        small_uv_area_ratio=0.3,
        small_boundary_ratio=0.2,
        small_structural_cleanup_enabled=True,
        small_structural_boundary_ratio=0.08,
        small_structural_angle=math.radians(60.0),
        small_structural_max_merges=8,
        small_cleanup_tests=16,
        small_cleanup_max_merges=8,
        small_cleanup_p95=1.35,
        small_cleanup_max_stretch=2.0,
        small_chain_max_faces=chain_limits.get("max_faces", 96),
        small_chain_max_absorptions=chain_limits.get("max_absorptions", 8),
        small_chain_max_diameter_ratio=chain_limits.get(
            "max_diameter_ratio", 0.0
        ),
        max_p95_stretch=1.5,
        max_stretch=3.0,
        respect_sharp=False,
        island_margin=0.002,
    )
    constraints = SimpleNamespace(
        locked_cuts=set(),
        forced_cuts=set(),
        face_classes={index: "PANEL" for index in range(4)},
    )
    return uv_optimize.small_island_cleanup.stitch_small_islands(
        obj,
        bm,
        uv_layer,
        settings,
        constraints,
        uv_optimize,
    )


def _run_panel_fragment_cleanup(
        obj,
        *,
        face_count,
        area_ratio,
        uv_area_ratio,
        max_faces,
        max_absorptions,
        max_diameter_ratio):
    _activate(obj)
    bm, uv_layer = _enter_edit(obj)
    settings = SimpleNamespace(
        small_cleanup_enabled=True,
        small_island_faces=1,
        small_island_area_ratio=area_ratio,
        small_uv_area_ratio=uv_area_ratio,
        small_boundary_ratio=0.2,
        small_structural_cleanup_enabled=True,
        small_structural_boundary_ratio=0.08,
        small_structural_angle=math.radians(60.0),
        small_structural_max_merges=8,
        small_cleanup_tests=16,
        small_cleanup_max_merges=8,
        small_cleanup_p95=1.35,
        small_cleanup_max_stretch=2.0,
        small_chain_max_faces=max_faces,
        small_chain_max_absorptions=max_absorptions,
        small_chain_max_diameter_ratio=max_diameter_ratio,
        max_p95_stretch=1.5,
        max_stretch=3.0,
        respect_sharp=False,
        island_margin=0.002,
    )
    constraints = SimpleNamespace(
        locked_cuts=set(),
        forced_cuts=set(),
        face_classes={index: "PANEL" for index in range(face_count)},
    )
    return uv_optimize.small_island_cleanup.stitch_small_islands(
        obj,
        bm,
        uv_layer,
        settings,
        constraints,
        uv_optimize,
    )


def _topology_signature(mesh):
    return (
        tuple(tuple(round(float(value), 9) for value in vertex.co)
              for vertex in mesh.vertices),
        tuple(tuple(sorted(int(index) for index in edge.vertices))
              for edge in mesh.edges),
        tuple(tuple(int(index) for index in polygon.vertices)
              for polygon in mesh.polygons),
    )


def _state_signature(mesh):
    active = mesh.uv_layers.active
    return (
        _topology_signature(mesh),
        tuple(bool(edge.use_seam) for edge in mesh.edges),
        tuple((round(float(item.uv.x), 9), round(float(item.uv.y), 9))
              for item in active.data),
        tuple(layer.name for layer in mesh.uv_layers),
        active.name,
    )


def _enter_edit(obj):
    _activate(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bm, uv_layer = uv_optimize._refresh_edit_bmesh(obj.data)
    uv_optimize._set_edit_mesh_hidden(bm, hidden=False)
    uv_optimize._set_uv_selection(
        bm, uv_layer, (face.index for face in bm.faces))
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=False, destructive=False)
    return uv_optimize._refresh_edit_bmesh(obj.data)


def _assert_locally_valid(bm, uv_layer):
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert not problem, "remaining problem charts: {}".format(len(problem))
    assert not mirrored, "remaining mirrored charts: {}".format(len(mirrored))


def _test_tree_cut_unwrap():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_LocalRepairUnwrap")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1

    original_projection = uv_optimize._project_chart_faces_individually

    def projection_must_not_run(*_args, **_kwargs):
        raise AssertionError("tree-cut unwrap unexpectedly needed face projection")

    uv_optimize._project_chart_faces_individually = projection_must_not_run
    forced_cuts = set()
    try:
        bm, uv_layer, _mirrored_count = (
            uv_optimize._repair_remaining_problem_charts(
                obj.data,
                bm,
                uv_layer,
                problem,
                SimpleNamespace(island_margin=0.002),
                forced_cuts,
            )
        )
    finally:
        uv_optimize._project_chart_faces_individually = original_projection
    assert forced_cuts, "closed chart did not receive necessary cycle cuts"
    _assert_locally_valid(bm, uv_layer)
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_single_convex_face_projection_fallback():
    _clear_scene()
    obj = _make_convex_ngon("VUV_LocalRepairProjection")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1
    assert uv_optimize._face_is_convex_for_projection_fallback(
        bm.faces[0])

    original_call = uv_optimize._call_uv_operator
    original_projection = uv_optimize._project_face_planar_positive
    fallback_triggered = {'value': False}

    def cancel_unwrap(operator, _operator_name=None, **kwargs):
        if _operator_name == 'unwrap':
            return {'CANCELLED'}
        return original_call(operator, _operator_name=_operator_name, **kwargs)

    def fail_primary_projection(*_args, **_kwargs):
        fallback_triggered['value'] = True
        return None

    uv_optimize._call_uv_operator = cancel_unwrap
    uv_optimize._project_face_planar_positive = fail_primary_projection
    forced_cuts = set()
    try:
        bm, uv_layer, _mirrored_count = (
            uv_optimize._repair_remaining_problem_charts(
                obj.data,
                bm,
                uv_layer,
                problem,
                SimpleNamespace(island_margin=0.002),
                forced_cuts,
            )
        )
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._project_face_planar_positive = original_projection
    assert fallback_triggered['value']
    local_charts = uv_optimize._uv_charts_for_faces(
        list(bm.faces), uv_layer)
    assert len(local_charts) == 1, len(local_charts)
    _assert_locally_valid(bm, uv_layer)
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_multi_face_chart_is_not_fragmented_by_projection_fallback():
    _clear_scene()
    face_count = 38
    obj = _make_connected_quad_strip(
        "VUV_LocalRepairMultiFaceGuard", face_count)
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1
    assert len(problem[0]) == face_count
    saved_uv = uv_optimize._save_uv(list(bm.faces), uv_layer)
    saved_seams = tuple(bool(edge.seam) for edge in bm.edges)
    original_projection = uv_optimize._project_face_planar_positive

    def projection_must_not_run(*_args, **_kwargs):
        raise AssertionError("multi-face chart entered face projection fallback")

    uv_optimize._project_face_planar_positive = projection_must_not_run
    forced_cuts = set()
    try:
        valid, mirrored_count = uv_optimize._project_chart_faces_individually(
            bm, uv_layer, problem[0], forced_cuts)
    finally:
        uv_optimize._project_face_planar_positive = original_projection
    assert not valid and mirrored_count == 0
    assert not forced_cuts
    assert tuple(bool(edge.seam) for edge in bm.edges) == saved_seams
    for face in bm.faces:
        for loop, uv in zip(face.loops, saved_uv[face.index]):
            assert (loop[uv_layer].uv - uv).length < 1.0e-12
    local_charts = uv_optimize._uv_charts_for_faces(
        list(bm.faces), uv_layer)
    assert len(local_charts) == 1, len(local_charts)
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_single_concave_face_does_not_use_convex_fallback():
    _clear_scene()
    obj = _make_concave_ngon("VUV_LocalRepairConcaveGuard")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1 and len(problem[0]) == 1
    assert not uv_optimize._face_is_convex_for_projection_fallback(
        bm.faces[0])

    original_projection = uv_optimize._project_face_planar_positive

    def projection_must_not_run(*_args, **_kwargs):
        raise AssertionError("concave face entered convex projection fallback")

    uv_optimize._project_face_planar_positive = projection_must_not_run
    forced_cuts = set()
    try:
        valid, mirrored_count = uv_optimize._project_chart_faces_individually(
            bm, uv_layer, problem[0], forced_cuts)
    finally:
        uv_optimize._project_face_planar_positive = original_projection
    assert not valid and mirrored_count == 0
    assert not forced_cuts
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_best_effort_local_repair_accepts_connected_chart():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_LocalRepairBestEffort")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1

    original_call = uv_optimize._call_uv_operator
    calls = []

    def track_calls(operator, _operator_name=None, **kwargs):
        calls.append(_operator_name)
        if _operator_name == 'smart_project':
            raise AssertionError("best-effort local repair called Smart UV")
        return original_call(
            operator, _operator_name=_operator_name, **kwargs)

    uv_optimize._call_uv_operator = track_calls
    forced_cuts = set()
    try:
        bm, uv_layer, accepted, rejected, _mirrored_count = (
            uv_optimize._try_local_problem_chart_repairs(
                obj.data,
                bm,
                uv_layer,
                problem,
                SimpleNamespace(
                    island_margin=0.002,
                    max_p95_stretch=1.5,
                    max_stretch=3.0,
                ),
                forced_cuts,
            )
        )
    finally:
        uv_optimize._call_uv_operator = original_call
    assert calls == ['unwrap'], calls
    assert accepted == 1 and rejected == 0
    assert forced_cuts
    assert len(uv_optimize._uv_charts_for_faces(
        list(bm.faces), uv_layer)) == 1
    _assert_locally_valid(bm, uv_layer)
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_best_effort_local_repair_restores_rejection():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_LocalRepairBestEffortRestore")
    obj.data.edges[0].use_seam = True
    obj.data.update()
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1
    saved_uv = uv_optimize._save_uv(list(bm.faces), uv_layer)
    saved_seams = tuple(bool(edge.seam) for edge in bm.edges)

    original_call = uv_optimize._call_uv_operator

    def reject_unwrap(operator, _operator_name=None, **kwargs):
        if _operator_name == 'unwrap':
            return {'CANCELLED'}
        if _operator_name == 'smart_project':
            raise AssertionError("best-effort rejection called Smart UV")
        return original_call(
            operator, _operator_name=_operator_name, **kwargs)

    uv_optimize._call_uv_operator = reject_unwrap
    forced_cuts = {987654}
    try:
        bm, uv_layer, accepted, rejected, mirrored_count = (
            uv_optimize._try_local_problem_chart_repairs(
                obj.data,
                bm,
                uv_layer,
                problem,
                SimpleNamespace(
                    island_margin=0.002,
                    max_p95_stretch=1.5,
                    max_stretch=3.0,
                ),
                forced_cuts,
            )
        )
    finally:
        uv_optimize._call_uv_operator = original_call
    assert accepted == 0 and rejected == 1 and mirrored_count == 0
    assert forced_cuts == {987654}
    assert tuple(bool(edge.seam) for edge in bm.edges) == saved_seams
    for face in bm.faces:
        for loop, uv in zip(face.loops, saved_uv[face.index]):
            assert (loop[uv_layer].uv - uv).length < 1.0e-12
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_near_planar_ngon_strict_rejection():
    _clear_scene()
    obj = _make_near_planar_ngon("VUV_LocalRepairNgon")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    for index, loop in enumerate(bm.faces[0].loops):
        loop[uv_layer].uv = Vector((index * 0.125, index * 0.03125))
    before_uv = [loop[uv_layer].uv.copy() for loop in bm.faces[0].loops]
    original_classify = uv_optimize._classify_problem_charts
    calls = {'count': 0}

    def reject_projection(charts, layer, bm=None):
        calls['count'] += 1
        return list(charts), []

    uv_optimize._classify_problem_charts = reject_projection
    try:
        dimensions = uv_optimize._project_face_planar_positive(
            bm, bm.faces[0], uv_layer, 0.0)
    finally:
        uv_optimize._classify_problem_charts = original_classify
    assert dimensions is None
    assert calls['count'] >= 2
    after_uv = [loop[uv_layer].uv.copy() for loop in bm.faces[0].loops]
    assert all(
        (after - before).length < 1.0e-12
        for before, after in zip(before_uv, after_uv)
    )
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_skinny_ear_stabilization():
    _clear_scene()
    obj = _make_skinny_triangle("VUV_LocalRepairSkinnyEar")
    bm, uv_layer = _enter_edit(obj)
    face = bm.faces[0]
    before = [loop[uv_layer].uv.copy() for loop in face.loops]
    shared_edge = next(
        edge for edge in bm.edges
        if {vert.index for vert in edge.verts} == {1, 2}
    )
    assert not shared_edge.seam
    stabilized = uv_optimize._stabilize_near_collinear_uv_ears(
        bm, uv_layer, [[face]])
    assert stabilized == 1
    after = [loop[uv_layer].uv.copy() for loop in face.loops]
    assert (after[0] - before[0]).length < 1.0e-12
    assert (after[1] - before[1]).length < 1.0e-12
    assert 9.9e-5 <= after[2].y <= 1.01e-4, after[2].y
    valid, _mirrored = uv_optimize._validate_local_repair(
        bm, uv_layer, [face])
    assert valid
    uv_optimize._derive_seams_from_uv(bm, uv_layer, set())
    assert shared_edge.seam


def _test_welded_vertex_fan_stops_at_chart_cuts():
    _clear_scene()
    obj = _make_skinny_triangle("VUV_LocalRepairWeldedFan")
    bm, uv_layer = _enter_edit(obj)
    face = bm.faces[0]
    neighbor = bm.faces[1]
    start_loop = next(loop for loop in face.loops if loop.vert.index == 2)
    shared_edge = next(
        edge for edge in bm.edges
        if {vert.index for vert in edge.verts} == {1, 2}
    )

    welded = uv_optimize._welded_chart_vertex_loops(
        [face, neighbor], start_loop, uv_layer)
    assert {loop.face.index for loop in welded} == {0, 1}

    shared_edge.seam = True
    cut_by_seam = uv_optimize._welded_chart_vertex_loops(
        [face, neighbor], start_loop, uv_layer)
    assert [loop.face.index for loop in cut_by_seam] == [0]

    shared_edge.seam = False
    neighbor_endpoint = next(
        loop for loop in neighbor.loops if loop.vert.index == 1)
    neighbor_endpoint[uv_layer].uv.x += 0.25
    cut_by_uv_split = uv_optimize._welded_chart_vertex_loops(
        [face, neighbor], start_loop, uv_layer)
    assert [loop.face.index for loop in cut_by_uv_split] == [0]


def _test_multi_face_local_repair_stabilizes_skinny_ear():
    _clear_scene()
    obj = _make_skinny_triangle("VUV_LocalRepairConnectedSkinnyEar")
    before_topology = _topology_signature(obj.data)
    bm, uv_layer = _enter_edit(obj)
    _, charts = uv_optimize._uv_charts(bm, uv_layer)
    problem, _mirrored = uv_optimize._classify_problem_charts(
        charts, uv_layer, bm=bm)
    assert len(problem) == 1 and len(problem[0]) == 2
    partition_before = tuple(sorted(
        tuple(sorted(face.index for face in chart))
        for chart in uv_optimize._uv_charts_for_faces(
            list(bm.faces), uv_layer)
    ))

    original_call = uv_optimize._call_uv_operator
    original_projection = uv_optimize._project_chart_faces_individually

    def keep_connected_skinny_uv(operator, _operator_name=None, **kwargs):
        if _operator_name == 'unwrap':
            return {'FINISHED'}
        return original_call(
            operator, _operator_name=_operator_name, **kwargs)

    def projection_must_not_run(*_args, **_kwargs):
        raise AssertionError(
            "connected skinny-ear chart unexpectedly used projection fallback")

    uv_optimize._call_uv_operator = keep_connected_skinny_uv
    uv_optimize._project_chart_faces_individually = projection_must_not_run
    forced_cuts = set()
    try:
        bm, uv_layer, _mirrored_count = (
            uv_optimize._repair_remaining_problem_charts(
                obj.data,
                bm,
                uv_layer,
                problem,
                SimpleNamespace(island_margin=0.002),
                forced_cuts,
            )
        )
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._project_chart_faces_individually = original_projection

    partition_after = tuple(sorted(
        tuple(sorted(face.index for face in chart))
        for chart in uv_optimize._uv_charts_for_faces(
            list(bm.faces), uv_layer)
    ))
    assert partition_after == partition_before
    _assert_locally_valid(bm, uv_layer)
    bmesh.update_edit_mesh(
        obj.data, loop_triangles=True, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert _topology_signature(obj.data) == before_topology


def _test_refine_layout_does_not_reseed_valid_uv():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_RefineExistingLayout")
    _activate(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    result = bpy.ops.uv.smart_project(
        angle_limit=math.radians(66.0),
        island_margin=0.002,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
    )
    assert 'FINISHED' in result
    bpy.ops.object.mode_set(mode='OBJECT')
    before_topology = _topology_signature(obj.data)

    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'REFINE_LAYOUT'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = False
    settings.small_cleanup_enabled = False
    settings.uv_group_layout_enabled = True

    original_call = uv_optimize._call_uv_operator
    original_repair_pack = uv_optimize._repair_pack_until_valid
    original_group_layout = uv_optimize.uv_group_layout.layout_active_uv_adaptive
    calls = []
    repair_pack_calls = {'count': 0}
    group_layout_calls = {'count': 0}

    def reject_seed_operators(operator, _operator_name=None, **kwargs):
        calls.append(_operator_name)
        if _operator_name in {
                'unwrap',
                'smart_project',
                'average_islands_scale',
                'pack_islands',
        }:
            raise AssertionError(
                "Refine Layout called destructive/global UV operator {}".format(
                    _operator_name))
        return original_call(
            operator, _operator_name=_operator_name, **kwargs)

    def reject_repair_pack(*_args, **_kwargs):
        repair_pack_calls['count'] += 1
        raise AssertionError(
            "Refine Layout sent a valid UV map to repair-pack fallback")

    def track_group_layout(*args, **kwargs):
        group_layout_calls['count'] += 1
        return original_group_layout(*args, **kwargs)

    uv_optimize._call_uv_operator = reject_seed_operators
    uv_optimize._repair_pack_until_valid = reject_repair_pack
    uv_optimize.uv_group_layout.layout_active_uv_adaptive = track_group_layout
    try:
        optimize_result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings)
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._repair_pack_until_valid = original_repair_pack
        uv_optimize.uv_group_layout.layout_active_uv_adaptive = original_group_layout
    assert optimize_result.initial_uv_mode == 'REFINE_LAYOUT'
    assert not ({
        'unwrap',
        'smart_project',
        'average_islands_scale',
        'pack_islands',
    } & set(calls)), calls
    assert repair_pack_calls['count'] == 0
    assert group_layout_calls['count'] == 1
    assert optimize_result.group_layout_applied
    uv_optimize._audit_unique_object_mesh(obj.data)
    assert _topology_signature(obj.data) == before_topology


def _run_refine_fragment_pair(
        artist_seam,
        inflate_accepted_merge=1.0,
        preserve_seams=True,
        structural_classes=False):
    _clear_scene()
    suffix = "Artist" if artist_seam else "Derived"
    obj, shared_edge_index = _make_small_fragment_pair(
        "VUV_RefineFragment" + suffix,
        artist_seam,
    )
    _activate(obj)
    before_topology = _topology_signature(obj.data)
    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'REFINE_LAYOUT'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = bool(preserve_seams)
    settings.respect_materials = True
    settings.hard_surface_respect_sharp = False
    settings.hard_surface_hidden_collapse = False
    settings.small_cleanup_enabled = True
    settings.small_island_faces = 1
    settings.small_island_area_ratio = 0.1
    settings.small_uv_area_ratio = 0.1
    settings.small_boundary_ratio = 0.25
    settings.small_cleanup_p95 = 1.35
    settings.small_cleanup_max_stretch = 2.0
    settings.uv_group_layout_enabled = True

    original_classifier = (
        uv_optimize.hard_surface.classify_edge_constraints)
    original_try_merge = uv_optimize._try_merge
    classifications = []

    def track_classifier(*args, **kwargs):
        constraints = original_classifier(*args, **kwargs)
        if structural_classes:
            constraints.face_classes.update({0: 'PANEL', 1: 'BEVEL'})
        classifications.append({
            'locked': set(constraints.locked_cuts),
            'forced': set(constraints.forced_cuts),
        })
        return constraints

    def inflate_merge_result(*args, **kwargs):
        face_indices = tuple(face.index for face in args[3])
        accepted, reason = original_try_merge(*args, **kwargs)
        if not accepted or inflate_accepted_merge <= 1.0:
            return accepted, reason
        current_bm, current_uv = uv_optimize._refresh_edit_bmesh(obj.data)
        loops = [
            loop
            for face_index in face_indices
            for loop in current_bm.faces[face_index].loops
        ]
        center = sum(
            (loop[current_uv].uv for loop in loops),
            Vector((0.0, 0.0)),
        ) / len(loops)
        for loop in loops:
            uv = loop[current_uv].uv
            loop[current_uv].uv = center + (
                uv - center
            ) * inflate_accepted_merge
        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=False,
            destructive=False,
        )
        return accepted, reason

    uv_optimize.hard_surface.classify_edge_constraints = track_classifier
    uv_optimize._try_merge = inflate_merge_result
    try:
        result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings)
    finally:
        uv_optimize.hard_surface.classify_edge_constraints = original_classifier
        uv_optimize._try_merge = original_try_merge

    assert len(classifications) == 1, classifications
    uv_optimize._audit_unique_object_mesh(obj.data)
    assert _topology_signature(obj.data) == before_topology
    return obj, shared_edge_index, result, classifications[0]


def _test_refine_artist_seam_blocks_small_cleanup():
    obj, shared_edge_index, result, classification = (
        _run_refine_fragment_pair(artist_seam=True))
    assert shared_edge_index in classification['locked'], classification
    assert shared_edge_index not in classification['forced'], classification
    assert result.small_cleanup_initial_charts == 2
    assert result.small_cleanup_final_charts == 2
    assert result.small_cleanup_merges == 0
    assert result.final_islands == 2
    assert result.small_cleanup_summary.get(
        'filter_funnel', {}).get('hard_cut', 0) >= 1
    assert obj.data.edges[shared_edge_index].use_seam


def _test_refine_derived_uv_cut_allows_safe_small_cleanup():
    obj, shared_edge_index, result, classification = (
        _run_refine_fragment_pair(artist_seam=False))
    assert shared_edge_index not in classification['locked'], classification
    assert shared_edge_index not in classification['forced'], classification
    assert result.small_cleanup_initial_charts == 2
    assert result.small_cleanup_final_charts == 1
    assert result.small_cleanup_merges == 1
    assert result.final_islands == 1
    assert result.small_cleanup_summary.get(
        'filter_funnel', {}).get('eligible', 0) >= 1
    assert not obj.data.edges[shared_edge_index].use_seam


def _test_refine_unlocked_seam_allows_structural_cleanup():
    obj, shared_edge_index, result, classification = (
        _run_refine_fragment_pair(
            artist_seam=True,
            preserve_seams=False,
            structural_classes=True,
        )
    )
    assert shared_edge_index not in classification['locked'], classification
    assert shared_edge_index not in classification['forced'], classification
    assert result.small_cleanup_initial_charts == 2
    assert result.small_cleanup_final_charts == 1
    assert result.small_cleanup_merges == 1
    assert result.small_cleanup_summary.get('accepted_structural', 0) == 1
    assert not obj.data.edges[shared_edge_index].use_seam


def _test_refine_copy_preserves_source_layer_and_mesh_seams():
    _clear_scene()
    obj, shared_edge_index = _make_small_fragment_pair(
        "VUV_RefineCopyIsolation", True)
    _activate(obj)
    mesh = obj.data
    target = mesh.uv_layers.active
    target.name = "VUV_RefineTarget"
    source = mesh.uv_layers.new(name="VUV_RefineSource")
    for source_item, target_item in zip(source.data, target.data):
        source_item.uv = target_item.uv + Vector((3.0, 5.0))
    mesh.uv_layers.active = target
    source_before = tuple(
        (float(item.uv.x), float(item.uv.y)) for item in source.data)
    seams_before = tuple(bool(edge.use_seam) for edge in mesh.edges)
    target_before = tuple(
        (float(item.uv.x), float(item.uv.y)) for item in target.data)

    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'REFINE_LAYOUT'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = False
    settings.respect_materials = True
    settings.hard_surface_respect_sharp = False
    settings.hard_surface_hidden_collapse = False
    settings.small_cleanup_enabled = True
    settings.small_island_faces = 1
    settings.small_island_area_ratio = 0.1
    settings.small_uv_area_ratio = 0.1
    settings.small_boundary_ratio = 0.25
    settings.small_cleanup_p95 = 1.35
    settings.small_cleanup_max_stretch = 2.0
    settings.uv_group_layout_enabled = True

    original_classifier = uv_optimize.hard_surface.classify_edge_constraints

    def structural_classifier(*args, **kwargs):
        constraints = original_classifier(*args, **kwargs)
        constraints.face_classes.update({0: 'PANEL', 1: 'BEVEL'})
        return constraints

    uv_optimize.hard_surface.classify_edge_constraints = structural_classifier
    try:
        result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings)
    finally:
        uv_optimize.hard_surface.classify_edge_constraints = original_classifier

    source_after = mesh.uv_layers.get("VUV_RefineSource")
    target_after = mesh.uv_layers.get("VUV_RefineTarget")
    assert source_after is not None
    assert target_after is not None
    assert tuple(
        (float(item.uv.x), float(item.uv.y)) for item in source_after.data
    ) == source_before
    assert tuple(bool(edge.use_seam) for edge in mesh.edges) == seams_before
    assert mesh.uv_layers.active.name == "VUV_RefineTarget"
    assert tuple(
        (float(item.uv.x), float(item.uv.y)) for item in target_after.data
    ) != target_before
    assert result.small_cleanup_merges == 1, result.small_cleanup_summary
    assert result.final_islands == 1
    assert mesh.edges[shared_edge_index].use_seam
    uv_optimize._audit_unique_object_mesh(mesh)


def _test_refine_cleanup_normalizes_unwrap_density_explosion():
    obj, shared_edge_index, result, _classification = (
        _run_refine_fragment_pair(
            artist_seam=False,
            inflate_accepted_merge=100.0,
        )
    )
    summary = result.small_cleanup_summary
    assert result.small_cleanup_merges == 1, summary
    assert result.final_islands == 1
    assert summary.get("density_normalized", 0) == 1, summary
    assert summary.get("max_density_ratio_before_normalize", 1.0) > 1000.0
    assert summary.get("max_density_ratio_after_normalize", 2.0) <= 1.00005
    assert summary.get("rejected_texel_density", 0) == 0
    assert not obj.data.edges[shared_edge_index].use_seam
    uv_optimize._audit_unique_object_mesh(obj.data)


def _test_structural_small_fragment_lane():
    obj, shared_edge_index, result = _small_cleanup_fixture(
        "VUV_StructuralPanelBevel")
    assert result["accepted"] == 1, result
    assert result["accepted_strict"] == 0, result
    assert result["accepted_structural"] == 1, result
    assert result["final_charts"] == 1, result
    assert result["filter_funnel"].get("class_mismatch", 0) >= 1
    assert result["filter_funnel"].get("structural_eligible", 0) >= 1
    assert not obj.data.edges[shared_edge_index].use_seam


def _test_structural_lane_keeps_hard_boundaries():
    cases = (
        ("Locked", {"locked": True}, "hard_cut"),
        ("Forced", {"forced": True}, "hard_cut"),
        (
            "Material",
            {"material_mismatch": True},
            "boundary_material_mismatch",
        ),
        (
            "CylinderCap",
            {"face_classes": ("PANEL", "RADIAL_CAP")},
            "structural_class_mismatch",
        ),
    )
    for suffix, kwargs, reason in cases:
        _obj, _edge_index, result = _small_cleanup_fixture(
            "VUV_StructuralReject" + suffix,
            **kwargs
        )
        assert result["accepted"] == 0, (suffix, result)
        assert result["final_charts"] == 2, (suffix, result)
        assert result["filter_funnel"].get(reason, 0) >= 1, (
            suffix, result
        )


def _test_structural_lane_rolls_back_stretch_rejection():
    obj, shared_edge_index, result = _small_cleanup_fixture(
        "VUV_StructuralStretchRollback",
        reject_stretch=True,
    )
    assert result["accepted"] == 0, result
    assert result["rejected_stretch"] == 1, result
    assert result["final_charts"] == 2, result
    bm, _uv_layer = uv_optimize._refresh_edit_bmesh(obj.data)
    assert bm.edges[shared_edge_index].seam


def _test_small_cleanup_extends_a_local_fragment_chain():
    result = _small_cleanup_chain_fixture("VUV_SmallChain")
    assert result["accepted"] == 3, result
    assert result["accepted_chain_extensions"] == 2, result
    assert result["max_chain_absorptions"] == 3, result
    assert result["max_chain_faces"] == 3, result
    assert result["final_charts"] == 1, result


def _test_small_cleanup_chain_limits_are_local():
    cases = (
        (
            "Faces",
            {"max_faces": 2},
            "chain_face_limit",
            2,
        ),
        (
            "Absorptions",
            {"max_absorptions": 1},
            "chain_absorption_limit",
            1,
        ),
        (
            "Diameter",
            {"max_diameter_ratio": 0.70},
            "chain_diameter_limit",
            1,
        ),
    )
    for suffix, limits, reason, maximum_absorptions in cases:
        result = _small_cleanup_chain_fixture(
            "VUV_SmallChain" + suffix,
            **limits
        )
        assert result["accepted"] == 2, (suffix, result)
        assert result["final_charts"] == 2, (suffix, result)
        assert result["max_chain_absorptions"] == maximum_absorptions, (
            suffix, result
        )
        assert result["filter_funnel"].get(reason, 0) >= 1, (
            suffix, result
        )


def _test_large_panel_is_not_charged_to_fragment_budget():
    _clear_scene()
    obj = _make_large_panel_fragment_chain("VUV_LargePanelFragments")
    result = _run_panel_fragment_cleanup(
        obj,
        face_count=102,
        area_ratio=0.02,
        uv_area_ratio=0.05,
        max_faces=2,
        max_absorptions=2,
        max_diameter_ratio=0.05,
    )
    assert result["initial_charts"] == 3, result
    assert result["accepted"] == 2, result
    assert result["accepted_chain_extensions"] == 1, result
    assert result["max_chain_faces"] == 2, result
    assert result["max_chain_diameter_ratio"] < 0.05, result
    assert result["final_charts"] == 1, result


def _test_distant_fragments_do_not_form_asset_chain():
    _clear_scene()
    obj = _make_distant_fragment_pair("VUV_DistantFragments")
    result = _run_panel_fragment_cleanup(
        obj,
        face_count=3,
        area_ratio=0.10,
        uv_area_ratio=0.10,
        max_faces=8,
        max_absorptions=8,
        max_diameter_ratio=0.15,
    )
    assert result["initial_charts"] == 3, result
    assert result["accepted"] == 1, result
    assert result["max_chain_faces"] == 1, result
    assert result["max_chain_absorptions"] == 1, result
    assert result["final_charts"] == 2, result
    assert result["filter_funnel"].get("chain_diameter_limit", 0) >= 1, result


def _test_final_pack_gate_repair():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_FinalGateRepair")
    before_topology = _topology_signature(obj.data)
    _activate(obj)
    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'LEGACY_SMART'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = True
    settings.uv_group_layout_enabled = False

    original_call = uv_optimize._call_uv_operator
    original_fallback = uv_optimize._repair_remaining_problem_charts
    pack_calls = {'count': 0}
    fallback_calls = {'count': 0}

    def inject_after_first_pack(operator, _operator_name=None, **kwargs):
        result = original_call(
            operator, _operator_name=_operator_name, **kwargs)
        if _operator_name == 'pack_islands':
            pack_calls['count'] += 1
            if pack_calls['count'] == 1 and 'FINISHED' in result:
                bm, uv_layer = uv_optimize._refresh_edit_bmesh(obj.data)
                point = bm.faces[0].loops[0][uv_layer].uv.copy()
                for loop in bm.faces[0].loops:
                    loop[uv_layer].uv = point
                bmesh.update_edit_mesh(
                    obj.data, loop_triangles=False, destructive=False)
        return result

    def track_fallback(*args, **kwargs):
        fallback_calls['count'] += 1
        return original_fallback(*args, **kwargs)

    uv_optimize._call_uv_operator = inject_after_first_pack
    uv_optimize._repair_remaining_problem_charts = track_fallback
    try:
        result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings)
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._repair_remaining_problem_charts = original_fallback
    assert fallback_calls['count'] >= 1
    assert result.safe_pack_retry
    assert result.repaired_charts >= 1
    uv_optimize._audit_unique_object_mesh(obj.data)
    assert _topology_signature(obj.data) == before_topology


def _test_hard_surface_repair_reapplies_direction():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_DirectedRepair")
    before_topology = _topology_signature(obj.data)
    _activate(obj)
    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'HARD_SURFACE'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = False
    settings.small_cleanup_enabled = False
    settings.uv_group_layout_enabled = False
    settings.hard_surface_direction_lock = True
    settings.uv_direction_space = 'OBJECT'
    settings.uv_direction_axis = 'AUTO'

    original_call = uv_optimize._call_uv_operator
    original_fallback = uv_optimize._repair_remaining_problem_charts
    original_align = uv_optimize.hard_surface.align_island_geometry
    pack_calls = {'count': 0}
    fallback_calls = {'count': 0}
    repair_returned = {'value': False}
    post_repair_reports = []

    def inject_after_first_pack(operator, _operator_name=None, **kwargs):
        result = original_call(
            operator, _operator_name=_operator_name, **kwargs)
        if _operator_name == 'pack_islands':
            pack_calls['count'] += 1
            if pack_calls['count'] == 1 and 'FINISHED' in result:
                bm, uv_layer = uv_optimize._refresh_edit_bmesh(obj.data)
                point = bm.faces[0].loops[0][uv_layer].uv.copy()
                for loop in bm.faces[0].loops:
                    loop[uv_layer].uv = point
                bmesh.update_edit_mesh(
                    obj.data, loop_triangles=False, destructive=False)
        return result

    def track_fallback(*args, **kwargs):
        fallback_calls['count'] += 1
        result = original_fallback(*args, **kwargs)
        repair_returned['value'] = True
        return result

    def track_alignment(faces, uv_layer, **kwargs):
        result = original_align(faces, uv_layer, **kwargs)
        if repair_returned['value']:
            report = uv_optimize.hard_surface.align_island_geometry_report(
                faces,
                uv_layer,
                axis_priority=kwargs.get(
                    'axis_priority',
                    uv_optimize.hard_surface.DEFAULT_GEOMETRY_AXIS_PRIORITY,
                ),
                geometry_matrix=kwargs.get('geometry_matrix'),
                write=False,
            )
            post_repair_reports.append(report)
        return result

    uv_optimize._call_uv_operator = inject_after_first_pack
    uv_optimize._repair_remaining_problem_charts = track_fallback
    uv_optimize.hard_surface.align_island_geometry = track_alignment
    try:
        result = uv_optimize.optimize_active_object(
            bpy.context, obj, settings)
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._repair_remaining_problem_charts = original_fallback
        uv_optimize.hard_surface.align_island_geometry = original_align

    assert fallback_calls['count'] >= 1, fallback_calls
    assert post_repair_reports, post_repair_reports
    assert all(
        report.selected_axis is not None
        and abs(report.angle_delta) < math.radians(3.0)
        for report in post_repair_reports
    ), [report.to_dict() for report in post_repair_reports]
    assert result.safe_pack_retry
    uv_optimize._audit_unique_object_mesh(obj.data)
    assert _topology_signature(obj.data) == before_topology


def _test_outer_transaction_rollback():
    _clear_scene()
    obj = _make_degenerate_cube("VUV_LocalRepairRollback")
    obj.data.edges[0].use_seam = True
    obj.data.update()
    before = _state_signature(obj.data)
    _activate(obj)
    settings = bpy.context.scene.vuv_settings
    settings.initial_uv_mode = 'LEGACY_SMART'
    settings.uv_usage = 'UNIQUE'
    settings.max_merge_tests = 0
    settings.preserve_seams = True
    settings.uv_group_layout_enabled = False

    original_call = uv_optimize._call_uv_operator
    original_fallback = uv_optimize._repair_remaining_problem_charts
    fallback_called = {'value': False}

    def keep_smart_project_degenerate(operator, _operator_name=None, **kwargs):
        if _operator_name == 'smart_project':
            return {'FINISHED'}
        return original_call(operator, _operator_name=_operator_name, **kwargs)

    def fail_after_mutation(
            mesh, bm, uv_layer, problem_charts, repair_settings, forced_cuts):
        fallback_called['value'] = True
        bm.faces[0].loops[0][uv_layer].uv = Vector((91.0, 73.0))
        bm.edges[0].seam = not bm.edges[0].seam
        bmesh.update_edit_mesh(
            mesh, loop_triangles=False, destructive=False)
        raise RuntimeError("synthetic local repair failure")

    uv_optimize._call_uv_operator = keep_smart_project_degenerate
    uv_optimize._repair_remaining_problem_charts = fail_after_mutation
    try:
        try:
            uv_optimize.optimize_active_object(bpy.context, obj, settings)
        except RuntimeError as error:
            assert "synthetic local repair failure" in str(error)
        else:
            raise AssertionError("synthetic local repair failure was swallowed")
    finally:
        uv_optimize._call_uv_operator = original_call
        uv_optimize._repair_remaining_problem_charts = original_fallback
    assert fallback_called['value']
    assert obj.mode == 'OBJECT'
    assert _state_signature(obj.data) == before


addon.register()
try:
    _test_tree_cut_unwrap()
    _test_single_convex_face_projection_fallback()
    _test_multi_face_chart_is_not_fragmented_by_projection_fallback()
    _test_single_concave_face_does_not_use_convex_fallback()
    _test_best_effort_local_repair_accepts_connected_chart()
    _test_best_effort_local_repair_restores_rejection()
    _test_near_planar_ngon_strict_rejection()
    _test_skinny_ear_stabilization()
    _test_welded_vertex_fan_stops_at_chart_cuts()
    _test_multi_face_local_repair_stabilizes_skinny_ear()
    _test_refine_layout_does_not_reseed_valid_uv()
    _test_refine_artist_seam_blocks_small_cleanup()
    _test_refine_derived_uv_cut_allows_safe_small_cleanup()
    _test_refine_unlocked_seam_allows_structural_cleanup()
    _test_refine_copy_preserves_source_layer_and_mesh_seams()
    _test_refine_cleanup_normalizes_unwrap_density_explosion()
    _test_structural_small_fragment_lane()
    _test_structural_lane_keeps_hard_boundaries()
    _test_structural_lane_rolls_back_stretch_rejection()
    _test_small_cleanup_extends_a_local_fragment_chain()
    _test_small_cleanup_chain_limits_are_local()
    _test_large_panel_is_not_charged_to_fragment_budget()
    _test_distant_fragments_do_not_form_asset_chain()
    _test_final_pack_gate_repair()
    _test_hard_surface_repair_reapplies_direction()
    _test_outer_transaction_rollback()
finally:
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    addon.unregister()

print("VUV_UNIQUE_LOCAL_REPAIR_OK")
