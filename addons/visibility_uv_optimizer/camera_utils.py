# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from mathutils import Vector


CAMERA_COLLECTION = "VUV_Cameras"
SPHERE_COLLECTION = "VUV_Sphere_Sampler"
SPHERE_OBJECT_NAME = "VUV_SPHERE_SAMPLER"
SPHERE_TAG = "vuv_sphere_sampler"
AUTO_CAMERA_TAG = "vuv_auto_camera"
AUTO_CAMERA_ID = "vuv_auto_camera_id"
AUTO_CAMERA_NAMES = (
    "VUV_AUTO_POS_X",
    "VUV_AUTO_NEG_X",
    "VUV_AUTO_POS_Y",
    "VUV_AUTO_NEG_Y",
    "VUV_AUTO_POS_Z",
    "VUV_AUTO_NEG_Z",
)


def selected_meshes(context):
    result = [obj for obj in context.selected_objects if obj.type == 'MESH']
    active = context.active_object
    if not result and active and active.type == 'MESH':
        result = [active]
    return result


def find_sphere_sampler():
    exact = bpy.data.objects.get(SPHERE_OBJECT_NAME)
    if exact is not None and exact.type == 'EMPTY' and exact.get(SPHERE_TAG, False):
        return exact
    return next((
        obj for obj in bpy.data.objects
        if obj.type == 'EMPTY' and obj.get(SPHERE_TAG, False)
    ), None)


def _find_auto_camera(identifier):
    exact = bpy.data.objects.get(identifier)
    if (exact is not None and exact.type == 'CAMERA'
            and exact.get(AUTO_CAMERA_TAG, False)):
        return exact
    return next((
        obj for obj in bpy.data.objects
        if obj.type == 'CAMERA'
        and obj.get(AUTO_CAMERA_TAG, False)
        and obj.get(AUTO_CAMERA_ID) == identifier
    ), None)


def _world_bbox_corners(objects):
    corners = []
    for obj in objects:
        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    return corners


def _basis(context, settings):
    active = context.active_object
    if settings.axis_basis == 'ACTIVE' and active:
        rotation = active.matrix_world.to_quaternion()
        return tuple((rotation @ axis).normalized() for axis in (
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
        ))
    return (
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
    )


def ensure_camera_collection(scene):
    collection = bpy.data.collections.get(CAMERA_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(CAMERA_COLLECTION)
        scene.collection.children.link(collection)
    elif collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    collection.hide_render = True
    return collection


def _ensure_collection(scene, name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
    elif collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    collection.hide_render = True
    return collection


def sphere_bounds(objects):
    corners = _world_bbox_corners(objects)
    if not corners:
        raise ValueError("Selected objects have no bounds")
    minimum = Vector((
        min(point.x for point in corners),
        min(point.y for point in corners),
        min(point.z for point in corners),
    ))
    maximum = Vector((
        max(point.x for point in corners),
        max(point.y for point in corners),
        max(point.z for point in corners),
    ))
    center = (minimum + maximum) * 0.5
    bounding_radius = max((point - center).length for point in corners)
    return center, max(bounding_radius, 0.01)


def create_sphere_sampler(context, settings, targets=None):
    targets = targets or selected_meshes(context)
    if not targets:
        raise ValueError("Select at least one mesh object")
    center, bounding_radius = sphere_bounds(targets)
    radius = bounding_radius * settings.sphere_radius_factor
    collection = _ensure_collection(context.scene, SPHERE_COLLECTION)
    sampler = find_sphere_sampler()
    if sampler is None:
        sampler = bpy.data.objects.new(SPHERE_OBJECT_NAME, None)
        collection.objects.link(sampler)
    elif sampler.name not in collection.objects:
        for owner in tuple(sampler.users_collection):
            owner.objects.unlink(sampler)
        collection.objects.link(sampler)
    sampler.empty_display_type = 'SPHERE'
    sampler.empty_display_size = radius
    sampler.scale = (1.0, 1.0, 1.0)
    sampler.location = center
    sampler.color = (0.12, 0.55, 1.0, 0.3)
    sampler.show_in_front = True
    sampler.hide_render = True
    sampler[SPHERE_TAG] = True
    sampler["vuv_radius"] = radius
    sampler["vuv_bounding_radius"] = bounding_radius
    sampler.hide_viewport = not settings.sphere_visible
    context.view_layer.update()
    return sampler


def remove_sphere_sampler():
    samplers = [
        obj for obj in bpy.data.objects if obj.get(SPHERE_TAG, False)
    ]
    for sampler in samplers:
        bpy.data.objects.remove(sampler, do_unlink=True)
    return len(samplers)


def get_sphere_sampler(context, settings, auto_create=False, targets=None):
    sampler = find_sphere_sampler()
    if sampler is None and auto_create:
        sampler = create_sphere_sampler(context, settings, targets)
    if sampler is not None:
        sampler.hide_viewport = not settings.sphere_visible
    return sampler


def create_auto_cameras(context, settings, targets=None):
    targets = targets or selected_meshes(context)
    if not targets:
        raise ValueError("Select at least one mesh object")

    corners = _world_bbox_corners(targets)
    if not corners:
        raise ValueError("Selected objects have no bounds")

    axes = _basis(context, settings)
    ranges = []
    for axis in axes:
        values = [corner.dot(axis) for corner in corners]
        ranges.append((min(values), max(values)))
    center = sum(
        (axis * ((axis_range[0] + axis_range[1]) * 0.5)
         for axis, axis_range in zip(axes, ranges)),
        Vector((0.0, 0.0, 0.0)),
    )
    diagonal = max((corner - center).length for corner in corners)
    diagonal = max(diagonal, 0.01)

    collection = ensure_camera_collection(context.scene)
    render = context.scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(
        render.resolution_y * render.pixel_aspect_y, 1.0)

    directions = (
        axes[0], -axes[0], axes[1], -axes[1], axes[2], -axes[2]
    )
    cameras = []
    for name, outward in zip(AUTO_CAMERA_NAMES, directions):
        camera_obj = _find_auto_camera(name)
        if camera_obj is None:
            camera_data = bpy.data.cameras.new(name)
            camera_obj = bpy.data.objects.new(name, camera_data)
            collection.objects.link(camera_obj)
        elif camera_obj.name not in collection.objects:
            for owner in tuple(camera_obj.users_collection):
                owner.objects.unlink(camera_obj)
            collection.objects.link(camera_obj)

        forward = -outward
        rotation = forward.to_track_quat('-Z', 'Y')
        right = rotation @ Vector((1.0, 0.0, 0.0))
        up = rotation @ Vector((0.0, 1.0, 0.0))
        half_width = max(abs((corner - center).dot(right)) for corner in corners)
        half_height = max(abs((corner - center).dot(up)) for corner in corners)
        half_depth = max(abs((corner - center).dot(outward)) for corner in corners)
        ortho_scale = max(half_height * 2.0, half_width * 2.0 / max(aspect, 1e-6))
        ortho_scale = max(ortho_scale * (1.0 + settings.camera_margin), 0.01)
        distance = half_depth + diagonal * 0.25 + 0.01

        camera_obj.location = center + outward * distance
        camera_obj.rotation_euler = rotation.to_euler()
        camera_obj.data.type = 'ORTHO'
        camera_obj.data.ortho_scale = ortho_scale
        camera_obj.data.clip_start = max(diagonal * 0.0001, 0.0001)
        camera_obj.data.clip_end = distance + diagonal * 3.0
        camera_obj.data.display_size = max(diagonal * 0.12, 0.05)
        camera_obj.hide_render = True
        camera_obj[AUTO_CAMERA_TAG] = True
        camera_obj[AUTO_CAMERA_ID] = name
        if "vuv_weight" not in camera_obj:
            camera_obj["vuv_weight"] = 1.0
        cameras.append(camera_obj)
    context.view_layer.update()
    return cameras


def remove_auto_cameras():
    removed = 0
    cameras = [
        obj for obj in bpy.data.objects
        if obj.type == 'CAMERA' and obj.get(AUTO_CAMERA_TAG, False)
    ]
    for obj in cameras:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            bpy.data.cameras.remove(data)
        removed += 1
    return removed


def get_analysis_cameras(context, settings, auto_create=False, targets=None):
    auto = []
    if settings.camera_source in {'AUTO', 'HYBRID'}:
        auto = [
            camera for name in AUTO_CAMERA_NAMES
            if (camera := _find_auto_camera(name)) is not None
        ]
        if auto_create and len(auto) != 6:
            auto = create_auto_cameras(context, settings, targets)

    manual = []
    if settings.camera_source in {'SELECTED', 'HYBRID'}:
        manual = [obj for obj in context.selected_objects if obj.type == 'CAMERA']

    result = []
    seen = set()
    for camera in auto + manual:
        if camera and camera.as_pointer() not in seen:
            seen.add(camera.as_pointer())
            result.append(camera)
    return result
