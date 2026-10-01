"""Build a small synthetic "music library" for trying trueband out.

    python scripts/make_demo_library.py demo/
    trueband demo/

Every file is generated from noise with a known history, so you can check that
trueband's report matches what was actually done to each one. The README's
example output was produced this way.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

SR = 44100


def noise(seconds: float, seed: int, corner_hz: float | None = None, order: int = 4) -> np.ndarray:
    """Stereo pink-ish noise with a beat envelope; optional gentle low-pass."""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    f = np.fft.rfftfreq(n, 1 / SR)
    gain = (np.maximum(f, 200.0) / 1000.0) ** -0.5
    gain[f < 20] = 0
    if corner_hz:
        gain /= np.sqrt(1 + (f / corner_hz) ** (2 * order))
    y = np.stack([np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * gain, n) for _ in range(2)], axis=1)
    t = np.arange(n) / SR
    y *= (0.35 + 0.65 * np.abs(np.sin(2 * np.pi * t)) ** 4)[:, None]
    return (0.25 * y / np.max(np.abs(y))).astype(np.float32)


def ff(*args: str, data: bytes | None = None) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], input=data, check=True)


def wav(path: Path, audio: np.ndarray) -> Path:
    ff(
        "-f",
        "f32le",
        "-ar",
        str(SR),
        "-ac",
        "2",
        "-i",
        "pipe:0",
        "-c:a",
        "pcm_s16le",
        str(path),
        data=audio.tobytes(),
    )
    return path


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "House").mkdir(exist_ok=True)
    (out / "Downloads").mkdir(exist_ok=True)
    (out / "STEMS").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        src = [wav(tmp / f"src{i}.wav", noise(40, seed=i)) for i in range(8)]
        mid = tmp / "mid.mp3"
        mid_aac = tmp / "mid.m4a"

        # Genuine, full-quality files.
        ff("-i", str(src[0]), "-c:a", "flac", str(out / "House/Night Drive (Original Mix).flac"))
        ff("-i", str(src[1]), "-c:a", "libmp3lame", "-b:a", "320k", str(out / "House/Warehouse Tool.mp3"))
        ff("-i", str(src[2]), "-c:a", "aac", "-b:a", "256k", str(out / "House/Sunrise Dub.m4a"))

        # A 128k rip "upgraded" to 320k: the classic case.
        ff("-i", str(src[3]), "-c:a", "libmp3lame", "-b:a", "128k", str(mid))
        ff(
            "-i",
            str(mid),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            str(out / "Downloads/Peak Time Anthem (320).mp3"),
        )

        # A 128k AAC (e.g. a video rip) converted to FLAC: a fake lossless file.
        ff("-i", str(src[4]), "-c:a", "aac", "-b:a", "128k", str(mid_aac))
        ff("-i", str(mid_aac), "-c:a", "flac", str(out / "Downloads/Rare Edit [FLAC].flac"))

        # An honest 192k MP3: borderline.
        ff("-i", str(src[5]), "-c:a", "libmp3lame", "-b:a", "192k", str(out / "Downloads/Old Promo.mp3"))

        # A naturally dark recording: low bandwidth, but no encoder cliff.
        wav(out / "House/Late Night Rhodes.wav", noise(40, seed=6, corner_hz=4000, order=3))

        # A stem: often band-limited on purpose; useful for --exclude.
        ff("-i", str(src[7]), "-af", "lowpass=f=12000:poles=2,lowpass=f=12000:poles=2", "-c:a", "pcm_s16le",
           str(out / "STEMS/Peak Time Anthem (Vocals).wav"))  # fmt: skip

        # Junk that real libraries contain.
        (out / "Downloads/Incomplete Download.mp3").write_bytes(b"")
        (out / "Downloads/not-really-audio.m4a").write_text("<html>404 Not Found</html>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="folder to create the demo library in")
    build(ap.parse_args().out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
