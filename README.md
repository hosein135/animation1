# Pelican Coastal Parade (GPU / CPU accelerated)

Animated Blender remake of [simonw/gpt-6-astra-blender-pelican-bicycle](https://github.com/simonw/gpt-6-astra-blender-pelican-bicycle): a pelican riding a bicycle along a sunset boardwalk. Scene build follows that project's final coastal-parade look; this repo adds ride animation, parallel Blender workers, and hardware-aware encode.

CUDA does not replace Python/Blender for scene setup — it accelerates Cycles and encode when the **runtime** machine has NVIDIA/Intel hardware (detected live).

## Tooling by platform

| Platform | How tools are installed |
|----------|-------------------------|
| **Windows (`run.cmd` / `run.ps1`)** | **winget:** Python (vfox), Blender, FFmpeg · live hardware inventory (CPU cores/RAM, DXGI LUIDs, skip virtual adapters) |
| **Nix (`run.sh` / flake)** | **store:** `blender`, `ffmpeg-full`, `python3` |

## Acceleration

| Mode | Workload |
|------|----------|
| `--renderer blender` + **EEVEE** (default) | Fast GPU raster, many parallel **Blender OS processes**, JPEG frames |
| `--engine cycles` | OptiX/CUDA + CPU hybrid; **1 process pinned per NVIDIA GPU** via `CUDA_VISIBLE_DEVICES` |
| Encode | **NVENC** → **QSV** → all-core **libx264** (`-threads 0`) |

Live **progress bars** on stderr: `Render` counts frame files as workers write them; `Encode` reads FFmpeg `-progress`.

Workers/threads auto-scale from live CPU/GPU counts (`blender_workers: 0`). Override with `--workers N` or `data/scene.json`. Headless Blender uses `--factory-startup -noaudio` and raised process priority.

## Quick start

```powershell
# Windows — double-click run.cmd, or from an admin shell:
.\run.cmd
.\run.ps1 --renderer blender --engine eevee
.\run.ps1 --engine cycles --workers 2
```

```bash
# Linux / macOS / WSL — Nix store packages only
chmod +x run.sh
./run.sh
./run.sh -- --renderer blender --engine cycles
nix run .#animate -- --renderer blender --workers 2
```

## Layout

| Path | Role |
|------|------|
| `run.cmd` | Elevated launcher for `run.ps1` (same pattern as `windows_search/setup.cmd`) |
| `run.ps1` | winget + vfox bootstrap, host inventory, pipeline |
| `run.sh` / `flake.nix` | Nix store bootstrap |
| `scripts/progress.py` | Render/encode progress bars (stdlib) |
| `scripts/hw_detect.py` | Runtime NVIDIA/NVENC/QSV/CPU detect |
| `scripts/pelican_build.py` | Pelican + bicycle + coastal set + ride keyframes |
| `scripts/render_animation.py` | Blender GPU + chunked workers |
| `scripts/encode_video.py` | NVENC / QSV / libx264 |
| `data/scene.json` | Timing, ride path, `acceleration` knobs |

Tune `data/scene.json` → `ride.x_start` / `ride.x_end`, `acceleration.blender_engine`, and `output.prefer_encoder` (`auto` / `nvenc` / `qsv` / `cpu`).

Output: `output/animation.mp4` (and `output/scene.blend` after the build step).
