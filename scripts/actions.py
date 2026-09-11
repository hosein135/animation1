"""Named animation actions applied from scene.json timeline entries."""

from __future__ import annotations

import math
from typing import Any, Callable


ActionFn = Callable[[dict, dict], None]


def _obj(ctx: dict, name: str):
    objs = ctx["objects"]
    if name not in objs:
        raise KeyError(f"Action target not found: {name}")
    return objs[name]


def _from_time(t: float, ctx: dict) -> int:
    frame = 1 + int(round(float(t) * float(ctx["fps"])))
    return max(int(ctx.get("frame_start", 1)), min(int(ctx["frame_end"]), frame))


def _key_frame(key: dict, ctx: dict) -> int:
    if "frame" in key:
        return int(key["frame"])
    if "time" in key:
        return _from_time(key["time"], ctx)
    raise ValueError("key requires 'frame' or 'time'")


def _frame_range(cfg: dict, ctx: dict) -> tuple[int, int]:
    start = int(cfg.get("frame_start", ctx.get("frame_start", 1)))
    end = int(cfg.get("frame_end", ctx["frame_end"]))
    if "time_start" in cfg:
        start = _from_time(cfg["time_start"], ctx)
    if "time_end" in cfg:
        end = _from_time(cfg["time_end"], ctx)
    return start, end


def _steps(cfg: dict, frame_start: int, frame_end: int) -> int:
    if "steps" in cfg:
        return max(2, int(cfg["steps"]))
    return max(8, (frame_end - frame_start) // 4)


def translate(cfg: dict, ctx: dict) -> None:
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    frm = cfg.get("from", list(obj.location))
    to = cfg["to"]
    obj.location = frm
    obj.keyframe_insert(data_path="location", frame=start)
    obj.location = to
    obj.keyframe_insert(data_path="location", frame=end)


def rotate(cfg: dict, ctx: dict) -> None:
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    frm = cfg.get("from", list(obj.rotation_euler))
    to = cfg["to"]
    obj.rotation_euler = frm
    obj.keyframe_insert(data_path="rotation_euler", frame=start)
    obj.rotation_euler = to
    obj.keyframe_insert(data_path="rotation_euler", frame=end)


def spin(cfg: dict, ctx: dict) -> None:
    """Spin around an euler axis. rotations may be given directly or via distance/radius."""
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    axis = int(cfg.get("axis", 1))  # default Y
    if "rotations" in cfg:
        turns = float(cfg["rotations"])
    else:
        distance = float(cfg["distance"])
        radius = float(cfg["radius"])
        turns = distance / (2 * math.pi * radius) if radius else 1.0
    sign = -1.0 if cfg.get("invert", True) else 1.0
    angles = [0.0, 0.0, 0.0]
    obj.rotation_euler = tuple(angles)
    obj.keyframe_insert(data_path="rotation_euler", frame=start)
    angles[axis] = sign * turns * math.tau
    obj.rotation_euler = tuple(angles)
    obj.keyframe_insert(data_path="rotation_euler", frame=end)


def bob(cfg: dict, ctx: dict) -> None:
    """Sinusoidal offset on a location axis."""
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    axis = int(cfg.get("axis", 2))  # default Z
    amplitude = float(cfg.get("amplitude", 0.045))
    cycles = float(cfg.get("cycles", 6.0))
    phase = float(cfg.get("phase", 0.0))
    base = list(obj.location)
    if "base" in cfg:
        base = list(cfg["base"])
    steps = _steps(cfg, start, end)
    for step in range(steps + 1):
        fr = start + int(step * (end - start) / steps)
        t = (fr - start) / max(1, end - start)
        loc = list(base)
        loc[axis] = base[axis] + amplitude * math.sin(t * math.pi * cycles + phase)
        obj.location = loc
        obj.keyframe_insert(data_path="location", frame=fr)


def flutter(cfg: dict, ctx: dict) -> None:
    """Sinusoidal euler rotation sway."""
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    axis = int(cfg.get("axis", 2))
    amplitude = float(cfg.get("amplitude", 0.18))
    cycles = float(cfg.get("cycles", 8.0))
    phase = float(cfg.get("phase", 0.0))
    base = list(obj.rotation_euler)
    if "base" in cfg:
        base = list(cfg["base"])
    steps = _steps(cfg, start, end)
    for step in range(steps + 1):
        fr = start + int(step * (end - start) / steps)
        t = (fr - start) / max(1, end - start)
        rot = list(base)
        rot[axis] = base[axis] + amplitude * math.sin(t * math.pi * cycles + phase)
        obj.rotation_euler = rot
        obj.keyframe_insert(data_path="rotation_euler", frame=fr)


def follow_axis(cfg: dict, ctx: dict) -> None:
    """Keyframe target axis from source start/end values scaled by factor."""
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    axis = int(cfg.get("axis", 0))
    factor = float(cfg.get("factor", 0.35))
    values = cfg.get("values")
    if values is None:
        source = cfg.get("source_values", [0.0, 0.0])
        values = [float(source[0]) * factor, float(source[1]) * factor]
    base = list(obj.location)
    if "base" in cfg:
        base = list(cfg["base"])
    loc0 = list(base)
    loc0[axis] = base[axis] + float(values[0])
    obj.location = loc0
    obj.keyframe_insert(data_path="location", frame=start)
    loc1 = list(base)
    loc1[axis] = base[axis] + float(values[1])
    obj.location = loc1
    obj.keyframe_insert(data_path="location", frame=end)


def look_at(cfg: dict, ctx: dict) -> None:
    """Orient camera/object toward points. Supports a multi-key `keys` list."""
    from mathutils import Vector

    obj = _obj(ctx, cfg["target"])
    if "keys" in cfg:
        for key in cfg["keys"]:
            fr = _key_frame(key, ctx)
            if "location" in key:
                obj.location = key["location"]
                obj.keyframe_insert(data_path="location", frame=fr)
            look = Vector(key["point"])
            obj.rotation_euler = (look - Vector(obj.location)).to_track_quat("-Z", "Y").to_euler()
            obj.keyframe_insert(data_path="rotation_euler", frame=fr)
        return

    start, end = _frame_range(cfg, ctx)
    points = cfg["points"]
    if len(points) < 2:
        raise ValueError("look_at requires at least two points")
    # Ensure location keyframes exist for matching frames if locations provided.
    if "locations" in cfg:
        locs = cfg["locations"]
        obj.location = locs[0]
        obj.keyframe_insert(data_path="location", frame=start)
        obj.location = locs[-1]
        obj.keyframe_insert(data_path="location", frame=end)

    obj.location = cfg.get("locations", [list(obj.location), list(obj.location)])[0]
    look = Vector(points[0])
    obj.rotation_euler = (look - obj.location).to_track_quat("-Z", "Y").to_euler()
    obj.keyframe_insert(data_path="rotation_euler", frame=start)

    obj.location = cfg.get("locations", [list(obj.location), list(obj.location)])[-1]
    look = Vector(points[-1])
    obj.rotation_euler = (look - obj.location).to_track_quat("-Z", "Y").to_euler()
    obj.keyframe_insert(data_path="rotation_euler", frame=end)


def keyframes(cfg: dict, ctx: dict) -> None:
    """Keyframe location, rotation_euler, scale, or camera ortho_scale."""
    obj = _obj(ctx, cfg["target"])
    prop = str(cfg.get("property", "location"))
    if prop == "ortho_scale":
        cam = obj.data
        for key in cfg["keys"]:
            fr = _key_frame(key, ctx)
            cam.ortho_scale = float(key["value"])
            cam.keyframe_insert(data_path="ortho_scale", frame=fr)
        return
    for key in cfg["keys"]:
        fr = _key_frame(key, ctx)
        val = key["value"]
        if prop == "location":
            obj.location = val
        elif prop == "rotation_euler":
            obj.rotation_euler = val
        elif prop == "scale":
            obj.scale = val
        else:
            raise ValueError(f"Unsupported keyframes property: {prop}")
        obj.keyframe_insert(data_path=prop, frame=fr)


def talk(cfg: dict, ctx: dict) -> None:
    """Speech-like pouch scale and optional jaw rotation (visual only, no audio)."""
    obj = _obj(ctx, cfg["target"])
    start, end = _frame_range(cfg, ctx)
    axes = cfg.get("axes", [1, 2])
    amplitude = float(cfg.get("amplitude", 0.22))
    cycles = float(cfg.get("cycles", 8.0))
    base = list(cfg["base"]) if "base" in cfg else list(obj.scale)
    jaw_axis = cfg.get("jaw_axis")
    jaw_open = float(cfg.get("jaw_open", 0.28))
    jaw_base = list(cfg["jaw_base"]) if "jaw_base" in cfg else list(obj.rotation_euler)
    steps = _steps(cfg, start, end)
    for step in range(steps + 1):
        fr = start + int(step * (end - start) / steps)
        t = (fr - start) / max(1, end - start)
        opens = abs(math.sin(t * math.pi * cycles)) ** 1.2
        chatter = 0.42 * abs(math.sin(t * math.pi * cycles * 1.9 + 0.55))
        gap = 0.38 + 0.62 * max(0.0, math.sin(t * math.pi * 3.2 + 0.2))
        pulse = (0.72 * opens + 0.28 * chatter) * gap
        if axes and amplitude:
            sc = list(base)
            for ax in axes:
                sc[int(ax)] = base[int(ax)] * (1.0 + pulse * amplitude)
            obj.scale = sc
            obj.keyframe_insert(data_path="scale", frame=fr)
        if jaw_axis is not None:
            rot = list(jaw_base)
            rot[int(jaw_axis)] = jaw_base[int(jaw_axis)] + jaw_open * pulse
            obj.rotation_euler = rot
            obj.keyframe_insert(data_path="rotation_euler", frame=fr)


def front_of(cfg: dict, ctx: dict) -> None:
    """Place an object in front of a target's local face axis and look at it."""
    import bpy
    from mathutils import Vector

    obj = _obj(ctx, cfg["target"])
    target = _obj(ctx, cfg["of"])
    start, end = _frame_range(cfg, ctx)
    distance = float(cfg.get("distance", 6.8))
    axis_name = str(cfg.get("axis", "X")).upper()
    axis_vec = {"X": Vector((1.0, 0.0, 0.0)), "Y": Vector((0.0, 1.0, 0.0)), "Z": Vector((0.0, 0.0, 1.0))}[axis_name]
    if cfg.get("invert"):
        axis_vec = -axis_vec
    lift = Vector(cfg.get("lift", [0.0, 0.0, 0.12]))
    look_along = float(cfg.get("look_along", 0.58))
    look_lift = Vector(cfg.get("look_lift", [0.0, 0.0, -0.18]))
    steps = _steps(cfg, start, end)
    scene = bpy.context.scene
    view = bpy.context.view_layer
    prev = scene.frame_current
    try:
        for step in range(steps + 1):
            fr = start + int(step * (end - start) / steps)
            scene.frame_set(fr)
            view.update()
            wm = target.matrix_world.copy()
            origin = wm.to_translation()
            face = wm.to_3x3() @ axis_vec
            if face.length < 1e-6:
                face = Vector((0.0, -1.0, 0.0))
            else:
                face.normalize()
            loc = origin + face * distance + lift
            look = origin + face * look_along + look_lift
            obj.location = loc
            obj.rotation_euler = (look - loc).to_track_quat("-Z", "Y").to_euler()
            obj.keyframe_insert(data_path="location", frame=fr)
            obj.keyframe_insert(data_path="rotation_euler", frame=fr)
    finally:
        scene.frame_set(prev)
        view.update()


def parent(cfg: dict, ctx: dict) -> None:
    from blender_prims import parent_to

    child = _obj(ctx, cfg["child"])
    parent_obj = _obj(ctx, cfg["parent"])
    parent_to(child, parent_obj)


def set_interpolation(cfg: dict, ctx: dict) -> None:
    mode = str(cfg.get("mode", "LINEAR")).upper()
    targets = cfg.get("targets") or [cfg["target"]]
    for name in targets:
        obj = _obj(ctx, name)
        if not obj.animation_data or not obj.animation_data.action:
            continue
        for fcurve in obj.animation_data.action.fcurves:
            for kp in fcurve.keyframe_points:
                kp.interpolation = mode


def bob_matching(cfg: dict, ctx: dict) -> None:
    """Bob all objects whose names start with a prefix (e.g. Party balloon)."""
    prefix = cfg["prefix"]
    matches = [n for n in ctx["objects"] if n.startswith(prefix)]
    for name in matches:
        sub = {**cfg, "target": name, "phase": float(cfg.get("phase", 0.0)) + (hash(name) % 7)}
        bob(sub, ctx)


ACTION_REGISTRY: dict[str, ActionFn] = {
    "translate": translate,
    "rotate": rotate,
    "spin": spin,
    "bob": bob,
    "flutter": flutter,
    "follow_axis": follow_axis,
    "look_at": look_at,
    "keyframes": keyframes,
    "talk": talk,
    "front_of": front_of,
    "parent": parent,
    "set_interpolation": set_interpolation,
    "bob_matching": bob_matching,
}


def known_actions() -> set[str]:
    return set(ACTION_REGISTRY)


def apply_action(cfg: dict, ctx: dict) -> None:
    name = cfg.get("action")
    if not name:
        raise ValueError("Action entry missing 'action' key")
    fn = ACTION_REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"Unknown action: {name}")
    fn(cfg, ctx)


def apply_actions(actions: list[dict], ctx: dict) -> None:
    for entry in actions:
        apply_action(entry, ctx)
