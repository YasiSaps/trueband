from __future__ import annotations

import pytest

from trueband.classify import Status, Thresholds, classify, expected_cutoff_hz, is_lossless
from trueband.track import Measurement


def meas(cutoff: float | None, **kw: object) -> Measurement:
    base: dict[str, object] = {
        "path": "/x.mp3",
        "cutoff_hz": cutoff,
        "nyquist_hz": 22050.0,
        "cliff_db": 80.0,
        "codec": "mp3",
        "sample_rate": 44100,
        "bit_rate": 320000,
    }
    base.update(kw)
    return Measurement(**base)  # type: ignore[arg-type]


TH = Thresholds()


@pytest.mark.parametrize(
    ("cutoff", "status"),
    [
        (15000, Status.SUSPECT),
        (16700, Status.SUSPECT),  # LAME 128k
        (17300, Status.SUSPECT),  # ffmpeg AAC 128k
        (17500, Status.SUSPECT),  # LAME 160k
        (17999, Status.SUSPECT),
        (18000, Status.BORDERLINE),
        (18900, Status.BORDERLINE),  # LAME 192k
        (19199, Status.BORDERLINE),
        (19200, Status.OK),
        (19500, Status.OK),  # LAME 256k
        (20200, Status.OK),  # LAME 320k
        (22050, Status.OK),
    ],
)
def test_default_tiers(cutoff: float, status: Status) -> None:
    assert classify(meas(cutoff), TH).status is status


def test_custom_thresholds() -> None:
    th = Thresholds(suspect_below_hz=17000, borderline_below_hz=19000)
    assert classify(meas(17300), th).status is Status.BORDERLINE
    assert classify(meas(19000), th).status is Status.OK


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        Thresholds(suspect_below_hz=20000, borderline_below_hz=19000)


def test_error_measurement() -> None:
    v = classify(Measurement(path="/x", error="empty file (0 bytes)"), TH)
    assert v.status is Status.ERROR
    assert v.note == "empty file (0 bytes)"


def test_reencode_note_for_high_bitrate_tag() -> None:
    v = classify(meas(16700, bit_rate=320000), TH)
    assert "re-encoded" in v.note and "320 kbps" in v.note


def test_reencode_note_for_mid_bitrate_tag() -> None:
    # A "192 kbps" file that only reaches 16 kHz came from something lower.
    v = classify(meas(16000, bit_rate=192000), TH)
    assert "re-encoded" in v.note


def test_genuine_low_bitrate_note() -> None:
    v = classify(meas(16700, bit_rate=128000), TH)
    assert v.status is Status.SUSPECT
    assert "consistent with its 128 kbps" in v.note


def test_lossless_note() -> None:
    v = classify(meas(16700, codec="flac", bit_rate=900000), TH)
    assert "lossless file with a lossy-style cutoff" in v.note


def test_gradual_rolloff_note_takes_priority() -> None:
    v = classify(meas(15000, cliff_db=6.0, codec="flac"), TH)
    assert v.status is Status.SUSPECT
    assert "gradual roll-off" in v.note


def test_low_sample_rate_note() -> None:
    v = classify(meas(11025, nyquist_hz=11025.0, sample_rate=22050), TH)
    assert v.status is Status.SUSPECT
    assert "sample rate caps bandwidth" in v.note


def test_ok_has_no_note() -> None:
    assert classify(meas(20200), TH).note == ""


def test_warnings_are_appended() -> None:
    v = classify(meas(20200, warnings=["1 excerpt(s) failed to decode: x"]), TH)
    assert "failed to decode" in v.note


@pytest.mark.parametrize("codec", ["flac", "alac", "pcm_s16le", "pcm_f32le", "wavpack"])
def test_is_lossless(codec: str) -> None:
    assert is_lossless(codec)


@pytest.mark.parametrize("codec", ["mp3", "aac", "opus", "vorbis", None, ""])
def test_is_not_lossless(codec: str | None) -> None:
    assert not is_lossless(codec)


def test_expected_cutoff_is_monotonic() -> None:
    values = [expected_cutoff_hz(k) for k in (64, 96, 128, 160, 192, 256, 320, 400)]
    assert values == sorted(values)
