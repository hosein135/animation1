#!/usr/bin/env python3
"""Validate structured inputs under data/ before rendering."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path


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

    needed_cols = {"frame_label", "value", "r", "g", "b"}
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("values.csv has no header", file=sys.stderr)
            return 1
        missing = needed_cols - set(reader.fieldnames)
        if missing:
            print(f"values.csv missing columns: {sorted(missing)}", file=sys.stderr)
            return 1

        rows = list(reader)
        if not rows:
            print("values.csv has no rows", file=sys.stderr)
            return 1

        for i, row in enumerate(rows, start=2):
            try:
                value = float(row["value"])
            except (TypeError, ValueError):
                print(f"values.csv row {i}: invalid value", file=sys.stderr)
                return 1
            if value < 0:
                print("values.csv contains negative values", file=sys.stderr)
                return 1

    total_frames = int(round(float(scene["fps"]) * float(scene["duration_seconds"])))
    print(f"Data OK: {len(rows)} series points, {total_frames} frames @ {scene['fps']} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
