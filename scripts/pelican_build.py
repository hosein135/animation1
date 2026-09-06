"""
Build an animated pelican-on-bicycle coastal parade scene (Blender bpy).

Geometry and look follow simonw/gpt-6-astra-blender-pelican-bicycle
(pelican_scene → pelican_flair → pelican_final), with ride animation for video.
"""

from __future__ import annotations

import math
import random
from pathlib import Path


def build_pelican_animation(scene_cfg: dict) -> None:
    import bpy
    from mathutils import Vector

    random.seed(14)
    fps = int(scene_cfg["fps"])
    duration = float(scene_cfg["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))
    ride_cfg = scene_cfg.get("ride", {})
    x_start = float(ride_cfg.get("x_start", -3.2))
    x_end = float(ride_cfg.get("x_end", 3.8))
    wheel_radius = float(ride_cfg.get("wheel_radius", 0.91))

    # --- helpers (from pelican_scene / flair) ---
    def mat(name, color, metal=0.0, rough=0.4):
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*color, 1)
        m.use_nodes = True
        p = m.node_tree.nodes.get("Principled BSDF")
        p.inputs["Base Color"].default_value = (*color, 1)
        p.inputs["Metallic"].default_value = metal
        p.inputs["Roughness"].default_value = rough
        return m

    def smooth(obj, material):
        obj.data.materials.append(material)
        if obj.type == "MESH":
            for poly in obj.data.polygons:
                poly.use_smooth = True
        return obj

    def ell(name, loc, scale, material):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, location=loc)
        o = bpy.context.object
        o.name = name
        o.scale = scale
        smooth(o, material)
        return o

    def rod(name, a, b, r, material):
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

    def path(name, coords, r, material):
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

    def torus(name, loc, major, minor, material):
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

    def mesh(name, verts, faces, material, bevel=0.06):
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

    def box(name, loc, scale, material, bevel=0.04):
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

    def area(name, loc, power, size):
        bpy.ops.object.light_add(type="AREA", location=loc)
        o = bpy.context.object
        o.name = name
        o.data.energy = power
        o.data.shape = "DISK"
        o.data.size = size
        o.rotation_euler = (
            Vector((0, 0, 2.3)) - o.location
        ).to_track_quat("-Z", "Y").to_euler()
        return o

    def parent_to(child, parent):
        """Parent while keeping the child's current world transform."""
        bpy.context.view_layer.update()
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()

    # --- clear ---
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

    white = mat("Warm ivory plumage", (0.94, 0.92, 0.83))
    cream = mat("Cream feather tips", (0.78, 0.81, 0.73))
    orange = mat("Golden orange bill", (1, 0.42, 0.065))
    pouch = mat("Soft amber throat pouch", (1, 0.62, 0.19))
    black = mat("Graphite rubber", (0.028, 0.043, 0.048))
    teal = mat("Turquoise enamel", (0.015, 0.42, 0.43), 0.45, 0.26)
    chrome = mat("Brushed aluminum", (0.6, 0.73, 0.74), 0.8, 0.23)
    dark = mat("Deep teal leather", (0.035, 0.13, 0.14))
    eye = mat("Obsidian eyes", (0.009, 0.014, 0.016), 0, 0.16)
    yellow = mat("Butter yellow", (0.98, 0.75, 0.25))
    coral = mat("Festival coral", (1, 0.19, 0.15))
    blue = mat("Ocean turquoise", (0.025, 0.55, 0.66), 0.15, 0.28)
    sand = mat("Peach sand", (0.95, 0.71, 0.45))
    wood = mat("Palm trunks", (0.40, 0.21, 0.095))
    leaf = mat("Emerald palm leaves", (0.035, 0.37, 0.19))
    foam = mat("Seafoam", (0.71, 0.95, 0.91))
    gold = mat("Sunshine", (1, 0.64, 0.055))
    purple = mat("Lavender", (0.49, 0.25, 0.75))
    lime = mat("Lime", (0.48, 0.8, 0.16))
    scarf_mat = mat("Coral scarf", (0.86, 0.16, 0.105))
    palette = [coral, yellow, teal, purple, lime, orange]

    ride = bpy.data.objects.new("RideRoot", None)
    bpy.context.collection.objects.link(ride)
    ride.empty_display_type = "PLAIN_AXES"

    rear = (-1.35, 0, 1.02)
    front = (1.4, 0, 1.02)
    bb = (-0.2, 0, 1.05)
    seat = (-0.64, 0, 2.27)
    head = (0.99, 0, 2.3)

    ride_parts: list = []
    wheel_objs: list = []

    for label, c in [("Rear", rear), ("Front", front)]:
        tire = torus(f"{label} tire", c, 0.91, 0.095, black)
        rim = torus(f"{label} silver rim", c, 0.82, 0.035, chrome)
        hub = rod(f"{label} hub", (c[0], -0.15, c[2]), (c[0], 0.15, c[2]), 0.10, chrome)
        wheel_group = bpy.data.objects.new(f"{label}Wheel", None)
        bpy.context.collection.objects.link(wheel_group)
        wheel_group.location = c
        for part in (tire, rim, hub):
            parent_to(part, wheel_group)
            ride_parts.append(part)
        for i in range(28):
            t = i * math.tau / 28
            sp = rod(
                f"{label} spoke",
                (c[0], -0.035 if i % 2 else 0.035, c[2]),
                (c[0] + 0.81 * math.cos(t), 0, c[2] + 0.81 * math.sin(t)),
                0.009,
                chrome,
            )
            parent_to(sp, wheel_group)
            ride_parts.append(sp)
        pts = [
            (c[0] + 1.04 * math.cos(t), 0, c[2] + 1.04 * math.sin(t))
            for t in [math.radians(24 + i * 132 / 18) for i in range(19)]
        ]
        mg = path(f"{label} curved mudguard", pts, 0.055, yellow)
        parent_to(mg, ride)
        ride_parts.append(mg)
        parent_to(wheel_group, ride)
        wheel_objs.append(wheel_group)

    for a, b in [(rear, seat), (seat, bb), (bb, rear), (seat, head), (head, bb)]:
        o = rod("Enamel frame tube", a, b, 0.065, teal)
        parent_to(o, ride)
        ride_parts.append(o)
    for y in [-0.13, 0.13]:
        o = rod("Front fork", (1.01, y, 2.33), (1.4, y, 1.02), 0.043, teal)
        parent_to(o, ride)
        ride_parts.append(o)
    for o in (
        rod("Seat post", seat, (-0.72, 0, 2.54), 0.04, chrome),
        ell("Leather saddle", (-0.72, 0, 2.55), (0.38, 0.22, 0.09), dark),
        rod("Steering stem", head, (0.90, 0, 2.72), 0.045, chrome),
        path(
            "Sweeping handlebar",
            [
                (0.9, -0.46, 2.69),
                (1.02, -0.29, 2.82),
                (0.9, 0, 2.76),
                (1.02, 0.29, 2.82),
                (0.9, 0.46, 2.69),
            ],
            0.037,
            chrome,
        ),
        ell("Bicycle bell", (0.91, -0.28, 2.87), (0.095, 0.095, 0.065), chrome),
        torus("Chainring", (-0.2, -0.15, 1.05), 0.225, 0.026, chrome),
        path(
            "Bicycle chain",
            [
                (-1.35, -0.17, 1.13),
                (-0.22, -0.17, 1.29),
                (0.035, -0.17, 1.07),
                (-0.19, -0.17, 0.82),
                (-1.35, -0.17, 0.91),
                (-1.45, -0.17, 1.02),
                (-1.35, -0.17, 1.13),
            ],
            0.017,
            dark,
        ),
    ):
        parent_to(o, ride)
        ride_parts.append(o)
    for y in [-1, 1]:
        o = rod("Hand grips", (0.90, y * 0.37, 2.7), (0.87, y * 0.57, 2.65), 0.058, dark)
        parent_to(o, ride)
        ride_parts.append(o)

    crank_parts = []
    for y, dx, dz in [(-0.23, 0.23, -0.20), (0.23, -0.23, 0.20)]:
        c1 = rod("Crank", (-0.2, y, 1.05), (-0.2 + dx, y, 1.05 + dz), 0.034, chrome)
        c2 = rod(
            "Pedal",
            (-0.2 + dx, y - 0.12, 1.05 + dz),
            (-0.2 + dx, y + 0.12, 1.05 + dz),
            0.055,
            dark,
        )
        for o in (c1, c2):
            parent_to(o, ride)
            crank_parts.append(o)
            ride_parts.append(o)

    body = ell("Plump pelican body", (-0.62, 0, 3.04), (0.79, 0.5, 0.88), white)
    body.rotation_euler[1] = -0.25
    parent_to(body, ride)
    ride_parts.append(body)
    for y in [-0.17, 0, 0.17]:
        o = ell("Tail plume", (-1.27, y, 2.92), (0.56, 0.13, 0.17), cream if y else white)
        o.rotation_euler[1] = -0.38
        parent_to(o, ride)
        ride_parts.append(o)
    for o in (
        path(
            "Curving pelican neck",
            [(-0.42, 0, 3.24), (-0.02, 0, 3.58), (0.02, 0, 4.02), (0.20, 0, 4.45)],
            0.27,
            white,
        ),
        ell("Head", (0.30, 0, 4.49), (0.46, 0.34, 0.42), white),
    ):
        parent_to(o, ride)
        ride_parts.append(o)
    for y in [-1, 1]:
        for o in (
            ell("Golden eye surround", (0.44, y * 0.301, 4.56), (0.127, 0.042, 0.135), pouch),
            ell("Bright black eye", (0.46, y * 0.337, 4.58), (0.069, 0.032, 0.08), eye),
            ell("Eye glint", (0.48, y * 0.362, 4.605), (0.021, 0.013, 0.023), white),
        ):
            parent_to(o, ride)
            ride_parts.append(o)

    # Sculpted pouch + tapered bill (pelican_final)
    verts, faces = [], []
    nr, ns = 28, 40
    for i in range(nr + 1):
        t = i / nr
        x = 0.55 + 1.45 * t
        width = 0.235 * (1 - t) ** 0.65 + 0.008
        depth = 0.40 * math.sin(math.pi * t) ** 0.75 + 0.015
        for j in range(ns):
            a = math.tau * j / ns
            verts.append(
                (
                    x,
                    width * math.cos(a),
                    4.32 - 0.055 * t - depth * (math.sin(a) + 1) / 2,
                )
            )
    for i in range(nr):
        for j in range(ns):
            a = i * ns + j
            b = i * ns + (j + 1) % ns
            faces.append((a, b, b + ns, a + ns))
    o = mesh("Sculpted soft pelican pouch", verts, faces, pouch, 0)
    parent_to(o, ride)
    ride_parts.append(o)

    verts, faces = [], []
    for i in range(31):
        t = i / 30
        for j in range(24):
            a = j * math.tau / 24
            verts.append(
                (
                    0.55 + 1.45 * t,
                    (0.235 * (1 - t) ** 0.7 + 0.003) * math.cos(a),
                    4.345 - 0.06 * t + 0.05 * (1 - t) * math.sin(a),
                )
            )
    for i in range(30):
        for j in range(24):
            a = i * 24 + j
            b = i * 24 + (j + 1) % 24
            faces.append((a, b, b + 24, a + 24))
    o = mesh("Elegant tapered upper bill", verts, faces, orange, 0)
    parent_to(o, ride)
    ride_parts.append(o)
    o = path(
        "Fine beak seam",
        [(0.59, -0.236, 4.32), (1.1, -0.19, 4.30), (1.64, -0.105, 4.28), (1.98, 0, 4.285)],
        0.009,
        wood,
    )
    parent_to(o, ride)
    ride_parts.append(o)
    for yy in [-1, 1]:
        for o in (
            ell("Small bill nostril", (0.78, yy * 0.195, 4.347), (0.027, 0.008, 0.011), wood),
            path(
                "Expressive brow",
                [(0.31, yy * 0.31, 4.72), (0.44, yy * 0.34, 4.76), (0.55, yy * 0.30, 4.72)],
                0.023,
                white,
            ),
            ell("Wingtip holding handlebar", (0.84, yy * 0.49, 2.73), (0.15, 0.12, 0.13), white),
        ):
            parent_to(o, ride)
            ride_parts.append(o)

    for y in [1, -1]:
        o = path(
            "Wing reaching handlebars",
            [(-0.78, y * 0.38, 3.40), (-0.32, y * 0.49, 3.25), (0.17, y * 0.51, 2.96), (0.87, y * 0.49, 2.70)],
            0.145,
            white,
        )
        parent_to(o, ride)
        ride_parts.append(o)
        for i in range(3):
            o = path(
                "Layered wing feather",
                [
                    (-0.92, y * (0.42 + i * 0.035), 3.42 - i * 0.12),
                    (-0.48, y * (0.54 + i * 0.027), 3.15 - i * 0.09),
                    (0.12 + i * 0.09, y * 0.53, 2.94 - i * 0.025),
                ],
                0.066,
                cream if i == 2 else white,
            )
            parent_to(o, ride)
            ride_parts.append(o)

    for y, foot in [(-0.29, (0.03, -0.31, 0.91)), (0.25, (-0.43, 0.27, 1.31))]:
        o = path(
            "Bent orange leg",
            [(-0.67, y, 2.52), (-0.04, y, 1.91), (-0.38, y, 1.57), foot],
            0.072,
            orange,
        )
        parent_to(o, ride)
        ride_parts.append(o)
        x, fy, z = foot
        o = mesh(
            "Webbed pedal foot",
            [
                (x - 0.13, fy - 0.09, z + 0.03),
                (x - 0.13, fy + 0.09, z + 0.03),
                (x + 0.29, fy + 0.16, z - 0.02),
                (x + 0.36, fy, z - 0.035),
                (x + 0.29, fy - 0.16, z - 0.02),
            ],
            [(0, 1, 2, 3, 4)],
            orange,
            0.045,
        )
        mod = o.modifiers.new("Webbed foot thickness", "SOLIDIFY")
        mod.thickness = 0.045
        parent_to(o, ride)
        ride_parts.append(o)

    for i in range(7):
        o = ell(
            "Delicate breast plumage",
            (-0.02 - i * 0.055, -0.37, 3.05 - i * 0.075),
            (0.085, 0.055, 0.15),
            white,
        )
        o.rotation_euler[1] = -0.35
        parent_to(o, ride)
        ride_parts.append(o)

    # Scarf + boater hat
    for o in (
        ell("Scarf knot", (-0.14, -0.26, 3.9), (0.13, 0.11, 0.13), scarf_mat),
        path(
            "Scarf collar",
            [
                (-0.19, -0.23, 3.88),
                (0.10, -0.22, 3.87),
                (0.24, 0, 3.9),
                (0.10, 0.24, 3.91),
                (-0.2, 0.2, 3.91),
            ],
            0.07,
            scarf_mat,
        ),
    ):
        parent_to(o, ride)
        ride_parts.append(o)
    scarf_flutter = mesh(
        "Scarf flutter",
        [
            (-0.18, 0.09, 3.92),
            (-0.62, 0.11, 4.03),
            (-1.16, 0.10, 3.93),
            (-1.52, 0.10, 4.08),
            (-1.39, 0.10, 3.79),
            (-0.99, 0.10, 3.73),
            (-0.53, 0.10, 3.84),
            (-0.18, 0.09, 3.80),
        ],
        [(0, 1, 2, 3, 4, 5, 6, 7)],
        scarf_mat,
        0.045,
    )
    parent_to(scarf_flutter, ride)
    ride_parts.append(scarf_flutter)

    for o in (
        ell("Boater hat brim", (0.23, 0, 4.89), (0.56, 0.43, 0.075), yellow),
        rod("Boater crown", (0.23, 0, 4.91), (0.23, 0, 5.12), 0.29, yellow),
        rod("Coral hat band", (0.23, 0, 4.93), (0.23, 0, 5.00), 0.297, coral),
    ):
        parent_to(o, ride)
        ride_parts.append(o)

    # Balloons trailing the saddle
    for i, (x, y, z) in enumerate([(-2.25, 0.5, 4.2), (-2.95, 0.6, 4.8), (-3.35, 0.4, 3.9)]):
        for o in (
            ell("Party balloon", (x, y, z), (0.32, 0.28, 0.43), palette[i]),
            ell("Balloon knot", (x, y, z - 0.43), (0.055, 0.04, 0.065), palette[i]),
            path(
                "Balloon ribbon",
                [(-0.9, 0.12, 2.55), (x + 0.4, y, 3.2), (x, y, z - 0.44)],
                0.012,
                white,
            ),
        ):
            parent_to(o, ride)
            ride_parts.append(o)

    # Basket of flowers + bike accents
    wicker = mat("Honey wicker", (0.55, 0.29, 0.105), 0, 0.6)
    cx, cy, cz = 1.64, 0.13, 2.43
    for k in range(10):
        z = cz - 0.32 + k * 0.055
        rx = 0.28 + k * 0.008
        ry = 0.23 + k * 0.006
        o = path(
            "Woven basket horizontal",
            [
                (cx + rx * math.cos(j * math.tau / 40), cy + ry * math.sin(j * math.tau / 40), z)
                for j in range(41)
            ],
            0.018,
            wicker,
        )
        parent_to(o, ride)
        ride_parts.append(o)
    for j in range(24):
        a = j * math.tau / 24
        o = rod(
            "Woven basket upright",
            (cx + 0.28 * math.cos(a), cy + 0.23 * math.sin(a), cz - 0.33),
            (cx + 0.36 * math.cos(a), cy + 0.29 * math.sin(a), cz + 0.19),
            0.013,
            wicker,
        )
        parent_to(o, ride)
        ride_parts.append(o)
    for i in range(7):
        fx = cx + random.uniform(-0.25, 0.25)
        fy = cy + random.uniform(-0.2, 0.2)
        fz = cz + random.uniform(0.35, 0.65)
        o = rod("Flower stalk", (cx, cy, cz - 0.05), (fx, fy, fz), 0.012, leaf)
        parent_to(o, ride)
        ride_parts.append(o)
        o = ell("Daisy center", (fx, fy - 0.025, fz), (0.06, 0.032, 0.06), gold)
        parent_to(o, ride)
        ride_parts.append(o)
        for j in range(8):
            a = j * math.tau / 8
            o = ell(
                "Daisy petal",
                (fx + 0.10 * math.cos(a), fy, fz + 0.10 * math.sin(a)),
                (0.075, 0.025, 0.035),
                white if i % 2 else coral,
            )
            o.rotation_euler[1] = -a
            parent_to(o, ride)
            ride_parts.append(o)

    for o in (
        ell("Brass bicycle headlamp", (1.37, -0.12, 2.15), (0.14, 0.13, 0.13), gold),
        ell("Headlamp glass", (1.47, -0.16, 2.15), (0.08, 0.10, 0.10), white),
        ell("Rear ruby reflector", (-1.86, -0.05, 1.91), (0.07, 0.08, 0.1), coral),
    ):
        parent_to(o, ride)
        ride_parts.append(o)

    # --- Environment (coastal parade) ---
    planks = mat("Sunlit peach timber", (0.68, 0.37, 0.23), 0, 0.7)
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

    for i in range(60):
        y = -12 + i * 0.26
        box("Individual boardwalk plank", (0, y, -0.045), (32, 0.25, 0.08), planks, 0.01)
    box("Beach sand", (0, 7, -0.04), (120, 9, 0.08), sand, 0.0)
    box("Ocean surface", (0, 55, -0.018), (180, 90, 0.02), blue, 0)

    # Camera first so sky can face it
    bpy.ops.object.camera_add(location=(5.6, -21, 6.3))
    cam = bpy.context.object
    cam.name = "ParadeCamera"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 9.4
    target = Vector((0, 0.6, 2.7))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    forward = Vector((-cam.location.x, -cam.location.y, 0)).normalized()
    right = Vector((forward.y, -forward.x, 0))
    center = forward * 20
    sky_mat = bpy.data.materials.new("Sunset sky gradient")
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
    mesh(
        "Horizon sky",
        [
            tuple(center - right * 80),
            tuple(center + right * 80),
            tuple(center + right * 80 + Vector((0, 0, 16))),
            tuple(center - right * 80 + Vector((0, 0, 16))),
        ],
        [(0, 1, 2, 3)],
        sky_mat,
        0,
    )

    sunmat = mat("Glowing apricot sun", (1, 0.63, 0.20))
    sp = sunmat.node_tree.nodes.get("Principled BSDF")
    if "Emission Color" in sp.inputs:
        sp.inputs["Emission Color"].default_value = (1, 0.44, 0.12, 1)
        sp.inputs["Emission Strength"].default_value = 1.5
    ell(
        "Low golden sun",
        tuple(center - forward * 0.15 + right * 2.65 + Vector((0, 0, 2.2))),
        (0.76, 0.76, 0.76),
        sunmat,
    )

    for i in range(8):
        y = 11.7 + i * 0.8
        path(
            "Rolling wave crest",
            [
                (x, y + 0.12 * math.sin(x * 1.7 + i), 0.03 + 0.03 * math.sin(x * 0.8 + i) ** 2)
                for x in [k * 0.2 for k in range(-75, 76)]
            ],
            0.028 if i < 3 else 0.013,
            foam,
        )
    for i in range(3):
        y = 11.5 + i * 0.18
        path(
            "Lapping shore foam",
            [(x, y + 0.18 * math.sin(x * 0.9), 0.021) for x in [k * 0.22 for k in range(-65, 66)]],
            0.018,
            white,
        )

    cloudmat = mat("Luminous peach clouds", (0.98, 0.82, 0.66))
    cp = cloudmat.node_tree.nodes.get("Principled BSDF")
    if "Emission Color" in cp.inputs:
        cp.inputs["Emission Color"].default_value = (1, 0.79, 0.62, 1)
        cp.inputs["Emission Strength"].default_value = 0.45
    for x, y, z in [(-4, 18, 2.7), (-1.5, 18, 3.4), (4.9, 18, 2.6)]:
        for j in range(5):
            ell(
                "Soft sunset cloud",
                (x + j * 0.28, y, z + 0.12 * math.sin(j)),
                (0.39, 0.15, 0.14 + 0.08 * math.sin(j)),
                cloudmat,
            )

    boatpos = (-2.1 + 2.7, 13.5, 0.09)
    ell("Distant sailboat hull", boatpos, (0.44, 0.12, 0.09), white)
    rod("Sailboat mast", (boatpos[0], 13.5, 0.1), (boatpos[0], 13.5, 1.03), 0.012, wood)
    mesh(
        "Little coral sail",
        [
            (boatpos[0] + 0.01, 13.5, 0.97),
            (boatpos[0] + 0.01, 13.5, 0.23),
            (boatpos[0] + 0.47, 13.5, 0.23),
        ],
        [(0, 1, 2)],
        coral,
        0.01,
    )
    mesh(
        "Little cream sail",
        [
            (boatpos[0] - 0.04, 13.5, 0.87),
            (boatpos[0] - 0.04, 13.5, 0.23),
            (boatpos[0] - 0.40, 13.5, 0.23),
        ],
        [(0, 1, 2)],
        white,
        0.01,
    )

    for x, y, col in [(-3.7, 5.2, coral), (-5.1, 5.5, teal)]:
        box("Painted beach cabin", (x, y, 0.66), (1.13, 1.1, 1.32), col)
        for dx in [-0.48, -0.32, -0.16, 0, 0.16, 0.32, 0.48]:
            box("Cabin wood batten", (x + dx, y - 0.558, 0.7), (0.023, 0.025, 1.2), white, 0.005)
        box("Cabin door", (x + 0.17, y - 0.59, 0.55), (0.4, 0.05, 1.05), white)
        box("Door inset", (x + 0.17, y - 0.624, 0.62), (0.29, 0.025, 0.72), col)
        mesh(
            "Cabin roof",
            [
                (x - 0.7, y - 0.66, 1.32),
                (x + 0.7, y - 0.66, 1.32),
                (x, y - 0.66, 1.91),
                (x - 0.7, y + 0.66, 1.32),
                (x + 0.7, y + 0.66, 1.32),
                (x, y + 0.66, 1.91),
            ],
            [(0, 2, 5, 3), (2, 1, 4, 5), (0, 1, 2)],
            dark,
            0.035,
        )
        box("Cabin step", (x, y - 0.75, 0.07), (1.13, 0.38, 0.14), white)

    for x, y, h in [(-4.6, 3.7, 4.5), (4.5, 5, 4.9)]:
        top = Vector((x - 0.3, y, h))
        path("Leaning palm", [(x, y, 0), (x + 0.08, y, h * 0.45), tuple(top)], 0.10, wood)
        for j in range(8):
            a = j * math.tau / 8
            d = Vector((math.cos(a), math.sin(a), 0))
            side = Vector((-math.sin(a), math.cos(a), 0))

            def palmpt(t, top=top, d=d):
                return top + d * 1.6 * t + Vector(
                    (0, 0, 0.45 * math.sin(math.pi * t) - 0.48 * t)
                )

            path("Frond rib", [tuple(palmpt(k / 10)) for k in range(11)], 0.013, leaf)
            for k in range(1, 10):
                t = k / 10
                v = palmpt(t)
                w = 0.26 * math.sin(math.pi * t) ** 0.6
                for sign in [-1, 1]:
                    mesh(
                        "Palm leaflet",
                        [
                            tuple(v - d * 0.07),
                            tuple(v + side * w * sign - d * 0.13 + Vector((0, 0, -0.08))),
                            tuple(v + d * 0.12),
                        ],
                        [(0, 1, 2)],
                        leaf,
                        0.007,
                    )

    path(
        "High festival string",
        [(-5, 2, 6.15), (-2.5, 2, 5.65), (0, 2, 5.58), (2.5, 2, 5.78), (5, 2, 6.2)],
        0.012,
        wood,
    )
    for i in range(20):
        x = -4.75 + i * 0.5
        z = 5.58 + 0.62 * (x / 5) ** 2
        mesh(
            "Fabric festival flag",
            [(x - 0.17, 2, z), (x + 0.17, 2, z), (x + 0.05, 1.95, z - 0.31)],
            [(0, 1, 2)],
            palette[i % 6],
            0.008,
        )

    for i in range(30):
        x = random.uniform(-4, 4)
        y = random.uniform(-2.5, 2.3)
        o = box(
            "Celebration confetti",
            (x, y, 0.012),
            (0.055, 0.10, 0.012),
            random.choice(palette),
            0.005,
        )
        o.rotation_euler[2] = random.random() * 6
    for x, y in [(3, 3.5), (2.7, 4), (-2.5, 3.7)]:
        for i in range(6):
            a = i * 0.23
            o = ell(
                "Scalloped seashell",
                (x + 0.06 * math.cos(a), y + 0.06 * math.sin(a), 0.035),
                (0.12, 0.035, 0.025),
                white,
            )
            o.rotation_euler[2] = a

    # Lighting
    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)
    a1 = area("Warm setting sun", (-5, -3, 8), 850, 4)
    a1.data.color = (1, 0.77, 0.53)
    a2 = area("Cool sky fill", (4, -5, 7), 450, 5)
    a2.data.color = (0.65, 0.84, 1)
    a3 = area("Golden rim", (-2, 6, 6), 1000, 3)
    a3.data.color = (1, 0.59, 0.29)

    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
    bg.inputs[0].default_value = (0.39, 0.60, 0.74, 1)
    bg.inputs[1].default_value = 0.24

    try:
        bpy.context.scene.view_settings.view_transform = "AgX"
    except Exception:
        pass

    # --- Animation: ride along +X, wheels roll, body bob, scarf flutter ---
    distance = x_end - x_start
    rotations = distance / (2 * math.pi * wheel_radius) if wheel_radius else 1.0

    ride.location = (x_start, 0.0, 0.0)
    ride.keyframe_insert(data_path="location", frame=1)
    ride.location = (x_end, 0.0, 0.0)
    ride.keyframe_insert(data_path="location", frame=frame_end)

    for w in wheel_objs:
        w.rotation_euler = (0.0, 0.0, 0.0)
        w.keyframe_insert(data_path="rotation_euler", frame=1)
        w.rotation_euler = (0.0, -rotations * math.tau, 0.0)
        w.keyframe_insert(data_path="rotation_euler", frame=frame_end)

    # Subtle pelican bounce (body Z)
    body_z0 = body.location.z
    steps = max(8, frame_end // 4)
    for step in range(steps + 1):
        fr = 1 + int(step * (frame_end - 1) / steps)
        t = (fr - 1) / max(1, frame_end - 1)
        bob = 0.045 * math.sin(t * math.pi * 6.0)
        body.location.z = body_z0 + bob
        body.keyframe_insert(data_path="location", frame=fr)

    # Scarf flutter sway
    scarf_z0 = scarf_flutter.rotation_euler[2]
    for step in range(steps + 1):
        fr = 1 + int(step * (frame_end - 1) / steps)
        t = (fr - 1) / max(1, frame_end - 1)
        scarf_flutter.rotation_euler[2] = scarf_z0 + 0.18 * math.sin(t * math.pi * 8.0)
        scarf_flutter.keyframe_insert(data_path="rotation_euler", frame=fr)

    # Balloon gentle sway
    for o in bpy.data.objects:
        if o.name.startswith("Party balloon"):
            z0 = o.location.z
            for step in range(0, steps + 1, 2):
                fr = 1 + int(step * (frame_end - 1) / steps)
                t = (fr - 1) / max(1, frame_end - 1)
                o.location.z = z0 + 0.08 * math.sin(t * math.pi * 5.0 + hash(o.name) % 7)
                o.keyframe_insert(data_path="location", frame=fr)

    # Camera slight follow on X so the rider stays framed
    cam_x0 = cam.location.x
    cam.location.x = cam_x0 + x_start * 0.35
    cam.keyframe_insert(data_path="location", frame=1)
    look = Vector((x_start, 0.6, 2.7))
    cam.rotation_euler = (look - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.keyframe_insert(data_path="rotation_euler", frame=1)
    cam.location.x = cam_x0 + x_end * 0.35
    cam.keyframe_insert(data_path="location", frame=frame_end)
    look = Vector((x_end, 0.6, 2.7))
    cam.rotation_euler = (look - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.keyframe_insert(data_path="rotation_euler", frame=frame_end)

    # Linear interpolation for travel; soft handles for bob
    for obj in (ride, *wheel_objs, cam):
        if obj.animation_data and obj.animation_data.action:
            for fcurve in obj.animation_data.action.fcurves:
                for kp in fcurve.keyframe_points:
                    kp.interpolation = "LINEAR"

    scn = bpy.context.scene
    scn.frame_start = 1
    scn.frame_end = frame_end
    print(f"Pelican coastal parade built: {frame_end} frames @ {fps} fps")
