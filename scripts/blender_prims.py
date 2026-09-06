"""Reusable Blender mesh / material / lighting primitives (stdlib + bpy)."""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence


def mat(name: str, color: Sequence[float], metal: float = 0.0, rough: float = 0.4):
    import bpy

    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color[:3], 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color[:3], 1)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    return m


def apply_emission(material, color: Sequence[float], strength: float) -> None:
    p = material.node_tree.nodes.get("Principled BSDF")
    if p is None:
        return
    if "Emission Color" in p.inputs:
        p.inputs["Emission Color"].default_value = (*color[:3], 1)
        p.inputs["Emission Strength"].default_value = strength


def wood_plank_material(name: str = "Sunlit peach timber"):
    """Timber with noise-mapped grain (used by boardwalk)."""
    import bpy

    planks = mat(name, (0.68, 0.37, 0.23), 0, 0.7)
    p = planks.node_tree.nodes.get("Principled BSDF")
    n = planks.node_tree.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = 7
    n.inputs["Detail"].default_value = 3
    tex = planks.node_tree.nodes.new("ShaderNodeTexCoord")
    mapping = planks.node_tree.nodes.new("ShaderNodeVectorMath")
    mapping.operation = "MULTIPLY"
    mapping.inputs[1].default_value = (1, 35, 4)
    planks.node_tree.links.new(tex.outputs["Generated"], mapping.inputs[0])
    planks.node_tree.links.new(mapping.outputs[0], n.inputs["Vector"])
    ramp = planks.node_tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.38, 0.17, 0.085, 1)
    ramp.color_ramp.elements[1].color = (0.78, 0.49, 0.28, 1)
    planks.node_tree.links.new(n.outputs["Fac"], ramp.inputs[0])
    planks.node_tree.links.new(ramp.outputs[0], p.inputs["Base Color"])
    return planks


def sunset_sky_material(name: str = "Sunset sky gradient"):
    import bpy

    sky_mat = bpy.data.materials.new(name)
    sky_mat.use_nodes = True
    nodes = sky_mat.node_tree.nodes
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    em = nodes.new("ShaderNodeEmission")
    coord = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    r = nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements.remove(r.color_ramp.elements[1])
    r.color_ramp.elements[0].position = 0
    r.color_ramp.elements[0].color = (1, 0.52, 0.28, 1)
    for pos, color in [
        (0.15, (1, 0.71, 0.50, 1)),
        (0.42, (0.43, 0.72, 0.80, 1)),
        (1, (0.12, 0.42, 0.64, 1)),
    ]:
        r.color_ramp.elements.new(pos).color = color
    sky_mat.node_tree.links.new(coord.outputs["Generated"], sep.inputs[0])
    sky_mat.node_tree.links.new(sep.outputs["Z"], r.inputs[0])
    sky_mat.node_tree.links.new(r.outputs[0], em.inputs[0])
    sky_mat.node_tree.links.new(em.outputs[0], out.inputs[0])
    em.inputs[1].default_value = 0.8
    return sky_mat


def smooth(obj, material):
    obj.data.materials.append(material)
    if obj.type == "MESH":
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return obj


def ell(name: str, loc, scale, material, segments: int = 40, rings: int = 24):
    import bpy

    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = scale
    smooth(o, material)
    return o


def rod(name: str, a, b, r: float, material):
    import bpy
    from mathutils import Vector

    a, b = Vector(a), Vector(b)
    d = b - a
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=24, radius=r, depth=d.length, location=(a + b) / 2
    )
    o = bpy.context.object
    o.name = name
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    smooth(o, material)
    bevel = o.modifiers.new("Rounded edges", "BEVEL")
    bevel.width = r * 0.28
    bevel.segments = 3
    o.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    return o


def path(name: str, coords, r: float, material):
    import bpy

    c = bpy.data.curves.new(name, "CURVE")
    c.dimensions = "3D"
    c.bevel_depth = r
    c.bevel_resolution = 4
    s = c.splines.new("BEZIER")
    s.bezier_points.add(len(coords) - 1)
    for p, co in zip(s.bezier_points, coords):
        p.co = co
        p.handle_left_type = "AUTO"
        p.handle_right_type = "AUTO"
    o = bpy.data.objects.new(name, c)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(material)
    return o


def torus(name: str, loc, major: float, minor: float, material):
    import bpy

    bpy.ops.mesh.primitive_torus_add(
        major_segments=80,
        minor_segments=16,
        location=loc,
        major_radius=major,
        minor_radius=minor,
        rotation=(math.pi / 2, 0, 0),
    )
    o = bpy.context.object
    o.name = name
    smooth(o, material)
    return o


def mesh(name: str, verts, faces, material, bevel: float = 0.06):
    import bpy

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    smooth(o, material)
    if bevel:
        mod = o.modifiers.new("Soft sculpted edges", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        o.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    return o


def box(name: str, loc, scale, material, bevel: float = 0.04):
    import bpy

    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    smooth(o, material)
    if bevel:
        b = o.modifiers.new("Soft corners", "BEVEL")
        b.width = bevel
        b.segments = 3
        o.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    return o


def area(name: str, loc, power: float, size: float, color=None, aim_at=(0, 0, 2.3)):
    import bpy
    from mathutils import Vector

    bpy.ops.object.light_add(type="AREA", location=loc)
    o = bpy.context.object
    o.name = name
    o.data.energy = power
    o.data.shape = "DISK"
    o.data.size = size
    if color is not None:
        o.data.color = color[:3]
    o.rotation_euler = (Vector(aim_at) - o.location).to_track_quat("-Z", "Y").to_euler()
    return o


def empty(name: str, loc=(0, 0, 0), display: str = "PLAIN_AXES"):
    import bpy

    o = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(o)
    o.empty_display_type = display
    o.location = loc
    return o


def parent_to(child, parent) -> None:
    """Parent while keeping the child's current world transform."""
    import bpy

    bpy.context.view_layer.update()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


def clear_scene() -> None:
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        if block.users == 0:
            bpy.data.materials.remove(block)
    for block in list(bpy.data.curves):
        if block.users == 0:
            bpy.data.curves.remove(block)
    for block in list(bpy.data.lights):
        if block.users == 0:
            bpy.data.lights.remove(block)
    for block in list(bpy.data.cameras):
        if block.users == 0:
            bpy.data.cameras.remove(block)


def set_world_bg(color: Sequence[float], strength: float = 0.24) -> None:
    import bpy

    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
    rgba = (*color[:3], color[3] if len(color) > 3 else 1.0)
    bg.inputs[0].default_value = rgba
    bg.inputs[1].default_value = strength


def make_camera(
    name: str,
    location,
    look_at,
    *,
    lens: float = 50,
    ortho_scale: float | None = None,
    camera_type: str = "ORTHO",
):
    import bpy
    from mathutils import Vector

    bpy.ops.object.camera_add(location=location)
    cam = bpy.context.object
    cam.name = name
    cam.data.lens = lens
    if camera_type.upper() == "ORTHO" and ortho_scale is not None:
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = ortho_scale
    elif camera_type.upper() == "PERSP":
        cam.data.type = "PERSP"
    target = Vector(look_at)
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def set_rotation(obj, euler) -> None:
    if euler is None:
        return
    obj.rotation_euler = euler


def set_scale(obj, scale) -> None:
    if scale is None:
        return
    obj.scale = scale


def solidify(obj, thickness: float) -> None:
    mod = obj.modifiers.new("Solidify", "SOLIDIFY")
    mod.thickness = thickness


def resolve_material(materials: dict[str, Any], name: str | None):
    if not name:
        return None
    if name not in materials:
        raise KeyError(f"Unknown material: {name}")
    return materials[name]


def as_tuple3(value: Iterable[float]) -> tuple[float, float, float]:
    vals = list(value)
    return (float(vals[0]), float(vals[1]), float(vals[2]))
