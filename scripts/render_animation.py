#!/usr/bin/env python3
"""
Blender headless renderer.

Builds a bar chart from data/values.csv and animates growth using data/scene.json.
Writes PNG frames to output/frames/.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    # Blender passes a "--" before user args
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = argv[1:]

    p = argparse.ArgumentParser(description="Render data-driven animation frames")
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    return p.parse_args(argv)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def bounce_out(t: float) -> float:
    """Approximate bounce ease-out for a lively finish."""
    t = max(0.0, min(1.0, t))
    n1 = 7.5625
    d1 = 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def load_scene(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_values(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "label": row["frame_label"],
                    "value": float(row["value"]),
                    "color": (float(row["r"]), float(row["g"]), float(row["b"]), 1.0),
                }
            )
    return rows


def clear_scene() -> None:
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    # Remove leftover orphan data lightly
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        if block.users == 0:
            bpy.data.materials.remove(block)


def make_material(name: str, rgba: tuple[float, float, float, float]):
    import bpy

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.35
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.5
    elif "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = 0.5
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def look_at(obj, target) -> None:
    import mathutils

    direction = mathutils.Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def setup_world(bg: list[float]) -> None:
    import bpy

    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node is None:
        bg_node = world.node_tree.nodes.new("ShaderNodeBackground")
    bg_node.inputs["Color"].default_value = tuple(bg)
    bg_node.inputs["Strength"].default_value = 1.0


def build_and_render(data_dir: Path, output_dir: Path) -> None:
    import bpy

    scene_cfg = load_scene(data_dir / "scene.json")
    series = load_values(data_dir / "values.csv")
    if not series:
        raise RuntimeError("No rows in values.csv")

    fps = int(scene_cfg["fps"])
    duration = float(scene_cfg["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))
    width, height = scene_cfg["resolution"]
    bar_cfg = scene_cfg["bar"]
    spacing = float(bar_cfg.get("spacing", 1.15))
    base_radius = float(bar_cfg.get("base_radius", 0.35))
    max_height = float(bar_cfg.get("max_height", 4.0))
    use_bounce = bool(bar_cfg.get("bounce", True))

    max_value = max(item["value"] for item in series) or 1.0
    n = len(series)
    x0 = -((n - 1) * spacing) / 2.0

    clear_scene()
    setup_world(scene_cfg.get("background_color", [0.05, 0.05, 0.08, 1.0]))

    # Ground plane
    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground_mat = make_material("GroundMat", (0.12, 0.14, 0.18, 1.0))
    ground.data.materials.append(ground_mat)

    bars = []
    for i, item in enumerate(series):
        target_h = (item["value"] / max_value) * max_height
        # Cylinder along Z: Blender default cylinder is along Z with height=2, radius=1
        bpy.ops.mesh.primitive_cylinder_add(
            radius=base_radius,
            depth=1.0,
            location=(x0 + i * spacing, 0.0, 0.5),
        )
        bar = bpy.context.active_object
        bar.name = f"Bar_{item['label']}"
        mat = make_material(f"Mat_{item['label']}", item["color"])
        bar.data.materials.append(mat)
        bars.append((bar, target_h, item["label"]))

    # Sun + fill light
    light_cfg = scene_cfg.get("lighting", {})
    bpy.ops.object.light_add(type="SUN", location=(4, -4, 10))
    sun = bpy.context.active_object
    sun.data.energy = float(light_cfg.get("sun_energy", 3.5))
    sun.rotation_euler = tuple(light_cfg.get("sun_rotation_euler", [0.9, 0.2, 0.4]))

    bpy.ops.object.light_add(type="AREA", location=(-6, -3, 5))
    fill = bpy.context.active_object
    fill.data.energy = float(light_cfg.get("fill_energy", 80.0))
    fill.data.size = 6.0

    # Camera
    cam_cfg = scene_cfg["camera"]
    bpy.ops.object.camera_add(location=tuple(cam_cfg["location"]))
    cam = bpy.context.active_object
    cam.data.lens = float(cam_cfg.get("lens", 40))
    look_at(cam, cam_cfg.get("look_at", [0, 0, 1.2]))
    bpy.context.scene.camera = cam

    # Timeline / render settings
    scn = bpy.context.scene
    # Blender 4.2+ uses EEVEE_NEXT; older builds use EEVEE
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scn.render.engine = engine
            break
        except Exception:
            continue

    scn.render.resolution_x = int(width)
    scn.render.resolution_y = int(height)
    scn.render.resolution_percentage = 100
    scn.render.fps = fps
    scn.frame_start = 1
    scn.frame_end = frame_end
    scn.render.image_settings.file_format = scene_cfg.get("output", {}).get("frame_format", "PNG")

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    scn.render.filepath = str(frames_dir / "frame_")

    # Keyframe bar heights (staggered grow)
    for i, (bar, target_h, _label) in enumerate(bars):
        start_frame = 1 + int((i / max(n, 1)) * (frame_end * 0.35))
        end_frame = min(frame_end, start_frame + int(frame_end * 0.55))

        # Start collapsed
        bar.scale = (1.0, 1.0, 0.001)
        bar.location.z = 0.0005
        bar.keyframe_insert(data_path="scale", frame=start_frame)
        bar.keyframe_insert(data_path="location", frame=start_frame)

        progress_frames = max(1, end_frame - start_frame)
        for step in range(progress_frames + 1):
            t = step / progress_frames
            eased = bounce_out(t) if use_bounce else ease_out_cubic(t)
            h = max(0.001, target_h * eased)
            bar.scale = (1.0, 1.0, h)
            bar.location.z = h / 2.0
            fr = start_frame + step
            bar.keyframe_insert(data_path="scale", frame=fr)
            bar.keyframe_insert(data_path="location", frame=fr)

        # Subtle idle bob near the end
        bob_start = end_frame
        for fr in range(bob_start, frame_end + 1):
            phase = (fr - bob_start) / max(1, frame_end - bob_start)
            bob = 1.0 + 0.03 * math.sin(phase * math.pi * 2.0)
            bar.scale = (1.0, 1.0, target_h * bob)
            bar.location.z = (target_h * bob) / 2.0
            bar.keyframe_insert(data_path="scale", frame=fr)
            bar.keyframe_insert(data_path="location", frame=fr)

    # Smooth interpolation
    for bar, *_ in bars:
        if bar.animation_data and bar.animation_data.action:
            for fcurve in bar.animation_data.action.fcurves:
                for kp in fcurve.keyframe_points:
                    kp.interpolation = "BEZIER"
                    kp.handle_left_type = "AUTO_CLAMPED"
                    kp.handle_right_type = "AUTO_CLAMPED"

    print(f"Rendering {frame_end} frames to {frames_dir} ...")
    bpy.ops.render.render(animation=True)
    print("Blender render finished.")


def main() -> int:
    args = parse_args(sys.argv)
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_and_render(data_dir, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
