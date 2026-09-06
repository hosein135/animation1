#!/usr/bin/env python3
"""Validate scene.json for the pelican bicycle animation pipeline."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    scene_path = data_dir / "scene.json"

    if not scene_path.is_file():
        print(f"Missing scene config: {scene_path}", file=sys.stderr)
        return 1

    with scene_path.open(encoding="utf-8") as f:
        scene = json.load(f)

    required = ["fps", "duration_seconds", "resolution", "camera", "ride"]
    for key in required:
        if key not in scene:
            print(f"scene.json missing key: {key}", file=sys.stderr)
            return 1

    ride = scene["ride"]
    for key in ("x_start", "x_end", "wheel_radius"):
        if key not in ride:
            print(f"scene.json ride missing key: {key}", file=sys.stderr)
            return 1

    res = scene["resolution"]
    if not isinstance(res, list) or len(res) != 2:
        print("scene.json resolution must be [width, height]", file=sys.stderr)
        return 1

    total_frames = int(round(float(scene["fps"]) * float(scene["duration_seconds"])))
    if total_frames < 1:
        print("duration_seconds / fps produce zero frames", file=sys.stderr)
        return 1

    print(
        f"Data OK: pelican ride {ride['x_start']}→{ride['x_end']}, "
        f"{total_frames} frames @ {scene['fps']} fps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
