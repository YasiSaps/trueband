"""Spectral cutoff estimation. Pure numpy, no I/O, so it can be tested directly.

Method, in plain terms:

1. Take the decoded audio (a few excerpts of a track), chop it into short
   overlapping frames, apply a Blackman-Harris window and FFT each one, and
   average the power over all frames and channels (Welch's method). The
   window's very low leakage keeps a loud tone from smearing into
   neighbouring frequencies. Averaging many frames
   gives a smooth, stable estimate of how much energy sits at each frequency.
2. Group the FFT bins into narrow bands (100 Hz by default) and take the median
   level in each band. The median ignores isolated tones (pilot tones, CRT
   whine, a single cymbal partial) that would otherwise look like "content".
3. Use the median band level between 1 and 5 kHz as the track's reference
   level. Everything is measured relative to it, so loudness doesn't matter.
4. Walking down from the top of the spectrum, the cutoff is the upper edge of
   the highest run of ``min_run`` adjacent bands (300 Hz by default) that are
   all within ``margin_db`` of the reference. A lone tone can't fill a run.

Lossy encoders apply a steep low-pass filter whose frequency depends on the
bitrate (roughly 16 kHz for 128 kbps MP3/AAC, 19-20 kHz at 256-320 kbps). That
filter survives any later re-encode or conversion to a lossless format, which
is what this measurement detects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_MARGIN_DB = 45.0
DEFAULT_BAND_HZ = 100.0
DEFAULT_FRAME_SIZE = 8192
DEFAULT_MIN_RUN = 3
REFERENCE_BAND_HZ = (1000.0, 5000.0)
#: Digital silence threshold (RMS, linear full scale): about -100 dBFS.
SILENCE_RMS = 1e-5
_EPS = 1e-30


class AnalysisError(Exception):
    """Raised when audio cannot be analysed (too short, silent, ...)."""


@dataclass(frozen=True)
class Spectrum:
    """A smoothed power spectrum in narrow bands, relative to a reference level.

    Attributes:
        band_lo_hz: Lower edge of each band.
        band_hi_hz: Upper edge of each band.
        level_db: Median band level in dB relative to ``reference_db``.
        reference_db: Absolute level (dB, arbitrary units) of the 1-5 kHz median.
        sample_rate: Sample rate of the analysed audio.
    """

    band_lo_hz: np.ndarray
    band_hi_hz: np.ndarray
    level_db: np.ndarray
    reference_db: float
    sample_rate: int

    @property
    def nyquist_hz(self) -> float:
        """Half the sample rate: the highest frequency the file can represent."""
        return self.sample_rate / 2.0


@dataclass(frozen=True)
class CutoffEstimate:
    """Result of :func:`estimate_cutoff`.

    Attributes:
        cutoff_hz: Highest frequency with sustained content (see module docs).
        cliff_db: How sharply the level falls across the cutoff: the median
            level 0.5-1.5 kHz below it minus the median 0.5-1.5 kHz above it.
            Encoder low-pass filters give a large value (often 40 dB+); a
            natural, gradual roll-off gives a small one. ``None`` when the
            cutoff is too close to Nyquist to measure.
        spectrum: The underlying band spectrum, for display.
    """

    cutoff_hz: float
    cliff_db: float | None
    spectrum: Spectrum


def blackman_harris(n: int) -> np.ndarray:
    """4-term Blackman-Harris window (sidelobes below -92 dB)."""
    k = 2.0 * np.pi * np.arange(n) / n
    return 0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)


def _frames_power(x: np.ndarray, frame_size: int) -> tuple[np.ndarray, int]:
    """Sum of windowed power spectra over all frames/channels, and frame count.

    ``x`` has shape ``(n_samples, n_channels)``.
    """
    hop = frame_size // 2
    window = blackman_harris(frame_size)
    n = x.shape[0]
    starts = range(0, n - frame_size + 1, hop)
    total = np.zeros(frame_size // 2 + 1, dtype=np.float64)
    count = 0
    for s in starts:
        frame = x[s : s + frame_size].astype(np.float64) * window[:, None]
        spec = np.fft.rfft(frame, axis=0)
        total += np.sum(spec.real**2 + spec.imag**2, axis=1)
        count += x.shape[1]
    return total, count


def average_power_spectrum(
    segments: Sequence[np.ndarray],
    sample_rate: int,
    frame_size: int = DEFAULT_FRAME_SIZE,
) -> tuple[np.ndarray, np.ndarray]:
    """Welch-averaged power spectrum over several audio segments.

    Args:
        segments: Arrays of shape ``(n,)`` (mono) or ``(n, channels)``.
        sample_rate: Sample rate in Hz.
        frame_size: FFT size. Shrunk automatically for very short audio.

    Returns:
        ``(freqs_hz, power)`` arrays.

    Raises:
        AnalysisError: if there is too little audio to analyse.
    """
    arrays = [s.reshape(-1, 1) if s.ndim == 1 else s for s in segments if s.size]
    longest = max((a.shape[0] for a in arrays), default=0)
    min_frame = 1024
    if longest < min_frame:
        raise AnalysisError(f"too little audio to analyse ({longest} samples)")
    # Largest power of two that fits in the longest segment, capped at frame_size.
    size = min(frame_size, 1 << (longest.bit_length() - 1))

    total = np.zeros(size // 2 + 1, dtype=np.float64)
    count = 0
    for a in arrays:
        if a.shape[0] < size:
            continue
        p, c = _frames_power(a, size)
        total += p
        count += c
    if count == 0:  # pragma: no cover - guarded by the size choice above
        raise AnalysisError("no complete analysis frames")
    freqs = np.fft.rfftfreq(size, 1.0 / sample_rate)
    return freqs, total / count


def band_spectrum(
    freqs: np.ndarray,
    power: np.ndarray,
    sample_rate: int,
    band_hz: float = DEFAULT_BAND_HZ,
) -> Spectrum:
    """Reduce a fine power spectrum to median levels in ``band_hz``-wide bands."""
    nyquist = sample_rate / 2.0
    power_db = 10.0 * np.log10(power + _EPS)

    edges = np.arange(0.0, nyquist + band_hz, band_hz)
    edges[-1] = min(edges[-1], nyquist)
    if edges[-1] <= edges[-2]:
        edges = edges[:-1]
    idx = np.digitize(freqs, edges) - 1
    n_bands = len(edges) - 1
    levels = np.full(n_bands, np.nan)
    for b in range(n_bands):
        sel = power_db[idx == b]
        if sel.size:
            levels[b] = float(np.median(sel))
    # Bands narrower than one FFT bin (tiny band_hz) inherit their neighbour.
    if np.isnan(levels).any():
        valid = ~np.isnan(levels)
        levels = np.interp(np.arange(n_bands), np.flatnonzero(valid), levels[valid])

    lo, hi = edges[:-1], edges[1:]
    ref_lo, ref_hi = REFERENCE_BAND_HZ
    ref_hi = min(ref_hi, nyquist * 0.6)  # keep a reference for very low sample rates
    ref_lo = min(ref_lo, ref_hi / 2)
    in_ref = (lo >= ref_lo) & (hi <= ref_hi)
    reference = float(np.median(levels[in_ref])) if in_ref.any() else float(np.median(levels))
    return Spectrum(
        band_lo_hz=lo,
        band_hi_hz=hi,
        level_db=levels - reference,
        reference_db=reference,
        sample_rate=sample_rate,
    )


def find_cutoff(
    spectrum: Spectrum, margin_db: float = DEFAULT_MARGIN_DB, min_run: int = DEFAULT_MIN_RUN
) -> float:
    """Upper edge of the highest run of ``min_run`` adjacent bands within ``margin_db``."""
    above = spectrum.level_db >= -margin_db
    run = 0
    for i in range(len(above) - 1, -1, -1):
        run = run + 1 if above[i] else 0
        if run >= min_run:
            return float(spectrum.band_hi_hz[i + min_run - 1])
    return 0.0


def cliff_depth(spectrum: Spectrum, cutoff_hz: float) -> float | None:
    """Level drop across ``cutoff_hz`` (see :class:`CutoffEstimate`)."""
    lo, hi, lev = spectrum.band_lo_hz, spectrum.band_hi_hz, spectrum.level_db
    below = (lo >= cutoff_hz - 1500) & (hi <= cutoff_hz - 500)
    above = (lo >= cutoff_hz + 500) & (hi <= cutoff_hz + 1500)
    if not below.any() or not above.any():
        return None
    return float(np.median(lev[below]) - np.median(lev[above]))


def estimate_cutoff(
    segments: Sequence[np.ndarray],
    sample_rate: int,
    *,
    margin_db: float = DEFAULT_MARGIN_DB,
    band_hz: float = DEFAULT_BAND_HZ,
    frame_size: int = DEFAULT_FRAME_SIZE,
) -> CutoffEstimate:
    """Estimate the spectral cutoff of some audio.

    Args:
        segments: One or more excerpts, each ``(n,)`` or ``(n, channels)``,
            float samples in full-scale units (±1.0).
        sample_rate: Sample rate in Hz.
        margin_db: How far below the 1-5 kHz reference level still counts as
            "content". Larger values push the cutoff higher.
        band_hz: Width of the analysis bands (the cutoff's resolution).
        frame_size: FFT size.

    Raises:
        AnalysisError: if the audio is too short or digitally silent.
    """
    if sample_rate <= 0:
        raise AnalysisError(f"invalid sample rate {sample_rate}")
    flat = [np.asarray(s, dtype=np.float64) for s in segments]
    total_sq = sum(float(np.sum(s * s)) for s in flat)
    total_n = sum(s.size for s in flat)
    if total_n == 0:
        raise AnalysisError("no audio samples")
    if not np.isfinite(total_sq):
        raise AnalysisError("audio contains non-finite samples")
    if np.sqrt(total_sq / total_n) < SILENCE_RMS:
        raise AnalysisError("audio is silent")

    freqs, power = average_power_spectrum(flat, sample_rate, frame_size)
    spectrum = band_spectrum(freqs, power, sample_rate, band_hz)
    cutoff = find_cutoff(spectrum, margin_db)
    return CutoffEstimate(cutoff_hz=cutoff, cliff_db=cliff_depth(spectrum, cutoff), spectrum=spectrum)
