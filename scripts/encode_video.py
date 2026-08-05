#!/usr/bin/env python3
"""Encode rendered PNG frames into an MP4 with FFmpeg."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--frames-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--scene", type=Path, required=True)
    args = p.parse_args()

    with args.scene.open(encoding="utf-8") as f:
        scene = json.load(f)

    fps = int(scene["fps"])
    out_cfg = scene.get("output", {})
    codec = out_cfg.get("video_codec", "libx264")
    crf = str(out_cfg.get("video_crf", 18))
    preset = out_cfg.get("video_preset", "medium")

    frames = sorted(args.frames_dir.glob("frame_*.png"))
    if not frames:
        # Blender may write frame_0001.png style
        frames = sorted(args.frames_dir.glob("*.png"))
    if not frames:
        print(f"No PNG frames found in {args.frames_dir}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Prefer numbered pattern if Blender used #### padding
    sample = frames[0].name
    if "frame_" in sample:
        pattern = str(args.frames_dir / "frame_%04d.png")
        # Detect actual padding from filenames
        # Blender default: frame_0001.png when filepath ends with frame_
        first = frames[0].stem.replace("frame_", "")
        pad = len(first) if first.isdigit() else 4
        pattern = str(args.frames_dir / f"frame_%0{pad}d.png")
    else:
        # Fallback: concat demuxer via pipe is overkill; use glob with ffmpeg
        pattern = str(args.frames_dir / "frame_%04d.png")

    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-c:v",
        codec,
        "-pix_fmt",
        "yuv420p",
        "-crf",
        crf,
        "-preset",
        preset,
        str(args.output),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
