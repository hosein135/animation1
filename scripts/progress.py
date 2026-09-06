#!/usr/bin/env python3
"""Stdlib progress bars for render + encode (no extra packages)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_FRAME_SUFFIXES = {".png", ".jpg", ".jpeg"}


def count_rendered_frames(frames_dir: Path) -> int:
    if not frames_dir.is_dir():
        return 0
    n = 0
    try:
        with os.scandir(frames_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name
                if not name.startswith("frame_"):
                    continue
                ext = Path(name).suffix.lower()
                if ext in _FRAME_SUFFIXES:
                    n += 1
    except FileNotFoundError:
        return 0
    return n


def _bar_chars(stream) -> tuple[str, str]:
    fill, empty = "#", "-"
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "█░".encode(encoding)
        return "█", "░"
    except Exception:
        return fill, empty


def _fmt_hms(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class ProgressBar:
    """Single-line \\r progress bar on stderr (works in Windows consoles)."""

    def __init__(self, total: int, label: str = "Progress", width: int = 28) -> None:
        self.total = max(1, int(total))
        self.label = label
        self.width = width
        self.stream = sys.stderr
        self._fill, self._empty = _bar_chars(self.stream)
        self._start = time.perf_counter()
        self._last_len = 0
        self._current = 0
        self._closed = False
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def update(self, current: int, suffix: str = "") -> None:
        if self._closed:
            return
        current = max(0, min(int(current), self.total))
        self._current = current
        frac = current / self.total
        filled = int(self.width * frac)
        bar = self._fill * filled + self._empty * (self.width - filled)
        elapsed = time.perf_counter() - self._start
        rate = current / elapsed if elapsed > 0.05 and current else 0.0
        remain = (self.total - current) / rate if rate > 0 else float("inf")
        extra = f"  {suffix}" if suffix else ""
        line = (
            f"{self.label:7s} [{bar}] {current:>4d}/{self.total}  {frac:6.1%}  "
            f"{rate:5.2f}/s  ETA {_fmt_hms(remain)}{extra}"
        )
        if self._tty:
            pad = max(0, self._last_len - len(line))
            self.stream.write("\r" + line + (" " * pad))
            self.stream.flush()
            self._last_len = len(line)
        else:
            # Non-TTY (piped logs): emit at 10% steps to avoid spam.
            step = max(1, self.total // 10)
            if current == 0 or current == self.total or current % step == 0:
                self.stream.write(line + "\n")
                self.stream.flush()

    def close(self, suffix: str = "done") -> None:
        if self._closed:
            return
        self.update(self.total, suffix=suffix)
        if self._tty:
            self.stream.write("\n")
            self.stream.flush()
        self._closed = True


def raise_process_priority(pid: int | None = None) -> None:
    """Best-effort: above-normal priority so render workers get CPU time."""
    pid = int(pid or os.getpid())
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_SET_INFORMATION = 0x0200
            ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
            handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
            if handle:
                kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
                kernel32.CloseHandle(handle)
        except Exception:
            pass
        return
    try:
        os.nice(-5)
    except Exception:
        pass
