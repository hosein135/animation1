"""Declarative Blender scene builder — loads materials, objects JSON, and actions."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import actions
import blender_prims as P
import factories


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_materials(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "materials.json"
    if not path.is_file():
        return {}
    raw = load_json(path)
    out: dict[str, Any] = {}
    for name, spec in raw.items():
        color = spec.get("color", [0.8, 0.8, 0.8])
        m = P.mat(name, color, float(spec.get("metallic", 0.0)), float(spec.get("roughness", 0.4)))
        if "emission" in spec:
            em = spec["emission"]
            P.apply_emission(m, em.get("color", color), float(em.get("strength", 1.0)))
        out[name] = m
    return out


def _register(ctx: dict, obj) -> Any:
    if obj is None:
        return None
    ctx["objects"][obj.name] = obj
    return obj


def spawn_node(
    node: dict,
    materials: dict,
    ctx: dict,
    data_dir: Path,
    parent=None,
) -> Any:
    """Spawn a single object node (inline or from file). Returns root object."""
    if "ref" in node:
        ref_path = data_dir / "objects" / f"{node['ref']}.json"
        if not ref_path.is_file():
            raise FileNotFoundError(f"Object ref not found: {ref_path}")
        loaded = load_json(ref_path)
        # Allow overrides from the placement node.
        merged = {**loaded, **{k: v for k, v in node.items() if k != "ref"}}
        # Keep children from file unless placement supplies its own.
        if "children" not in node and "children" in loaded:
            merged["children"] = loaded["children"]
        return spawn_node(merged, materials, ctx, data_dir, parent)

    ntype = node.get("type", "group")
    name = node.get("name", ntype)
    loc = P.as_tuple3(node["location"]) if "location" in node else (0.0, 0.0, 0.0)

    obj = None

    if ntype in ("group", "empty"):
        obj = P.empty(name, loc, node.get("display", "PLAIN_AXES"))
    elif ntype == "sphere":
        mat = P.resolve_material(materials, node.get("material"))
        scale = node.get("scale", [1, 1, 1])
        obj = P.ell(name, loc, scale, mat)
    elif ntype == "box":
        mat = P.resolve_material(materials, node.get("material"))
        scale = node.get("scale", [1, 1, 1])
        obj = P.box(name, loc, scale, mat, float(node.get("bevel", 0.04)))
    elif ntype in ("cylinder", "rod"):
        mat = P.resolve_material(materials, node.get("material"))
        obj = P.rod(name, node["from"], node["to"], float(node["radius"]), mat)
    elif ntype == "torus":
        mat = P.resolve_material(materials, node.get("material"))
        obj = P.torus(
            name,
            loc,
            float(node["major"]),
            float(node["minor"]),
            mat,
        )
    elif ntype == "path":
        mat = P.resolve_material(materials, node.get("material"))
        obj = P.path(name, [tuple(c) for c in node["points"]], float(node["radius"]), mat)
    elif ntype == "mesh":
        mat = P.resolve_material(materials, node.get("material"))
        obj = P.mesh(
            name,
            [tuple(v) for v in node["verts"]],
            [tuple(f) for f in node["faces"]],
            mat,
            float(node.get("bevel", 0.06)),
        )
        if "solidify" in node:
            P.solidify(obj, float(node["solidify"]))
    elif ntype == "camera":
        cam_cfg = node
        obj = P.make_camera(
            name,
            cam_cfg.get("location", loc),
            cam_cfg.get("look_at", [0, 0, 0]),
            lens=float(cam_cfg.get("lens", 50)),
            ortho_scale=cam_cfg.get("ortho_scale"),
            camera_type=str(cam_cfg.get("camera_type", "ORTHO")),
        )
    elif ntype == "light":
        color = node.get("color")
        obj = P.area(
            name,
            loc,
            float(node.get("energy", 500)),
            float(node.get("size", 4)),
            color=color,
            aim_at=tuple(node.get("aim_at", [0, 0, 2.3])),
        )
    elif ntype == "factory":
        fname = node.get("factory") or node.get("name")
        params = dict(node.get("params") or {})
        params.setdefault("name", name)
        obj = factories.run_factory(fname, params, materials, parent)
        if obj is not None and name and obj.name != name:
            # Prefer explicit name when unique.
            try:
                obj.name = name
            except Exception:
                pass
        _register(ctx, obj)
        # Factories may create many children already parented; still spawn nested children.
        for child in node.get("children", []):
            spawn_node(child, materials, ctx, data_dir, parent=obj if obj is not None else parent)
        return obj
    elif ntype == "instance":
        # Alias for ref-style nesting without top-level ref key.
        return spawn_node(
            {"ref": node["object"], **{k: v for k, v in node.items() if k not in ("type", "object")}},
            materials,
            ctx,
            data_dir,
            parent,
        )
    else:
        raise ValueError(f"Unknown object type: {ntype}")

    if obj is None:
        return None

    if "rotation" in node:
        P.set_rotation(obj, node["rotation"])
    if "scale" in node and ntype not in ("sphere", "box"):
        P.set_scale(obj, node["scale"])

    if parent is not None and ntype not in ("factory",):
        P.parent_to(obj, parent)
    elif node.get("parent"):
        # Deferred parent by name — resolve after full spawn if needed.
        pname = node["parent"]
        if pname in ctx["objects"]:
            P.parent_to(obj, ctx["objects"][pname])
        else:
            ctx.setdefault("pending_parents", []).append((obj, pname))

    _register(ctx, obj)

    for child in node.get("children", []):
        spawn_node(child, materials, ctx, data_dir, parent=obj)

    return obj


def apply_pending_parents(ctx: dict) -> None:
    for obj, pname in ctx.pop("pending_parents", []):
        if pname not in ctx["objects"]:
            raise KeyError(f"Parent not found: {pname}")
        P.parent_to(obj, ctx["objects"][pname])


def apply_scene_camera(scene_cfg: dict, ctx: dict) -> None:
    cam = scene_cfg.get("camera")
    if not cam:
        return
    # Skip if a camera object was already spawned.
    for o in ctx["objects"].values():
        if getattr(o, "type", None) == "CAMERA":
            return
    obj = P.make_camera(
        cam.get("name", "Camera"),
        cam["location"],
        cam["look_at"],
        lens=float(cam.get("lens", 50)),
        ortho_scale=cam.get("ortho_scale"),
        camera_type=str(cam.get("type", "ORTHO" if cam.get("ortho_scale") else "PERSP")),
    )
    _register(ctx, obj)


def apply_scene_lights(scene_cfg: dict, materials: dict, ctx: dict, data_dir: Path) -> None:
    lights = scene_cfg.get("lights")
    if not lights:
        # Fallback: lighting energy hints only — skip if no light definitions.
        return
    import bpy

    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)
    for light in lights:
        spawn_node(light, materials, ctx, data_dir, parent=None)


def build_animation(data_dir: Path, scene_cfg: dict | None = None) -> dict:
    """Build the Blender scene from data_dir/scene.json (+ objects + materials)."""
    data_dir = Path(data_dir)
    if scene_cfg is None:
        scene_cfg = load_json(data_dir / "scene.json")

    random.seed(int(scene_cfg.get("seed", 14)))

    fps = int(scene_cfg["fps"])
    duration = float(scene_cfg["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))

    P.clear_scene()
    materials = load_materials(data_dir)

    ctx: dict[str, Any] = {
        "objects": {},
        "materials": materials,
        "fps": fps,
        "frame_end": frame_end,
        "frame_start": 1,
        "params": scene_cfg.get("params", {}),
        "pending_parents": [],
    }

    # Camera first so factories like horizon_sky can face it.
    apply_scene_camera(scene_cfg, ctx)

    for node in scene_cfg.get("objects", []):
        spawn_node(node, materials, ctx, data_dir, parent=None)

    apply_pending_parents(ctx)

    if scene_cfg.get("lights"):
        apply_scene_lights(scene_cfg, materials, ctx, data_dir)

    bg = scene_cfg.get("background_color", [0.39, 0.60, 0.74, 1.0])
    P.set_world_bg(bg, float(scene_cfg.get("background_strength", 0.24)))

    try:
        import bpy

        bpy.context.scene.view_settings.view_transform = "AgX"
    except Exception:
        pass

    # Re-index all scene objects by name so actions can find factory children.
    import bpy

    for o in bpy.data.objects:
        ctx["objects"][o.name] = o

    scn = bpy.context.scene
    scn.frame_start = 1
    scn.frame_end = frame_end
    actions.apply_actions(scene_cfg.get("actions", []), ctx)
    title = scene_cfg.get("title", "Animation")
    print(f"Scene built ({title}): {frame_end} frames @ {fps} fps, {len(ctx['objects'])} objects")
    return scene_cfg
