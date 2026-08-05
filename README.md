# Data-Driven Animation (GPU / CPU accelerated)

Validates `data/`, renders a bar-chart animation, encodes `output/animation.mp4`.

CUDA does not replace Python/Blender for scene setup — it accelerates rendering and encode when the **runtime** machine has NVIDIA/Intel hardware (detected live).

## Tooling by platform

| Platform | How tools are installed |
|----------|-------------------------|
| **Windows (`run.ps1`)** | **winget:** Python (vfox), Blender, FFmpeg · **pip:** `moderngl` `numpy` `pillow` |
| **Nix (`run.sh` / flake)** | **store:** `blender`, `ffmpeg-full`, `python3` + `moderngl` / `numpy` / `pillow` (no pip) |

## Acceleration

| Mode | Workload |
|------|----------|
| `--renderer gpu` | ModernGL OpenGL → raw frames piped to FFmpeg |
| `--renderer blender` | Build `.blend` once, parallel Blender workers (OptiX/CUDA/EEVEE) |
| Encode | **NVENC** → **QSV** → threaded **libx264** |

## Quick start

```powershell
# Windows (admin) — winget + pip
.\run.ps1
.\run.ps1 --renderer gpu
.\run.ps1 --renderer blender --engine cycles
```

```bash
# Linux / macOS / WSL — Nix store packages only
chmod +x run.sh
./run.sh
./run.sh -- --renderer gpu
nix run .#animate -- --renderer auto --workers 4
```

## Layout

| Path | Role |
|------|------|
| `run.ps1` | winget + pip bootstrap |
| `run.sh` / `flake.nix` | Nix store bootstrap (`python3.withPackages`) |
| `scripts/pipeline.py` | Orchestrator |
| `scripts/hw_detect.py` | Runtime NVIDIA/NVENC/QSV/CPU detect |
| `scripts/gpu_native_render.py` | ModernGL path |
| `scripts/render_animation.py` | Blender GPU + chunked workers |
| `scripts/encode_video.py` | NVENC / QSV / libx264 |
| `data/scene.json` | Timing + `acceleration` knobs |

Tune `data/scene.json` → `acceleration.renderer` (`auto` / `gpu` / `blender`) and `output.prefer_encoder` (`auto` / `nvenc` / `qsv` / `cpu`).
