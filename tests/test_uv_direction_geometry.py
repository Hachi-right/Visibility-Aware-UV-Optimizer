"""Cross-version regressions for directed hard-surface UV alignment."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

import bmesh
from mathutils import Vector


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "visibility_uv_optimizer"
    / "hard_surface.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "vuv_hard_surface_direction_regression", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HARD = _load_module()


def _face_with_uv(points, uv_points, *, update_indices=True):
    bm = bmesh.new()
    vertices = [bm.verts.new(point) for point in points]
    face = bm.faces.new(vertices)
    layer = bm.loops.layers.uv.new("UVMap")
    for loop, uv in zip(face.loops, uv_points):
        loop[layer].uv = uv
    if update_indices:
        bm.verts.index_update()
        bm.faces.index_update()
    return bm, face, layer


def _relative_uvs(face, layer):
    values = [loop[layer].uv.copy() for loop in face.loops]
    center = sum(values, Vector((0.0, 0.0))) / len(values)
    return tuple(value - center for value in values)


def _faces_with_uv(face_specs):
    bm = bmesh.new()
    faces = []
    layer = bm.loops.layers.uv.new("UVMap")
    for points, uv_points in face_specs:
        vertices = [bm.verts.new(point) for point in points]
        face = bm.faces.new(vertices)
        for loop, uv in zip(face.loops, uv_points):
            loop[layer].uv = uv
        faces.append(face)
    bm.verts.index_update()
    bm.faces.index_update()
    return bm, faces, layer


def _assert_uvs_close(left, right, tolerance=1.0e-6):
    assert len(left) == len(right)
    assert all(
        (first - second).length <= tolerance
        for first, second in zip(left, right)
    ), (left, right)


def _test_positive_z_is_modulo_360():
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 2.0),
        (0.0, 0.0, 2.0),
    )
    upright_uv = (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 2.0),
        (0.0, 2.0),
    )
    inverted_uv = tuple((1.0 - u, 2.0 - v) for u, v in upright_uv)
    upright_bm, upright_face, upright_layer = _face_with_uv(
        points, upright_uv
    )
    inverted_bm, inverted_face, inverted_layer = _face_with_uv(
        points, inverted_uv
    )
    try:
        upright = HARD.align_island_geometry_report(
            (upright_face,), upright_layer, write=True
        )
        inverted = HARD.align_island_geometry_report(
            (inverted_face,), inverted_layer, write=True
        )
        assert upright.selected_axis == inverted.selected_axis == "Z"
        assert abs(upright.angle_delta) < 1.0e-6, upright.to_dict()
        assert abs(abs(inverted.angle_delta) - math.pi) < 1.0e-6, (
            inverted.to_dict()
        )
        _assert_uvs_close(
            _relative_uvs(upright_face, upright_layer),
            _relative_uvs(inverted_face, inverted_layer),
        )
        second = HARD.align_island_geometry_report(
            (inverted_face,), inverted_layer, write=True
        )
        assert abs(second.angle_delta) < 1.0e-6, second.to_dict()
    finally:
        upright_bm.free()
        inverted_bm.free()


def _test_horizontal_cap_falls_back_to_positive_x():
    points = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    uv = ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0))
    bm, face, layer = _face_with_uv(points, uv)
    try:
        report = HARD.align_island_geometry_report((face,), layer)
        assert report.selected_axis == "X", report.to_dict()
        assert abs(report.angle_delta - math.pi * 0.5) < 1.0e-6
    finally:
        bm.free()


def _test_near_normal_z_falls_back_but_shallow_slope_keeps_z():
    uv = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

    def report_for_slope(slope):
        tangent = Vector((math.cos(slope), 0.0, math.sin(slope)))
        points = (
            (0.0, 0.0, 0.0),
            tuple(tangent),
            tuple(tangent + Vector((0.0, 1.0, 0.0))),
            (0.0, 1.0, 0.0),
        )
        bm, face, layer = _face_with_uv(points, uv)
        try:
            return HARD.align_island_geometry_report((face,), layer)
        finally:
            bm.free()

    numerical_noise = report_for_slope(5.0e-5)
    meaningful_slope = report_for_slope(5.0e-4)
    assert numerical_noise.selected_axis == "X", numerical_noise.to_dict()
    assert meaningful_slope.selected_axis == "Z", meaningful_slope.to_dict()


def _test_degenerate_uv_is_unchanged():
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    uv = ((0.0, 0.0), (0.2, 0.0), (0.4, 0.0), (0.6, 0.0))
    bm, face, layer = _face_with_uv(points, uv)
    try:
        before = tuple(loop[layer].uv.copy() for loop in face.loops)
        report = HARD.align_island_geometry_report(
            (face,), layer, write=True
        )
        after = tuple(loop[layer].uv.copy() for loop in face.loops)
        assert report.degenerate
        assert report.selected_axis is None
        assert not report.written
        _assert_uvs_close(before, after, tolerance=0.0)
    finally:
        bm.free()


def _test_dirty_bmesh_indices_keep_distinct_loop_keys():
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    uv = ((1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0))
    bm, face, layer = _face_with_uv(
        points, uv, update_indices=False
    )
    try:
        report = HARD.align_island_geometry_report(
            (face,), layer, write=True
        )
        assert report.written and not report.degenerate, report.to_dict()
        assert len(report.loop_uvs) == 4, report.to_dict()
        assert len({tuple(loop[layer].uv) for loop in face.loops}) == 4
    finally:
        bm.free()


def _test_auto_falls_back_from_bimodal_z_but_explicit_z_is_preserved():
    large = (
        (0.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    small = (
        (4.0, 0.0, 0.0),
        (6.0, 0.0, 0.0),
        (6.0, 0.0, 1.0),
        (4.0, 0.0, 1.0),
    )
    # Both patches keep +X toward UV +U.  The smaller patch reverses +Z,
    # making Z bimodal while X remains perfectly coherent.
    large_uv = tuple((point[0], point[2]) for point in large)
    small_uv = tuple((point[0], -point[2]) for point in small)
    bm, faces, layer = _faces_with_uv((
        (large, large_uv),
        (small, small_uv),
    ))
    try:
        automatic = HARD.align_island_geometry_report(faces, layer)
        explicit = HARD.align_island_geometry_report(
            faces,
            layer,
            axis_priority=("Z",),
        )
        assert automatic.selected_axis == "X", automatic.to_dict()
        assert explicit.selected_axis == "Z", explicit.to_dict()
    finally:
        bm.free()


_test_positive_z_is_modulo_360()
_test_horizontal_cap_falls_back_to_positive_x()
_test_near_normal_z_falls_back_but_shallow_slope_keeps_z()
_test_degenerate_uv_is_unchanged()
_test_dirty_bmesh_indices_keep_distinct_loop_keys()
_test_auto_falls_back_from_bimodal_z_but_explicit_z_is_preserved()
print("VUV_DIRECTED_GEOMETRY_OK")
