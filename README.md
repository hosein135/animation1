# Animation Generator (GPU / CPU accelerated)

Declarative Blender animation pipeline: describe objects and actions in JSON, render with parallel Blender workers, encode with hardware-aware FFmpeg.

Default data recreates a pelican riding a bicycle along a sunset boardwalk (inspired by [simonw/gpt-6-astra-blender-pelican-bicycle](https://github.com/simonw/gpt-6-astra-blender-pelican-bicycle)): the pelican bikes, takes off, looks at the camera and talks (no audio), then lands and bikes on — all in 10 seconds. The generator is scene-agnostic.

## Tooling by platform

| Platform | How tools are installed |
|----------|-------------------------|
| **Windows (`run.cmd` / `run.ps1`)** | **winget:** Python (vfox), Blender, FFmpeg · live hardware inventory |
| **Nix (`run.sh` / flake)** | **store:** `blender`, `ffmpeg-full`, `python3` |

## Acceleration

| Mode | Workload |
|------|----------|
| `--renderer blender` + **EEVEE** (default) | Fast GPU raster, many parallel **Blender OS processes**, JPEG frames |
| `--engine cycles` | OptiX/CUDA + CPU hybrid; **1 process pinned per NVIDIA GPU** |
| Encode | **NVENC** → **QSV** → all-core **libx264** |

## Quick start

```powershell
# Windows
.\run.cmd
.\run.ps1 --renderer blender --engine eevee
.\run.ps1 --engine cycles --workers 2
```

```bash
# Linux / macOS / WSL
chmod +x run.sh
./run.sh
./run.sh -- --renderer blender --engine cycles
nix run .#animate -- --renderer blender --workers 2
```

## Authoring a scene

| Path | Role |
|------|------|
| `data/scene.json` | Timing, camera, lights, object placements, **actions**, render/encode knobs |
| `data/materials.json` | Shared material palette (`color`, `metallic`, `roughness`, optional `emission`) |
| `data/objects/*.json` | Reusable object graphs (primitives, groups, factory refs) |

### Object types

`group` / `empty`, `sphere`, `box`, `rod` / `cylinder`, `torus`, `path`, `mesh`, `camera`, `light`, `factory`, `instance` / `ref`

Procedural pieces (wheels, palms, waves, pouch meshes, …) use `"type": "factory"` with a name from `scripts/factories.py`.

### Actions

Declared under `scene.json` → `actions`. Built-ins in `scripts/actions.py`:

| Action | Purpose |
|--------|---------|
| `translate` | Keyframe location A→B |
| `rotate` | Keyframe euler rotation |
| `spin` | Continuous spin (or from `distance` / `radius`) |
| `bob` | Sinusoidal location offset |
| `flutter` | Sinusoidal rotation sway |
| `follow_axis` | Offset one axis over time |
| `look_at` | Orient toward points |
| `keyframes` | Multi-key location / rotation / scale / ortho zoom |
| `talk` | Visual speech pulse (scale, no audio) |
| `parent` | Parent keep-transform |
| `bob_matching` | Bob all objects with a name prefix |
| `set_interpolation` | LINEAR / BEZIER on fcurves |

Example placement:

```json
{
  "objects": [
    { "ref": "ride_root", "name": "RideRoot", "children": [
      { "ref": "bicycle", "name": "Bicycle" },
      { "ref": "pelican", "name": "Pelican" }
    ]},
    { "ref": "boardwalk" },
    { "ref": "environment" }
  ],
  "actions": [
    { "action": "translate", "target": "RideRoot", "from": [-3.2, 0, 0], "to": [3.8, 0, 0] }
  ]
}
```

Point the pipeline at another data directory with `--data-dir` / `DATA_DIR`.

## Layout

| Path | Role |
|------|------|
| `run.cmd` / `run.ps1` | Windows bootstrap + pipeline |
| `run.sh` / `flake.nix` | Nix bootstrap |
| `scripts/pipeline.py` | Orchestrator + timing/hardware summary |
| `scripts/scene_builder.py` | JSON → Blender scene |
| `scripts/blender_prims.py` | Mesh / material primitives |
| `scripts/actions.py` | Animation action registry |
| `scripts/factories.py` | Procedural object factories |
| `scripts/render_animation.py` | Blender GPU + chunked workers |
| `scripts/encode_video.py` | NVENC / QSV / libx264 |
| `scripts/validate_data.py` | Schema + ref/action checks |
| `data/` | Scene, materials, objects |

Tune `acceleration.blender_engine` and `output.prefer_encoder` (`auto` / `nvenc` / `qsv` / `cpu`) in `data/scene.json`.

Output: `output/animation.mp4` (and `output/scene.blend` after the build step).
