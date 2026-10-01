"""Analyse a single file: probe it, decode a few excerpts, estimate the cutoff."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from trueband.analysis import (
    DEFAULT_BAND_HZ,
    DEFAULT_MARGIN_DB,
    AnalysisError,
    CutoffEstimate,
    estimate_cutoff,
)
from trueband.ffmpeg import AudioInfo, DecodeError, ProbeError, Tools, decode, probe

#: Fractions of the track at which excerpts are taken. Skips intros and outros,
#: which are often quieter or thinner than the body of a track.
DEFAULT_POSITIONS = (0.2, 0.5, 0.75)
DEFAULT_SEGMENT_SECONDS = 6.0
#: Bump whenever a code change alters measurements, so stale caches are dropped.
ALGORITHM_VERSION = 1


@dataclass(frozen=True)
class AnalysisParams:
    """Settings that affect the *measurement* (and so invalidate a cache)."""

    margin_db: float = DEFAULT_MARGIN_DB
    band_hz: float = DEFAULT_BAND_HZ
    segment_seconds: float = DEFAULT_SEGMENT_SECONDS
    positions: tuple[float, ...] = DEFAULT_POSITIONS

    def signature(self) -> dict[str, Any]:
        """A JSON-friendly fingerprint used to validate cached results."""
        return {
            "algorithm": ALGORITHM_VERSION,
            "margin_db": self.margin_db,
            "band_hz": self.band_hz,
            "segment_seconds": self.segment_seconds,
            "positions": list(self.positions),
        }


@dataclass
class Measurement:
    """What trueband measured about one file (independent of thresholds).

    ``error`` is set, and the numeric fields may be ``None``, when the file
    could not be analysed.
    """

    path: str
    cutoff_hz: float | None = None
    nyquist_hz: float | None = None
    cliff_db: float | None = None
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_rate: int | None = None
    duration: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON types."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Measurement:
        """Inverse of :meth:`to_dict`; ignores unknown keys."""
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def plan_segments(
    duration: float | None, segment_seconds: float, positions: tuple[float, ...]
) -> list[tuple[float, float]]:
    """Choose ``(start, length)`` excerpts to decode.

    Short files (or files of unknown length) are analysed from the start in
    one piece rather than as several overlapping excerpts.
    """
    total = segment_seconds * len(positions)
    if duration is None:
        return [(0.0, total)]
    if duration <= total * 1.5:
        return [(0.0, duration)]
    segs = []
    for p in positions:
        start = min(max(duration * p, 0.0), duration - segment_seconds)
        segs.append((start, segment_seconds))
    return segs


def _decode_excerpts(
    path: Path, tools: Tools, info: AudioInfo, params: AnalysisParams, warnings: list[str]
) -> list[np.ndarray]:
    channels = 2 if (info.channels or 1) >= 2 else 1
    excerpts = []
    errors = []
    for start, length in plan_segments(info.duration, params.segment_seconds, params.positions):
        try:
            excerpts.append(
                decode(
                    path, tools, start=start, duration=length, sample_rate=info.sample_rate, channels=channels
                )
            )
        except DecodeError as exc:
            errors.append(str(exc))
    if errors and excerpts:
        warnings.append(f"{len(errors)} excerpt(s) failed to decode: {errors[0]}")
    if not excerpts:
        raise DecodeError(errors[0] if errors else "nothing decoded")
    return excerpts


def _file_problem(path: Path) -> str | None:
    """Why ``path`` can't be read at all (missing, empty, unreadable), or None."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return "file not found"
    except PermissionError:
        return "permission denied"
    except OSError as exc:
        return f"cannot stat file: {exc.strerror or exc}"
    if size == 0:
        return "empty file (0 bytes)"
    if not os.access(path, os.R_OK):
        return "permission denied"
    return None


def measure_audio(
    path: Path, tools: Tools, params: AnalysisParams
) -> tuple[Measurement, CutoffEstimate | None]:
    """Fully analyse one file, returning the measurement and the raw estimate.

    Never raises for per-file problems: they are reported in
    ``Measurement.error``. Only programming errors propagate.
    """
    m = Measurement(path=str(path), error=_file_problem(path))
    if m.error:
        return m, None

    try:
        info = probe(path, tools)
    except ProbeError as exc:
        m.error = f"not readable as audio: {exc}"
        return m, None

    m.codec = info.codec
    m.sample_rate = info.sample_rate
    m.channels = info.channels
    m.bit_rate = info.bit_rate
    m.duration = round(info.duration, 2) if info.duration else None
    m.nyquist_hz = info.sample_rate / 2.0

    try:
        excerpts = _decode_excerpts(path, tools, info, params, m.warnings)
        est = estimate_cutoff(excerpts, info.sample_rate, margin_db=params.margin_db, band_hz=params.band_hz)
    except DecodeError as exc:
        m.error = f"decode failed: {exc}"
        return m, None
    except AnalysisError as exc:
        m.error = str(exc)
        return m, None

    m.cutoff_hz = est.cutoff_hz
    m.cliff_db = round(est.cliff_db, 1) if est.cliff_db is not None else None
    return m, est


def measure(path: Path, tools: Tools, params: AnalysisParams) -> Measurement:
    """Like :func:`measure_audio` but returns only the measurement."""
    return measure_audio(path, tools, params)[0]
