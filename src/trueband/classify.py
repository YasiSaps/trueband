"""Turn a :class:`~trueband.track.Measurement` into a status and a human note.

Default thresholds come from ``scripts/calibrate.py`` (see docs/VALIDATION.md):

=====================  ==================
source                 measured cutoff
=====================  ==================
MP3 128 kbps (LAME)    16.7-16.8 kHz
AAC 128 kbps (ffmpeg)  17.3 kHz
MP3 160 kbps           17.4-17.5 kHz
MP3 192 kbps           18.8-18.9 kHz
AAC 192 kbps           19.3-19.4 kHz
MP3 256 kbps           19.5 kHz
MP3 320 kbps           20.2 kHz
=====================  ==================

so the defaults put "suspect" below 18 kHz and "borderline" below 19.2 kHz,
each several hundred Hz away from the nearest measured encoder cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from trueband.track import Measurement

DEFAULT_SUSPECT_BELOW_HZ = 18000.0
DEFAULT_BORDERLINE_BELOW_HZ = 19200.0
#: A drop at least this deep across the cutoff looks like an encoder's
#: low-pass filter. Natural roll-offs measured 5-8 dB; encoders 69-93 dB.
DEFAULT_SHARP_CLIFF_DB = 20.0

#: (kbps, measured cutoff Hz) for LAME MP3 from scripts/calibrate.py. Used only
#: to word the note, never to decide the status. Other encoders differ a little.
_TYPICAL_CUTOFFS = (
    (96, 15200.0),
    (128, 16700.0),
    (160, 17400.0),
    (192, 18800.0),
    (256, 19500.0),
    (320, 20200.0),
)
_EXPECTED_TOLERANCE_HZ = 700.0

LOSSLESS_CODECS = frozenset({"flac", "alac", "wavpack", "ape", "tta", "mlp", "truehd", "shorten", "tak"})


class Status(str, Enum):
    """Verdict for one file. Ordered from most to least worrying in reports."""

    SUSPECT = "suspect"
    BORDERLINE = "borderline"
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class Thresholds:
    """Classification boundaries (do not affect the measurement itself)."""

    suspect_below_hz: float = DEFAULT_SUSPECT_BELOW_HZ
    borderline_below_hz: float = DEFAULT_BORDERLINE_BELOW_HZ
    sharp_cliff_db: float = DEFAULT_SHARP_CLIFF_DB

    def __post_init__(self) -> None:
        if self.suspect_below_hz > self.borderline_below_hz:
            raise ValueError("the suspect threshold must not exceed the borderline threshold")


@dataclass(frozen=True)
class Verdict:
    """Status plus a short, plain-language explanation."""

    status: Status
    note: str


def is_lossless(codec: str | None) -> bool:
    """True for lossless codecs, including any raw PCM variant."""
    if not codec:
        return False
    return codec in LOSSLESS_CODECS or codec.startswith("pcm_")


def expected_cutoff_hz(kbps: float) -> float:
    """Cutoff a typical (LAME) encode at ``kbps`` produces, interpolated."""
    rates, cutoffs = zip(*_TYPICAL_CUTOFFS, strict=True)
    return float(np.interp(kbps, rates, cutoffs))


def classify(m: Measurement, th: Thresholds) -> Verdict:
    """Classify a measurement against thresholds."""
    if m.error is not None or m.cutoff_hz is None:
        return Verdict(Status.ERROR, m.error or "not analysed")

    cutoff = m.cutoff_hz
    if cutoff < th.suspect_below_hz:
        status = Status.SUSPECT
    elif cutoff < th.borderline_below_hz:
        status = Status.BORDERLINE
    else:
        status = Status.OK

    notes: list[str] = []
    nyquist = m.nyquist_hz or 0.0
    rate_limited = bool(nyquist) and nyquist < th.borderline_below_hz
    if rate_limited:
        notes.append(f"{m.sample_rate} Hz sample rate caps bandwidth at {nyquist / 1000:.1f} kHz")
    elif status is not Status.OK:
        sharp = m.cliff_db is not None and m.cliff_db >= th.sharp_cliff_db
        kbps = round(m.bit_rate / 1000) if m.bit_rate else None
        if not sharp:
            notes.append("gradual roll-off, no sharp encoder cliff: may be the recording itself")
        elif is_lossless(m.codec):
            notes.append("lossless file with a lossy-style cutoff: likely converted from a lossy source")
        elif kbps is not None:
            expected = expected_cutoff_hz(kbps)
            if cutoff < expected - _EXPECTED_TOLERANCE_HZ:
                notes.append(
                    f"tagged {kbps} kbps, but a typical {kbps} kbps encode reaches "
                    f"~{expected / 1000:.1f} kHz: likely re-encoded from a lower-bitrate source"
                )
            else:
                notes.append(
                    f"bandwidth consistent with its {kbps} kbps bitrate: a genuinely low-bitrate file"
                )
        else:
            notes.append("sharp cutoff typical of a lossy encoder")
    if m.warnings:
        notes.extend(m.warnings)
    return Verdict(status, "; ".join(notes))
