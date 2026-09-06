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
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from encode_video import encode_frames  # noqa: E402
from hw_detect import HardwareProfile, detect, format_involvement_report  # noqa: E402
from progress import ProgressBar, count_rendered_frames, raise_process_priority  # noqa: E402


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


def _blender_base_args(blender: str) -> list[str]:
    """Faster headless startup: skip user addons/audio."""
    args = [blender, "--background", "--factory-startup"]
    # Blender accepts -noaudio on most builds; ignore if the binary rejects it later.
    args.append("-noaudio")
    return args


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
    logs_dir = output_dir / "logs"
    frames_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    for old in frames_dir.glob("frame_*"):
        old.unlink(missing_ok=True)

    render_py = _SCRIPTS / "render_animation.py"
    nvidia_n = len(hw.nvidia_gpus)
    pin_gpus = resolved_engine == "cycles" and nvidia_n >= 2

    def extra_cmd(*extra: str) -> list[str]:
        return [
            *_blender_base_args(blender),
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

    raise_process_priority()
    print(f"==> Blender build → {blend}")
    print(
        f"==> Perf plan: engine={resolved_engine} multiprocess={workers} "
        f"threads/worker={threads} nvidia={nvidia_n} pin_gpus={pin_gpus} "
        f"cpu={hw.cpu_count}"
    )
    build_env = os.environ.copy()
    build_env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    build_log = logs_dir / "build.log"
    with build_log.open("wb") as logf:
        subprocess.run(
            extra_cmd("--mode", "build", "--blend", str(blend)),
            check=True,
            env=build_env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )

    ranges = _chunk_ranges(total, workers)
    print(f"==> Multiprocess render: {workers} Blender process(es), ranges={ranges}")
    print("==> Progress (live frame files on disk):")

    procs: list[subprocess.Popen[bytes]] = []
    log_handles: list = []
    try:
        for idx, (fs, fe) in enumerate(ranges):
            env = os.environ.copy()
            env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
            if pin_gpus:
                gpu_id = str(idx % nvidia_n)
                env["CUDA_VISIBLE_DEVICES"] = gpu_id
                env["HIP_VISIBLE_DEVICES"] = gpu_id
            elif nvidia_n == 1 and resolved_engine == "cycles":
                env["CUDA_VISIBLE_DEVICES"] = "0"
            cmd = [
                *_blender_base_args(blender),
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
            logf = (logs_dir / f"worker-{idx}.log").open("wb")
            log_handles.append(logf)
            proc = subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT)
            raise_process_priority(proc.pid)
            procs.append(proc)

        bar = ProgressBar(total, label="Render")
        failures: list[str] = []
        while True:
            done = count_rendered_frames(frames_dir)
            alive = sum(1 for p in procs if p.poll() is None)
            bar.update(done, suffix=f"{alive} proc · {resolved_engine}")
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.25)

        done = count_rendered_frames(frames_dir)
        bar.update(min(done, total), suffix=f"0 proc · {resolved_engine}")
        bar.close(suffix=f"{done} frames")

        for idx, proc in enumerate(procs):
            if proc.returncode not in (0, None):
                failures.append(
                    f"worker-{idx} exit {proc.returncode} (see {logs_dir / f'worker-{idx}.log'})"
                )
        if failures:
            raise SystemExit("Blender workers failed:\n  " + "\n  ".join(failures))
        if done < total:
            raise SystemExit(f"Expected {total} frames, found {done} in {frames_dir}")
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        for fh in log_handles:
            try:
                fh.close()
            except Exception:
                pass


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
        codec = encode_frames(
            output_dir / "frames",
            output_dir / "animation.mp4",
            scene,
            hw,
            expected_frames=_frame_count(scene),
        )
        print(f"==> Codec: {codec}")

    dt = time.perf_counter() - t0
    print(f"==> Ready: {output_dir / 'animation.mp4'} ({dt:.2f}s wall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
