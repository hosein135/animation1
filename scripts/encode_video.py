#!/usr/bin/env python3
"""Encode frames to MP4 — prefers NVENC, then Intel QSV, then threaded libx264."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Allow `python scripts/encode_video.py` and Blender-adjacent imports.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from hw_detect import HardwareProfile, detect  # noqa: E402


def _nvenc_extra(out_cfg: dict) -> list[str]:
    return [
        "-preset",
        out_cfg.get("nvenc_preset", "p4"),
        "-tune",
        "hq",
        "-rc",
        "vbr",
        "-cq",
        str(out_cfg.get("video_cq", out_cfg.get("video_crf", 19))),
        "-b:v",
        "0",
        "-spatial-aq",
        "1",
    ]


def _qsv_extra(out_cfg: dict) -> list[str]:
    # look_ahead often trips MFX_ERR_DEVICE_FAILED (-17) on older Intel iGPUs (HD 620 etc.).
    # format=nv12 is the reliable system-memory path into h264_qsv.
    return [
        "-vf",
        "format=nv12",
        "-global_quality",
        str(out_cfg.get("video_cq", out_cfg.get("video_crf", 22))),
        "-look_ahead",
        "0",
        "-async_depth",
        "1",
    ]


def _x264_extra(out_cfg: dict) -> list[str]:
    return [
        "-crf",
        str(out_cfg.get("video_crf", 18)),
        "-preset",
        out_cfg.get("video_preset", "veryfast"),
        "-threads",
        "0",
    ]


def _pick_codec(
    scene: dict,
    hw: HardwareProfile,
    *,
    allow_qsv: bool = True,
) -> tuple[str, list[str]]:
    """Return (codec_name, extra_ffmpeg_args).

    allow_qsv=False skips Quick Sync — needed for OpenGL→FFmpeg pipes on Intel iGPUs,
    where D3D/QSV sessions often fail (device failed -17) while GL holds the device.
    """
    out_cfg = scene.get("output", {})
    forced = out_cfg.get("video_codec")
    # Explicit user override (except legacy default libx264 — treat as auto).
    if forced and forced not in ("auto", "libx264"):
        if forced.endswith("_nvenc"):
            return forced, _nvenc_extra(out_cfg)
        if forced.endswith("_qsv"):
            if not allow_qsv:
                return "libx264", _x264_extra(out_cfg)
            return forced, _qsv_extra(out_cfg)
        return forced, []

    prefer = out_cfg.get("prefer_encoder", "auto")  # auto | nvenc | qsv | cpu

    if prefer == "cpu":
        return "libx264", _x264_extra(out_cfg)

    if prefer in ("auto", "nvenc") and hw.has_nvenc:
        return "h264_nvenc", _nvenc_extra(out_cfg)

    # Fall through to QSV when NVENC was preferred but not usable (Intel-only / old driver).
    if allow_qsv and prefer in ("auto", "qsv", "nvenc") and hw.has_qsv:
        return "h264_qsv", _qsv_extra(out_cfg)

    return "libx264", _x264_extra(out_cfg)


def resolve_frame_pattern(frames_dir: Path) -> str:
    frames = sorted(frames_dir.glob("frame_*.png")) + sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        frames = sorted(frames_dir.glob("*.png")) + sorted(frames_dir.glob("*.jpg"))
    if not frames:
        raise FileNotFoundError(f"No frames in {frames_dir}")

    sample = frames[0]
    stem = sample.stem
    suffix = sample.suffix
    if stem.startswith("frame_"):
        digits = stem.replace("frame_", "", 1)
        pad = len(digits) if digits.isdigit() else 4
        return str(frames_dir / f"frame_%0{pad}d{suffix}")
    return str(frames_dir / f"%0{4}d{suffix}")


def build_ffmpeg_cmd(
    pattern: str,
    output: Path,
    fps: int,
    codec: str,
    extra: list[str],
    hw: HardwareProfile,
) -> list[str]:
    ffmpeg = hw.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-c:v",
        codec,
        *extra,
        "-pix_fmt",
        "nv12" if codec.endswith("_qsv") else "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return cmd


def encode_frames(
    frames_dir: Path,
    output: Path,
    scene: dict,
    hw: HardwareProfile | None = None,
) -> str:
    hw = hw or detect()
    fps = int(scene["fps"])
    codec, extra = _pick_codec(scene, hw)
    pattern = resolve_frame_pattern(frames_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_ffmpeg_cmd(pattern, output, fps, codec, extra, hw)
    print(f"Encode: {codec} | {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if codec == "libx264":
            raise
        print(f"Encode with {codec} failed — falling back to libx264")
        codec, extra = "libx264", _x264_extra(scene.get("output", {}))
        cmd = build_ffmpeg_cmd(pattern, output, fps, codec, extra, hw)
        print(f"Encode: {codec} | {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
    return codec


def _out_pix_fmt(codec: str) -> str:
    # h264_qsv wants nv12; yuv420p after format=nv12 fights the encoder on older MFX.
    return "nv12" if codec.endswith("_qsv") else "yuv420p"


def encode_raw_pipe_cmd(
    output: Path,
    scene: dict,
    width: int,
    height: int,
    hw: HardwareProfile | None = None,
    *,
    force_libx264: bool = False,
) -> tuple[list[str], str]:
    """FFmpeg command that reads raw RGB24 frames from stdin.

    QSV is skipped by default: concurrent OpenGL + Quick Sync on the same Intel
    iGPU commonly hits MFX_ERR_DEVICE_FAILED (-17). Use force_libx264 for fallback.
    """
    hw = hw or detect()
    fps = int(scene["fps"])
    out_cfg = scene.get("output", {})

    if force_libx264:
        codec, extra = "libx264", _x264_extra(out_cfg)
    else:
        # OpenGL pipe: NVENC OK (discrete GPU); QSV clashes with iGPU GL — use CPU.
        codec, extra = _pick_codec(scene, hw, allow_qsv=False)

    ffmpeg = hw.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-c:v",
        codec,
        *extra,
        "-pix_fmt",
        _out_pix_fmt(codec),
        "-movflags",
        "+faststart",
        str(output),
    ]
    return cmd, codec

def main() -> int:
    p = argparse.ArgumentParser(description="GPU/CPU-accelerated frame encode")
    p.add_argument("--frames-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--scene", type=Path, required=True)
    args = p.parse_args()

    with args.scene.open(encoding="utf-8") as f:
        scene = json.load(f)

    hw = detect()
    print(f"Hardware: {hw.summary()}")
    codec = encode_frames(args.frames_dir, args.output, scene, hw)
    print(f"Wrote {args.output} ({codec})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
