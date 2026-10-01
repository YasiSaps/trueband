"""Thin, defensive wrappers around the ``ffprobe`` and ``ffmpeg`` executables.

All decoding goes through ffmpeg so that every format ffmpeg understands
(MP3, AAC/M4A, FLAC, WAV, AIFF, Ogg Vorbis, Opus, ...) is handled the same way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Environment variables that override where the tools are looked up.
FFMPEG_ENV = "TRUEBAND_FFMPEG"
FFPROBE_ENV = "TRUEBAND_FFPROBE"

_INSTALL_HINT = (
    "trueband needs ffmpeg and ffprobe on your PATH.\n"
    "  macOS:          brew install ffmpeg\n"
    "  Debian/Ubuntu:  sudo apt install ffmpeg\n"
    "  Fedora:         sudo dnf install ffmpeg\n"
    "  Windows:        winget install ffmpeg   (or choco install ffmpeg)\n"
    f"Or point {FFMPEG_ENV} / {FFPROBE_ENV} at the executables."
)


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot be located."""


class ProbeError(Exception):
    """Raised when ffprobe cannot read a file as audio."""


class DecodeError(Exception):
    """Raised when ffmpeg cannot decode the requested part of a file."""


@dataclass(frozen=True)
class Tools:
    """Resolved paths of the ffmpeg and ffprobe executables."""

    ffmpeg: str
    ffprobe: str


@dataclass(frozen=True)
class AudioInfo:
    """Metadata about the first audio stream of a file, as reported by ffprobe."""

    codec: str
    sample_rate: int
    channels: int | None
    duration: float | None
    bit_rate: int | None
    format_name: str


def find_tools() -> Tools:
    """Locate ffmpeg and ffprobe, honouring the override environment variables.

    Raises:
        FFmpegNotFoundError: if either executable cannot be found.
    """
    found: dict[str, str] = {}
    for name, env in (("ffmpeg", FFMPEG_ENV), ("ffprobe", FFPROBE_ENV)):
        candidate = os.environ.get(env) or name
        resolved = shutil.which(candidate)
        if resolved is None:
            raise FFmpegNotFoundError(f"Could not find `{candidate}`.\n{_INSTALL_HINT}")
        found[name] = resolved
    return Tools(ffmpeg=found["ffmpeg"], ffprobe=found["ffprobe"])


def _ffmpeg_url(path: Path) -> str:
    """Return a path string ffmpeg will never mistake for an option or protocol.

    Absolute paths can't start with ``-``, and the ``file:`` prefix stops names
    like ``Artist: Title.mp3`` being parsed as a protocol URL.
    """
    return "file:" + str(path.resolve())


def _last_line(text: str, path: Path | None = None) -> str:
    """Last non-empty line of ffmpeg's stderr, without the echoed file name."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    line = lines[-1] if lines else "unknown error"
    if path is not None:
        line = line.removeprefix(_ffmpeg_url(path) + ": ")
    return line


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def probe(path: Path, tools: Tools, timeout: float = 30.0) -> AudioInfo:
    """Read codec, sample rate, duration and bitrate of the first audio stream.

    Raises:
        ProbeError: if the file isn't readable audio (corrupt, wrong type, ...).
    """
    cmd = [
        tools.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration,bit_rate,format_name:stream=codec_name,sample_rate,channels,bit_rate,duration",
        "-of",
        "json",
        _ffmpeg_url(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        raise ProbeError(_last_line(proc.stderr, path))
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned unparseable output") from exc

    streams = data.get("streams") or []
    if not streams:
        raise ProbeError("no audio stream found")
    stream = streams[0]
    fmt = data.get("format") or {}

    sample_rate = _to_int(stream.get("sample_rate"))
    if not sample_rate or sample_rate <= 0:
        raise ProbeError("audio stream has no valid sample rate")

    return AudioInfo(
        codec=str(stream.get("codec_name") or "unknown"),
        sample_rate=sample_rate,
        channels=_to_int(stream.get("channels")),
        duration=_to_float(stream.get("duration")) or _to_float(fmt.get("duration")),
        # Prefer the stream bitrate: the container figure includes cover art etc.
        bit_rate=_to_int(stream.get("bit_rate")) or _to_int(fmt.get("bit_rate")),
        format_name=str(fmt.get("format_name") or "unknown"),
    )


def decode(
    path: Path,
    tools: Tools,
    *,
    start: float,
    duration: float,
    sample_rate: int,
    channels: int,
    timeout: float = 60.0,
) -> np.ndarray:
    """Decode part of a file to float32 PCM.

    Returns:
        An array of shape ``(n_samples, channels)``.

    Raises:
        DecodeError: if ffmpeg fails and produced no audio at all. A file that
            is partly corrupt but still yields samples is returned as-is.
    """
    cmd = [
        tools.ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{max(start, 0.0):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        _ffmpeg_url(path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise DecodeError(f"ffmpeg timed out after {timeout:.0f}s") from exc

    usable = len(proc.stdout) - len(proc.stdout) % (4 * channels)
    if usable == 0:
        reason = (
            _last_line(proc.stderr.decode("utf-8", "replace"), path)
            if proc.returncode
            else "no audio decoded"
        )
        raise DecodeError(reason)
    samples = np.frombuffer(proc.stdout[:usable], dtype="<f4")
    return samples.reshape(-1, channels)
