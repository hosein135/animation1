#!/usr/bin/env python3
"""
Concurrent animation pipeline — pelican-on-bicycle coastal parade.

Deps provisioned by platform:
  Windows (run.ps1 / run.cmd) — winget: Python, Blender, FFmpeg
  Nix (flake)                 — store: blender, ffmpeg-full, python3

Modes:
  blender — parallel Blender workers (OptiX/CUDA/EEVEE) → FFmpeg  (default)
  auto    — same as blender for this scene (bar-chart ModernGL path removed)
  gpu     — legacy ModernGL path (not used for pelican geometry)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from encode_video import encode_frames  # noqa: E402
from hw_detect import HardwareProfile, detect, format_involvement_report  # noqa: E402


def _gpu_deps_available() -> bool:
    try:
        import moderngl  # noqa: F401
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def validate(data_dir: Path) -> None:
    env = os.environ.copy()
    env["DATA_DIR"] = str(data_dir)
    subprocess.run(
        [sys.executable, str(_SCRIPTS / "validate_data.py")],
        check=True,
        env=env,
    )


def _frame_count(scene: dict) -> int:
    return max(1, int(round(float(scene["fps"]) * float(scene["duration_seconds"]))))


def _chunk_ranges(total: int, workers: int) -> list[tuple[int, int]]:
    workers = max(1, min(workers, total))
    base = total // workers
    rem = total % workers
    ranges: list[tuple[int, int]] = []
    start = 1
    for i in range(workers):
        count = base + (1 if i < rem else 0)
        end = start + count - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def require_tools(hw: HardwareProfile) -> tuple[str, str]:
    blender = hw.blender_path or shutil.which("blender")
    ffmpeg = hw.ffmpeg_path or shutil.which("ffmpeg")
    missing = []
    if not blender:
        missing.append("blender (winget: BlenderFoundation.Blender | nix: blender)")
    if not ffmpeg:
        missing.append("ffmpeg (winget: Gyan.FFmpeg | nix: ffmpeg-full)")
    if missing:
        raise SystemExit("Missing required tools:\n  - " + "\n  - ".join(missing))
    return blender, ffmpeg


def run_gpu(data_dir: Path, output_dir: Path) -> None:
    from gpu_native_render import render_animation

    render_animation(data_dir, output_dir, pipe_to_ffmpeg=True)


def run_blender_parallel(
    data_dir: Path,
    output_dir: Path,
    scene: dict,
    hw: HardwareProfile,
    engine: str,
) -> None:
    blender, _ffmpeg = require_tools(hw)
    accel = scene.get("acceleration", {})
    resolved_engine = engine if engine != "auto" else str(accel.get("blender_engine", "eevee"))

    workers = int(accel.get("blender_workers", 0)) or hw.recommended_blender_workers_for(resolved_engine)
    threads_cfg = int(accel.get("blender_threads", 0))
    threads = threads_cfg or hw.recommended_cpu_threads_per_worker(workers, resolved_engine)

    total = _frame_count(scene)
    blend = output_dir / "scene.blend"
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for old in frames_dir.glob("frame_*"):
        old.unlink(missing_ok=True)

    render_py = _SCRIPTS / "render_animation.py"
    nvidia_n = len(hw.nvidia_gpus)
    pin_gpus = resolved_engine == "cycles" and nvidia_n >= 2

    def blender_cmd(*extra: str) -> list[str]:
        return [
            blender,
            "--background",
            "--python",
            str(render_py),
            "--",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--engine",
            resolved_engine,
            "--threads",
            str(threads),
            *extra,
        ]

    # Build once with all GPUs visible (scene construction is CPU-bound).
    print(f"==> Blender build → {blend}")
    print(
        f"==> Perf plan: engine={resolved_engine} workers={workers} "
        f"threads/worker={threads} nvidia={nvidia_n} pin_gpus={pin_gpus}"
    )
    build_env = os.environ.copy()
    # Prefer discrete GPU for OpenGL/EEVEE on Windows mixed systems.
    build_env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    subprocess.run(
        blender_cmd("--mode", "build", "--blend", str(blend)),
        check=True,
        env=build_env,
    )

    ranges = _chunk_ranges(total, workers)
    print(f"==> Blender parallel render: {workers} worker(s), ranges={ranges}")

    def worker(idx: int, fs: int, fe: int) -> tuple[int, int, float]:
        t0 = time.perf_counter()
        env = os.environ.copy()
        env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
        # Pin Cycles workers to distinct NVIDIA GPUs when we have several.
        if pin_gpus:
            gpu_id = str(idx % nvidia_n)
            env["CUDA_VISIBLE_DEVICES"] = gpu_id
            env["HIP_VISIBLE_DEVICES"] = gpu_id
            print(f"  worker frames {fs}-{fe} → GPU {gpu_id}")
        elif nvidia_n == 1 and resolved_engine == "cycles":
            env["CUDA_VISIBLE_DEVICES"] = "0"
        cmd = [
            blender,
            "--background",
            str(blend),
            "--python",
            str(render_py),
            "--",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--mode",
            "render",
            "--blend",
            str(blend),
            "--frame-start",
            str(fs),
            "--frame-end",
            str(fe),
            "--engine",
            resolved_engine,
            "--threads",
            str(threads),
        ]
        subprocess.run(cmd, check=True, env=env)
        return fs, fe, time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(worker, i, fs, fe) for i, (fs, fe) in enumerate(ranges)
        ]
        for fut in as_completed(futures):
            fs, fe, dt = fut.result()
            print(f"  done frames {fs}-{fe} in {dt:.1f}s")


def main() -> int:
    p = argparse.ArgumentParser(description="Accelerated animation pipeline")
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument(
        "--renderer",
        choices=("auto", "blender", "gpu"),
        default=None,
        help="auto | gpu (ModernGL) | blender",
    )
    p.add_argument(
        "--engine",
        choices=("auto", "cycles", "eevee"),
        default=None,
        help="Blender engine override",
    )
    p.add_argument("--workers", type=int, default=None, help="Parallel Blender workers")
    args = p.parse_args()

    root = Path(os.environ.get("PROJECT_ROOT", Path.cwd())).resolve()
    data_dir = (args.data_dir or Path(os.environ.get("DATA_DIR", root / "data"))).resolve()
    output_dir = (args.output_dir or Path(os.environ.get("OUTPUT_DIR", root / "output"))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with (data_dir / "scene.json").open(encoding="utf-8") as f:
        scene = json.load(f)

    accel = dict(scene.get("acceleration", {}))
    if args.workers is not None:
        accel["blender_workers"] = args.workers
        scene = {**scene, "acceleration": accel}

    renderer = args.renderer or accel.get("renderer", "auto")
    engine = args.engine or accel.get("blender_engine", "eevee")
    if args.engine:
        accel["blender_engine"] = args.engine
        scene = {**scene, "acceleration": accel}

    hw = detect()
    blender, ffmpeg = require_tools(hw)
    print(f"==> blender: {blender}")
    print(f"==> ffmpeg:  {ffmpeg}")
    print(f"==> Renderer mode: {renderer}")

    t0 = time.perf_counter()
    print("==> Validating data...")
    validate(data_dir)

    if renderer == "auto":
        # Pelican scene is Blender-only; ModernGL bar path does not apply.
        renderer = "blender"
        print(f"==> auto → {renderer}")

    print(format_involvement_report(hw, scene, renderer))

    if renderer == "gpu":
        if not _gpu_deps_available():
            raise SystemExit(
                "renderer=gpu needs moderngl+numpy (legacy bar path).\n"
                "  Pelican parade: use --renderer blender"
            )
        print("==> GPU-native OpenGL render + FFmpeg pipe (legacy)...")
        run_gpu(data_dir, output_dir)
    else:
        print("==> Blender pelican parade (GPU/CPU + parallel workers)...")
        run_blender_parallel(data_dir, output_dir, scene, hw, engine)
        print("==> Encoding (NVENC → QSV → libx264)...")
        codec = encode_frames(output_dir / "frames", output_dir / "animation.mp4", scene, hw)
        print(f"==> Codec: {codec}")

    dt = time.perf_counter() - t0
    print(f"==> Ready: {output_dir / 'animation.mp4'} ({dt:.2f}s wall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
