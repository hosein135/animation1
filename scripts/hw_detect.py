#!/usr/bin/env python3
"""Runtime hardware detection - never hardcodes host topology."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HardwareProfile:
    cpu_count: int
    cpu_name: str = "Unknown CPU"
    nvidia_gpus: list[str] = field(default_factory=list)
    intel_gpus: list[str] = field(default_factory=list)
    other_gpus: list[str] = field(default_factory=list)
    has_cuda: bool = False
    has_optix: bool = False
    has_nvenc: bool = False
    has_qsv: bool = False
    ffmpeg_path: str | None = None
    blender_path: str | None = None
    nvidia_smi: str | None = None
    nvenc_skip_reason: str | None = None
    qsv_skip_reason: str | None = None

    @property
    def recommended_blender_workers(self) -> int:
        """Scale frame workers from live CPU/GPU counts."""
        if len(self.nvidia_gpus) >= 2:
            return min(len(self.nvidia_gpus), max(1, self.cpu_count // 2))
        if self.nvidia_gpus:
            # Single GPU: prefer 1-2 Blender processes to avoid context thrash.
            return max(1, min(2, self.cpu_count))
        return max(1, min(self.cpu_count, 8))

    @property
    def recommended_io_workers(self) -> int:
        return max(2, min(16, self.cpu_count * 2))

    def summary(self) -> str:
        nvidia = ", ".join(self.nvidia_gpus) if self.nvidia_gpus else "none"
        intel = ", ".join(self.intel_gpus) if self.intel_gpus else "none"
        return (
            f"CPU={self.cpu_name} ({self.cpu_count} threads) | "
            f"NVIDIA=[{nvidia}] IntelGPU=[{intel}] "
            f"CUDA={self.has_cuda} OptiX={self.has_optix} "
            f"NVENC={self.has_nvenc} QSV={self.has_qsv}"
        )


def _run(cmd: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _ffmpeg_encoders(ffmpeg: str) -> set[str]:
    proc = _run([ffmpeg, "-hide_banner", "-encoders"])
    if not proc or proc.returncode != 0:
        return set()
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s*\S+\s+(\S+)\s+", line)
        if m:
            names.add(m.group(1))
    return names


def _nvidia_gpus(nvidia_smi: str) -> list[str]:
    proc = _run([nvidia_smi, "-L"])
    if not proc or proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("GPU ")]


def _probe_encoder(ffmpeg: str, codec: str, extra: list[str]) -> bool:
    """True only if FFmpeg can open the encoder on this machine (not merely list it)."""
    with tempfile.TemporaryDirectory(prefix="anim_enc_probe_") as tmp:
        out = Path(tmp) / "probe.mp4"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.04",
            "-frames:v",
            "1",
            "-c:v",
            codec,
            *extra,
            "-f",
            "mp4",
            "-y",
            str(out),
        ]
        proc = _run(cmd, timeout=20.0)
        if not proc or proc.returncode != 0:
            return False
        return out.is_file() and out.stat().st_size > 0


def _cpu_name() -> str:
    if sys.platform == "win32":
        proc = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)",
            ]
        )
        if proc and proc.returncode == 0 and proc.stdout.strip():
            return re.sub(r"\s+", " ", proc.stdout.strip())
    proc = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if proc and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine() or "Unknown CPU"


def _windows_video_controllers() -> list[str]:
    if sys.platform != "win32":
        return []
    proc = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
        ]
    )
    if not proc or proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _classify_adapters(names: list[str], nvidia_from_smi: list[str]) -> tuple[list[str], list[str], list[str]]:
    nvidia: list[str] = list(nvidia_from_smi)
    intel: list[str] = []
    other: list[str] = []
    seen_nvidia = {n.lower() for n in nvidia}

    for name in names:
        low = name.lower()
        if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx " in low or "gtx " in low:
            if name.lower() not in seen_nvidia and not any(name.lower() in s.lower() for s in seen_nvidia):
                nvidia.append(name)
                seen_nvidia.add(name.lower())
            continue
        if "intel" in low:
            intel.append(name)
            continue
        other.append(name)
    return nvidia, intel, other


def detect() -> HardwareProfile:
    cpu = os.cpu_count() or 1
    profile = HardwareProfile(
        cpu_count=cpu,
        cpu_name=_cpu_name(),
        ffmpeg_path=shutil.which("ffmpeg"),
        blender_path=shutil.which("blender"),
        nvidia_smi=shutil.which("nvidia-smi"),
    )

    smi_gpus: list[str] = []
    if profile.nvidia_smi:
        smi_gpus = _nvidia_gpus(profile.nvidia_smi)
        profile.has_cuda = bool(smi_gpus)
    elif shutil.which("nvidia-smi") is None:
        profile.nvenc_skip_reason = "nvidia-smi not found (NVIDIA driver / CUDA toolkit not on PATH)"

    adapters = _windows_video_controllers()
    nvidia, intel, other = _classify_adapters(adapters, smi_gpus)
    # Linux/mac fallback: keep nvidia-smi list even without CIM adapters.
    if not adapters and smi_gpus:
        nvidia = smi_gpus
    profile.nvidia_gpus = nvidia
    profile.intel_gpus = intel
    profile.other_gpus = other
    if profile.nvidia_gpus:
        profile.has_cuda = True

    enc: set[str] = set()
    if profile.ffmpeg_path:
        enc = _ffmpeg_encoders(profile.ffmpeg_path)
        if "h264_nvenc" in enc and profile.nvidia_gpus:
            profile.has_nvenc = _probe_encoder(
                profile.ffmpeg_path,
                "h264_nvenc",
                ["-preset", "p4", "-cq", "28", "-b:v", "0"],
            )
            if not profile.has_nvenc:
                profile.nvenc_skip_reason = (
                    "FFmpeg lists h264_nvenc but encode probe failed "
                    "(driver / session / outdated FFmpeg build)"
                )
        elif profile.nvidia_gpus and "h264_nvenc" not in enc:
            profile.nvenc_skip_reason = "FFmpeg build has no h264_nvenc encoder"
        elif not profile.nvidia_gpus:
            profile.nvenc_skip_reason = profile.nvenc_skip_reason or "No NVIDIA GPU detected"

        if "h264_qsv" in enc:
            # Match production flags; look_ahead=1 falsely passes then dies mid-encode on HD 6xx.
            profile.has_qsv = _probe_encoder(
                profile.ffmpeg_path,
                "h264_qsv",
                ["-vf", "format=nv12", "-global_quality", "28", "-look_ahead", "0", "-async_depth", "1"],
            )
            if not profile.has_qsv:
                profile.qsv_skip_reason = (
                    "FFmpeg lists h264_qsv but encode probe failed "
                    "(Intel iGPU driver / Quick Sync unavailable)"
                )
        elif profile.intel_gpus:
            profile.qsv_skip_reason = "FFmpeg build has no h264_qsv encoder"
        else:
            profile.qsv_skip_reason = "No Intel GPU detected for Quick Sync"
    else:
        profile.nvenc_skip_reason = "ffmpeg not found on PATH"
        profile.qsv_skip_reason = "ffmpeg not found on PATH"

    # Exact OptiX availability is confirmed inside Blender; NVIDIA present implies try OptiX.
    profile.has_optix = profile.has_cuda
    return profile


def _prefer_encoder(scene: dict) -> str:
    return str(scene.get("output", {}).get("prefer_encoder", "auto")).lower()


def _planned_codec(hw: HardwareProfile, scene: dict, renderer: str) -> tuple[str, str]:
    """Return (codec, why) matching encode_video._pick_codec policy."""
    prefer = _prefer_encoder(scene)
    pipe_mode = renderer == "gpu"
    allow_qsv = not pipe_mode

    if prefer == "cpu":
        return "libx264", "output.prefer_encoder=cpu"

    if prefer in ("auto", "nvenc") and hw.has_nvenc:
        return "h264_nvenc", "NVENC available and selected"

    if prefer == "nvenc" and not hw.has_nvenc:
        # Fall through like encode_video
        pass

    if allow_qsv and prefer in ("auto", "qsv", "nvenc") and hw.has_qsv:
        return "h264_qsv", "QSV available (NVENC unused or unavailable)"

    if pipe_mode and hw.has_qsv and prefer in ("auto", "qsv", "nvenc"):
        return (
            "libx264",
            "OpenGL→FFmpeg pipe skips QSV (Intel iGPU GL + Quick Sync often conflict)",
        )

    return "libx264", "software x264 (no usable GPU encoder for this path)"


def resolve_renderer(scene: dict, renderer_arg: str | None = None) -> str:
    accel = scene.get("acceleration", {})
    renderer = (renderer_arg or accel.get("renderer") or "auto").lower()
    if renderer != "auto":
        return renderer
    try:
        import moderngl  # noqa: F401
        import numpy  # noqa: F401

        return "gpu"
    except ImportError:
        return "blender"


def involvement_rows(
    hw: HardwareProfile,
    scene: dict | None = None,
    renderer: str = "auto",
) -> list[dict[str, str]]:
    """Structured involvement for NVIDIA / Intel GPU / CPU."""
    scene = scene or {}
    resolved = renderer if renderer in ("gpu", "blender") else resolve_renderer(scene, "auto")

    codec, codec_why = _planned_codec(hw, scene, resolved)
    prefer = _prefer_encoder(scene)
    engine = str(scene.get("acceleration", {}).get("blender_engine", "auto")).lower()
    prefer_cycles_gpu = bool(scene.get("acceleration", {}).get("prefer_cycles_gpu", False))

    rows: list[dict[str, str]] = []

    # --- NVIDIA (external / discrete) ---
    nvidia_label = "External / discrete NVIDIA GPU"
    if not hw.nvidia_gpus:
        why = "No NVIDIA GPU detected on this machine"
        if hw.nvenc_skip_reason and "nvidia gpu" not in hw.nvenc_skip_reason.lower():
            why = f"{why}; also: {hw.nvenc_skip_reason}"
        rows.append(
            {
                "device": nvidia_label,
                "status": "NOT involved",
                "detail": why,
            }
        )
    else:
        names = "; ".join(hw.nvidia_gpus)
        roles: list[str] = []
        if resolved == "gpu":
            roles.append("OpenGL render (ModernGL may place context on discrete GPU when available)")
        elif resolved == "blender":
            if prefer_cycles_gpu or engine in ("auto", "cycles"):
                roles.append("Blender Cycles GPU (CUDA/OptiX when Blender enables it)")
            else:
                roles.append("Blender present but Cycles GPU not preferred (EEVEE/CPU path possible)")
        if codec == "h264_nvenc":
            roles.append(f"encode via NVENC ({codec_why})")
        elif not hw.has_nvenc:
            roles.append(f"encode: NVENC unused - {hw.nvenc_skip_reason or 'unavailable'}")
        else:
            roles.append(f"encode: NVENC present but not selected ({codec_why}; prefer={prefer})")
        rows.append(
            {
                "device": nvidia_label,
                "status": "INVOLVED",
                "detail": f"{names} | " + " ; ".join(roles),
            }
        )

    # --- Intel integrated / Arc GPU ---
    intel_label = "Internal Intel GPU (iGPU / Arc)"
    if not hw.intel_gpus:
        why = "No Intel GPU adapter detected"
        if hw.qsv_skip_reason and "intel gpu" not in hw.qsv_skip_reason.lower():
            why = f"{why}; also: {hw.qsv_skip_reason}"
        rows.append(
            {
                "device": intel_label,
                "status": "NOT involved",
                "detail": why,
            }
        )
    else:
        names = "; ".join(hw.intel_gpus)
        if codec == "h264_qsv":
            rows.append(
                {
                    "device": intel_label,
                    "status": "INVOLVED",
                    "detail": f"{names} | encode via Quick Sync (h264_qsv) - {codec_why}",
                }
            )
        elif resolved == "gpu" and hw.has_qsv:
            rows.append(
                {
                    "device": intel_label,
                    "status": "NOT involved",
                    "detail": (
                        f"{names} | detected + QSV works, but OpenGL→FFmpeg pipe skips QSV "
                        "(Intel iGPU GL session often fights Quick Sync / MFX_ERR_DEVICE_FAILED). "
                        "May still host the OpenGL context if NVIDIA is absent."
                    ),
                }
            )
        elif not hw.has_qsv:
            rows.append(
                {
                    "device": intel_label,
                    "status": "NOT involved",
                    "detail": f"{names} | {hw.qsv_skip_reason or 'Quick Sync encode probe failed'}",
                }
            )
        else:
            rows.append(
                {
                    "device": intel_label,
                    "status": "NOT involved",
                    "detail": (
                        f"{names} | QSV available but not selected "
                        f"(codec={codec}; {codec_why}; prefer={prefer})"
                    ),
                }
            )

    # --- CPU ---
    is_intel_cpu = "intel" in hw.cpu_name.lower()
    cpu_label = "Intel CPU" if is_intel_cpu else f"CPU ({hw.cpu_name.split()[0] if hw.cpu_name else 'host'})"
    cpu_roles = [
        f"{hw.cpu_name} - {hw.cpu_count} logical threads",
        "pipeline orchestration (Python / process spawn)",
    ]
    if resolved == "blender":
        cpu_roles.append(
            f"Blender workers up to ~{hw.recommended_blender_workers} "
            "(CPU BVH / hybrid tiles even when Cycles uses GPU)"
        )
    if resolved == "gpu":
        cpu_roles.append("hosts ModernGL process + feeds raw frames to FFmpeg")
    if codec == "libx264":
        cpu_roles.append(f"encode via threaded libx264 - {codec_why}")
    else:
        cpu_roles.append(f"encode offloaded to GPU ({codec}); CPU still runs FFmpeg mux/IO")

    rows.append(
        {
            "device": cpu_label,
            "status": "INVOLVED",
            "detail": " | ".join(cpu_roles),
        }
    )

    if hw.other_gpus:
        rows.append(
            {
                "device": "Other GPU adapter(s)",
                "status": "Detected only",
                "detail": "; ".join(hw.other_gpus) + " - not used by NVENC/QSV path",
            }
        )

    return rows


def format_involvement_report(
    hw: HardwareProfile,
    scene: dict | None = None,
    renderer: str = "auto",
) -> str:
    scene = scene or {}
    resolved = renderer if renderer in ("gpu", "blender") else resolve_renderer(scene, "auto")
    codec, codec_why = _planned_codec(hw, scene, resolved)

    lines = [
        "=== Hardware inventory ===",
        f"  CPU:    {hw.cpu_name} ({hw.cpu_count} threads)",
        f"  NVIDIA: {', '.join(hw.nvidia_gpus) if hw.nvidia_gpus else '(none)'}",
        f"  Intel:  {', '.join(hw.intel_gpus) if hw.intel_gpus else '(none)'}",
    ]
    if hw.other_gpus:
        lines.append(f"  Other:  {', '.join(hw.other_gpus)}")
    lines += [
        f"  Caps:   CUDA={hw.has_cuda} OptiX={hw.has_optix} NVENC={hw.has_nvenc} QSV={hw.has_qsv}",
        f"  Plan:   renderer={resolved} | encoder={codec} ({codec_why})",
        "=== Involvement (this run) ===",
    ]
    for row in involvement_rows(hw, scene, resolved):
        lines.append(f"  [{row['status']}] {row['device']}")
        lines.append(f"           {row['detail']}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Detect GPUs/CPU and print involvement for this project")
    p.add_argument("--scene", type=Path, default=None, help="Path to scene.json (optional)")
    p.add_argument(
        "--renderer",
        choices=("auto", "gpu", "blender"),
        default="auto",
        help="Renderer mode used to decide involvement",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = p.parse_args()

    scene: dict = {}
    if args.scene and args.scene.is_file():
        with args.scene.open(encoding="utf-8") as f:
            scene = json.load(f)

    hw = detect()
    resolved = resolve_renderer(scene, args.renderer)
    if args.json:
        payload = {
            "summary": hw.summary(),
            "cpu_name": hw.cpu_name,
            "cpu_count": hw.cpu_count,
            "nvidia_gpus": hw.nvidia_gpus,
            "intel_gpus": hw.intel_gpus,
            "other_gpus": hw.other_gpus,
            "has_cuda": hw.has_cuda,
            "has_optix": hw.has_optix,
            "has_nvenc": hw.has_nvenc,
            "has_qsv": hw.has_qsv,
            "nvenc_skip_reason": hw.nvenc_skip_reason,
            "qsv_skip_reason": hw.qsv_skip_reason,
            "renderer": resolved,
            "involvement": involvement_rows(hw, scene, resolved),
            "report": format_involvement_report(hw, scene, resolved),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_involvement_report(hw, scene, resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
