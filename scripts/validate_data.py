#!/usr/bin/env python3
"""Validate structured inputs under data/ before rendering."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    scene_path = data_dir / "scene.json"
    csv_path = data_dir / "values.csv"

    if not scene_path.is_file():
        print(f"Missing scene config: {scene_path}", file=sys.stderr)
        return 1
    if not csv_path.is_file():
        print(f"Missing values CSV: {csv_path}", file=sys.stderr)
        return 1

    with scene_path.open(encoding="utf-8") as f:
        scene = json.load(f)

    required = ["fps", "duration_seconds", "resolution", "camera", "bar"]
    for key in required:
        if key not in scene:
            print(f"scene.json missing key: {key}", file=sys.stderr)
            return 1

    df = pd.read_csv(csv_path)
    needed_cols = {"frame_label", "value", "r", "g", "b"}
    missing = needed_cols - set(df.columns)
    if missing:
        print(f"values.csv missing columns: {sorted(missing)}", file=sys.stderr)
        return 1
    if df.empty:
        print("values.csv has no rows", file=sys.stderr)
        return 1
    if (df["value"] < 0).any():
        print("values.csv contains negative values", file=sys.stderr)
        return 1

    total_frames = int(round(float(scene["fps"]) * float(scene["duration_seconds"])))
    print(f"Data OK: {len(df)} series points, {total_frames} frames @ {scene['fps']} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
