"""Measure trueband's cutoff on real encoder output, to back the default thresholds.

Generates synthetic "programme" audio, pushes it through ffmpeg encoders at a
range of bitrates (and through re-encode chains such as 128k MP3 -> 320k MP3 or
128k AAC -> FLAC), and prints what trueband measures for each.

    python scripts/calibrate.py            # markdown table to stdout
    python scripts/calibrate.py --json     # machine-readable

The output of this script is what docs/VALIDATION.md is based on.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from trueband.ffmpeg import find_tools
from trueband.track import AnalysisParams, measure

SR = 44100


def make_source(kind: str, seconds: float, seed: int = 1) -> np.ndarray:
    """Stereo full-band test signal with a music-like spectral tilt and dynamics."""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    slope = {"pink": 1.0, "dark": 2.0}[kind]  # power ~ 1/f^slope above 200 Hz
    f = np.fft.rfftfreq(n, 1 / SR)
    gain = (np.maximum(f, 200.0) / 1000.0) ** (-slope / 2)
    gain[f < 20] = 0
    chans = []
    for _ in range(2):
        x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * gain, n)
        chans.append(x)
    y = np.stack(chans, axis=1)
    # 2 Hz "beat" envelope so the encoder sees transients, not steady noise.
    t = np.arange(n) / SR
    env = 0.35 + 0.65 * np.abs(np.sin(np.pi * 2 * t)) ** 4
    y = y * env[:, None]
    return (0.25 * y / np.max(np.abs(y))).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
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
        ],
        input=audio.tobytes(),
        check=True,
    )


def encode(src: Path, dst: Path, args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *args, str(dst)], check=True)


CODECS: dict[str, tuple[str, list[str]]] = {
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a"]),
    "aac": (".m4a", ["-c:a", "aac", "-b:a"]),
    "opus": (".opus", ["-c:a", "libopus", "-b:a"]),
}

DIRECT = (
    [("mp3", r) for r in ("96k", "128k", "160k", "192k", "256k", "320k")]
    + [("aac", r) for r in ("96k", "128k", "192k", "256k", "320k")]
    + [("opus", r) for r in ("64k", "96k", "128k", "160k")]
)

CHAINS = [
    (("mp3", "128k"), ("mp3", "320k")),
    (("aac", "128k"), ("mp3", "320k")),
    (("aac", "128k"), ("aac", "256k")),
    (("mp3", "128k"), "flac"),
    (("aac", "128k"), "flac"),
    (("mp3", "192k"), "flac"),
    (("mp3", "320k"), "flac"),
]


def run(seconds: float) -> list[dict[str, object]]:
    tools = find_tools()
    params = AnalysisParams()
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        for kind in ("pink", "dark"):
            src = tmp / f"{kind}.wav"
            write_wav(src, make_source(kind, seconds))
            m = measure(src, tools, params)
            rows.append(
                {
                    "source": kind,
                    "chain": "original WAV (full band)",
                    "cutoff_hz": m.cutoff_hz,
                    "cliff_db": m.cliff_db,
                }
            )
            for codec, rate in DIRECT:
                ext, args = CODECS[codec]
                out = tmp / f"{kind}_{codec}_{rate}{ext}"
                encode(src, out, [*args, rate])
                m = measure(out, tools, params)
                rows.append(
                    {
                        "source": kind,
                        "chain": f"{codec} {rate}",
                        "cutoff_hz": m.cutoff_hz,
                        "cliff_db": m.cliff_db,
                    }
                )
            for first, second in CHAINS:
                c1, r1 = first
                ext1, a1 = CODECS[c1]
                mid = tmp / f"{kind}_mid_{c1}_{r1}{ext1}"
                encode(src, mid, [*a1, r1])
                if second == "flac":
                    out = tmp / f"{kind}_{c1}{r1}_to_flac.flac"
                    encode(mid, out, ["-c:a", "flac"])
                    label = f"{c1} {r1} -> FLAC"
                else:
                    c2, r2 = second
                    ext2, a2 = CODECS[c2]
                    out = tmp / f"{kind}_{c1}{r1}_to_{c2}{r2}{ext2}"
                    encode(mid, out, [*a2, r2])
                    label = f"{c1} {r1} -> {c2} {r2}"
                m = measure(out, tools, params)
                rows.append(
                    {"source": kind, "chain": label, "cutoff_hz": m.cutoff_hz, "cliff_db": m.cliff_db}
                )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON instead of a markdown table")
    ap.add_argument("--seconds", type=float, default=40.0, help="length of the generated source")
    ns = ap.parse_args()
    rows = run(ns.seconds)
    if ns.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0
    print("| source | encode chain | measured cutoff | cliff |")
    print("|---|---|---:|---:|")
    for r in rows:
        cut = f"{float(r['cutoff_hz'] or 0) / 1000:.1f} kHz"  # type: ignore[arg-type]
        cliff = "-" if r["cliff_db"] is None else f"{r['cliff_db']:.0f} dB"
        print(f"| {r['source']} | {r['chain']} | {cut} | {cliff} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
