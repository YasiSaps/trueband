"""Real-world mess: broken, empty, fake, unreadable and odd files."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from signals import shaped_noise

from trueband.ffmpeg import FFmpegNotFoundError, Tools, find_tools
from trueband.track import AnalysisParams, measure, plan_segments

pytestmark = pytest.mark.ffmpeg
P = AnalysisParams()


def test_zero_byte_file(tmp_path: Path, tools: Tools) -> None:
    f = tmp_path / "empty.mp3"
    f.touch()
    assert measure(f, tools, P).error == "empty file (0 bytes)"


def test_text_file_with_audio_extension(tmp_path: Path, tools: Tools) -> None:
    f = tmp_path / "fake.mp3"
    f.write_text("this is not audio\n" * 100)
    m = measure(f, tools, P)
    assert m.error is not None and m.error.startswith("not readable as audio")
    assert str(tmp_path) not in m.error  # no echoed path noise


def test_random_bytes_flac(tmp_path: Path, tools: Tools) -> None:
    f = tmp_path / "garbage.flac"
    f.write_bytes(np.random.default_rng(0).integers(0, 256, 50_000, dtype=np.uint8).tobytes())
    assert measure(f, tools, P).error is not None


def test_missing_file(tmp_path: Path, tools: Tools) -> None:
    assert measure(tmp_path / "gone.mp3", tools, P).error == "file not found"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, non-root")
def test_permission_denied(make_audio: Callable[..., Path], tools: Tools) -> None:
    f = make_audio("locked.wav", shaped_noise(2, 44100), 44100, ["-c:a", "pcm_s16le"])
    f.chmod(0)
    try:
        assert measure(f, tools, P).error == "permission denied"
    finally:
        f.chmod(0o644)


def test_truncated_mp3_still_analysed(make_audio: Callable[..., Path], tools: Tools) -> None:
    f = make_audio(
        "full.mp3", shaped_noise(30, 44100, channels=2), 44100, ["-c:a", "libmp3lame", "-b:a", "128k"]
    )
    data = f.read_bytes()
    cut = f.with_name("cut.mp3")
    cut.write_bytes(data[: len(data) // 2])  # e.g. an interrupted download
    m = measure(cut, tools, P)
    assert m.error is None, m.error
    assert m.cutoff_hz is not None and m.cutoff_hz < 18000


def test_silent_file(make_audio: Callable[..., Path], tools: Tools) -> None:
    f = make_audio("silence.wav", np.zeros(44100 * 5, dtype=np.float32), 44100, ["-c:a", "pcm_s16le"])
    assert measure(f, tools, P).error == "audio is silent"


@pytest.mark.parametrize("seconds", [0.15, 0.5, 2.0, 7.0])
def test_short_clips(make_audio: Callable[..., Path], tools: Tools, seconds: float) -> None:
    f = make_audio("short.wav", shaped_noise(seconds, 44100, cutoff_hz=16000), 44100, ["-c:a", "pcm_s16le"])
    m = measure(f, tools, P)
    assert m.error is None, m.error
    assert m.cutoff_hz == pytest.approx(16000, abs=150)


def test_too_short_to_analyse(make_audio: Callable[..., Path], tools: Tools) -> None:
    f = make_audio("blip.wav", shaped_noise(0.01, 44100), 44100, ["-c:a", "pcm_s16le"])
    m = measure(f, tools, P)
    assert m.error is not None and "too little audio" in m.error


@pytest.mark.parametrize(
    "name",
    [
        "-starts-with-dash.wav",
        "Artist: Title (feat. X) [Remix].wav",
        "ünïcødé 曲.wav",
        "percent %20 and #hash.wav",
    ],
)
def test_awkward_filenames(make_audio: Callable[..., Path], tools: Tools, name: str) -> None:
    if sys.platform == "win32" and ":" in name:
        pytest.skip("':' is not allowed in Windows filenames")
    f = make_audio(name, shaped_noise(3, 44100), 44100, ["-c:a", "pcm_s16le"])
    m = measure(f, tools, P)
    assert m.error is None, m.error


def test_mp3_with_cover_art(make_audio: Callable[..., Path], tools: Tools, tmp_path: Path) -> None:
    # Files with embedded artwork have a video stream; the audio stream must be
    # the one analysed even when the picture comes first.
    audio = make_audio(
        "plain.mp3", shaped_noise(10, 44100, channels=2), 44100, ["-c:a", "libmp3lame", "-b:a", "320k"]
    )
    cover = tmp_path / "cover.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64",
            "-frames:v",
            "1",
            str(cover),
        ],
        check=True,
    )
    tagged = tmp_path / "tagged.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(cover),
            "-i",
            str(audio),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            "-id3v2_version",
            "3",
            "-disposition:v",
            "attached_pic",
            str(tagged),
        ],
        check=True,
    )
    m = measure(tagged, tools, P)
    assert m.error is None, m.error
    assert m.codec == "mp3"
    assert m.cutoff_hz is not None and m.cutoff_hz > 19200


def test_plan_segments() -> None:
    assert plan_segments(None, 6, (0.2, 0.5, 0.75)) == [(0.0, 18)]
    assert plan_segments(10.0, 6, (0.2, 0.5, 0.75)) == [(0.0, 10.0)]
    segs = plan_segments(300.0, 6, (0.2, 0.5, 0.75))
    assert [s for s, _ in segs] == [60.0, 150.0, 225.0]
    # A position near the end never runs past the file.
    assert plan_segments(30.0, 6, (0.95,)) == [(24.0, 6)]


def test_missing_ffmpeg_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUEBAND_FFMPEG", "definitely-not-ffmpeg-xyz")
    with pytest.raises(FFmpegNotFoundError, match="brew install ffmpeg"):
        find_tools()
