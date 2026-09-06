#!/usr/bin/env python3
"""
Concurrent animation pipeline — pelican-on-bicycle coastal parade.

Deps provisioned by platform:
  Windows (run.ps1 / run.cmd) — winget: Python, Blender, FFmpeg
  Nix (flake)                 — store: blender, ffmpeg-full, python3

Renders with parallel Blender workers (EEVEE / Cycles) then FFmpeg encode.
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


def _blender_base_args(blender: str) -> list[str]:
    """Faster headless startup: skip user addons/audio."""
    args = [blender, "--background", "--factory-startup"]
    # Blender accepts -noaudio on most builds; ignore if the binary rejects it later.
    args.append("-noaudio")
    return args


def _fmt_secs(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{int(m)}m {s:05.2f}s"
    h, m = divmod(int(m), 60)
    return f"{h}h {m}m {s:05.2f}s"


def _device_label(hw: HardwareProfile, kind: str) -> str:
    if kind == "nvidia":
        return "; ".join(hw.nvidia_gpus) if hw.nvidia_gpus else "(none)"
    if kind == "intel":
        return "; ".join(hw.intel_gpus) if hw.intel_gpus else "(none)"
    return f"{hw.cpu_name} ({hw.cpu_count} threads)"


def print_run_summary(
    *,
    output_mp4: Path,
    hw: HardwareProfile,
    scene: dict,
    engine: str,
    workers: int,
    threads_per_worker: int,
    frames: int,
    pin_gpus: bool,
    codec: str,
    timings: dict[str, float],
) -> None:
    total = timings.get("total", 0.0)
    nvidia = _device_label(hw, "nvidia")
    intel = _device_label(hw, "intel")
    cpu = _device_label(hw, "cpu")
    eng = (engine or "eevee").lower()

    if eng == "eevee":
        render_who = (
            f"NVIDIA GPU raster (EEVEE) on {nvidia}"
            if hw.nvidia_gpus
            else (
                f"GPU/CPU EEVEE ({intel})"
                if hw.intel_gpus
                else f"CPU / software GL ({cpu})"
            )
        )
        render_note = f"{workers} Blender process(es) × {threads_per_worker} CPU thread(s) each"
    else:
        if hw.nvidia_gpus:
            render_who = f"NVIDIA Cycles (OptiX/CUDA) on {nvidia}"
            if pin_gpus and len(hw.nvidia_gpus) >= 2:
                render_note = f"{workers} process(es) pinned 1:1 to NVIDIA GPUs + CPU hybrid tiles"
            else:
                render_note = f"{workers} process(es), GPU + CPU hybrid ({threads_per_worker} threads/worker)"
        else:
            render_who = f"Cycles CPU on {cpu}"
            render_note = f"{workers} process(es) × {threads_per_worker} threads"

    if codec.endswith("_nvenc"):
        encode_who = f"NVIDIA NVENC on {nvidia}"
    elif codec.endswith("_qsv"):
        encode_who = f"Intel Quick Sync on {intel}"
    else:
        encode_who = f"CPU libx264 on {cpu}"

    lines = [
        "",
        "=" * 64,
        " Animation complete — timing & hardware summary",
        "=" * 64,
        f"  Output     : {output_mp4}",
        f"  Frames     : {frames}",
        f"  Wall clock : {_fmt_secs(total)}",
        "",
        "  Stage timings:",
        f"    validate : {_fmt_secs(timings.get('validate', 0.0))}",
        f"    build    : {_fmt_secs(timings.get('build', 0.0))}   (CPU scene construction)",
        f"    render   : {_fmt_secs(timings.get('render', 0.0))}   ({render_note})",
        f"    encode   : {_fmt_secs(timings.get('encode', 0.0))}   ({codec})",
        "",
        "  Who did what:",
        f"    CPU      : orchestration, Blender scene build, worker threads, FFmpeg mux",
        f"               {cpu}",
        f"    Render   : {render_who}",
        f"    Encode   : {encode_who}",
    ]
    if hw.nvidia_gpus and not codec.endswith("_nvenc") and eng == "eevee":
        lines.append("    Note     : NVIDIA used for EEVEE render; encode used a non-NVENC path")
    if hw.intel_gpus and codec.endswith("_qsv"):
        lines.append("    Intel GPU: encode only (Quick Sync); not used for Blender Cycles when NVIDIA present")
    elif hw.intel_gpus and not codec.endswith("_qsv"):
        lines.append(f"    Intel GPU: detected ({intel}) — not selected for encode this run")
    lines += ["=" * 64, ""]
    print("\n".join(lines))


def run_blender_parallel(
    data_dir: Path,
    output_dir: Path,
    scene: dict,
    hw: HardwareProfile,
    engine: str,
) -> dict:
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
    t_build = time.perf_counter()
    with build_log.open("wb") as logf:
        subprocess.run(
            extra_cmd("--mode", "build", "--blend", str(blend)),
            check=True,
            env=build_env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    build_s = time.perf_counter() - t_build

    ranges = _chunk_ranges(total, workers)
    print(f"==> Multiprocess render: {workers} Blender process(es), ranges={ranges}")
    print("==> Progress (live frame files on disk):")

    procs: list[subprocess.Popen[bytes]] = []
    log_handles: list = []
    t_render = time.perf_counter()
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
    render_s = time.perf_counter() - t_render

    return {
        "engine": resolved_engine,
        "workers": workers,
        "threads": threads,
        "frames": total,
        "pin_gpus": pin_gpus,
        "build_s": build_s,
        "render_s": render_s,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Accelerated animation pipeline")
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument(
        "--renderer",
        choices=("auto", "blender"),
        default=None,
        help="auto | blender (default)",
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
    timings: dict[str, float] = {}
    print("==> Validating data...")
    t_val = time.perf_counter()
    validate(data_dir)
    timings["validate"] = time.perf_counter() - t_val

    if renderer == "auto":
        renderer = "blender"
        print(f"==> auto → {renderer}")

    print(format_involvement_report(hw, scene, renderer))

    print("==> Blender pelican parade (GPU/CPU + parallel workers)...")
    render_meta = run_blender_parallel(data_dir, output_dir, scene, hw, engine)
    timings["build"] = float(render_meta["build_s"])
    timings["render"] = float(render_meta["render_s"])
    print("==> Encoding (NVENC → QSV → libx264)...")
    t_enc = time.perf_counter()
    codec = encode_frames(
        output_dir / "frames",
        output_dir / "animation.mp4",
        scene,
        hw,
        expected_frames=_frame_count(scene),
    )
    timings["encode"] = time.perf_counter() - t_enc
    print(f"==> Codec: {codec}")

    timings["total"] = time.perf_counter() - t0
    out_mp4 = output_dir / "animation.mp4"
    print_run_summary(
        output_mp4=out_mp4,
        hw=hw,
        scene=scene,
        engine=str(render_meta.get("engine", engine)),
        workers=int(render_meta.get("workers", 0)),
        threads_per_worker=int(render_meta.get("threads", 0)),
        frames=int(render_meta.get("frames", _frame_count(scene))),
        pin_gpus=bool(render_meta.get("pin_gpus", False)),
        codec=codec,
        timings=timings,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
