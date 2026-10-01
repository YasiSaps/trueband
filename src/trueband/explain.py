"""``--explain``: show one file's spectrum so you can see *why* it was classified."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

import numpy as np

from trueband.analysis import CutoffEstimate
from trueband.classify import Thresholds, Verdict
from trueband.track import Measurement

#: Range of the bar chart, in dB relative to the 1-5 kHz reference.
_FLOOR_DB = -100.0


def _bar_chars(stream: TextIO) -> tuple[str, str]:
    encoding = (getattr(stream, "encoding", None) or "ascii").lower()
    return ("█", "·") if "utf" in encoding else ("#", ".")


def render_text(
    m: Measurement,
    verdict: Verdict,
    est: CutoffEstimate | None,
    thresholds: Thresholds,
    margin_db: float,
    out: TextIO,
    *,
    step_hz: float = 500.0,
    width: int = 50,
) -> None:
    """Print file details, the verdict, and a text bar chart of the spectrum."""
    out.write(f"File:        {m.path}\n")
    if m.codec:
        kbps = f", {round(m.bit_rate / 1000)} kbps" if m.bit_rate else ""
        ch = f", {m.channels} ch" if m.channels else ""
        dur = f", {m.duration:.1f}s" if m.duration else ""
        out.write(f"Format:      {m.codec}, {m.sample_rate} Hz{ch}{kbps}{dur}\n")
    out.write(f"Status:      {verdict.status.value.upper()}")
    out.write(f"  ({verdict.note})\n" if verdict.note else "\n")
    if est is None:
        return
    nyq = est.spectrum.nyquist_hz
    cliff = "n/a (content reaches Nyquist)" if est.cliff_db is None else f"{est.cliff_db:.0f} dB"
    out.write(f"Cutoff:      {est.cutoff_hz / 1000:.2f} kHz   (Nyquist {nyq / 1000:.2f} kHz)\n")
    out.write(
        f"Cliff:       {cliff}   (>= {thresholds.sharp_cliff_db:.0f} dB looks like an encoder filter)\n"
    )
    out.write(
        f"Thresholds:  suspect < {thresholds.suspect_below_hz / 1000:.1f} kHz, "
        f"borderline < {thresholds.borderline_below_hz / 1000:.1f} kHz, "
        f"content = within {margin_db:.0f} dB of the 1-5 kHz level\n\n"
    )

    full, empty = _bar_chars(out)
    spec = est.spectrum
    marker_col = round((-margin_db - _FLOOR_DB) / -_FLOOR_DB * width)
    out.write(f"{'band':>12}  level vs 1-5 kHz reference ('|' = -{margin_db:.0f} dB content threshold)\n")
    lo = 0.0
    cutoff_marked = False
    while lo < nyq:
        hi = min(lo + step_hz, nyq)
        sel = (spec.band_lo_hz >= lo) & (spec.band_hi_hz <= hi + 1e-6)
        if not sel.any():
            break
        level = float(np.median(spec.level_db[sel]))
        n = round((min(max(level, _FLOOR_DB), 0.0) - _FLOOR_DB) / -_FLOOR_DB * width)
        cells = [full if i < n else empty for i in range(width)]
        if 0 <= marker_col < width:
            cells[marker_col] = "|"
        tag = ""
        if not cutoff_marked and hi >= est.cutoff_hz:
            tag = "  <- cutoff"
            cutoff_marked = True
        out.write(f"{lo / 1000:5.1f}-{hi / 1000:4.1f}k  {''.join(cells)} {level:6.1f} dB{tag}\n")
        lo = hi


def render_plot(
    m: Measurement, est: CutoffEstimate, thresholds: Thresholds, margin_db: float, dest: Path
) -> None:
    """Save a PNG of the spectrum. Needs the optional ``matplotlib`` dependency.

    Raises:
        ImportError: if matplotlib isn't installed.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = est.spectrum
    centers = (spec.band_lo_hz + spec.band_hi_hz) / 2 / 1000
    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=120)
    ax.plot(centers, spec.level_db, lw=1.2, color="#2b6cb0", label="band level")
    ax.axhline(-margin_db, color="#718096", ls="--", lw=1, label=f"content threshold (-{margin_db:.0f} dB)")
    ax.axvline(est.cutoff_hz / 1000, color="#c53030", lw=1.2, label=f"cutoff {est.cutoff_hz / 1000:.2f} kHz")
    ax.axvspan(0, thresholds.suspect_below_hz / 1000, color="#c53030", alpha=0.04)
    ax.axvspan(
        thresholds.suspect_below_hz / 1000, thresholds.borderline_below_hz / 1000, color="#d69e2e", alpha=0.06
    )
    ax.set_xlim(0, spec.nyquist_hz / 1000)
    ax.set_ylim(max(float(np.nanmin(spec.level_db)), -140) - 5, 15)
    ax.set_xlabel("frequency (kHz)")
    ax.set_ylabel("dB relative to 1-5 kHz median")
    ax.set_title(Path(m.path).name, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(dest)
    plt.close(fig)
