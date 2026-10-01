from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path

import numpy as np
import pytest

from trueband.ffmpeg import Tools

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("ffmpeg") and not HAVE_FFMPEG:
            item.add_marker(pytest.mark.skip(reason="ffmpeg/ffprobe not installed"))


@cache
def encoders() -> frozenset[str]:
    if not HAVE_FFMPEG:
        return frozenset()
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] == "A":
            names.add(parts[1])
    return frozenset(names)


def require_encoder(*names: str) -> str:
    """Return the first available encoder from ``names`` or skip the test."""
    for name in names:
        if name in encoders():
            return name
    pytest.skip(f"ffmpeg has none of these encoders: {', '.join(names)}")


def write_audio(path: Path, samples: np.ndarray, sample_rate: int, codec_args: Sequence[str]) -> Path:
    """Encode float samples to ``path`` with ffmpeg."""
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            *codec_args,
            str(path),
        ],
        input=np.ascontiguousarray(samples, dtype="<f4").tobytes(),
        check=True,
    )
    return path


def transcode(src: Path, dst: Path, codec_args: Sequence[str]) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *codec_args, str(dst)], check=True)
    return dst


@pytest.fixture(scope="session")
def tools() -> Tools:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    from trueband.ffmpeg import find_tools

    return find_tools()


@pytest.fixture
def make_audio(tmp_path: Path) -> Callable[..., Path]:
    """Factory: ``make_audio(name, samples, sample_rate, codec_args)`` -> path."""

    def _make(name: str, samples: np.ndarray, sample_rate: int, codec_args: Sequence[str] = ()) -> Path:
        if not HAVE_FFMPEG:
            pytest.skip("ffmpeg/ffprobe not installed")
        return write_audio(tmp_path / name, samples, sample_rate, codec_args)

    return _make
