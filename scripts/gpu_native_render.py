#!/usr/bin/env python3
"""
GPU-native bar-chart renderer (ModernGL / OpenGL).

Deps:
  Nix  — python3Packages.{moderngl,numpy,pillow} from the store
  Win  — pip install moderngl numpy pillow (via run.ps1)

Streams raw RGB into FFmpeg (NVENC/QSV/libx264). Falls back to JPEG + thread pool.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from encode_video import encode_frames, encode_raw_pipe_cmd  # noqa: E402
from hw_detect import detect  # noqa: E402


VERT = """
#version 330
in vec3 in_pos;
in vec3 in_normal;
uniform mat4 mvp;
uniform mat3 normal_mat;
out vec3 v_normal;
out vec3 v_pos;
void main() {
    v_pos = in_pos;
    v_normal = normalize(normal_mat * in_normal);
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""

FRAG = """
#version 330
in vec3 v_normal;
in vec3 v_pos;
uniform vec3 u_color;
uniform vec3 u_light_dir;
uniform vec3 u_eye;
uniform vec3 u_ambient;
out vec4 f_color;
void main() {
    vec3 n = normalize(v_normal);
    vec3 l = normalize(-u_light_dir);
    float ndotl = max(dot(n, l), 0.0);
    vec3 view = normalize(u_eye - v_pos);
    vec3 halfv = normalize(l + view);
    float spec = pow(max(dot(n, halfv), 0.0), 32.0) * 0.25;
    vec3 col = u_color * (u_ambient + ndotl * 0.85) + vec3(spec);
    f_color = vec4(col, 1.0);
}
"""


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def bounce_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    n1 = 7.5625
    d1 = 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def load_scene(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_values(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "label": row["frame_label"],
                    "value": float(row["value"]),
                    "color": (float(row["r"]), float(row["g"]), float(row["b"])),
                }
            )
    return rows


def bar_heights(series: list[dict], scene: dict, frame: int, frame_end: int) -> list[float]:
    bar_cfg = scene["bar"]
    max_height = float(bar_cfg.get("max_height", 4.0))
    use_bounce = bool(bar_cfg.get("bounce", True))
    max_value = max(item["value"] for item in series) or 1.0
    n = len(series)
    heights: list[float] = []
    for i, item in enumerate(series):
        target_h = (item["value"] / max_value) * max_height
        start_frame = 1 + int((i / max(n, 1)) * (frame_end * 0.35))
        end_frame = min(frame_end, start_frame + int(frame_end * 0.55))
        if frame < start_frame:
            heights.append(0.001)
            continue
        if frame <= end_frame:
            t = (frame - start_frame) / max(1, end_frame - start_frame)
            eased = bounce_out(t) if use_bounce else ease_out_cubic(t)
            heights.append(max(0.001, target_h * eased))
            continue
        phase = (frame - end_frame) / max(1, frame_end - end_frame)
        bob = 1.0 + 0.03 * math.sin(phase * math.pi * 2.0)
        heights.append(target_h * bob)
    return heights


def perspective(fovy_deg: float, aspect: float, near: float, far: float):
    import numpy as np

    f = 1.0 / math.tan(math.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def look_at_matrix(eye, target, up):
    import numpy as np

    eye = np.asarray(eye, dtype="f4")
    target = np.asarray(target, dtype="f4")
    up = np.asarray(up, dtype="f4")
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4, dtype="f4")
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    t = np.eye(4, dtype="f4")
    t[:3, 3] = -eye
    return m @ t


def translation(x, y, z):
    import numpy as np

    m = np.eye(4, dtype="f4")
    m[0, 3] = x
    m[1, 3] = y
    m[2, 3] = z
    return m


def scale_mat(sx, sy, sz):
    import numpy as np

    m = np.eye(4, dtype="f4")
    m[0, 0] = sx
    m[1, 1] = sy
    m[2, 2] = sz
    return m


def cylinder_mesh(radius: float, segments: int = 24):
    import numpy as np

    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []

    def add_vertex(px, py, pz, nx, ny, nz):
        positions.extend((px, py, pz))
        normals.extend((nx, ny, nz))
        return len(positions) // 3 - 1

    ring_bottom: list[int] = []
    ring_top: list[int] = []
    for i in range(segments):
        ang = (i / segments) * math.tau
        c, s = math.cos(ang), math.sin(ang)
        x, y = radius * c, radius * s
        ring_bottom.append(add_vertex(x, y, 0.0, c, s, 0.0))
        ring_top.append(add_vertex(x, y, 1.0, c, s, 0.0))

    for i in range(segments):
        j = (i + 1) % segments
        a, b, c, d = ring_bottom[i], ring_bottom[j], ring_top[j], ring_top[i]
        indices.extend((a, b, c, a, c, d))

    bottom_center = add_vertex(0.0, 0.0, 0.0, 0.0, 0.0, -1.0)
    top_center = add_vertex(0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    bottom_rim: list[int] = []
    top_rim: list[int] = []
    for i in range(segments):
        ang = (i / segments) * math.tau
        c, s = math.cos(ang), math.sin(ang)
        x, y = radius * c, radius * s
        bottom_rim.append(add_vertex(x, y, 0.0, 0.0, 0.0, -1.0))
        top_rim.append(add_vertex(x, y, 1.0, 0.0, 0.0, 1.0))
    for i in range(segments):
        j = (i + 1) % segments
        indices.extend((bottom_center, bottom_rim[j], bottom_rim[i]))
        indices.extend((top_center, top_rim[i], top_rim[j]))

    pos = np.asarray(positions, dtype="f4")
    nor = np.asarray(normals, dtype="f4")
    idx = np.asarray(indices, dtype="i4")
    interleaved = np.empty((pos.shape[0] // 3, 6), dtype="f4")
    interleaved[:, 0:3] = pos.reshape(-1, 3)
    interleaved[:, 3:6] = nor.reshape(-1, 3)
    return interleaved, idx


def ground_mesh(size: float = 40.0):
    import numpy as np

    h = size / 2.0
    verts = np.array(
        [
            [-h, -h, 0, 0, 0, 1],
            [h, -h, 0, 0, 0, 1],
            [h, h, 0, 0, 0, 1],
            [-h, -h, 0, 0, 0, 1],
            [h, h, 0, 0, 0, 1],
            [-h, h, 0, 0, 0, 1],
        ],
        dtype="f4",
    )
    idx = np.arange(6, dtype="i4")
    return verts, idx


def require_deps():
    try:
        import moderngl  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "GPU renderer needs moderngl + numpy.\n"
            "  Nix: provided by flake pythonEnv\n"
            "  Win: run.ps1 installs via pip\n"
            f"Detail: {exc}"
        ) from exc


def render_animation(
    data_dir: Path,
    output_dir: Path,
    pipe_to_ffmpeg: bool = True,
) -> Path:
    require_deps()
    import moderngl
    import numpy as np

    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore

    scene = load_scene(data_dir / "scene.json")
    series = load_values(data_dir / "values.csv")
    if not series:
        raise RuntimeError("No rows in values.csv")

    hw = detect()
    fps = int(scene["fps"])
    duration = float(scene["duration_seconds"])
    frame_end = max(1, int(round(fps * duration)))
    width, height = map(int, scene["resolution"])
    bar_cfg = scene["bar"]
    spacing = float(bar_cfg.get("spacing", 1.15))
    base_radius = float(bar_cfg.get("base_radius", 0.35))
    n = len(series)
    x0 = -((n - 1) * spacing) / 2.0
    bg = scene.get("background_color", [0.06, 0.08, 0.12, 1.0])
    cam = scene["camera"]
    eye = tuple(cam["location"])
    target = tuple(cam.get("look_at", [0, 0, 1.2]))
    lens = float(cam.get("lens", 40))
    fovy = 2.0 * math.degrees(math.atan((24.0 / 2.0) / lens))

    ctx = moderngl.create_standalone_context(require=330)
    print(f"OpenGL: {ctx.info.get('GL_RENDERER', 'unknown')} | {ctx.info.get('GL_VENDOR', '')}")

    prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
    cyl_v, cyl_i = cylinder_mesh(base_radius, segments=28)
    gr_v, gr_i = ground_mesh(40.0)

    def make_vao(verts, indices):
        vbo = ctx.buffer(verts.tobytes())
        ibo = ctx.buffer(indices.tobytes())
        return ctx.vertex_array(
            prog,
            [(vbo, "3f 3f", "in_pos", "in_normal")],
            index_buffer=ibo,
            index_element_size=4,
        )

    vao_cyl = make_vao(cyl_v, cyl_i)
    vao_ground = make_vao(gr_v, gr_i)

    fbo = ctx.framebuffer(
        color_attachments=[ctx.texture((width, height), 3)],
        depth_attachment=ctx.depth_renderbuffer((width, height)),
    )
    fbo.use()
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.disable(moderngl.CULL_FACE)

    proj = perspective(fovy, width / height, 0.1, 200.0)
    view = look_at_matrix(eye, target, (0.0, 0.0, 1.0))
    light_dir = np.array([0.4, -0.5, -0.75], dtype="f4")
    light_dir /= np.linalg.norm(light_dir)

    out_mp4 = output_dir / "animation.mp4"
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    def _run_encode_pass(*, force_libx264: bool) -> tuple[Path, str]:
        ffmpeg_proc = None
        codec_used = "pipe"
        if pipe_to_ffmpeg:
            cmd, codec_used = encode_raw_pipe_cmd(
                out_mp4, scene, width, height, hw, force_libx264=force_libx264
            )
            print(f"GPU→FFmpeg pipe ({codec_used})")
            # Inherit stderr so encoder errors stay visible; do not PIPE (deadlock risk).
            ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

        t0 = time.perf_counter()
        io_workers = hw.recommended_io_workers
        write_pool = ThreadPoolExecutor(max_workers=io_workers) if ffmpeg_proc is None else None
        pending = []

        ambient = np.array([0.18, 0.18, 0.2], dtype="f4")
        ground_color = np.array([0.12, 0.14, 0.18], dtype="f4")

        for frame in range(1, frame_end + 1):
            heights = bar_heights(series, scene, frame, frame_end)
            fbo.clear(bg[0], bg[1], bg[2], 1.0 if len(bg) < 4 else bg[3])

            model = np.eye(4, dtype="f4")
            mvp = proj @ view @ model
            prog["mvp"].write(mvp.T.tobytes())
            prog["normal_mat"].write(np.eye(3, dtype="f4").T.tobytes())
            prog["u_color"].value = tuple(ground_color.tolist())
            prog["u_light_dir"].value = tuple(light_dir.tolist())
            prog["u_eye"].value = eye
            prog["u_ambient"].value = tuple(ambient.tolist())
            vao_ground.render()

            for i, (item, h) in enumerate(zip(series, heights)):
                x = x0 + i * spacing
                model = translation(x, 0.0, 0.0) @ scale_mat(1.0, 1.0, h)
                mvp = proj @ view @ model
                nmat = np.linalg.inv(model[:3, :3]).T.astype("f4")
                prog["mvp"].write(mvp.T.tobytes())
                prog["normal_mat"].write(nmat.T.tobytes())
                prog["u_color"].value = item["color"]
                vao_cyl.render()

            data = fbo.read(components=3, alignment=1)
            arr = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            arr = np.flipud(arr)
            rgb = arr.tobytes()

            if ffmpeg_proc is not None and ffmpeg_proc.stdin is not None:
                try:
                    ffmpeg_proc.stdin.write(rgb)
                except BrokenPipeError as exc:
                    raise RuntimeError("FFmpeg pipe broke during encode") from exc
            else:
                if Image is None:
                    raise RuntimeError("Pillow required for frame-file fallback")
                path = frames_dir / f"frame_{frame:04d}.jpg"

                def _save(p=path, pixels=rgb):
                    Image.frombytes("RGB", (width, height), pixels).save(p, quality=92, optimize=False)

                pending.append(write_pool.submit(_save))  # type: ignore[union-attr]

        if ffmpeg_proc is not None:
            assert ffmpeg_proc.stdin is not None
            ffmpeg_proc.stdin.close()
            rc = ffmpeg_proc.wait()
            if rc != 0:
                raise RuntimeError(f"FFmpeg exited {rc}")
            print(f"Wrote {out_mp4} ({codec_used}) in {time.perf_counter() - t0:.2f}s")
            return out_mp4, codec_used

        assert write_pool is not None
        for fut in pending:
            fut.result()
        write_pool.shutdown(wait=True)
        print(f"Wrote {frame_end} JPEG frames in {time.perf_counter() - t0:.2f}s — encoding...")
        encode_frames(frames_dir, out_mp4, scene, hw)
        return out_mp4, "frames"

    try:
        path, _ = _run_encode_pass(force_libx264=False)
        return path
    except RuntimeError as exc:
        if not pipe_to_ffmpeg or "FFmpeg" not in str(exc):
            raise
        _, first_codec = encode_raw_pipe_cmd(out_mp4, scene, width, height, hw)
        if first_codec == "libx264":
            raise
        print(f"Pipe encode failed ({exc}) — retrying with libx264")
        path, _ = _run_encode_pass(force_libx264=True)
        return path


def main() -> int:
    p = argparse.ArgumentParser(description="GPU-native animation renderer")
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--no-pipe", action="store_true")
    args = p.parse_args()
    render_animation(args.data_dir.resolve(), args.output_dir.resolve(), pipe_to_ffmpeg=not args.no_pipe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
