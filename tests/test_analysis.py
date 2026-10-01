"""Validate the detector against synthetic audio with a known, imposed cutoff.

These tests need only numpy: they feed samples straight into the analysis code.
"""

from __future__ import annotations

import numpy as np
import pytest
from signals import natural_rolloff, shaped_noise

from trueband.analysis import AnalysisError, estimate_cutoff

# The detector reports band edges on a 100 Hz grid, so a brick-wall cutoff
# should be recovered to within one band.
TOL_HZ = 150.0


@pytest.mark.parametrize(
    "cutoff", [4000, 8000, 11025, 14000, 15000, 16000, 16500, 17000, 18000, 19000, 19500, 20000, 20500, 21000]
)
def test_recovers_brickwall_cutoff_44k(cutoff: float) -> None:
    x = shaped_noise(12, 44100, cutoff_hz=cutoff)
    est = estimate_cutoff([x], 44100)
    assert est.cutoff_hz == pytest.approx(cutoff, abs=TOL_HZ)


@pytest.mark.parametrize("sample_rate", [22050, 32000, 44100, 48000, 88200, 96000, 192000])
@pytest.mark.parametrize("fraction", [0.4, 0.7, 0.9])
def test_recovers_cutoff_at_any_sample_rate(sample_rate: int, fraction: float) -> None:
    cutoff = round(sample_rate / 2 * fraction)
    x = shaped_noise(6, sample_rate, cutoff_hz=cutoff)
    est = estimate_cutoff([x], sample_rate)
    assert est.cutoff_hz == pytest.approx(cutoff, abs=TOL_HZ)


@pytest.mark.parametrize("sample_rate", [32000, 44100, 48000, 96000])
def test_full_band_signal_reaches_nyquist(sample_rate: int) -> None:
    x = shaped_noise(6, sample_rate)
    est = estimate_cutoff([x], sample_rate)
    assert est.cutoff_hz >= sample_rate / 2 - TOL_HZ
    assert est.cliff_db is None


@pytest.mark.parametrize("tilt", [0.0, 1.0, 2.0])
def test_spectral_tilt_does_not_move_cutoff(tilt: float) -> None:
    # White, pink and "brown above 200 Hz" spectra: real masters sit in this range.
    x = shaped_noise(10, 44100, cutoff_hz=16000, tilt=tilt)
    assert estimate_cutoff([x], 44100).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_steep_but_not_brickwall_filter() -> None:
    # Encoder low-passes have a few hundred Hz of transition; the detected
    # cutoff must fall inside that transition band.
    x = shaped_noise(10, 44100, cutoff_hz=16000, transition_hz=400)
    cutoff = estimate_cutoff([x], 44100).cutoff_hz
    assert 15600 - TOL_HZ <= cutoff <= 16000 + TOL_HZ


@pytest.mark.parametrize("gain_db", [0.0, -20.0, -40.0, -60.0])
def test_loudness_does_not_matter(gain_db: float) -> None:
    x = shaped_noise(8, 44100, cutoff_hz=16000) * np.float32(10 ** (gain_db / 20))
    assert estimate_cutoff([x], 44100).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_stereo_and_mono_agree() -> None:
    mono = shaped_noise(8, 44100, cutoff_hz=17000)
    stereo = shaped_noise(8, 44100, cutoff_hz=17000, channels=2)
    assert estimate_cutoff([mono], 44100).cutoff_hz == pytest.approx(
        estimate_cutoff([stereo], 44100).cutoff_hz, abs=100
    )


def test_out_of_phase_stereo_is_not_cancelled() -> None:
    # A naive mono downmix (L+R) would cancel this signal entirely.
    x = shaped_noise(8, 44100, cutoff_hz=16000)
    stereo = np.stack([x, -x], axis=1)
    assert estimate_cutoff([stereo], 44100).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


@pytest.mark.parametrize("tone_hz", [17330.0, 18970.0, 19000.0, 19050.0, 20000.0])
@pytest.mark.parametrize("amplitude", [0.01, 0.05, 0.2])
def test_isolated_tone_above_cutoff_is_ignored(tone_hz: float, amplitude: float) -> None:
    # e.g. a 19 kHz pilot tone or a single bright partial over a 16 kHz rip.
    # (Regression: with a Hann window and a 2-band rule, a loud tone's leakage
    # used to be reported as the cutoff.)
    sr = 44100
    x = shaped_noise(8, sr, cutoff_hz=16000)
    t = np.arange(len(x)) / sr
    x = x + np.float32(amplitude) * np.sin(2 * np.pi * tone_hz * t).astype(np.float32)
    assert estimate_cutoff([x], sr).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_two_separate_tones_are_ignored() -> None:
    sr = 44100
    x = shaped_noise(8, sr, cutoff_hz=16000)
    t = np.arange(len(x)) / sr
    for f in (18000.0, 19500.0):
        x = x + np.float32(0.05) * np.sin(2 * np.pi * f * t).astype(np.float32)
    assert estimate_cutoff([x], sr).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_multiple_segments_are_combined() -> None:
    segs = [shaped_noise(4, 44100, cutoff_hz=16000, seed=s) for s in range(3)]
    assert estimate_cutoff(segs, 44100).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_short_clip() -> None:
    x = shaped_noise(0.5, 44100, cutoff_hz=16000)
    assert estimate_cutoff([x], 44100).cutoff_hz == pytest.approx(16000, abs=TOL_HZ)


def test_too_short_raises() -> None:
    with pytest.raises(AnalysisError, match="too little audio"):
        estimate_cutoff([np.ones(500, dtype=np.float32) * 0.1], 44100)


def test_silence_raises() -> None:
    with pytest.raises(AnalysisError, match="silent"):
        estimate_cutoff([np.zeros(44100 * 2, dtype=np.float32)], 44100)


def test_empty_raises() -> None:
    with pytest.raises(AnalysisError):
        estimate_cutoff([np.zeros(0, dtype=np.float32)], 44100)


def test_non_finite_raises() -> None:
    x = shaped_noise(2, 44100)
    x[100] = np.nan
    with pytest.raises(AnalysisError, match="non-finite"):
        estimate_cutoff([x], 44100)


def test_bigger_margin_never_lowers_cutoff() -> None:
    x = natural_rolloff(8, 44100, corner_hz=5000, order=4)
    cutoffs = [estimate_cutoff([x], 44100, margin_db=m).cutoff_hz for m in (30, 40, 45, 50, 60)]
    assert cutoffs == sorted(cutoffs)


def test_cliff_separates_encoder_filters_from_natural_rolloff() -> None:
    hard = estimate_cutoff([shaped_noise(8, 44100, cutoff_hz=16000, transition_hz=300)], 44100)
    soft = estimate_cutoff([natural_rolloff(8, 44100, corner_hz=5000, order=4)], 44100)
    assert hard.cliff_db is not None and hard.cliff_db > 40
    assert soft.cliff_db is not None and soft.cliff_db < 15
    assert soft.cutoff_hz < 18000  # low bandwidth, but...
    # ...the cliff metric is what tells the two apart (see classify tests).


def test_spectrum_reference_is_zero_db_in_1_to_5k() -> None:
    est = estimate_cutoff([shaped_noise(6, 44100)], 44100)
    s = est.spectrum
    band = (s.band_lo_hz >= 1000) & (s.band_hi_hz <= 5000)
    assert np.median(s.level_db[band]) == pytest.approx(0.0, abs=1e-9)
