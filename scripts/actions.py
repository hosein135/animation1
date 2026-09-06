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


def _frame_range(cfg: dict, ctx: dict) -> tuple[int, int]:
    start = int(cfg.get("frame_start", 1))
    end = int(cfg.get("frame_end", ctx["frame_end"]))
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
    """Orient camera/object toward points at start and end frames."""
    from mathutils import Vector

    obj = _obj(ctx, cfg["target"])
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
