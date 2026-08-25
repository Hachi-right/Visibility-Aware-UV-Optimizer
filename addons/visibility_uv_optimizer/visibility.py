# SPDX-License-Identifier: GPL-2.0-or-later

import math
from collections import deque

from mathutils import Vector
from mathutils.bvhtree import BVHTree


VISIBILITY_ATTRIBUTE = "vuv_visibility"
AUTO_VISIBILITY_ATTRIBUTE = "vuv_visibility_auto"
HIT_ATTRIBUTE = "vuv_hit_count"
OVERRIDE_ATTRIBUTE = "vuv_override"
EXTERIOR_RATIO_ATTRIBUTE = "vuv_exterior_ratio"
EXTERIOR_DEPTH_ATTRIBUTE = "vuv_exterior_depth"


def _ensure_attribute(mesh, name, data_type):
    attr = mesh.attributes.get(name)
    if attr and (
        attr.domain != 'FACE'
        or attr.data_type != data_type
        or len(attr.data) != len(mesh.polygons)
    ):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name=name, type=data_type, domain='FACE')
    return attr


def has_face_attribute(mesh, name, data_type=None):
    attr = mesh.attributes.get(name)
    return bool(
        attr is not None
        and attr.domain == 'FACE'
        and (data_type is None or attr.data_type == data_type)
    )


def read_face_float(mesh, name=VISIBILITY_ATTRIBUTE, default=0.0):
    polygon_count = len(mesh.polygons)
    attr = mesh.attributes.get(name)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'FLOAT':
        return [default] * polygon_count
    values = [default] * polygon_count
    for index, item in enumerate(attr.data):
        if index >= polygon_count:
            break
        values[index] = item.value
    return values


def read_face_int(mesh, name=OVERRIDE_ATTRIBUTE, default=0):
    polygon_count = len(mesh.polygons)
    attr = mesh.attributes.get(name)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT':
        return [default] * polygon_count
    values = [default] * polygon_count
    for index, item in enumerate(attr.data):
        if index >= polygon_count:
            break
        values[index] = item.value
    return values


def read_object_face_float(obj, name=VISIBILITY_ATTRIBUTE, default=0.0):
    if obj.mode != 'EDIT':
        return read_face_float(obj.data, name=name, default=default)

    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    layer = bm.faces.layers.float.get(name)
    if layer is None:
        return [default] * len(bm.faces)
    return [float(face[layer]) for face in bm.faces]


def read_object_face_int(obj, name=OVERRIDE_ATTRIBUTE, default=0):
    if obj.mode != 'EDIT':
        return read_face_int(obj.data, name=name, default=default)

    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    layer = bm.faces.layers.int.get(name)
    if layer is None:
        return [default] * len(bm.faces)
    return [int(face[layer]) for face in bm.faces]


def write_face_float(mesh, values, name=VISIBILITY_ATTRIBUTE):
    if len(values) != len(mesh.polygons):
        raise ValueError("Face value count does not match mesh polygon count")
    attr = _ensure_attribute(mesh, name, 'FLOAT')
    for index, value in enumerate(values):
        attr.data[index].value = float(value)


def write_face_int(mesh, values, name=OVERRIDE_ATTRIBUTE):
    if len(values) != len(mesh.polygons):
        raise ValueError("Face value count does not match mesh polygon count")
    attr = _ensure_attribute(mesh, name, 'INT')
    for index, value in enumerate(values):
        attr.data[index].value = int(value)


def apply_overrides(automatic_scores, overrides):
    scores = list(automatic_scores)
    for index, override in enumerate(overrides[:len(scores)]):
        if override > 0:
            scores[index] = 1.0
        elif override < 0:
            scores[index] = 0.0
    return scores


def effective_face_visibility(mesh, default=0.0):
    if has_face_attribute(mesh, AUTO_VISIBILITY_ATTRIBUTE, 'FLOAT'):
        automatic_scores = read_face_float(
            mesh, AUTO_VISIBILITY_ATTRIBUTE, default=default)
    else:
        automatic_scores = read_face_float(
            mesh, VISIBILITY_ATTRIBUTE, default=default)
    return apply_overrides(automatic_scores, read_face_int(mesh))


def effective_object_visibility(obj, default=0.0):
    if obj.mode != 'EDIT':
        return effective_face_visibility(obj.data, default=default)

    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    automatic_layer = bm.faces.layers.float.get(AUTO_VISIBILITY_ATTRIBUTE)
    visibility_layer = bm.faces.layers.float.get(VISIBILITY_ATTRIBUTE)
    if automatic_layer is not None:
        automatic_scores = read_object_face_float(
            obj, AUTO_VISIBILITY_ATTRIBUTE, default=default)
    elif visibility_layer is not None:
        automatic_scores = read_object_face_float(
            obj, VISIBILITY_ATTRIBUTE, default=default)
    else:
        automatic_scores = [default] * len(bm.faces)
    return apply_overrides(automatic_scores, read_object_face_int(obj))


def refresh_effective_visibility(mesh):
    if not (
            has_face_attribute(mesh, AUTO_VISIBILITY_ATTRIBUTE, 'FLOAT')
            or has_face_attribute(mesh, VISIBILITY_ATTRIBUTE, 'FLOAT')):
        return None
    scores = effective_face_visibility(mesh)
    write_face_float(mesh, scores, VISIBILITY_ATTRIBUTE)
    return scores


def validate_analysis_objects(objects):
    if not objects:
        raise ValueError("Select at least one mesh object")
    mesh_owners = {}
    for obj in objects:
        if obj.type != 'MESH':
            raise ValueError("Visibility analysis only supports mesh objects")
        mesh_key = obj.data.as_pointer()
        previous = mesh_owners.get(mesh_key)
        if previous is not None:
            raise ValueError(
                "Objects '{}' and '{}' share mesh data; make them single-user "
                "before visibility analysis".format(previous.name, obj.name)
            )
        mesh_owners[mesh_key] = obj


def _grid_index(x, y, z, dimensions):
    size_x, size_y, _ = dimensions
    return (z * size_y + y) * size_x + x


def _point_triangle_distance_squared(point, first, second, third):
    """Return the squared distance from a point to a triangle."""
    edge_ab = second - first
    edge_ac = third - first
    to_a = point - first
    dot_ab = edge_ab.dot(to_a)
    dot_ac = edge_ac.dot(to_a)
    if dot_ab <= 0.0 and dot_ac <= 0.0:
        return to_a.length_squared

    to_b = point - second
    dot_bb = edge_ab.dot(to_b)
    dot_bc = edge_ac.dot(to_b)
    if dot_bb >= 0.0 and dot_bc <= dot_bb:
        return to_b.length_squared

    edge_bc = third - second
    to_c = point - third
    dot_cc = edge_bc.dot(to_c)
    dot_cb = (-edge_ac).dot(to_c)
    if dot_cc >= 0.0 and dot_cb <= dot_cc:
        return to_c.length_squared

    edge_ab_cross = dot_ab * dot_bc - dot_bb * dot_ac
    if edge_ab_cross <= 0.0 and dot_ab >= 0.0 and dot_bb <= 0.0:
        factor = dot_ab / max(dot_ab - dot_bb, 1e-20)
        closest = first + edge_ab * factor
        return (point - closest).length_squared

    edge_ac_cross = dot_ac * dot_cc - dot_cb * dot_ac
    if edge_ac_cross <= 0.0 and dot_ac >= 0.0 and dot_cc <= 0.0:
        factor = dot_ac / max(dot_ac - dot_cc, 1e-20)
        closest = first + edge_ac * factor
        return (point - closest).length_squared

    edge_bc_cross = dot_bb * dot_cc - dot_cb * dot_bc
    if edge_bc_cross <= 0.0 and dot_bc >= 0.0 and dot_cb <= 0.0:
        factor = dot_bc / max(dot_bc - dot_cb, 1e-20)
        closest = second + edge_bc * factor
        return (point - closest).length_squared

    normal = edge_ab.cross(edge_ac)
    normal_length_squared = normal.length_squared
    if normal_length_squared <= 1e-20:
        return min(
            (point - first).length_squared,
            (point - second).length_squared,
            (point - third).length_squared,
        )
    distance = normal.dot(to_a)
    return (distance * distance) / normal_length_squared


def _mark_triangle_surface(surface, dimensions, origin, cell_size, first,
                            second, third):
    """Rasterize a triangle into conservative occupied surface voxels."""
    size_x, size_y, size_z = dimensions
    normal = (second - first).cross(third - first)
    normal_length = normal.length
    if normal_length <= 1e-12:
        return
    normal /= normal_length
    depth_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    projected_axes = tuple(axis for axis in range(3) if axis != depth_axis)
    projected_first = (first[projected_axes[0]], first[projected_axes[1]])
    projected_second = (second[projected_axes[0]], second[projected_axes[1]])
    projected_third = (third[projected_axes[0]], third[projected_axes[1]])
    denominator = (
        (projected_second[1] - projected_third[1]) *
        (projected_first[0] - projected_third[0])
        + (projected_third[0] - projected_second[0]) *
        (projected_first[1] - projected_third[1])
    )

    def voxel_axis(point, axis):
        return int(math.floor((point[axis] - origin[axis]) / cell_size))

    if abs(denominator) <= 1e-12:
        # Degenerate projection: retain a small conservative AABB footprint.
        minimum = Vector((
            min(first.x, second.x, third.x),
            min(first.y, second.y, third.y),
            min(first.z, second.z, third.z),
        ))
        maximum = Vector((
            max(first.x, second.x, third.x),
            max(first.y, second.y, third.y),
            max(first.z, second.z, third.z),
        ))
        minimum_index = [voxel_axis(minimum, axis) - 1 for axis in range(3)]
        maximum_index = [voxel_axis(maximum, axis) + 1 for axis in range(3)]
        for z in range(max(0, minimum_index[2]), min(size_z, maximum_index[2] + 1)):
            for y in range(max(0, minimum_index[1]), min(size_y, maximum_index[1] + 1)):
                for x in range(max(0, minimum_index[0]), min(size_x, maximum_index[0] + 1)):
                    surface[_grid_index(x, y, z, dimensions)] = 1
        return

    minimum_u = max(0, min(
        voxel_axis(first, projected_axes[0]),
        voxel_axis(second, projected_axes[0]),
        voxel_axis(third, projected_axes[0]),
    ) - 1)
    maximum_u = min(dimensions[projected_axes[0]], max(
                        voxel_axis(first, projected_axes[0]),
                        voxel_axis(second, projected_axes[0]),
                        voxel_axis(third, projected_axes[0]),
                    ) + 2)
    minimum_v = max(0, min(
        voxel_axis(first, projected_axes[1]),
        voxel_axis(second, projected_axes[1]),
        voxel_axis(third, projected_axes[1]),
    ) - 1)
    maximum_v = min(dimensions[projected_axes[1]], max(
                        voxel_axis(first, projected_axes[1]),
                        voxel_axis(second, projected_axes[1]),
                        voxel_axis(third, projected_axes[1]),
                    ) + 2)

    for first_index in range(minimum_u, maximum_u):
        coordinate_u = origin[projected_axes[0]] + (first_index + 0.5) * cell_size
        for second_index in range(minimum_v, maximum_v):
            coordinate_v = origin[projected_axes[1]] + (second_index + 0.5) * cell_size
            weight_first = (
                (projected_second[1] - projected_third[1]) *
                (coordinate_u - projected_third[0])
                + (projected_third[0] - projected_second[0]) *
                (coordinate_v - projected_third[1])
            ) / denominator
            weight_second = (
                (projected_third[1] - projected_first[1]) *
                (coordinate_u - projected_third[0])
                + (projected_first[0] - projected_third[0]) *
                (coordinate_v - projected_third[1])
            ) / denominator
            weight_third = 1.0 - weight_first - weight_second
            if min(weight_first, weight_second, weight_third) < -0.08:
                continue
            depth = (
                first[depth_axis] * weight_first
                + second[depth_axis] * weight_second
                + third[depth_axis] * weight_third
            )
            depth_index = int(math.floor(
                (depth - origin[depth_axis]) / cell_size))
            for offset in (-1, 0, 1):
                index = depth_index + offset
                if index < 0:
                    continue
                if index >= dimensions[depth_axis]:
                    continue
                coordinates = [0, 0, 0]
                coordinates[depth_axis] = index
                coordinates[projected_axes[0]] = first_index
                coordinates[projected_axes[1]] = second_index
                surface[_grid_index(
                    coordinates[0], coordinates[1], coordinates[2], dimensions
                )] = 1

    # Small triangles may not contain a projected voxel center.
    centroid = (first + second + third) / 3.0
    center_coordinates = [voxel_axis(centroid, axis) for axis in range(3)]
    for axis in range(3):
        for offset in (-1, 0, 1):
            coordinates = center_coordinates.copy()
            coordinates[axis] += offset
            if all(0 <= coordinates[axis_index] < dimensions[axis_index]
                   for axis_index in range(3)):
                surface[_grid_index(
                    coordinates[0], coordinates[1], coordinates[2], dimensions
                )] = 1


def _flood_exterior(surface, dimensions):
    """Flood empty voxels from the grid boundary and return air distances."""
    size_x, size_y, size_z = dimensions
    exterior = bytearray(len(surface))
    distances = [0] * len(surface)
    queue = deque()

    def enqueue(x, y, z):
        index = _grid_index(x, y, z, dimensions)
        if surface[index] or exterior[index]:
            return
        exterior[index] = 1
        queue.append(index)

    for z in range(size_z):
        for y in range(size_y):
            enqueue(0, y, z)
            enqueue(size_x - 1, y, z)
    for z in range(size_z):
        for x in range(size_x):
            enqueue(x, 0, z)
            enqueue(x, size_y - 1, z)
    for y in range(size_y):
        for x in range(size_x):
            enqueue(x, y, 0)
            enqueue(x, y, size_z - 1)

    maximum_distance = 0
    while queue:
        index = queue.popleft()
        current_distance = distances[index]
        if current_distance > maximum_distance:
            maximum_distance = current_distance
        x = index % size_x
        yz = index // size_x
        y = yz % size_y
        z = yz // size_y
        for next_x, next_y, next_z in (
            (x - 1, y, z), (x + 1, y, z),
            (x, y - 1, z), (x, y + 1, z),
            (x, y, z - 1), (x, y, z + 1),
        ):
            if not (0 <= next_x < size_x and 0 <= next_y < size_y and
                    0 <= next_z < size_z):
                continue
            next_index = _grid_index(next_x, next_y, next_z, dimensions)
            if surface[next_index] or exterior[next_index]:
                continue
            exterior[next_index] = 1
            distances[next_index] = current_distance + 1
            queue.append(next_index)
    return exterior, distances, maximum_distance


def _world_to_voxel(point, origin, cell_size, dimensions):
    coordinates = [
        int(math.floor((point[axis] - origin[axis]) / cell_size))
        for axis in range(3)
    ]
    if not all(0 <= coordinates[axis] < dimensions[axis] for axis in range(3)):
        return None
    return tuple(coordinates)


def _probe_exterior(point, normal, origin, cell_size, dimensions, exterior,
                    distances, surface):
    if normal.length_squared <= 1e-20:
        return None
    normal = normal.normalized()
    left_surface = False
    for factor in (0.5, 0.75, 1.0, 1.25, 1.75, 2.5, 3.5):
        probe = point + normal * (cell_size * factor)
        coordinates = _world_to_voxel(
            probe, origin, cell_size, dimensions)
        if coordinates is None:
            continue
        index = _grid_index(*coordinates, dimensions)
        if surface[index]:
            if left_surface:
                break
            continue
        left_surface = True
        if exterior[index]:
            return distances[index]
    return None


def _face_samples(objects, triangles, triangle_map, samples_per_triangle=4,
                  sample_limit=16):
    samples = {
        obj: [[] for _ in obj.data.polygons]
        for obj in objects
    }
    for (first, second, third), (obj, face_index) in zip(
            triangles, triangle_map):
        normal = (second - first).cross(third - first)
        if normal.length_squared <= 1e-20:
            continue
        normal.normalize()
        points = (
            (first + second + third) / 3.0,
            first * 0.5 + second * 0.25 + third * 0.25,
            first * 0.25 + second * 0.5 + third * 0.25,
            first * 0.25 + second * 0.25 + third * 0.5,
            (first + second) * 0.5,
            (second + third) * 0.5,
            (third + first) * 0.5,
        )
        face_samples = samples[obj][face_index]
        if len(face_samples) < sample_limit:
            count = max(1, min(int(samples_per_triangle), len(points)))
            face_samples.extend((point, normal) for point in points[:count])
            if len(face_samples) > sample_limit:
                del face_samples[sample_limit:]
    return samples


def analyze_exterior(objects, resolution=96, depth_falloff=0.12,
                     samples_per_triangle=4):
    """Classify faces by connectivity to outside air using voxel flood fill."""
    validate_analysis_objects(objects)
    triangles = []
    triangle_map = []
    all_points = []
    for obj in objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        world_vertices = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        for triangle in mesh.loop_triangles:
            first, second, third = (
                world_vertices[index] for index in triangle.vertices)
            triangles.append((first, second, third))
            triangle_map.append((obj, triangle.polygon_index))
            all_points.extend((first, second, third))
    if not triangles:
        raise ValueError("Selected meshes contain no triangles")

    minimum = Vector((
        min(point.x for point in all_points),
        min(point.y for point in all_points),
        min(point.z for point in all_points),
    ))
    maximum = Vector((
        max(point.x for point in all_points),
        max(point.y for point in all_points),
        max(point.z for point in all_points),
    ))
    extent = maximum - minimum
    max_extent = max(extent.x, extent.y, extent.z)
    if max_extent <= 1e-8:
        raise ValueError("Selected meshes are too small for exterior analysis")
    resolution = max(int(resolution), 16)
    cell_size = max_extent / resolution
    padding = 3
    origin = minimum - Vector((cell_size * padding,) * 3)
    dimensions = tuple(
        max(4, int(math.ceil(value / cell_size)) + padding * 2 + 1)
        for value in extent
    )
    total_cells = dimensions[0] * dimensions[1] * dimensions[2]
    if total_cells > 24_000_000:
        raise ValueError(
            "Exterior voxel grid is too large; lower Exterior Resolution"
        )
    surface = bytearray(total_cells)
    for first, second, third in triangles:
        _mark_triangle_surface(
            surface, dimensions, origin, cell_size, first, second, third)
    exterior, distances, maximum_distance = _flood_exterior(
        surface, dimensions)
    samples = _face_samples(
        objects, triangles, triangle_map,
        samples_per_triangle=samples_per_triangle,
    )
    raw_scores = {obj: [0.0] * len(obj.data.polygons) for obj in objects}
    hit_totals = {obj: [0] * len(obj.data.polygons) for obj in objects}
    ratios = {obj: [0.0] * len(obj.data.polygons) for obj in objects}
    depths = {obj: [0.0] * len(obj.data.polygons) for obj in objects}
    for obj in objects:
        for face_index, face_samples in enumerate(samples[obj]):
            if not face_samples:
                continue
            exposed = 0
            face_depths = []
            for point, normal in face_samples:
                positive = _probe_exterior(
                    point, normal, origin, cell_size, dimensions,
                    exterior, distances, surface)
                negative = _probe_exterior(
                    point, -normal, origin, cell_size, dimensions,
                    exterior, distances, surface)
                candidates = [value for value in (positive, negative)
                              if value is not None]
                if not candidates:
                    continue
                exposed += 1
                face_depths.append(min(candidates))
            ratio = exposed / max(len(face_samples), 1)
            ratio_value = float(max(0.0, min(1.0, ratio)))
            depth = min(face_depths, default=0)
            relative_depth = max(float(depth - padding), 0.0)
            depth_factor = 1.0 / (1.0 + max(float(depth_falloff), 0.0) * relative_depth)
            ratios[obj][face_index] = ratio_value
            depths[obj][face_index] = relative_depth * cell_size
            raw_scores[obj][face_index] = ratio_value * depth_factor
            hit_totals[obj][face_index] = exposed

    maximum = max((max(values, default=0.0) for values in raw_scores.values()), default=0.0)
    maximum = max(maximum, 1e-12)
    result = {}
    for obj in objects:
        automatic_scores = [value / maximum for value in raw_scores[obj]]
        overrides = read_face_int(obj.data)
        scores = apply_overrides(automatic_scores, overrides)
        write_face_float(
            obj.data, automatic_scores, AUTO_VISIBILITY_ATTRIBUTE)
        write_face_float(obj.data, scores)
        write_face_float(obj.data, ratios[obj], EXTERIOR_RATIO_ATTRIBUTE)
        write_face_float(obj.data, depths[obj], EXTERIOR_DEPTH_ATTRIBUTE)
        hits_attr = _ensure_attribute(obj.data, HIT_ATTRIBUTE, 'INT')
        for index, hits in enumerate(hit_totals[obj]):
            hits_attr.data[index].value = int(hits)
        result[obj] = scores
    exterior_count = sum(1 for value in exterior if value)
    stats = {
        "dimensions": dimensions,
        "cell_size": cell_size,
        "exterior_cells": exterior_count,
        "maximum_distance": maximum_distance,
    }
    return result, hit_totals, stats


def build_combined_bvh(objects):
    validate_analysis_objects(objects)
    vertices = []
    triangles = []
    triangle_map = []
    for obj in objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        offset = len(vertices)
        vertices.extend(obj.matrix_world @ vertex.co for vertex in mesh.vertices)
        for triangle in mesh.loop_triangles:
            triangles.append(tuple(offset + index for index in triangle.vertices))
            triangle_map.append((obj, triangle.polygon_index))
    if not triangles:
        raise ValueError("Selected meshes contain no triangles")
    return BVHTree.FromPolygons(vertices, triangles, all_triangles=True), triangle_map


def _camera_rays(scene, camera_obj, resolution):
    rotation = camera_obj.matrix_world.to_quaternion()
    right = rotation @ Vector((1.0, 0.0, 0.0))
    up = rotation @ Vector((0.0, 1.0, 0.0))
    forward = rotation @ Vector((0.0, 0.0, -1.0))
    origin = camera_obj.matrix_world.translation.copy()
    camera = camera_obj.data
    render = scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(
        render.resolution_y * render.pixel_aspect_y, 1.0)

    if camera.type == 'ORTHO':
        half_height = camera.ortho_scale * 0.5
        half_width = half_height * aspect
        for row in range(resolution):
            ny = ((row + 0.5) / resolution) * 2.0 - 1.0
            for column in range(resolution):
                nx = ((column + 0.5) / resolution) * 2.0 - 1.0
                ray_origin = origin + right * (nx * half_width) + up * (ny * half_height)
                yield ray_origin, forward
    else:
        tan_x = math.tan(camera.angle_x * 0.5)
        tan_y = math.tan(camera.angle_y * 0.5)
        for row in range(resolution):
            ny = ((row + 0.5) / resolution) * 2.0 - 1.0 + camera.shift_y * 2.0
            for column in range(resolution):
                nx = ((column + 0.5) / resolution) * 2.0 - 1.0 + camera.shift_x * 2.0
                direction = (forward + right * (nx * tan_x) + up * (ny * tan_y)).normalized()
                yield origin, direction


def sphere_directions(samples, basis=None):
    """Return equal-area directions with exact X/Y/Z reflection symmetry."""
    sample_count = max(int(samples), 8)
    octant_count = max((sample_count + 7) // 8, 1)
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    directions = []
    for index in range(octant_count):
        z = (index + 0.5) / octant_count
        radial = math.sqrt(max(1.0 - z * z, 0.0))
        # Keep the azimuth inside one octant, then reflect it into all eight
        # octants. This makes a bilateral model receive matching directions.
        theta = (golden_angle * (index + 0.5)) % (math.pi * 0.5)
        base = Vector((
            radial * math.cos(theta),
            radial * math.sin(theta),
            z,
        )).normalized()
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    local_direction = Vector((
                        base.x * sx,
                        base.y * sy,
                        base.z * sz,
                    ))
                    if basis is None:
                        directions.append(local_direction)
                    else:
                        directions.append(sum(
                            (axis * value for axis, value in zip(
                                basis, local_direction)),
                            Vector((0.0, 0.0, 0.0)),
                        ).normalized())
    return directions


def _projection_range(center, corners, axis, fallback):
    if not corners:
        return -fallback, fallback
    values = [(corner - center).dot(axis) for corner in corners]
    return min(values), max(values)


def sphere_rays(center, radius, samples, coverage_grid=1, corners=None,
                basis=None):
    """Yield sphere-surface rays, using a symmetric inward coverage bundle."""
    grid = max(int(coverage_grid), 1)
    directions = sphere_directions(samples, basis=basis)
    for outward in directions:
        reference = basis[2] if basis is not None else Vector((0.0, 0.0, 1.0))
        if abs(outward.dot(reference)) > 0.9:
            reference = basis[1] if basis is not None else Vector((0.0, 1.0, 0.0))
        right = reference.cross(outward).normalized()
        up = outward.cross(right).normalized()
        fallback = radius * 0.65
        minimum_u, maximum_u = _projection_range(
            center, corners, right, fallback)
        minimum_v, maximum_v = _projection_range(
            center, corners, up, fallback)

        if grid == 1:
            u_values = (0.0,)
            v_values = (0.0,)
        else:
            middle_u = (minimum_u + maximum_u) * 0.5
            middle_v = (minimum_v + maximum_v) * 0.5
            half_u = (maximum_u - minimum_u) * 0.5 * 0.96
            half_v = (maximum_v - minimum_v) * 0.5 * 0.96
            u_values = tuple(
                middle_u + half_u * (2.0 * index / (grid - 1) - 1.0)
                for index in range(grid)
            )
            v_values = tuple(
                middle_v + half_v * (2.0 * index / (grid - 1) - 1.0)
                for index in range(grid)
            )

        for u in u_values:
            for v in v_values:
                lateral = right * u + up * v
                maximum_lateral = radius * 0.98
                if lateral.length > maximum_lateral:
                    lateral *= maximum_lateral / lateral.length
                axial_squared = max(radius * radius - lateral.length_squared, 0.0)
                origin = center + outward * math.sqrt(axial_squared) + lateral
                yield origin, -outward


def _ray_hits(bvh, origin, direction, max_distance, ignore_backfaces, epsilon, layers):
    hits = []
    remaining = max_distance
    current_origin = origin
    for _ in range(max(layers * 8, 8)):
        location, normal, triangle_index, distance = bvh.ray_cast(
            current_origin, direction, remaining)
        if triangle_index is None:
            break
        if not ignore_backfaces or normal.dot(direction) < -1e-6:
            hits.append(triangle_index)
            if len(hits) >= layers:
                break
        step = max(distance, 0.0) + epsilon
        current_origin = location + direction * epsilon
        remaining -= step
        if remaining <= 0.0:
            break
    return hits


def analyze(scene, objects, cameras=None, resolution=128, ignore_backfaces=True,
            sphere=None, sphere_samples=256, sphere_layers=3,
            sphere_layer_falloff=0.35, sphere_weight=1.0,
            sphere_coverage_grid=1, sphere_basis=None, exterior=False,
            exterior_resolution=96, exterior_depth_falloff=0.12,
            exterior_surface_samples=4):
    if exterior:
        scores, hit_totals, _ = analyze_exterior(
            objects,
            resolution=exterior_resolution,
            depth_falloff=exterior_depth_falloff,
            samples_per_triangle=exterior_surface_samples,
        )
        return scores, hit_totals
    bvh, triangle_map = build_combined_bvh(objects)
    raw_scores = {obj: [0.0] * len(obj.data.polygons) for obj in objects}
    hit_totals = {obj: [0] * len(obj.data.polygons) for obj in objects}
    all_corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if all_corners:
        minimum = Vector((
            min(point.x for point in all_corners),
            min(point.y for point in all_corners),
            min(point.z for point in all_corners),
        ))
        maximum = Vector((
            max(point.x for point in all_corners),
            max(point.y for point in all_corners),
            max(point.z for point in all_corners),
        ))
        diagonal = (maximum - minimum).length
    else:
        diagonal = 1.0
    epsilon = max(diagonal * 1e-6, 1e-7)
    ray_sources = []
    for camera in cameras or ():
        ray_sources.append((
            _camera_rays(scene, camera, resolution),
            max(camera.data.clip_end, diagonal * 4.0),
            max(float(camera.get("vuv_weight", 1.0)), 0.0),
            1,
            resolution * resolution,
            False,
        ))
    if sphere is not None:
        center, radius = sphere
        if radius <= epsilon:
            raise ValueError("Sphere radius is too small")
        max_corner_radius = max(
            ((corner - center).length for corner in all_corners),
            default=0.0,
        )
        if max_corner_radius > radius + epsilon:
            raise ValueError(
                "Sphere does not enclose the selected meshes; increase Sphere Radius"
            )
        sphere_ray_list = list(sphere_rays(
            center,
            radius,
            sphere_samples,
            coverage_grid=sphere_coverage_grid,
            corners=all_corners,
            basis=sphere_basis,
        ))
        ray_sources.append((
            iter(sphere_ray_list),
            radius * 2.0 + epsilon,
            max(float(sphere_weight), 0.0),
            max(int(sphere_layers), 1),
            len(sphere_ray_list),
            True,
        ))

    for (ray_iterator, max_distance, source_weight, source_layers,
         source_ray_count, is_sphere_source) in ray_sources:
        for origin, direction in ray_iterator:
            hits = _ray_hits(
                bvh, origin, direction, max_distance, ignore_backfaces, epsilon,
                source_layers)
            for layer_index, triangle_index in enumerate(hits):
                obj, face_index = triangle_map[triangle_index]
                layer_weight = (
                    sphere_layer_falloff ** layer_index
                    if is_sphere_source else 1.0
                )
                hit_totals[obj][face_index] += 1
                raw_scores[obj][face_index] += (
                    source_weight * layer_weight / max(source_ray_count, 1)
                )
    maximum = max((max(values, default=0.0) for values in raw_scores.values()), default=0.0)
    maximum = max(maximum, 1e-12)
    result = {}
    for obj in objects:
        automatic_scores = [value / maximum for value in raw_scores[obj]]
        overrides = read_face_int(obj.data)
        scores = apply_overrides(automatic_scores, overrides)
        write_face_float(
            obj.data, automatic_scores, AUTO_VISIBILITY_ATTRIBUTE)
        write_face_float(obj.data, scores)
        hits_attr = _ensure_attribute(obj.data, HIT_ATTRIBUTE, 'INT')
        for index, hits in enumerate(hit_totals[obj]):
            hits_attr.data[index].value = int(hits)
        result[obj] = scores
    return result, hit_totals
