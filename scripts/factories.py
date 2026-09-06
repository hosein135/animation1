"""Procedural object factories for geometry that is awkward as static JSON."""

from __future__ import annotations

import math
import random
from typing import Any, Callable

import blender_prims as P


FactoryFn = Callable[[dict, dict, Any | None], Any]


def _mat(materials: dict, name: str):
    if name not in materials:
        raise KeyError(f"Factory material not found: {name}")
    return materials[name]


def _parent(obj, parent) -> Any:
    if parent is not None:
        P.parent_to(obj, parent)
    return obj


def bicycle_wheel(params: dict, materials: dict, parent=None):
    """Tire, rim, hub, spokes, mudguard; returns the spinning wheel group."""
    label = params.get("label", "Wheel")
    c = tuple(params["center"])
    major = float(params.get("tire_major", 0.91))
    minor = float(params.get("tire_minor", 0.095))
    rim_major = float(params.get("rim_major", 0.82))
    spoke_count = int(params.get("spokes", 28))
    spoke_len = float(params.get("spoke_length", 0.81))

    tire = P.torus(f"{label} tire", c, major, minor, _mat(materials, "Graphite rubber"))
    rim = P.torus(
        f"{label} silver rim", c, rim_major, float(params.get("rim_minor", 0.035)),
        _mat(materials, "Brushed aluminum"),
    )
    hub = P.rod(
        f"{label} hub",
        (c[0], -0.15, c[2]),
        (c[0], 0.15, c[2]),
        0.10,
        _mat(materials, "Brushed aluminum"),
    )
    wheel_group = P.empty(f"{label}Wheel", c)
    for part in (tire, rim, hub):
        P.parent_to(part, wheel_group)

    chrome = _mat(materials, "Brushed aluminum")
    for i in range(spoke_count):
        t = i * math.tau / spoke_count
        sp = P.rod(
            f"{label} spoke",
            (c[0], -0.035 if i % 2 else 0.035, c[2]),
            (c[0] + spoke_len * math.cos(t), 0, c[2] + spoke_len * math.sin(t)),
            0.009,
            chrome,
        )
        P.parent_to(sp, wheel_group)

    # Keep mudguard clear of the tire (outer ~ major+minor); sit just above it.
    mg_r = float(params.get("mudguard_radius", major + minor + 0.06))
    pts = [
        (c[0] + mg_r * math.cos(t), 0, c[2] + mg_r * math.sin(t))
        for t in [math.radians(24 + i * 132 / 18) for i in range(19)]
    ]
    mg = P.path(f"{label} curved mudguard", pts, 0.055, _mat(materials, "Butter yellow"))
    if parent is not None:
        P.parent_to(mg, parent)
        P.parent_to(wheel_group, parent)
    return wheel_group


def pelican_pouch(params: dict, materials: dict, parent=None):
    verts, faces = [], []
    nr, ns = int(params.get("nr", 28)), int(params.get("ns", 40))
    for i in range(nr + 1):
        t = i / nr
        x = 0.55 + 1.45 * t
        width = 0.235 * (1 - t) ** 0.65 + 0.008
        depth = 0.40 * math.sin(math.pi * t) ** 0.75 + 0.015
        for j in range(ns):
            a = math.tau * j / ns
            verts.append(
                (x, width * math.cos(a), 4.32 - 0.055 * t - depth * (math.sin(a) + 1) / 2)
            )
    for i in range(nr):
        for j in range(ns):
            a = i * ns + j
            b = i * ns + (j + 1) % ns
            faces.append((a, b, b + ns, a + ns))
    o = P.mesh("Sculpted soft pelican pouch", verts, faces, _mat(materials, "Soft amber throat pouch"), 0)
    return _parent(o, parent)


def pelican_bill(params: dict, materials: dict, parent=None):
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
    o = P.mesh("Elegant tapered upper bill", verts, faces, _mat(materials, "Golden orange bill"), 0)
    return _parent(o, parent)


def woven_basket(params: dict, materials: dict, parent=None):
    cx, cy, cz = params.get("center", [1.64, 0.13, 2.43])
    wicker = _mat(materials, "Honey wicker")
    leaf = _mat(materials, "Emerald palm leaves")
    gold = _mat(materials, "Sunshine")
    white = _mat(materials, "Warm ivory plumage")
    coral = _mat(materials, "Festival coral")
    root = P.empty(params.get("name", "FlowerBasket"), (cx, cy, cz))
    if parent is not None:
        P.parent_to(root, parent)

    for k in range(10):
        z = cz - 0.32 + k * 0.055
        rx = 0.28 + k * 0.008
        ry = 0.23 + k * 0.006
        o = P.path(
            "Woven basket horizontal",
            [
                (cx + rx * math.cos(j * math.tau / 40), cy + ry * math.sin(j * math.tau / 40), z)
                for j in range(41)
            ],
            0.018,
            wicker,
        )
        P.parent_to(o, root)
    for j in range(24):
        a = j * math.tau / 24
        o = P.rod(
            "Woven basket upright",
            (cx + 0.28 * math.cos(a), cy + 0.23 * math.sin(a), cz - 0.33),
            (cx + 0.36 * math.cos(a), cy + 0.29 * math.sin(a), cz + 0.19),
            0.013,
            wicker,
        )
        P.parent_to(o, root)

    rng = random.Random(int(params.get("seed", 14)))
    for i in range(7):
        fx = cx + rng.uniform(-0.25, 0.25)
        fy = cy + rng.uniform(-0.2, 0.2)
        fz = cz + rng.uniform(0.35, 0.65)
        o = P.rod("Flower stalk", (cx, cy, cz - 0.05), (fx, fy, fz), 0.012, leaf)
        P.parent_to(o, root)
        o = P.ell("Daisy center", (fx, fy - 0.025, fz), (0.06, 0.032, 0.06), gold)
        P.parent_to(o, root)
        for j in range(8):
            a = j * math.tau / 8
            o = P.ell(
                "Daisy petal",
                (fx + 0.10 * math.cos(a), fy, fz + 0.10 * math.sin(a)),
                (0.075, 0.025, 0.035),
                white if i % 2 else coral,
            )
            o.rotation_euler[1] = -a
            P.parent_to(o, root)
    return root


def boardwalk_planks(params: dict, materials: dict, parent=None):
    count = int(params.get("count", 60))
    if "Sunlit peach timber" not in materials:
        materials["Sunlit peach timber"] = P.wood_plank_material()
    planks = materials["Sunlit peach timber"]
    root = P.empty(params.get("name", "Boardwalk"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    for i in range(count):
        y = -12 + i * 0.26
        o = P.box("Individual boardwalk plank", (0, y, -0.045), (32, 0.25, 0.08), planks, 0.01)
        P.parent_to(o, root)
    return root


def horizon_sky(params: dict, materials: dict, parent=None):
    """Sky plane facing the active camera."""
    import bpy
    from mathutils import Vector

    cam = bpy.context.scene.camera
    if cam is None:
        raise RuntimeError("horizon_sky factory requires an active camera")
    forward = Vector((-cam.location.x, -cam.location.y, 0)).normalized()
    right = Vector((forward.y, -forward.x, 0))
    center = forward * float(params.get("distance", 20))
    if "Sunset sky gradient" not in materials:
        materials["Sunset sky gradient"] = P.sunset_sky_material()
    sky_mat = materials["Sunset sky gradient"]
    o = P.mesh(
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
    # Store sun placement hint on empty for companion factories
    sun_empty = P.empty(
        "SkySunAnchor",
        tuple(center - forward * 0.15 + right * 2.65 + Vector((0, 0, 2.2))),
    )
    if parent is not None:
        P.parent_to(o, parent)
        P.parent_to(sun_empty, parent)
    return o


def golden_sun(params: dict, materials: dict, parent=None):
    import bpy

    anchor = bpy.data.objects.get("SkySunAnchor")
    loc = tuple(params["location"]) if "location" in params else (
        tuple(anchor.location) if anchor else (0, 20, 2.2)
    )
    if "Glowing apricot sun" not in materials:
        materials["Glowing apricot sun"] = P.mat("Glowing apricot sun", (1, 0.63, 0.20))
        P.apply_emission(materials["Glowing apricot sun"], (1, 0.44, 0.12), 1.5)
    o = P.ell("Low golden sun", loc, (0.76, 0.76, 0.76), materials["Glowing apricot sun"])
    return _parent(o, parent)


def rolling_waves(params: dict, materials: dict, parent=None):
    foam = _mat(materials, "Seafoam")
    white = _mat(materials, "Warm ivory plumage")
    root = P.empty(params.get("name", "Waves"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    for i in range(8):
        y = 11.7 + i * 0.8
        o = P.path(
            "Rolling wave crest",
            [
                (x, y + 0.12 * math.sin(x * 1.7 + i), 0.03 + 0.03 * math.sin(x * 0.8 + i) ** 2)
                for x in [k * 0.2 for k in range(-75, 76)]
            ],
            0.028 if i < 3 else 0.013,
            foam,
        )
        P.parent_to(o, root)
    for i in range(3):
        y = 11.5 + i * 0.18
        o = P.path(
            "Lapping shore foam",
            [(x, y + 0.18 * math.sin(x * 0.9), 0.021) for x in [k * 0.22 for k in range(-65, 66)]],
            0.018,
            white,
        )
        P.parent_to(o, root)
    return root


def sunset_clouds(params: dict, materials: dict, parent=None):
    if "Luminous peach clouds" not in materials:
        materials["Luminous peach clouds"] = P.mat("Luminous peach clouds", (0.98, 0.82, 0.66))
        P.apply_emission(materials["Luminous peach clouds"], (1, 0.79, 0.62), 0.45)
    cloudmat = materials["Luminous peach clouds"]
    root = P.empty(params.get("name", "Clouds"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    for x, y, z in params.get("centers", [(-4, 18, 2.7), (-1.5, 18, 3.4), (4.9, 18, 2.6)]):
        for j in range(5):
            o = P.ell(
                "Soft sunset cloud",
                (x + j * 0.28, y, z + 0.12 * math.sin(j)),
                (0.39, 0.15, 0.14 + 0.08 * math.sin(j)),
                cloudmat,
            )
            P.parent_to(o, root)
    return root


def palm_tree(params: dict, materials: dict, parent=None):
    from mathutils import Vector

    x, y, h = params["x"], params["y"], params["height"]
    wood = _mat(materials, "Palm trunks")
    leaf = _mat(materials, "Emerald palm leaves")
    top = Vector((x - 0.3, y, h))
    root = P.empty(params.get("name", "Palm"), (x, y, 0))
    if parent is not None:
        P.parent_to(root, parent)
    trunk = P.path("Leaning palm", [(x, y, 0), (x + 0.08, y, h * 0.45), tuple(top)], 0.10, wood)
    P.parent_to(trunk, root)
    for j in range(8):
        a = j * math.tau / 8
        d = Vector((math.cos(a), math.sin(a), 0))
        side = Vector((-math.sin(a), math.cos(a), 0))

        def palmpt(t, top=top, d=d):
            return top + d * 1.6 * t + Vector((0, 0, 0.45 * math.sin(math.pi * t) - 0.48 * t))

        rib = P.path("Frond rib", [tuple(palmpt(k / 10)) for k in range(11)], 0.013, leaf)
        P.parent_to(rib, root)
        for k in range(1, 10):
            t = k / 10
            v = palmpt(t)
            w = 0.26 * math.sin(math.pi * t) ** 0.6
            for sign in [-1, 1]:
                leaflet = P.mesh(
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
                P.parent_to(leaflet, root)
    return root


def festival_flags(params: dict, materials: dict, parent=None):
    wood = _mat(materials, "Palm trunks")
    palette_names = params.get(
        "palette",
        [
            "Festival coral",
            "Butter yellow",
            "Turquoise enamel",
            "Lavender",
            "Lime",
            "Golden orange bill",
        ],
    )
    palette = [_mat(materials, n) for n in palette_names]
    root = P.empty(params.get("name", "FestivalFlags"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    string = P.path(
        "High festival string",
        [(-5, 2, 6.15), (-2.5, 2, 5.65), (0, 2, 5.58), (2.5, 2, 5.78), (5, 2, 6.2)],
        0.012,
        wood,
    )
    P.parent_to(string, root)
    for i in range(20):
        x = -4.75 + i * 0.5
        z = 5.58 + 0.62 * (x / 5) ** 2
        flag = P.mesh(
            "Fabric festival flag",
            [(x - 0.17, 2, z), (x + 0.17, 2, z), (x + 0.05, 1.95, z - 0.31)],
            [(0, 1, 2)],
            palette[i % 6],
            0.008,
        )
        P.parent_to(flag, root)
    return root


def confetti(params: dict, materials: dict, parent=None):
    palette_names = params.get(
        "palette",
        [
            "Festival coral",
            "Butter yellow",
            "Turquoise enamel",
            "Lavender",
            "Lime",
            "Golden orange bill",
        ],
    )
    palette = [_mat(materials, n) for n in palette_names]
    rng = random.Random(int(params.get("seed", 14)))
    root = P.empty(params.get("name", "Confetti"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    for _ in range(int(params.get("count", 30))):
        x = rng.uniform(-4, 4)
        y = rng.uniform(-2.5, 2.3)
        o = P.box(
            "Celebration confetti",
            (x, y, 0.012),
            (0.055, 0.10, 0.012),
            rng.choice(palette),
            0.005,
        )
        o.rotation_euler[2] = rng.random() * 6
        P.parent_to(o, root)
    return root


def seashells(params: dict, materials: dict, parent=None):
    white = _mat(materials, "Warm ivory plumage")
    root = P.empty(params.get("name", "Seashells"), (0, 0, 0))
    if parent is not None:
        P.parent_to(root, parent)
    for x, y in params.get("centers", [(3, 3.5), (2.7, 4), (-2.5, 3.7)]):
        for i in range(6):
            a = i * 0.23
            o = P.ell(
                "Scalloped seashell",
                (x + 0.06 * math.cos(a), y + 0.06 * math.sin(a), 0.035),
                (0.12, 0.035, 0.025),
                white,
            )
            o.rotation_euler[2] = a
            P.parent_to(o, root)
    return root


def beach_cabin(params: dict, materials: dict, parent=None):
    x, y = params["x"], params["y"]
    col = _mat(materials, params["color"])
    white = _mat(materials, "Warm ivory plumage")
    dark = _mat(materials, "Deep teal leather")
    root = P.empty(params.get("name", "BeachCabin"), (x, y, 0))
    if parent is not None:
        P.parent_to(root, parent)
    body = P.box("Painted beach cabin", (x, y, 0.66), (1.13, 1.1, 1.32), col)
    P.parent_to(body, root)
    for dx in [-0.48, -0.32, -0.16, 0, 0.16, 0.32, 0.48]:
        b = P.box("Cabin wood batten", (x + dx, y - 0.558, 0.7), (0.023, 0.025, 1.2), white, 0.005)
        P.parent_to(b, root)
    door = P.box("Cabin door", (x + 0.17, y - 0.59, 0.55), (0.4, 0.05, 1.05), white)
    P.parent_to(door, root)
    inset = P.box("Door inset", (x + 0.17, y - 0.624, 0.62), (0.29, 0.025, 0.72), col)
    P.parent_to(inset, root)
    roof = P.mesh(
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
    P.parent_to(roof, root)
    step = P.box("Cabin step", (x, y - 0.75, 0.07), (1.13, 0.38, 0.14), white)
    P.parent_to(step, root)
    return root


FACTORY_REGISTRY: dict[str, FactoryFn] = {
    "bicycle_wheel": bicycle_wheel,
    "pelican_pouch": pelican_pouch,
    "pelican_bill": pelican_bill,
    "woven_basket": woven_basket,
    "boardwalk_planks": boardwalk_planks,
    "horizon_sky": horizon_sky,
    "golden_sun": golden_sun,
    "rolling_waves": rolling_waves,
    "sunset_clouds": sunset_clouds,
    "palm_tree": palm_tree,
    "festival_flags": festival_flags,
    "confetti": confetti,
    "seashells": seashells,
    "beach_cabin": beach_cabin,
}


def known_factories() -> set[str]:
    return set(FACTORY_REGISTRY)


def run_factory(name: str, params: dict, materials: dict, parent=None):
    fn = FACTORY_REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"Unknown factory: {name}")
    return fn(params or {}, materials, parent)
