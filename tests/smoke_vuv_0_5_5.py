"""Visibility Aware UV Optimizer 0.5.5 cross-version contract smoke test.

Run from Blender with:

    blender --background --factory-startup --python smoke_vuv_0_5_5.py -- \
        --addon-parent <addon-parent> --output <json> --strict-055

The script creates only synthetic meshes in the current (usually factory)
scene. It does not open, modify, or save a user fixture.
"""

from __future__ import annotations

from collections import defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-parent", required=True)
    parser.add_argument("--output", required=True)
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict-054",
        action="store_true",
        help="Require the 0.5.4 hard-surface settings and contracts.",
    )
    strict_group.add_argument(
        "--strict-055",
        action="store_true",
        help="Require the 0.5.5 hard-surface settings and inherited contracts.",
    )
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


args = parse_args()
STRICT = bool(args.strict_054 or args.strict_055)
SCHEMA = "vuv-055-smoke-v1" if args.strict_055 else "vuv-054-smoke-v1"
OK_STATUS = "VUV_055_SMOKE_OK" if args.strict_055 else "VUV_054_SMOKE_OK"
FAIL_STATUS = "VUV_055_SMOKE_FAIL" if args.strict_055 else "VUV_054_SMOKE_FAIL"
sys.path.insert(0, args.addon_parent)

import bmesh  # noqa: E402  (Blender imports must happen after sys.path setup.)
import bpy  # noqa: E402

import visibility_uv_optimizer as addon  # noqa: E402
from visibility_uv_optimizer import hard_surface  # noqa: E402
from visibility_uv_optimizer import uv_group_layout  # noqa: E402
from visibility_uv_optimizer import uv_optimize  # noqa: E402


EPS = 1.0e-6
UV_SELECTION_ATTRIBUTES = (
    '.uv_select_vert',
    '.uv_select_edge',
    '.uv_select_face',
)


def assert_finished(result, label):
    if "FINISHED" not in result:
        raise AssertionError("{} returned {}".format(label, sorted(result)))


def assert_cancelled(result, label):
    if "CANCELLED" not in result:
        raise AssertionError("{} returned {}".format(label, sorted(result)))


def clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def activate(obj, others=()):
    bpy.ops.object.select_all(action="DESELECT")
    for item in others:
        item.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def make_mesh(name, vertices, faces, material_indices=None):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if material_indices is not None:
        materials = []
        for index in sorted(set(material_indices)):
            material = bpy.data.materials.new("{}_Material_{}".format(name, index))
            materials.append(material)
            mesh.materials.append(material)
        for polygon, index in zip(mesh.polygons, material_indices):
            polygon.material_index = index
    activate(obj)
    return obj


def build_split_panels(name="VUV054_SplitPanels"):
    # Two coplanar quads sharing the internal edge (1, 2). This isolates
    # material and seam policy from a geometric angle break.
    vertices = [
        (-1.0, -1.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0),
        (-1.0, 1.0, 0.0),
        (1.0, -1.0, 0.0),
        (1.0, 1.0, 0.0),
    ]
    faces = [(0, 1, 2, 3), (1, 4, 5, 2)]
    return make_mesh(name, vertices, faces, [0, 1])


def build_marked_grid(name="VUV054_MarkedGrid"):
    columns = 2
    rows = 2
    vertices = []
    faces = []
    for column in range(columns + 1):
        for row in range(rows + 1):
            vertices.append((float(column), float(row), 0.0))
    stride = rows + 1
    for column in range(columns):
        for row in range(rows):
            first = column * stride + row
            faces.append((first, first + stride, first + stride + 1, first + 1))
    return make_mesh(name, vertices, faces, [0] * len(faces))


def build_rail(name="VUV054_Rail"):
    columns = 12
    rows = 3
    vertices = []
    faces = []
    for column in range(columns + 1):
        for row in range(rows + 1):
            vertices.append((float(column), float(row) - rows * 0.5, 0.0))
    stride = rows + 1
    for column in range(columns):
        for row in range(rows):
            first = column * stride + row
            faces.append((first, first + stride, first + stride + 1, first + 1))
    return make_mesh(name, vertices, faces, [0] * len(faces))


def build_cylinder(name="VUV054_Cylinder"):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=16,
        radius=1.0,
        depth=2.0,
        end_fill_type="NGON",
        location=(0.0, 0.0, 0.0),
    )
    obj = bpy.context.object
    obj.name = name
    activate(obj)
    return obj


def edge_for_vertices(obj, first, second):
    target = tuple(sorted((first, second)))
    for edge in obj.data.edges:
        if tuple(sorted(edge.vertices)) == target:
            return edge
    raise AssertionError("Missing edge {}".format(target))


def edge_records(obj):
    records = defaultdict(list)
    for polygon in obj.data.polygons:
        loop_indices = list(polygon.loop_indices)
        for offset, loop_index in enumerate(loop_indices):
            next_index = loop_indices[(offset + 1) % len(loop_indices)]
            loop = obj.data.loops[loop_index]
            next_loop = obj.data.loops[next_index]
            edge_index = loop.edge_index
            records[edge_index].append((
                polygon.index,
                {
                    loop.vertex_index: obj.data.uv_layers.active.data[loop_index].uv.copy(),
                    next_loop.vertex_index: obj.data.uv_layers.active.data[next_index].uv.copy(),
                },
            ))
    return records


def edge_is_discontinuous(obj, edge_index, epsilon=EPS):
    layer = obj.data.uv_layers.active
    if layer is None:
        return False
    records = defaultdict(list)
    for polygon in obj.data.polygons:
        for loop_index in polygon.loop_indices:
            loop = obj.data.loops[loop_index]
            if loop.edge_index != edge_index:
                continue
            next_index = polygon.loop_indices[(list(polygon.loop_indices).index(loop_index) + 1) % len(polygon.loop_indices)]
            next_loop = obj.data.loops[next_index]
            records[polygon.index].append((loop.vertex_index, layer.data[loop_index].uv.copy()))
            records[polygon.index].append((next_loop.vertex_index, layer.data[next_index].uv.copy()))
    if len(records) != 2:
        return False
    values = list(records.values())
    first = dict(values[0])
    second = dict(values[1])
    edge_vertices = obj.data.edges[edge_index].vertices
    return any(
        (first[vertex] - second[vertex]).length_squared > epsilon * epsilon
        for vertex in edge_vertices
    )


def island_ids(obj):
    """Return polygon -> UV island id, excluding non-manifold links."""
    if obj.data.uv_layers.active is None:
        return {}
    parent = list(range(len(obj.data.polygons)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        first = find(first)
        second = find(second)
        if first != second:
            parent[second] = first

    records = edge_records(obj)
    for edge_index, uses in records.items():
        if len(uses) != 2:
            continue
        (face_a, uv_a), (face_b, uv_b) = uses
        if all(
            (uv_a[vertex] - uv_b[vertex]).length_squared <= EPS * EPS
            for vertex in obj.data.edges[edge_index].vertices
        ):
            union(face_a, face_b)
    roots = {}
    result = {}
    for polygon in obj.data.polygons:
        root = find(polygon.index)
        roots.setdefault(root, len(roots))
        result[polygon.index] = roots[root]
    return result


def audit_uv(obj, uv_optimize_module=None, allow_degenerate_faces=()):
    layer = obj.data.uv_layers.active
    if layer is None:
        raise AssertionError("No active UV layer")
    finite = all(
        math.isfinite(item.uv.x) and math.isfinite(item.uv.y)
        for item in layer.data
    )
    if not finite:
        raise AssertionError("Non-finite UV coordinate")
    allowed = set(allow_degenerate_faces)
    degenerate = []
    for polygon in obj.data.polygons:
        points = [layer.data[index].uv for index in polygon.loop_indices]
        area = abs(sum(
            points[index].x * points[(index + 1) % len(points)].y
            - points[(index + 1) % len(points)].x * points[index].y
            for index in range(len(points))
        )) * 0.5
        if area <= 1.0e-12 and polygon.index not in allowed:
            degenerate.append(polygon.index)
    if degenerate:
        raise AssertionError("Degenerate UV polygons: {}".format(degenerate[:8]))

    false_negative = []
    for edge_index, uses in edge_records(obj).items():
        if len(uses) != 2:
            continue
        (face_a, uv_a), (face_b, uv_b) = uses
        discontinuous = any(
            (uv_a[vertex] - uv_b[vertex]).length_squared > EPS * EPS
            for vertex in obj.data.edges[edge_index].vertices
        )
        if discontinuous and not obj.data.edges[edge_index].use_seam:
            false_negative.append(edge_index)
    if false_negative:
        raise AssertionError("UV cuts without seam flags: {}".format(false_negative[:8]))

    overlap = False
    if uv_optimize_module is not None:
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.active
            triangles = uv_optimize_module._triangle_data(list(bm.faces), uv_layer)
            overlap = bool(uv_optimize_module._has_overlap(triangles))
        finally:
            bm.free()
    if overlap:
        raise AssertionError("Positive-area UV overlap")
    return {
        "loops": len(layer.data),
        "islands": len(set(island_ids(obj).values())),
        "seams": sum(bool(edge.use_seam) for edge in obj.data.edges),
        "false_negative": len(false_negative),
        "overlap": overlap,
    }


def layer_snapshot(mesh):
    result = {
        "active_index": int(mesh.uv_layers.active_index) if mesh.uv_layers else -1,
        "layers": [],
    }
    for layer in mesh.uv_layers:
        result["layers"].append({
            "name": layer.name,
            "active": bool(getattr(layer, "active", False)),
            "active_render": bool(getattr(layer, "active_render", False)),
            "uv": [
                (round(float(item.uv.x), 8), round(float(item.uv.y), 8))
                for item in layer.data
            ],
            "selection": [
                (
                    bool(getattr(item, "select", False)),
                    bool(getattr(item, "select_edge", False)),
                    bool(getattr(item, "pin_uv", False)),
                )
                for item in layer.data
            ],
        })
    return result


def mesh_snapshot(obj):
    smooth = []
    for edge in obj.data.edges:
        if hasattr(edge, "smooth"):
            state = bool(edge.smooth)
        elif hasattr(edge, "use_edge_sharp"):
            state = not bool(edge.use_edge_sharp)
        else:
            state = True
        if state:
            smooth.append(int(edge.index))
    uv_selection_attributes = {}
    for name in UV_SELECTION_ATTRIBUTES:
        attribute = obj.data.attributes.get(name)
        uv_selection_attributes[name] = (
            None if attribute is None else {
                "domain": str(attribute.domain),
                "values": [bool(item.value) for item in attribute.data],
            }
        )
    detail_attribute = obj.data.attributes.get('vuv_detail_score')
    return {
        "layers": layer_snapshot(obj.data),
        "seams": [int(edge.index) for edge in obj.data.edges if edge.use_seam],
        "smooth": smooth,
        "materials": [int(polygon.material_index) for polygon in obj.data.polygons],
        "vertex_selection": [bool(item.select) for item in obj.data.vertices],
        "edge_selection": [bool(item.select) for item in obj.data.edges],
        "face_selection": [bool(item.select) for item in obj.data.polygons],
        "vertex_hidden": [bool(item.hide) for item in obj.data.vertices],
        "edge_hidden": [bool(item.hide) for item in obj.data.edges],
        "face_hidden": [bool(item.hide) for item in obj.data.polygons],
        "uv_selection_attributes": uv_selection_attributes,
        "detail_score": (
            None if detail_attribute is None else
            [round(float(item.value), 8) for item in detail_attribute.data]
        ),
    }


def stable_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def enum_ids(owner, name):
    prop = owner.bl_rna.properties.get(name)
    if prop is None or not hasattr(prop, "enum_items"):
        return set()
    return {item.identifier for item in prop.enum_items}


def set_initial_mode(settings, mode, strict=False):
    prop = settings.bl_rna.properties.get("initial_uv_mode")
    if prop is None:
        if strict and mode != "LEGACY_SMART":
            raise AssertionError("0.5.4 initial_uv_mode property is missing")
        return False
    allowed = enum_ids(settings, "initial_uv_mode")
    if mode not in allowed:
        raise AssertionError("initial_uv_mode lacks {}: {}".format(mode, sorted(allowed)))
    settings.initial_uv_mode = mode
    return True


def set_profile(settings, strict=False):
    profile = settings.bl_rna.properties.get("hard_surface_profile")
    if profile is None:
        # The candidate build names the texture contract ``uv_usage`` rather
        # than exposing a second profile enum. UNIQUE is the hard-surface
        # non-stacking contract used by these fixtures.
        usage = settings.bl_rna.properties.get("uv_usage")
        if usage is None:
            if strict:
                raise AssertionError(
                    "0.5.4 needs hard_surface_profile or uv_usage"
                )
            return False
        allowed = enum_ids(settings, "uv_usage")
        if "UNIQUE" not in allowed:
            raise AssertionError("uv_usage lacks UNIQUE: {}".format(sorted(allowed)))
        settings.uv_usage = "UNIQUE"
        return "uv_usage:UNIQUE"
    allowed = enum_ids(settings, "hard_surface_profile")
    if "WEAPON" in allowed:
        settings.hard_surface_profile = "WEAPON"
    elif "HARD_SURFACE" in allowed:
        settings.hard_surface_profile = "HARD_SURFACE"
    elif "DEFAULT" in allowed:
        settings.hard_surface_profile = "DEFAULT"
    else:
        raise AssertionError("No usable hard-surface profile: {}".format(sorted(allowed)))
    return "hard_surface_profile:" + str(settings.hard_surface_profile)


class _UVProxy:
    def __init__(self, real_uv, calls):
        self._real_uv = real_uv
        self._calls = calls

    def __getattr__(self, name):
        operator = getattr(self._real_uv, name)
        if not callable(operator):
            return operator

        def invoke(*args, **kwargs):
            self._calls.append((name, dict(kwargs)))
            return operator(*args, **kwargs)

        return invoke


class _OpsProxy:
    def __init__(self, real_ops, calls):
        self._real_ops = real_ops
        self._calls = calls
        self.uv = _UVProxy(real_ops.uv, calls)

    def __getattr__(self, name):
        return getattr(self._real_ops, name)


class _BpyProxy:
    def __init__(self, real_bpy, calls):
        self._real_bpy = real_bpy
        self.ops = _OpsProxy(real_bpy.ops, calls)

    def __getattr__(self, name):
        return getattr(self._real_bpy, name)


def test_operator_rna_filter(settings, strict):
    operations = (
        "smart_project",
        "unwrap",
        "follow_active_quads",
        "select_all",
        "average_islands_scale",
        "pack_islands",
    )
    supported = {
        name: {
            prop.identifier
            for prop in getattr(bpy.ops.uv, name).get_rna_type().properties
            if prop.identifier != "rna_type"
        }
        for name in operations
    }
    filter_fn = getattr(uv_optimize, "_filter_operator_kwargs", None)
    helper_probe = None
    if filter_fn is None:
        if strict:
            raise AssertionError("0.5.4 _filter_operator_kwargs helper is missing")
    else:
        helper_probe = filter_fn(
            bpy.ops.uv.pack_islands,
            {"rotate": False, "margin": 0.002, "rotate_method": "CARDINAL", "bogus": 1},
        )
        if not isinstance(helper_probe, dict):
            raise AssertionError("_filter_operator_kwargs did not return a dict")
        if set(helper_probe) - supported["pack_islands"]:
            raise AssertionError(
                "RNA filter retained unsupported keys: {}".format(
                    sorted(set(helper_probe) - supported["pack_islands"])
                )
            )
    calls = []
    real_bpy = uv_optimize.bpy
    uv_optimize.bpy = _BpyProxy(bpy, calls)
    try:
        clear_scene()
        obj = build_rail("VUV054_RNA")
        set_initial_mode(settings, "LEGACY_SMART", strict=False)
        settings.max_merge_tests = 0
        assert_finished(bpy.ops.vuv.optimize_uv(), "RNA-filter optimize")
    finally:
        uv_optimize.bpy = real_bpy
    invalid = {
        name: sorted(set(kwargs) - supported.get(name, set()))
        for name, kwargs in calls
        if set(kwargs) - supported.get(name, set())
    }
    if invalid:
        raise AssertionError("Unsupported UV kwargs reached Blender: {}".format(invalid))
    return {
        "supported": {name: sorted(values) for name, values in supported.items()},
        "helper_probe": None if helper_probe is None else sorted(helper_probe),
        "calls": [{"operator": name, "kwargs": sorted(kwargs)} for name, kwargs in calls],
        "invalid": invalid,
    }


def test_empty_uv(settings, strict):
    results = {}
    for mode in ("LEGACY_SMART", "HARD_SURFACE"):
        if mode != "LEGACY_SMART" and not set_initial_mode(settings, mode, strict=False):
            continue
        clear_scene()
        obj = build_split_panels("VUV054_EmptyUV_" + mode)
        while obj.data.uv_layers:
            obj.data.uv_layers.remove(obj.data.uv_layers[0])
        set_initial_mode(settings, mode, strict=strict and mode != "LEGACY_SMART")
        settings.max_merge_tests = 0
        assert_finished(bpy.ops.vuv.optimize_uv(), "empty UV {}".format(mode))
        if obj.data.uv_layers.active is None or len(obj.data.uv_layers.active.data) != len(obj.data.loops):
            raise AssertionError("{} did not create a valid active UV layer".format(mode))
        results[mode] = audit_uv(obj, uv_optimize)
    return results


def test_hidden_geometry_scope(settings, strict):
    clear_scene()
    obj = build_split_panels("VUV054_HiddenScope")
    mesh = obj.data
    mesh.uv_layers.new(name="UVMap")
    mesh.polygons[1].hide = True
    hidden_before = {
        "vertices": [bool(item.hide) for item in mesh.vertices],
        "edges": [bool(item.hide) for item in mesh.edges],
        "faces": [bool(item.hide) for item in mesh.polygons],
    }
    settings.preserve_seams = True
    settings.respect_materials = True
    settings.hard_surface_respect_sharp = False
    set_initial_mode(settings, "HARD_SURFACE", strict=strict)
    if settings.bl_rna.properties.get("uv_usage") is not None:
        settings.uv_usage = "UNIQUE"
    settings.max_merge_tests = 0
    assert_finished(bpy.ops.vuv.optimize_uv(), "hidden geometry optimize")
    hidden_after = {
        "vertices": [bool(item.hide) for item in mesh.vertices],
        "edges": [bool(item.hide) for item in mesh.edges],
        "faces": [bool(item.hide) for item in mesh.polygons],
    }
    if hidden_before != hidden_after:
        raise AssertionError("Successful optimize changed mesh hide state")
    return {
        "hidden_state_preserved": True,
        "audit": audit_uv(obj, uv_optimize),
    }


def test_auto_contract_names(settings):
    clear_scene()
    obj = build_split_panels("VUV054_AutoContract")
    settings.uv_usage = 'AUTO'
    cases = {
        "SM_Weapon_LOD1": "UNIQUE",
        "SM_Weapon_TrimSheet_LOD1": "TRIM_SHEET",
        "SM_Weapon_InfoAtlas_LOD1": "INFO_ATLAS",
        "SM_Weapon_LED_LOD1": "LED",
        "SM_Weapon_LED1_LOD1": "LED",
        "SM_Weapon_VFX_LOD1": "LED",
    }
    resolved = {}
    for name, expected in cases.items():
        obj.name = name
        value = uv_optimize._resolve_uv_usage(obj, settings)
        resolved[name] = value
        if value != expected:
            raise AssertionError(
                "Auto UV contract {} resolved to {}, expected {}".format(
                    name, value, expected))
    return resolved


def test_panel_flatness_setting(settings):
    clear_scene()
    rise = math.tan(math.radians(8.0))
    obj = make_mesh(
        "VUV054_PanelFlatness",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
         (2.0, 0.0, rise), (2.0, 1.0, rise)],
        [(0, 1, 2, 3), (1, 4, 5, 2)],
        [0, 0],
    )
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        kinds = {}
        for label, degrees in (("tight", 2.0), ("loose", 6.0)):
            settings.hard_surface_planar_angle = math.radians(degrees)
            constraints = hard_surface.classify_edge_constraints(
                bm, settings, mode='HARD_SURFACE')
            records = hard_surface.build_region_seed_records(bm, constraints)
            if len(records) != 1:
                raise AssertionError(
                    "Panel flatness fixture produced {} regions".format(
                        len(records)))
            kinds[label] = records[0].kind
        if kinds != {"tight": "mixed", "loose": "panel"}:
            raise AssertionError(
                "Panel Flatness did not affect seed classification: {}".format(
                    kinds))
        return kinds
    finally:
        bm.free()
        try:
            settings.property_unset('hard_surface_planar_angle')
        except (AttributeError, RuntimeError, TypeError):
            settings.hard_surface_planar_angle = math.radians(5.0)


def test_active_render_layer(settings, strict):
    clear_scene()
    obj = build_split_panels("VUV054_ActiveRender")
    mesh = obj.data
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
    active_layer = mesh.uv_layers.new(name="UVMap")
    render_layer = mesh.uv_layers.new(name="RenderUV")
    mesh.uv_layers.active_index = 0
    for item in active_layer.data:
        item.uv = (0.11, 0.23)
    for item in render_layer.data:
        item.uv = (0.71, 0.83)
    render_layer.active_render = True
    before = layer_snapshot(mesh)
    set_initial_mode(settings, "LEGACY_SMART", strict=False)
    settings.max_merge_tests = 0
    assert_finished(bpy.ops.vuv.optimize_uv(), "active/render UV optimize")
    after = layer_snapshot(mesh)
    before_render = next(item for item in before["layers"] if item["name"] == "RenderUV")
    after_render = next(item for item in after["layers"] if item["name"] == "RenderUV")
    if before_render != after_render:
        raise AssertionError("Non-active render UV layer was modified")
    if before["active_index"] != after["active_index"]:
        raise AssertionError("Active UV layer index changed")
    return {
        "active_before": before["active_index"],
        "active_after": after["active_index"],
        "render_layer_preserved": True,
        "layer_hash_before": stable_hash(before),
        "layer_hash_after": stable_hash(after),
    }


def test_preserve_non_unique(settings, strict):
    clear_scene()
    obj = build_split_panels("VUV054_TrimSheet")
    mesh = obj.data
    layer = mesh.uv_layers.new(name="TrimUV")
    mesh.uv_layers.active_index = 0
    for index, item in enumerate(layer.data):
        # Deliberate stacked/atlas coordinates, including values outside 0-1.
        item.uv = (-0.25 + (index % 4) * 0.125, 1.25 + (index // 4) * 0.25)
    edge_for_vertices(obj, 1, 2).use_seam = True
    before = mesh_snapshot(obj)
    set_initial_mode(settings, "AUTO", strict=strict)
    if settings.bl_rna.properties.get("uv_usage") is not None:
        settings.uv_usage = "AUTO"
    settings.max_merge_tests = 30
    assert_finished(bpy.ops.vuv.optimize_uv(), "non-unique preserve")
    after = mesh_snapshot(obj)
    before_contract = dict(before)
    after_contract = dict(after)
    before_contract.pop("detail_score", None)
    after_contract.pop("detail_score", None)
    if before_contract != after_contract:
        raise AssertionError("Auto modified a TrimSheet UV layout or seam flag")
    if strict and "PRESERVE_LAYOUT" not in settings.last_optimize:
        raise AssertionError("Auto did not report the Preserve Layout contract")
    return {
        "mesh_hash": stable_hash(after_contract),
        "unchanged": True,
        "summary": settings.last_optimize,
    }


def test_constraints(settings, strict):
    cases = ("marked", "material", "sharp")
    results = {}
    for case in cases:
        clear_scene()
        obj = build_split_panels("VUV054_" + case)
        target = edge_for_vertices(obj, 1, 2)
        target_index = int(target.index)
        if case != "material":
            # Keep the marked/sharp cases independent from material policy.
            for polygon in obj.data.polygons:
                polygon.material_index = 0
        target.use_seam = case == "marked"
        if hasattr(target, "use_edge_sharp"):
            target.use_edge_sharp = case == "sharp"
        elif hasattr(target, "smooth"):
            target.smooth = case != "sharp"
        settings.preserve_seams = True
        settings.respect_materials = case == "material"
        settings.respect_sharp = case == "sharp"
        settings.hard_surface_respect_sharp = case == "sharp"
        set_initial_mode(settings, "MARKED_SEAMS" if case == "marked" else "HARD_SURFACE", strict=strict)
        settings.max_merge_tests = 30
        assert_finished(bpy.ops.vuv.optimize_uv(), case + " constraint optimize")
        audit = audit_uv(obj, uv_optimize)
        target_after = obj.data.edges[target_index]
        if case == "marked" and not target_after.use_seam:
            raise AssertionError("{} target seam flag was removed".format(case))
        if not edge_is_discontinuous(obj, target_index):
            raise AssertionError("{} policy did not create a UV discontinuity".format(case))
        results[case] = {
            "edge": target_index,
            "seam": bool(target_after.use_seam),
            "discontinuous": True,
            "audit": audit,
        }
    return results


def test_cylinder_and_rail(settings, strict):
    results = {}
    clear_scene()
    rail = build_rail()
    set_initial_mode(settings, "HARD_SURFACE", strict=strict)
    settings.max_merge_tests = 30
    assert_finished(bpy.ops.vuv.optimize_uv(), "hard-surface rail")
    rail_audit = audit_uv(rail, uv_optimize)
    rail_points = [item.uv.copy() for item in rail.data.uv_layers.active.data]
    rail_audit["uv_bounds"] = [
        min(point.x for point in rail_points),
        min(point.y for point in rail_points),
        max(point.x for point in rail_points),
        max(point.y for point in rail_points),
    ]
    results["rail"] = rail_audit

    clear_scene()
    cylinder = build_cylinder()
    set_initial_mode(settings, "HARD_SURFACE", strict=strict)
    settings.max_merge_tests = 60
    assert_finished(bpy.ops.vuv.optimize_uv(), "hard-surface cylinder")
    cylinder_audit = audit_uv(cylinder, uv_optimize)
    ids = island_ids(cylinder)
    cap_faces = [
        polygon.index for polygon in cylinder.data.polygons
        if abs(polygon.normal.z) >= 0.9
    ]
    side_faces = [polygon.index for polygon in cylinder.data.polygons if polygon.index not in cap_faces]
    cap_islands = {ids[index] for index in cap_faces}
    side_islands = {ids[index] for index in side_faces}
    if strict and cap_islands.intersection(side_islands):
        raise AssertionError("Cylinder cap and side share a UV island")
    if strict and len(side_islands) != 1:
        raise AssertionError(
            "Cylinder side must be one continuous strip, got {} islands".format(
                len(side_islands)
            )
        )
    if strict and len(cap_islands) != len(cap_faces):
        raise AssertionError("Each cylinder cap must have its own UV island")
    cylinder_audit.update({
        "cap_faces": len(cap_faces),
        "side_faces": len(side_faces),
        "cap_islands": sorted(cap_islands),
        "side_islands": sorted(side_islands),
    })
    results["cylinder"] = cylinder_audit
    return results


def test_rollback(settings, strict):
    clear_scene()
    obj = build_marked_grid("VUV054_Rollback")
    mesh = obj.data
    uv_layer = mesh.uv_layers.new(name="RollbackUV")
    mesh.uv_layers.active_index = 0
    for index, item in enumerate(uv_layer.data):
        item.uv = (0.13 + index * 0.001, 0.27 + index * 0.002)
        if hasattr(item, "select"):
            item.select = index % 2 == 0
        if hasattr(item, "select_edge"):
            item.select_edge = index % 3 == 0
        if hasattr(item, "pin_uv"):
            item.pin_uv = index % 4 == 0
    if uv_layer.data and not hasattr(uv_layer.data[0], "select"):
        attribute_specs = (
            ('.uv_select_vert', 'CORNER'),
            ('.uv_select_edge', 'CORNER'),
            ('.uv_select_face', 'FACE'),
        )
        for offset, (name, domain) in enumerate(attribute_specs):
            attribute = mesh.attributes.get(name)
            if attribute is None:
                attribute = mesh.attributes.new(
                    name=name, type='BOOLEAN', domain=domain)
            for index, item in enumerate(attribute.data):
                item.value = (index + offset) % 3 == 0
    mesh.vertices[0].select = True
    mesh.edges[0].select = True
    mesh.polygons[0].select = True
    mesh.vertices[-1].hide = True
    mesh.edges[-1].hide = True
    mesh.polygons[-1].hide = True
    edge_for_vertices(obj, 1, 4).use_seam = True
    other = build_split_panels("VUV054_RollbackOther")
    activate(obj, (other,))
    before = mesh_snapshot(obj)
    selected_before = sorted(item.name for item in bpy.context.selected_objects)
    active_before = bpy.context.view_layer.objects.active.name
    mode_before = obj.mode

    original = getattr(uv_optimize, "_call_uv_operator", None)
    if original is None:
        if strict:
            raise AssertionError("0.5.4 _call_uv_operator rollback hook is missing")
        return {
            "cancelled": False,
            "mesh_restored": None,
            "selection_restored": None,
            "legacy_expected_gap": True,
            "reason": "no transactional UV operator wrapper",
        }

    def fail_after_mutation(operator, **kwargs):
        layer = obj.data.uv_layers.active
        for item in layer.data:
            item.uv = (9.0, 9.0)
            if hasattr(item, "select"):
                item.select = not item.select
            if hasattr(item, "select_edge"):
                item.select_edge = not item.select_edge
            if hasattr(item, "pin_uv"):
                item.pin_uv = not item.pin_uv
        for edge in obj.data.edges:
            edge.use_seam = not edge.use_seam
            edge.select = not edge.select
        for vertex in obj.data.vertices:
            vertex.select = not vertex.select
        for face in obj.data.polygons:
            face.select = not face.select
            face.hide = not face.hide
        for vertex in obj.data.vertices:
            vertex.hide = not vertex.hide
        for edge in obj.data.edges:
            edge.hide = not edge.hide
        for name in UV_SELECTION_ATTRIBUTES:
            attribute = obj.data.attributes.get(name)
            if attribute is not None:
                for item in attribute.data:
                    item.value = not item.value
        raise RuntimeError("synthetic rollback failure")

    uv_optimize._call_uv_operator = fail_after_mutation
    try:
        set_initial_mode(settings, "LEGACY_SMART", strict=False)
        try:
            result = bpy.ops.vuv.optimize_uv()
        except RuntimeError:
            # Background Blender raises when an operator reports ERROR while
            # returning CANCELLED; both forms represent the same contract.
            result = {'CANCELLED'}
    finally:
        uv_optimize._call_uv_operator = original
    assert_cancelled(result, "synthetic rollback")
    after = mesh_snapshot(obj)
    selected_after = sorted(item.name for item in bpy.context.selected_objects)
    active_after = bpy.context.view_layer.objects.active.name
    restored = before == after
    selection_restored = (
        selected_before == selected_after
        and active_before == active_after
        and mode_before == obj.mode
    )
    if strict and not restored:
        raise AssertionError("UV/seam state was not restored after failure")
    if strict and not selection_restored:
        raise AssertionError("Object selection/mode was not restored after failure")
    return {
        "cancelled": True,
        "mesh_restored": restored,
        "selection_restored": selection_restored,
        "legacy_expected_gap": not strict and not restored,
        "snapshot_hash": stable_hash(before),
    }


def test_detail_score_transaction(settings, strict):
    clear_scene()
    obj = build_rail("VUV054_DetailRollback")
    mesh = obj.data
    mesh.uv_layers.new(name="UVMap")
    detail = mesh.attributes.new(
        name='vuv_detail_score', type='FLOAT', domain='FACE')
    for index, item in enumerate(detail.data):
        item.value = index / max(len(detail.data), 1)
    before = mesh_snapshot(obj)
    original = uv_optimize.write_face_float

    def fail_detail_write(target_mesh, values, name='vuv_detail_score'):
        attribute = target_mesh.attributes.get(name)
        if attribute is None:
            attribute = target_mesh.attributes.new(
                name=name, type='FLOAT', domain='FACE')
        for item in attribute.data:
            item.value = 99.0
        raise RuntimeError("synthetic detail-score failure")

    uv_optimize.write_face_float = fail_detail_write
    try:
        set_initial_mode(settings, "LEGACY_SMART", strict=False)
        settings.uv_usage = 'UNIQUE'
        settings.max_merge_tests = 0
        try:
            result = bpy.ops.vuv.optimize_uv()
        except RuntimeError:
            result = {'CANCELLED'}
    finally:
        uv_optimize.write_face_float = original
    assert_cancelled(result, "detail-score rollback")
    after = mesh_snapshot(obj)
    if strict and before != after:
        raise AssertionError(
            "Detail-score write failure did not roll back the transaction")
    return {
        "cancelled": True,
        "restored": before == after,
        "snapshot_hash": stable_hash(before),
    }


def _audit_plugin_unique(obj):
    mesh = obj.data
    active = mesh.uv_layers.active
    if active is None:
        raise AssertionError("Quality-gate fixture has no active UV layer")
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.get(active.name)
        return uv_optimize._audit_unique_layout(bm, uv_layer)
    finally:
        bm.free()


def _build_two_triangles(name):
    return make_mesh(
        name,
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        [(0, 1, 2), (0, 2, 3)],
        [0, 0],
    )


def _build_fan(name):
    vertices = [(0.0, 0.0, 0.0)]
    for index in range(8):
        angle = math.tau * index / 8.0
        vertices.append((math.cos(angle), math.sin(angle), 0.0))
    faces = [
        (0, 1 + index, 1 + ((index + 1) % 8))
        for index in range(8)
    ]
    return make_mesh(name, vertices, faces, [0] * len(faces))


def _inject_cross_chart_overlap(bm, uv_layer):
    faces = sorted(bm.faces, key=lambda face: face.index)
    source = [loop[uv_layer].uv.copy() for loop in faces[0].loops]
    for loop, uv in zip(faces[1].loops, source):
        loop[uv_layer].uv = uv


def _inject_mixed_winding(bm, uv_layer):
    coordinates = {
        0: (0.1, 0.1),
        1: (0.9, 0.1),
        2: (0.9, 0.9),
        3: (0.9, 0.1),
    }
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv = coordinates[loop.vert.index]


def _inject_fan_self_overlap(bm, uv_layer):
    coordinates = {0: (0.5, 0.5)}
    for index in range(8):
        angle = math.radians(index * 90.0)
        coordinates[1 + index] = (
            0.5 + 0.35 * math.cos(angle),
            0.5 + 0.35 * math.sin(angle),
        )
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv = coordinates[loop.vert.index]


def _inject_uv_degenerate(bm, uv_layer):
    for index, loop in enumerate(bm.faces[0].loops):
        loop[uv_layer].uv = (0.2 + index * 0.2, 0.5)


def _inject_out_of_bounds(bm, uv_layer):
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv.x += 2.0


def _run_quality_gate_failure(settings, label, builder, injector):
    clear_scene()
    obj = builder("VUV054_Gate_" + label)
    before = mesh_snapshot(obj)
    set_initial_mode(settings, "LEGACY_SMART", strict=False)
    if hasattr(settings, "uv_usage"):
        settings.uv_usage = "UNIQUE"
    settings.max_merge_tests = 0
    original_normalize = uv_optimize._normalize_uv_to_tile
    calls = [0]

    def inject_after_normalize(bm, uv_layer, margin):
        result = original_normalize(bm, uv_layer, margin)
        injector(bm, uv_layer)
        calls[0] += 1
        return result

    uv_optimize._normalize_uv_to_tile = inject_after_normalize
    try:
        try:
            result = bpy.ops.vuv.optimize_uv()
        except RuntimeError:
            result = {'CANCELLED'}
    finally:
        uv_optimize._normalize_uv_to_tile = original_normalize
    assert_cancelled(result, label)
    after = mesh_snapshot(obj)
    if before != after:
        raise AssertionError("{} did not restore the complete mesh snapshot".format(label))
    if calls[0] < 1:
        raise AssertionError("{} fault injection did not run".format(label))
    return {
        "cancelled": True,
        "restored": True,
        "injections": calls[0],
        "snapshot_hash": stable_hash(before),
    }


def test_unique_quality_gate(settings, strict):
    if not strict:
        return {"skipped": True}

    clear_scene()
    obj = build_rail("VUV054_Gate_Negative")
    set_initial_mode(settings, "LEGACY_SMART", strict=False)
    settings.uv_usage = "UNIQUE"
    settings.max_merge_tests = 0
    original_normalize = uv_optimize._normalize_uv_to_tile

    def mirror_after_normalize(bm, uv_layer, margin):
        result = original_normalize(bm, uv_layer, margin)
        loops = [loop for face in bm.faces for loop in face.loops]
        minimum = min(loop[uv_layer].uv.x for loop in loops)
        maximum = max(loop[uv_layer].uv.x for loop in loops)
        center = (minimum + maximum) * 0.5
        for loop in loops:
            loop[uv_layer].uv.x = 2.0 * center - loop[uv_layer].uv.x
        return result

    uv_optimize._normalize_uv_to_tile = mirror_after_normalize
    try:
        result = bpy.ops.vuv.optimize_uv()
    finally:
        uv_optimize._normalize_uv_to_tile = original_normalize
    assert_finished(result, "uniform negative chart")
    negative_audit = _audit_plugin_unique(obj)
    if uv_optimize._unique_layout_error(negative_audit) is not None:
        raise AssertionError(
            "Uniform negative chart was not repaired: {}".format(negative_audit))

    failures = {
        "cross_chart_overlap": _run_quality_gate_failure(
            settings, "CrossOverlap", build_split_panels,
            _inject_cross_chart_overlap),
        "intra_chart_overlap": _run_quality_gate_failure(
            settings, "SelfOverlap", _build_fan,
            _inject_fan_self_overlap),
        "mixed_winding": _run_quality_gate_failure(
            settings, "MixedWinding", _build_two_triangles,
            _inject_mixed_winding),
        "uv_degenerate": _run_quality_gate_failure(
            settings, "Degenerate", lambda name: make_mesh(
                name,
                [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                [(0, 1, 2)],
                [0],
            ), _inject_uv_degenerate),
        "out_of_bounds": _run_quality_gate_failure(
            settings, "Bounds", lambda name: make_mesh(
                name,
                [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                [(0, 1, 2)],
                [0],
            ), _inject_out_of_bounds),
    }
    return {"uniform_negative": negative_audit, "failures": failures}


def test_small_repeat_layout_priority(strict):
    if not strict:
        return {"skipped": True}

    def island(island_id, bounds, area, small):
        return SimpleNamespace(
            island_id=island_id,
            model_bounds=bounds,
            area_3d=area,
            material_indices=(0,),
            neighbor_ids=(),
            is_small=small,
        )

    islands = (
        island(0, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0), 10.0, False),
        island(1, (10.0, 0.0, 0.0, 11.0, 1.0, 1.0), 10.0, False),
        island(2, (1.05, 0.0, 0.0, 1.10, 0.05, 0.05), 0.01, True),
        island(3, (9.90, 0.0, 0.0, 9.95, 0.05, 0.05), 0.01, True),
    )
    repeated_small = SimpleNamespace(member_ids=(2, 3), relation="REPEATED")
    groups = uv_group_layout.build_layout_groups(
        islands,
        (repeated_small,),
        object_diagonal=20.0,
        options=uv_group_layout.GroupLayoutOptions(
            proximity_radius_ratio=0.025,
        ),
    )
    membership = {
        member_id: group.group_id
        for group in groups
        for member_id in group.member_ids
    }
    group = next(item for item in groups if 2 in item.small_member_ids)
    owners = dict(zip(group.small_member_ids, group.small_anchor_ids))
    if owners != {2: 0, 3: 1}:
        raise AssertionError(
            "Repeated tiny fragments did not follow their nearest model anchors"
        )
    if group.owner_cohorts != ((0, 1),):
        raise AssertionError("Repeated owners did not retain cohort metadata")
    return {
        "left_anchor_group": membership[0],
        "left_small_group": membership[2],
        "right_anchor_group": membership[1],
        "right_small_group": membership[3],
        "left_small_owner": owners[2],
        "right_small_owner": owners[3],
    }


def run():
    addon_version = tuple(addon.bl_info.get("version", ()))
    if args.strict_055 and addon_version < (0, 5, 5):
        raise AssertionError("--strict-055 requires addon version >= 0.5.5")
    if args.strict_054 and addon_version < (0, 5, 4):
        raise AssertionError("--strict-054 requires addon version >= 0.5.4")
    addon.register()
    try:
        settings = bpy.context.scene.vuv_settings
        profile_available = set_profile(settings, strict=STRICT)
        result = {
            "schema": SCHEMA,
            "blender_version": bpy.app.version_string,
            "addon_version": list(addon_version),
            "strict_054": bool(args.strict_054),
            "strict_055": bool(args.strict_055),
            "hard_surface_contract": profile_available,
        }
        result["operator_rna"] = test_operator_rna_filter(settings, STRICT)
        result["empty_uv"] = test_empty_uv(settings, STRICT)
        result["hidden_geometry"] = test_hidden_geometry_scope(
            settings, STRICT)
        result["auto_contract_names"] = test_auto_contract_names(settings)
        result["panel_flatness"] = test_panel_flatness_setting(settings)
        result["active_render"] = test_active_render_layer(settings, STRICT)
        result["non_unique_preserve"] = test_preserve_non_unique(
            settings, STRICT)
        result["constraints"] = test_constraints(settings, STRICT)
        result["hard_surface"] = test_cylinder_and_rail(settings, STRICT)
        result["unique_quality_gate"] = test_unique_quality_gate(
            settings, STRICT)
        result["rollback"] = test_rollback(settings, STRICT)
        result["detail_score_transaction"] = test_detail_score_transaction(
            settings, STRICT)
        result["small_repeat_layout_priority"] = (
            test_small_repeat_layout_priority(STRICT)
        )
        result["status"] = OK_STATUS
        return result
    finally:
        addon.unregister()


output_path = Path(args.output)
output_path.parent.mkdir(parents=True, exist_ok=True)
try:
    result = run()
except Exception as error:
    result = {
        "schema": SCHEMA,
        "status": FAIL_STATUS,
        "blender_version": getattr(bpy.app, "version_string", "unknown"),
        "error": str(error),
        "traceback": traceback.format_exc(),
    }
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(FAIL_STATUS + " " + json.dumps(result, sort_keys=True))
    raise
else:
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(OK_STATUS + " " + json.dumps(result, sort_keys=True))
