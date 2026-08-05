# Data-Driven Animation (Nix + Blender + FFmpeg)

Command-line pipeline that bootstraps **curl** and **Nix**, enters a flake pinned to **nixos-25.05**, then renders a short bar-chart animation from `data/` into `output/`.

## Requirements

- Linux (or macOS with Nix). On Windows, use WSL2.
- Ability to install packages (sudo) if curl/Nix are missing.

## Quick start

```bash
chmod +x run.sh
./run.sh
```

What `run.sh` does:

1. Checks for `curl`; installs it via apt/dnf/yum/pacman/zypper/apk/emerge/xbps/brew if needed.
2. Checks for Nix; installs it (daemon on systemd Linux, single-user otherwise).
3. Enables flakes (`experimental-features = nix-command flakes`).
4. Runs `nix run .#animate` using **nixpkgs nixos-25.05**.

## Manual Nix commands

```bash
nix develop          # shell with python3, blender, ffmpeg
nix run .#animate    # validate data → Blender frames → FFmpeg MP4
```

## Layout

| Path | Role |
|------|------|
| `run.sh` | Bootstrap + launch |
| `flake.nix` | Pinned nixos-25.05 env (Python/pandas/numpy, Blender, FFmpeg) |
| `data/scene.json` | FPS, resolution, camera, lighting |
| `data/values.csv` | Monthly values + RGB colors for each bar |
| `scripts/validate_data.py` | CSV/JSON checks |
| `scripts/render_animation.py` | Headless Blender animation |
| `scripts/encode_video.py` | FFmpeg MP4 encode |
| `output/` | Frames + `animation.mp4` |

## Customizing

Edit `data/values.csv` (labels, values, colors) and/or `data/scene.json` (timing, camera, bounce), then re-run `./run.sh` or `nix run .#animate`.
