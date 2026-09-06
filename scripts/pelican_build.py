"""
Back-compat entry point.

The pelican parade is now built declaratively from data/scene.json + data/objects/.
Prefer scene_builder.build_animation(data_dir, scene_cfg).
"""

from __future__ import annotations

from pathlib import Path


def build_pelican_animation(scene_cfg: dict, data_dir: Path | None = None) -> None:
    from scene_builder import build_animation

    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[1] / "data"
    build_animation(Path(data_dir), scene_cfg)
