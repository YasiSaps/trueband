"""End-to-end validation with real encoders: the scenarios trueband exists for.

A full-band source is encoded at known bitrates and through re-encode chains
(the "128 kbps rip saved as 320 kbps / FLAC" case). The whole pipeline is
exercised: ffprobe, ffmpeg decoding, analysis and classification.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from conftest import require_encoder, transcode
from signals import shaped_noise

from trueband.classify import Status, Thresholds, classify
from trueband.ffmpeg import Tools
from trueband.track import AnalysisParams, Measurement, measure

pytestmark = pytest.mark.ffmpeg

SR = 44100
TH = Thresholds()
PARAMS = AnalysisParams()


@pytest.fixture(scope="module")
def source_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from conftest import HAVE_FFMPEG, write_audio

    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    audio = shaped_noise(30, SR, channels=2, envelope=True, seed=7)
    return write_audio(tmp_path_factory.mktemp("src") / "source.wav", audio, SR, ["-c:a", "pcm_s16le"])


def run(path: Path, tools: Tools) -> tuple[Measurement, Status, str]:
    m = measure(path, tools, PARAMS)
    assert m.error is None, m.error
    v = classify(m, TH)
    return m, v.status, v.note


def lossy(name: str, bitrate: str) -> list[str]:
    if name == "mp3":
        return ["-c:a", require_encoder("libmp3lame"), "-b:a", bitrate]
    if name == "aac":
        return ["-c:a", require_encoder("aac"), "-b:a", bitrate]
    raise ValueError(name)


def test_original_is_full_band(source_wav: Path, tools: Tools) -> None:
    m, status, _ = run(source_wav, tools)
    assert status is Status.OK
    assert m.cutoff_hz is not None and m.cutoff_hz > 21500


@pytest.mark.parametrize(
    ("codec", "bitrate", "expected"),
    [
        ("mp3", "96k", Status.SUSPECT),
        ("mp3", "128k", Status.SUSPECT),
        ("mp3", "160k", Status.SUSPECT),
        ("mp3", "192k", Status.BORDERLINE),
        ("mp3", "256k", Status.OK),
        ("mp3", "320k", Status.OK),
        ("aac", "96k", Status.SUSPECT),
        ("aac", "128k", Status.SUSPECT),
        ("aac", "256k", Status.OK),
    ],
)
def test_direct_encodes(
    source_wav: Path, tools: Tools, tmp_path: Path, codec: str, bitrate: str, expected: Status
) -> None:
    out = transcode(source_wav, tmp_path / f"x.{'mp3' if codec == 'mp3' else 'm4a'}", lossy(codec, bitrate))
    _, status, _ = run(out, tools)
    assert status is expected


@pytest.mark.parametrize(
    ("first", "second", "ext"),
    [
        (("mp3", "128k"), ("mp3", "320k"), "mp3"),
        (("aac", "128k"), ("mp3", "320k"), "mp3"),
        (("aac", "128k"), ("aac", "256k"), "m4a"),
    ],
)
def test_low_bitrate_reencoded_higher_is_caught(
    source_wav: Path, tools: Tools, tmp_path: Path, first: tuple[str, str], second: tuple[str, str], ext: str
) -> None:
    mid = transcode(source_wav, tmp_path / f"mid.{'mp3' if first[0] == 'mp3' else 'm4a'}", lossy(*first))
    out = transcode(mid, tmp_path / f"out.{ext}", lossy(*second))
    m, status, note = run(out, tools)
    assert m.bit_rate is not None and m.bit_rate >= 250_000  # the tag looks high quality...
    assert status is Status.SUSPECT  # ...but the content gives it away
    assert "re-encoded" in note


@pytest.mark.parametrize(("codec", "bitrate"), [("mp3", "128k"), ("aac", "128k")])
@pytest.mark.parametrize(
    ("container", "args"),
    [("flac", ["-c:a", "flac"]), ("wav", ["-c:a", "pcm_s16le"]), ("aiff", ["-c:a", "pcm_s16be"])],
)
def test_lossy_converted_to_lossless_is_caught(
    source_wav: Path, tools: Tools, tmp_path: Path, codec: str, bitrate: str, container: str, args: list[str]
) -> None:
    mid = transcode(source_wav, tmp_path / f"mid.{'mp3' if codec == 'mp3' else 'm4a'}", lossy(codec, bitrate))
    out = transcode(mid, tmp_path / f"fake.{container}", args)
    _, status, note = run(out, tools)
    assert status is Status.SUSPECT
    assert "lossless file with a lossy-style cutoff" in note


def test_real_lossless_is_ok(source_wav: Path, tools: Tools, tmp_path: Path) -> None:
    out = transcode(source_wav, tmp_path / "real.flac", ["-c:a", "flac"])
    _, status, _ = run(out, tools)
    assert status is Status.OK


def test_320k_mp3_converted_to_flac_is_ok(source_wav: Path, tools: Tools, tmp_path: Path) -> None:
    # A lossy file is still "lossy", but a 320k source has (near) full bandwidth,
    # which is all this tool claims to measure.
    mid = transcode(source_wav, tmp_path / "mid.mp3", lossy("mp3", "320k"))
    out = transcode(mid, tmp_path / "out.flac", ["-c:a", "flac"])
    _, status, _ = run(out, tools)
    assert status is Status.OK


def test_opus(source_wav: Path, tools: Tools, tmp_path: Path) -> None:
    out = transcode(source_wav, tmp_path / "x.opus", ["-c:a", require_encoder("libopus"), "-b:a", "128k"])
    m, status, _ = run(out, tools)
    assert m.codec == "opus" and m.sample_rate == 48000
    assert status is Status.OK  # Opus keeps ~20 kHz bandwidth even at modest bitrates


def test_ogg_vorbis(make_audio: Callable[..., Path], tools: Tools) -> None:
    enc = require_encoder("libvorbis", "vorbis")
    extra = ["-strict", "experimental"] if enc == "vorbis" else []
    bandlimited = shaped_noise(12, SR, cutoff_hz=16000, channels=2)
    path = make_audio("x.ogg", bandlimited, SR, ["-c:a", enc, *extra, "-q:a", "8"])
    m, status, _ = run(path, tools)
    assert m.codec == "vorbis"
    assert m.cutoff_hz == pytest.approx(16000, abs=400)
    assert status is Status.SUSPECT


@pytest.mark.parametrize("sample_rate", [32000, 44100, 48000, 88200, 96000])
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_bandlimited_recovers_cutoff(
    make_audio: Callable[..., Path], tools: Tools, sample_rate: int, channels: int
) -> None:
    audio = shaped_noise(8, sample_rate, cutoff_hz=15000, channels=channels)
    path = make_audio(f"x{sample_rate}.flac", audio, sample_rate, ["-c:a", "flac"])
    m, status, _ = run(path, tools)
    assert m.sample_rate == sample_rate
    assert m.cutoff_hz == pytest.approx(15000, abs=150)
    assert status is Status.SUSPECT


def test_24bit_wav(make_audio: Callable[..., Path], tools: Tools) -> None:
    audio = shaped_noise(8, 96000, channels=2)
    path = make_audio("hires.wav", audio, 96000, ["-c:a", "pcm_s24le"])
    m, status, _ = run(path, tools)
    assert m.codec == "pcm_s24le"
    assert status is Status.OK
    assert m.cutoff_hz is not None and m.cutoff_hz > 40000


def test_low_sample_rate_is_explained(make_audio: Callable[..., Path], tools: Tools) -> None:
    path = make_audio("lo.wav", shaped_noise(6, 22050), 22050, ["-c:a", "pcm_s16le"])
    _, status, note = run(path, tools)
    assert status is Status.SUSPECT
    assert "sample rate caps bandwidth" in note


def test_long_file_uses_three_excerpts(make_audio: Callable[..., Path], tools: Tools) -> None:
    # 60 s with full band only in the middle of the file: the excerpts at 20%,
    # 50% and 75% must be what gets analysed, not the start.
    sr = 22050 * 2
    quiet_start = shaped_noise(60, sr, cutoff_hz=12000, seed=1)
    body = shaped_noise(60, sr, cutoff_hz=16000, seed=2)
    audio = np.concatenate([quiet_start[: sr * 5], body[sr * 5 :]])
    path = make_audio("long.flac", audio, sr, ["-c:a", "flac"])
    m, _, _ = run(path, tools)
    assert m.cutoff_hz == pytest.approx(16000, abs=150)
