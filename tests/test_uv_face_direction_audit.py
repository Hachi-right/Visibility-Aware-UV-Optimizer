"""Focused regressions for the read-only per-triangle UV direction audit."""

from __future__ import annotations

import importlib.util
import json
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
        "vuv_hard_surface_face_direction_audit", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load {}".format(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HARD = _load_module()


def _mesh_from_faces(face_specs):
    """Build a small BMesh from ``(points, uvs)`` face specifications."""

    bm = bmesh.new()
    faces = []
    layer = bm.loops.layers.uv.new("UVMap")
    for points, uvs in face_specs:
        vertices = [bm.verts.new(point) for point in points]
        face = bm.faces.new(vertices)
        for loop, uv in zip(face.loops, uvs):
            loop[layer].uv = uv
        faces.append(face)
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return bm, faces, layer


def _rotated_uvs(points, angle, scale=1.0, offset=(0.0, 0.0)):
    """Map an X/Z plane so +Z has signed UV angle ``angle``."""

    cosine = math.cos(angle)
    sine = math.sin(angle)
    return tuple(
        Vector((
            (float(point[0]) * cosine + float(point[2]) * sine) * scale
            + float(offset[0]),
            (-float(point[0]) * sine + float(point[2]) * cosine) * scale
            + float(offset[1]),
        ))
        for point in points
    )


def _test_coherent_quad_has_one_direction():
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    bm, faces, layer = _mesh_from_faces(
        ((points, _rotated_uvs(points, 0.0)),)
    )
    try:
        before = tuple(loop[layer].uv.copy() for loop in faces[0].loops)
        result = HARD.audit_island_geometry_direction(faces, layer)
        after = tuple(loop[layer].uv.copy() for loop in faces[0].loops)
        assert result.selected_axis == "Z", result.to_dict()
        assert result.triangle_count == result.valid_triangle_count == 2
        assert result.skipped_triangle_count == 0
        assert result.low_signal_triangle_count == 0
        assert result.negative_triangle_count == 0
        assert result.unstable_triangle_count == 0
        assert result.circular_concentration > 0.999999, result.to_dict()
        assert result.resolved, result.to_dict()
        assert result.angle_p95 is not None
        assert result.angle_p95 < 1.0e-6, result.to_dict()
        assert before == after, "direction audit must not write UVs"
        # The nested report must be safe to persist in JSON diagnostics.
        json.dumps(result.to_dict(), sort_keys=True)
    finally:
        bm.free()


def _test_smaller_opposite_patch_is_reported_even_when_aggregate_resolves():
    large = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 0.0, 2.0),
        (0.0, 0.0, 2.0),
    )
    small = (
        (3.0, 0.0, 0.0),
        (4.0, 0.0, 0.0),
        (4.0, 0.0, 1.0),
        (3.0, 0.0, 1.0),
    )
    bm, faces, layer = _mesh_from_faces((
        (large, _rotated_uvs(large, 0.0)),
        (small, _rotated_uvs(small, math.pi)),
    ))
    try:
        result = HARD.audit_island_geometry_direction(faces, layer)
        assert result.selected_axis == "Z", result.to_dict()
        assert result.valid_triangle_count == 4, result.to_dict()
        # The large patch keeps the aggregate resolver positive, while the
        # smaller patch is still an explicit 180-degree direction failure.
        assert result.negative_triangle_count == 2, result.to_dict()
        assert result.unstable_triangle_count == 2, result.to_dict()
        assert result.circular_concentration < 1.0
        assert not result.resolved, result.to_dict()
        assert result.angle_p95 is not None
        assert result.angle_p95 > math.radians(30.0), result.to_dict()
    finally:
        bm.free()


def _test_low_signal_axis_falls_back_and_is_counted():
    points = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    # A horizontal cap has no +Z tangent signal; AUTO must use +X next.
    bm, faces, layer = _mesh_from_faces(
        ((points, (
            Vector((0.0, 0.0)),
            Vector((2.0, 0.0)),
            Vector((2.0, 1.0)),
            Vector((0.0, 1.0)),
        )),)
    )
    try:
        result = HARD.audit_island_geometry_direction(faces, layer)
        assert result.selected_axis == "X", result.to_dict()
        assert result.axis_projection["Z"]["normalized_strength"] < 1.0e-6
        # The X component occupies one leg of the two-vector Jacobian, so
        # its normalized strength is 1/sqrt(2) for this unit cap.
        assert result.axis_projection["X"]["normalized_strength"] > 0.6
        assert result.low_signal_triangle_count == 0, result.to_dict()
        assert result.resolved, result.to_dict()
    finally:
        bm.free()


def _test_mixed_angles_fail_concentration_gate():
    first = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    second = (
        (2.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 0.0, 1.0),
        (2.0, 0.0, 1.0),
    )
    bm, faces, layer = _mesh_from_faces((
        (first, _rotated_uvs(first, 0.0)),
        (second, _rotated_uvs(second, math.radians(120.0))),
    ))
    try:
        result = HARD.audit_island_geometry_direction(
            faces,
            layer,
            min_concentration=0.85,
            unstable_angle=math.radians(45.0),
        )
        assert result.selected_axis == "Z", result.to_dict()
        assert result.circular_concentration < 0.6, result.to_dict()
        assert result.unstable_triangle_count == 4, result.to_dict()
        assert not result.concentration_stable
        assert not result.resolved
    finally:
        bm.free()


def _test_invalid_triangle_is_separated_from_low_signal():
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 1.0),
    )
    degenerate_uvs = (
        Vector((0.0, 0.0)),
        Vector((1.0, 0.0)),
        Vector((2.0, 0.0)),
        Vector((3.0, 0.0)),
    )
    bm, faces, layer = _mesh_from_faces(((points, degenerate_uvs),))
    try:
        result = HARD.audit_island_geometry_direction(faces, layer)
        assert result.triangle_count == 2
        assert result.valid_triangle_count == 0
        assert result.skipped_triangle_count == 2
        assert result.low_signal_triangle_count == 0
        assert result.selected_axis is None
        assert not result.resolved
    finally:
        bm.free()


_test_coherent_quad_has_one_direction()
_test_smaller_opposite_patch_is_reported_even_when_aggregate_resolves()
_test_low_signal_axis_falls_back_and_is_counted()
_test_mixed_angles_fail_concentration_gate()
_test_invalid_triangle_is_separated_from_low_signal()
print("VUV_FACE_DIRECTION_AUDIT_OK")
