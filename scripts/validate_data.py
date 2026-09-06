#!/usr/bin/env python3
"""Validate scene.json + object refs + actions for the animation generator."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from actions import known_actions  # noqa: E402
from factories import known_factories  # noqa: E402


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _check_node(node: dict, data_dir: Path, actions_ok: set[str], factories_ok: set[str]) -> list[str]:
    errors: list[str] = []
    if "ref" in node:
        ref_path = data_dir / "objects" / f"{node['ref']}.json"
        if not ref_path.is_file():
            errors.append(f"missing object ref: {node['ref']} ({ref_path})")
        else:
            try:
                loaded = _load(ref_path)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid JSON in objects/{node['ref']}.json: {exc}")
                return errors
            merged = {**loaded, **{k: v for k, v in node.items() if k != "ref"}}
            if "children" not in node and "children" in loaded:
                merged["children"] = loaded["children"]
            errors.extend(_check_node(merged, data_dir, actions_ok, factories_ok))
        return errors

    ntype = node.get("type", "group")
    if ntype == "factory":
        fname = node.get("factory") or node.get("name")
        if fname not in factories_ok:
            errors.append(f"unknown factory: {fname}")
    elif ntype == "instance":
        errors.extend(
            _check_node(
                {"ref": node["object"], **{k: v for k, v in node.items() if k not in ("type", "object")}},
                data_dir,
                actions_ok,
                factories_ok,
            )
        )

    for child in node.get("children", []):
        errors.extend(_check_node(child, data_dir, actions_ok, factories_ok))
    return errors


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    scene_path = data_dir / "scene.json"

    if not scene_path.is_file():
        print(f"Missing scene config: {scene_path}", file=sys.stderr)
        return 1

    try:
        scene = _load(scene_path)
    except json.JSONDecodeError as exc:
        print(f"Invalid scene.json: {exc}", file=sys.stderr)
        return 1

    required = ["fps", "duration_seconds", "resolution"]
    for key in required:
        if key not in scene:
            print(f"scene.json missing key: {key}", file=sys.stderr)
            return 1

    res = scene["resolution"]
    if not isinstance(res, list) or len(res) != 2:
        print("scene.json resolution must be [width, height]", file=sys.stderr)
        return 1

    total_frames = int(round(float(scene["fps"]) * float(scene["duration_seconds"])))
    if total_frames < 1:
        print("duration_seconds / fps produce zero frames", file=sys.stderr)
        return 1

    materials_path = data_dir / "materials.json"
    if not materials_path.is_file():
        print(f"Missing materials file: {materials_path}", file=sys.stderr)
        return 1

    actions_ok = known_actions()
    factories_ok = known_factories()
    errors: list[str] = []

    for i, node in enumerate(scene.get("objects", [])):
        errors.extend(_check_node(node, data_dir, actions_ok, factories_ok))

    for i, light in enumerate(scene.get("lights", [])):
        errors.extend(_check_node(light, data_dir, actions_ok, factories_ok))

    for i, action in enumerate(scene.get("actions", [])):
        name = action.get("action")
        if not name:
            errors.append(f"actions[{i}] missing 'action'")
        elif name not in actions_ok:
            errors.append(f"actions[{i}] unknown action: {name}")

    if errors:
        print("Validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    n_obj = len(scene.get("objects", []))
    n_act = len(scene.get("actions", []))
    title = scene.get("title", "untitled")
    print(f"Data OK: {title!r}, {n_obj} object roots, {n_act} actions, {total_frames} frames @ {scene['fps']} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
