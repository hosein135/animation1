#!/usr/bin/env python3
"""
Blender headless renderer — GPU-first (OptiX → CUDA → HIP → EEVEE), CPU hybrid.

Modes:
  build   — construct scene + animation, save .blend (no frames)
  render  — render a frame slice (from live build or --blend)
  all     — build + render in one process (single-worker fallback)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = argv[1:]

    p = argparse.ArgumentParser(description="GPU-accelerated Blender frame render")
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--mode", choices=("all", "build", "render"), default="all")
    p.add_argument("--blend", type=Path, default=None, help="Path to read/write .blend")
    p.add_argument("--frame-start", type=int, default=None)
    p.add_argument("--frame-end", type=int, default=None)
    p.add_argument(
        "--engine",
        choices=("auto", "cycles", "eevee"),
        default="auto",
        help="auto: Cycles+GPU if available else EEVEE",
    )
    p.add_argument("--samples", type=int, default=None, help="Cycles samples override")
    p.add_argument("--threads", type=int, default=0, help="0 = all logical CPUs")
    return p.parse_args(argv)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def bounce_out(t: float) -> float:
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


def configure_gpu_cycles(scn, samples: int) -> str:
    """Enable Cycles on OptiX/CUDA/HIP + CPU hybrid. Returns device label."""
    import bpy

    scn.render.engine = "CYCLES"
    scn.cycles.samples = samples
    scn.cycles.use_denoising = False
    scn.cycles.device = "GPU"

    # All CPU threads for BVH / hybrid tiles.
    scn.render.threads_mode = "FIXED" if scn.render.threads else "AUTO"

    label = "CYCLES/CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        scn.cycles.device = "CPU"
        return label

    # Prefer OptiX → CUDA → HIP → METAL → ONEAPI
    for compute in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = compute
            prefs.get_devices()
            devices = list(getattr(prefs, "devices", []))
            if not devices:
                continue
            enabled = 0
            for d in devices:
                # Use matching GPUs + CPUs for hybrid.
                use = d.type in {compute, "CPU"}
                d.use = use
                if use and d.type != "CPU":
                    enabled += 1
            if enabled:
                scn.cycles.device = "GPU"
                label = f"CYCLES/{compute}+CPU"
                print(f"Blender Cycles devices ({compute}):")
                for d in devices:
                    print(f"  [{[' ', 'x'][bool(d.use)]}] {d.name} ({d.type})")
                return label
        except Exception as exc:
            print(f"Cycles {compute} unavailable: {exc}")
            continue

    scn.cycles.device = "CPU"
    return "CYCLES/CPU"


def configure_eevee(scn) -> str:
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scn.render.engine = engine
            # Best-effort GPU/viewport settings across Blender versions.
            eevee = getattr(scn, "eevee", None)
            if eevee is not None:
                for attr, val in (
                    ("taa_render_samples", 16),
                    ("use_gtao", True),
                    ("use_bloom", False),
                ):
                    if hasattr(eevee, attr):
                        setattr(eevee, attr, val)
            return f"EEVEE/{engine}"
        except Exception:
            continue
    return configure_gpu_cycles(scn, samples=32)


def apply_render_settings(
    scn,
    scene_cfg: dict,
    engine_choice: str,
    samples_override: int | None,
    threads: int,
) -> str:
    import bpy

    fps = int(scene_cfg["fps"])
    duration = float(scene_cfg["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))
    width, height = scene_cfg["resolution"]
    out_cfg = scene_cfg.get("output", {})
    accel = scene_cfg.get("acceleration", {})

    if threads and threads > 0:
        scn.render.threads_mode = "FIXED"
        scn.render.threads = threads
    else:
        scn.render.threads_mode = "AUTO"

    scn.render.resolution_x = int(width)
    scn.render.resolution_y = int(height)
    scn.render.resolution_percentage = 100
    scn.render.fps = fps
    scn.frame_start = 1
    scn.frame_end = frame_end

    fmt = out_cfg.get("frame_format", "JPEG")
    scn.render.image_settings.file_format = fmt
    if fmt in ("JPEG", "JPG"):
        scn.render.image_settings.quality = int(out_cfg.get("jpeg_quality", 92))
    if fmt == "PNG":
        scn.render.image_settings.compression = int(out_cfg.get("png_compression", 15))

    # Performance: skip color management overhead where possible.
    try:
        scn.render.use_persistent_data = True
    except Exception:
        pass

    samples = samples_override
    if samples is None:
        samples = int(accel.get("cycles_samples", out_cfg.get("cycles_samples", 64)))

    prefer = engine_choice
    if prefer == "auto":
        prefer = accel.get("blender_engine", "auto")

    if prefer == "eevee":
        return configure_eevee(scn)
    if prefer == "cycles":
        return configure_gpu_cycles(scn, samples)

    # auto: try Cycles GPU, fall back to EEVEE (usually faster for this scene)
    if accel.get("prefer_cycles_gpu", False):
        label = configure_gpu_cycles(scn, samples)
        if "CPU" in label and "OPTIX" not in label and "CUDA" not in label and "HIP" not in label:
            print("No Cycles GPU — falling back to EEVEE")
            return configure_eevee(scn)
        return label

    # Default: EEVEE (fast raster) unless forced.
    return configure_eevee(scn)


def build_scene(data_dir: Path) -> dict:
    import bpy

    scene_cfg = load_scene(data_dir / "scene.json")
    series = load_values(data_dir / "values.csv")
    if not series:
        raise RuntimeError("No rows in values.csv")

    fps = int(scene_cfg["fps"])
    duration = float(scene_cfg["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))
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

    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground.data.materials.append(make_material("GroundMat", (0.12, 0.14, 0.18, 1.0)))

    bars = []
    for i, item in enumerate(series):
        target_h = (item["value"] / max_value) * max_height
        bpy.ops.mesh.primitive_cylinder_add(
            radius=base_radius,
            depth=1.0,
            location=(x0 + i * spacing, 0.0, 0.5),
        )
        bar = bpy.context.active_object
        bar.name = f"Bar_{item['label']}"
        bar.data.materials.append(make_material(f"Mat_{item['label']}", item["color"]))
        bars.append((bar, target_h, item["label"]))

    light_cfg = scene_cfg.get("lighting", {})
    bpy.ops.object.light_add(type="SUN", location=(4, -4, 10))
    sun = bpy.context.active_object
    sun.data.energy = float(light_cfg.get("sun_energy", 3.5))
    sun.rotation_euler = tuple(light_cfg.get("sun_rotation_euler", [0.9, 0.2, 0.4]))

    bpy.ops.object.light_add(type="AREA", location=(-6, -3, 5))
    fill = bpy.context.active_object
    fill.data.energy = float(light_cfg.get("fill_energy", 80.0))
    fill.data.size = 6.0

    cam_cfg = scene_cfg["camera"]
    bpy.ops.object.camera_add(location=tuple(cam_cfg["location"]))
    cam = bpy.context.active_object
    cam.data.lens = float(cam_cfg.get("lens", 40))
    look_at(cam, cam_cfg.get("look_at", [0, 0, 1.2]))
    bpy.context.scene.camera = cam

    for i, (bar, target_h, _label) in enumerate(bars):
        start_frame = 1 + int((i / max(n, 1)) * (frame_end * 0.35))
        end_frame = min(frame_end, start_frame + int(frame_end * 0.55))

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

        bob_start = end_frame
        for fr in range(bob_start, frame_end + 1):
            phase = (fr - bob_start) / max(1, frame_end - bob_start)
            bob = 1.0 + 0.03 * math.sin(phase * math.pi * 2.0)
            bar.scale = (1.0, 1.0, target_h * bob)
            bar.location.z = (target_h * bob) / 2.0
            bar.keyframe_insert(data_path="scale", frame=fr)
            bar.keyframe_insert(data_path="location", frame=fr)

    for bar, *_ in bars:
        if bar.animation_data and bar.animation_data.action:
            for fcurve in bar.animation_data.action.fcurves:
                for kp in fcurve.keyframe_points:
                    kp.interpolation = "BEZIER"
                    kp.handle_left_type = "AUTO_CLAMPED"
                    kp.handle_right_type = "AUTO_CLAMPED"

    return scene_cfg


def render_frames(
    output_dir: Path,
    scene_cfg: dict,
    frame_start: int | None,
    frame_end: int | None,
    engine: str,
    samples: int | None,
    threads: int,
) -> None:
    import bpy

    scn = bpy.context.scene
    label = apply_render_settings(scn, scene_cfg, engine, samples, threads)

    total_end = scn.frame_end
    fs = frame_start if frame_start is not None else scn.frame_start
    fe = frame_end if frame_end is not None else total_end
    scn.frame_start = fs
    scn.frame_end = fe

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    scn.render.filepath = str(frames_dir / "frame_")

    print(f"Rendering frames {fs}-{fe} via {label} → {frames_dir}")
    bpy.ops.render.render(animation=True)
    print(f"Worker finished frames {fs}-{fe}")


def main() -> int:
    args = parse_args(sys.argv)
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    blend_path = args.blend
    if blend_path is None:
        blend_path = output_dir / "scene.blend"
    else:
        blend_path = blend_path.resolve()

    import bpy

    if args.mode == "build":
        scene_cfg = build_scene(data_dir)
        apply_render_settings(bpy.context.scene, scene_cfg, args.engine, args.samples, args.threads)
        # Store absolute render path inside blend for workers.
        frames_dir = output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(frames_dir / "frame_")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        print(f"Saved blend: {blend_path}")
        return 0

    if args.mode == "render":
        if blend_path.is_file():
            bpy.ops.wm.open_mainfile(filepath=str(blend_path))
            scene_cfg = load_scene(data_dir / "scene.json")
        else:
            scene_cfg = build_scene(data_dir)
        render_frames(
            output_dir,
            scene_cfg,
            args.frame_start,
            args.frame_end,
            args.engine,
            args.samples,
            args.threads,
        )
        return 0

    # mode == all
    scene_cfg = build_scene(data_dir)
    render_frames(
        output_dir,
        scene_cfg,
        args.frame_start,
        args.frame_end,
        args.engine,
        args.samples,
        args.threads,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
