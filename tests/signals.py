"""Synthetic test signals with known spectral properties."""

from __future__ import annotations

import numpy as np


def shaped_noise(
    seconds: float,
    sample_rate: int,
    *,
    cutoff_hz: float | None = None,
    transition_hz: float = 0.0,
    tilt: float = 1.0,
    channels: int = 1,
    seed: int = 0,
    level: float = 0.25,
    envelope: bool = False,
) -> np.ndarray:
    """Noise with a chosen spectral tilt and an optional low-pass cutoff.

    Args:
        seconds: Duration.
        sample_rate: Sample rate in Hz.
        cutoff_hz: Frequency above which the signal is removed (None = full band).
        transition_hz: Width of a raised-cosine roll-off ending at ``cutoff_hz``.
            0 gives a brick-wall filter.
        tilt: Power slope: power ~ 1/f**tilt above 200 Hz (0 = white, 1 = pink).
        channels: 1 or 2 (independent noise per channel).
        seed: RNG seed.
        level: Peak amplitude after normalisation.
        envelope: Apply a 2 Hz pulsing envelope, so encoders see transients.

    Returns:
        float32 array of shape ``(n,)`` for mono or ``(n, channels)``.
    """
    rng = np.random.default_rng(seed)
    n = round(seconds * sample_rate)
    f = np.fft.rfftfreq(n, 1.0 / sample_rate)
    gain = (np.maximum(f, 200.0) / 1000.0) ** (-tilt / 2)
    gain[f < 20] = 0.0
    if cutoff_hz is not None:
        if transition_hz > 0:
            start = cutoff_hz - transition_hz
            ramp = np.clip((f - start) / transition_hz, 0.0, 1.0)
            gain *= 0.5 * (1 + np.cos(np.pi * ramp))
        gain[f >= cutoff_hz] = 0.0
    out = np.stack(
        [np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * gain, n) for _ in range(channels)], axis=1
    )
    if envelope:
        t = np.arange(n) / sample_rate
        out *= (0.35 + 0.65 * np.abs(np.sin(2 * np.pi * t)) ** 4)[:, None]
    out = level * out / np.max(np.abs(out))
    out = out.astype(np.float32)
    return out[:, 0] if channels == 1 else out


def natural_rolloff(
    seconds: float, sample_rate: int, corner_hz: float, order: int, seed: int = 0
) -> np.ndarray:
    """Pink noise through a gentle Butterworth-shaped low-pass (no hard cliff)."""
    rng = np.random.default_rng(seed)
    n = round(seconds * sample_rate)
    f = np.fft.rfftfreq(n, 1.0 / sample_rate)
    gain = (np.maximum(f, 200.0) / 1000.0) ** -0.5 / np.sqrt(1 + (f / corner_hz) ** (2 * order))
    y = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * gain, n)
    return (0.25 * y / np.max(np.abs(y))).astype(np.float32)
