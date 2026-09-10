#!/usr/bin/env python3
"""
Blender headless renderer — GPU-first (OptiX → CUDA → HIP → EEVEE), CPU hybrid.

Builds a declarative scene from data/scene.json + data/objects/, then renders
frame slices for FFmpeg.

Modes:
  build   — construct scene + animation, save .blend (no frames)
  render  — render a frame slice (from live build or --blend)
  all     — build + render in one process (single-worker fallback)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def parse_args(argv: list[str]) -> argparse.Namespace:
    # Blender 4.x on Linux often leaves its own flags in sys.argv. Prefer the
    # explicit "--" split, else start at our first flag so "--background" etc.
    # are not fed to argparse (that exits 1 and is what Colab was hitting).
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    elif "--data-dir" in argv:
        argv = argv[argv.index("--data-dir") :]
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


def load_scene(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def configure_gpu_cycles(scn, samples: int, accel: dict | None = None) -> str:
    """Enable Cycles on OptiX/CUDA/HIP + CPU hybrid. Returns device label."""
    import bpy

    accel = accel or {}
    scn.render.engine = "CYCLES"
    scn.cycles.device = "GPU"

    # Adaptive sampling: stop early on clean tiles (big win on stylized scenes).
    use_adaptive = bool(accel.get("cycles_adaptive", True))
    if hasattr(scn.cycles, "use_adaptive_sampling"):
        scn.cycles.use_adaptive_sampling = use_adaptive
    if use_adaptive and hasattr(scn.cycles, "adaptive_threshold"):
        scn.cycles.adaptive_threshold = float(accel.get("cycles_noise_threshold", 0.05))
    if hasattr(scn.cycles, "adaptive_min_samples"):
        scn.cycles.adaptive_min_samples = int(accel.get("cycles_min_samples", max(4, samples // 4)))

    scn.cycles.samples = samples

    # GPU denoiser when available (OptiX); skip if samples already high and user opts out.
    use_denoise = bool(accel.get("cycles_denoise", True))
    scn.cycles.use_denoising = use_denoise
    if use_denoise:
        prefer = os.environ.get("ANIM_CYCLES_DEVICE", "").strip().upper()
        denoisers = ("OPENIMAGEDENOISE", "NLM") if prefer.startswith("CUDA") else ("OPTIX", "OPENIMAGEDENOISE", "NLM")
        for denoiser in denoisers:
            try:
                scn.cycles.denoiser = denoiser
                break
            except Exception:
                continue

    # Large tiles for GPU path tracing.
    tile = int(accel.get("cycles_tile_size", 512))
    for attr in ("tile_x", "tile_y"):
        if hasattr(scn.cycles, attr):
            setattr(scn.cycles, attr, tile)
    if hasattr(scn.cycles, "tile_size"):
        scn.cycles.tile_size = tile

    # All CPU threads for BVH / hybrid tiles (unless FIXED was set by caller).
    if scn.render.threads_mode != "FIXED":
        scn.render.threads_mode = "AUTO"

    label = "CYCLES/CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        scn.cycles.device = "CPU"
        return label

    # Prefer OptiX → CUDA → … unless ANIM_CYCLES_DEVICE overrides (Colab T4: CUDA).
    default_order = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")
    raw = os.environ.get("ANIM_CYCLES_DEVICE", "").strip()
    if raw:
        aliases = {"GPU": "CUDA"}
        order = tuple(
            aliases.get(p.strip().upper(), p.strip().upper())
            for p in raw.replace(";", ",").split(",")
            if p.strip()
        ) or default_order
    else:
        order = default_order
    for compute in order:
        try:
            prefs.compute_device_type = compute
            prefs.get_devices()
            devices = list(getattr(prefs, "devices", []))
            if not devices:
                continue
            enabled = 0
            for d in devices:
                use = d.type in {compute, "CPU"}
                d.use = use
                if use and d.type != "CPU":
                    enabled += 1
            if enabled:
                scn.cycles.device = "GPU"
                label = f"CYCLES/{compute}+CPU({enabled} GPU)"
                print(f"Blender Cycles devices ({compute}):")
                for d in devices:
                    print(f"  [{[' ', 'x'][bool(d.use)]}] {d.name} ({d.type})")
                return label
        except Exception as exc:
            print(f"Cycles {compute} unavailable: {exc}")
            continue

    # CPU-only fallback: enable every CPU device entry.
    try:
        prefs.compute_device_type = "NONE"
        prefs.get_devices()
        for d in list(getattr(prefs, "devices", [])):
            d.use = d.type == "CPU"
    except Exception:
        pass
    scn.cycles.device = "CPU"
    return "CYCLES/CPU"


def configure_eevee(scn, accel: dict | None = None) -> str:
    accel = accel or {}
    samples = int(accel.get("eevee_samples", 8))
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scn.render.engine = engine
            eevee = getattr(scn, "eevee", None)
            if eevee is not None:
                # Lean settings: stylized scenes read well with fewer TAA samples.
                for attr, val in (
                    ("taa_render_samples", samples),
                    ("taa_samples", samples),
                    ("use_gtao", True),
                    ("use_bloom", False),
                    ("use_ssr", False),
                    ("use_motion_blur", False),
                    ("use_raytracing", False),
                    ("use_shadows", True),
                    ("shadow_ray_count", 1),
                    ("shadow_step_count", 2),
                ):
                    if hasattr(eevee, attr):
                        try:
                            setattr(eevee, attr, val)
                        except Exception:
                            pass
            return f"EEVEE/{engine}(taa={samples})"
        except Exception:
            continue
    return configure_gpu_cycles(scn, samples=24, accel=accel)


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
        scn.render.image_settings.quality = int(out_cfg.get("jpeg_quality", 90))
    if fmt == "PNG":
        scn.render.image_settings.compression = int(out_cfg.get("png_compression", 1))

    # Keep scene data between frames (huge for animation workers).
    try:
        scn.render.use_persistent_data = True
    except Exception:
        pass
    try:
        scn.render.use_motion_blur = False
    except Exception:
        pass
    try:
        scn.render.film_transparent = False
    except Exception:
        pass

    try:
        scn.render.use_compositing = False
        scn.render.use_sequencer = False
    except Exception:
        pass
    samples = samples_override
    if samples is None:
        samples = int(accel.get("cycles_samples", out_cfg.get("cycles_samples", 32)))

    prefer = engine_choice
    if prefer == "auto":
        prefer = accel.get("blender_engine", "eevee")

    if prefer == "eevee":
        return configure_eevee(scn, accel)
    if prefer == "cycles":
        return configure_gpu_cycles(scn, samples, accel)

    # auto + prefer_cycles_gpu: try Cycles GPU, else EEVEE
    if accel.get("prefer_cycles_gpu", False):
        label = configure_gpu_cycles(scn, samples, accel)
        if "GPU" not in label and "OPTIX" not in label and "CUDA" not in label and "HIP" not in label:
            print("No Cycles GPU — falling back to EEVEE")
            return configure_eevee(scn, accel)
        return label

    return configure_eevee(scn, accel)


def build_scene(data_dir: Path) -> dict:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from scene_builder import build_animation

    scene_cfg = load_scene(data_dir / "scene.json")
    build_animation(data_dir, scene_cfg)
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
    fmt = scn.render.image_settings.file_format
    ext = ".jpg" if fmt in ("JPEG", "JPG") else ".png"
    try:
        scn.render.use_file_extension = True
        scn.render.use_overwrite = True
        scn.render.use_placeholder = False
    except Exception:
        pass

    # Per-frame stills so the orchestrator progress bar can count files live.
    print(f"Rendering frames {fs}-{fe} via {label} → {frames_dir}", flush=True)
    for f in range(fs, fe + 1):
        scn.frame_set(f)
        scn.render.filepath = str(frames_dir / f"frame_{f:04d}")
        bpy.ops.render.render(write_still=True)
        print(f"FRAME {f}{ext}", flush=True)
    print(f"Worker finished frames {fs}-{fe}", flush=True)


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
