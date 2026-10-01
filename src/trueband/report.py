"""Render scan results as a table, CSV or JSON."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from trueband import __version__
from trueband.classify import Status, Thresholds, Verdict
from trueband.track import Measurement

_STATUS_ORDER = {Status.SUSPECT: 0, Status.BORDERLINE: 1, Status.ERROR: 2, Status.OK: 3}


@dataclass(frozen=True)
class Row:
    """One file's line in a report."""

    display: str
    measurement: Measurement
    verdict: Verdict

    def as_dict(self) -> dict[str, Any]:
        """Flat, JSON/CSV-friendly representation."""
        m = self.measurement
        return {
            "path": self.display,
            "status": self.verdict.status.value,
            "cutoff_hz": m.cutoff_hz,
            "nyquist_hz": m.nyquist_hz,
            "cliff_db": m.cliff_db,
            "codec": m.codec,
            "bitrate_kbps": round(m.bit_rate / 1000) if m.bit_rate else None,
            "sample_rate": m.sample_rate,
            "channels": m.channels,
            "duration_s": m.duration,
            "note": self.verdict.note,
            "absolute_path": m.path,
        }


def sort_rows(rows: Sequence[Row], key: str) -> list[Row]:
    """Sort by ``"status"`` (worst first, then lowest cutoff), ``"cutoff"`` or ``"path"``."""
    if key == "path":
        return sorted(rows, key=lambda r: r.display.lower())
    if key == "cutoff":
        return sorted(
            rows, key=lambda r: (r.measurement.cutoff_hz is None, r.measurement.cutoff_hz or 0, r.display)
        )
    return sorted(
        rows,
        key=lambda r: (
            _STATUS_ORDER[r.verdict.status],
            r.measurement.cutoff_hz if r.measurement.cutoff_hz is not None else 0,
            r.display.lower(),
        ),
    )


def counts(rows: Sequence[Row]) -> Counter[Status]:
    """Number of files per status."""
    return Counter(r.verdict.status for r in rows)


def summary_line(rows: Sequence[Row], elapsed: float | None = None) -> str:
    """E.g. ``"Scanned 956 files: 300 suspect, 62 borderline, 593 ok, 1 error (41.2s)"``."""
    c = counts(rows)
    parts = [f"{c[s]} {s.value}" for s in (Status.SUSPECT, Status.BORDERLINE, Status.OK, Status.ERROR)]
    tail = f" ({elapsed:.1f}s)" if elapsed is not None else ""
    noun = "file" if len(rows) == 1 else "files"
    return f"Scanned {len(rows)} {noun}: {', '.join(parts)}{tail}"


def _khz(hz: float | None) -> str:
    return "-" if hz is None else f"{hz / 1000:.1f}k"


def write_table(rows: Sequence[Row], out: TextIO, *, color: bool = False) -> None:
    """Human-readable aligned table."""
    colors = {Status.SUSPECT: "31", Status.BORDERLINE: "33", Status.OK: "32", Status.ERROR: "35"}
    header = ["STATUS", "CUTOFF", "CLIFF", "CODEC", "KBPS", "PATH"]
    body = []
    for r in rows:
        m = r.measurement
        body.append(
            [
                r.verdict.status.value,
                _khz(m.cutoff_hz),
                "-" if m.cliff_db is None else f"{m.cliff_db:.0f}dB",
                m.codec or "-",
                str(round(m.bit_rate / 1000)) if m.bit_rate else "-",
                r.display + (f"  ({r.verdict.note})" if r.verdict.note else ""),
            ]
        )
    widths = [max(len(header[i]), *(len(b[i]) for b in body)) if body else len(header[i]) for i in range(5)]

    def fmt(cells: list[str], status: Status | None = None) -> str:
        first = cells[0].ljust(widths[0])
        if color and status is not None:
            first = f"\033[{colors[status]}m{first}\033[0m"
        mid = [
            cells[1].rjust(widths[1]),
            cells[2].rjust(widths[2]),
            cells[3].ljust(widths[3]),
            cells[4].rjust(widths[4]),
        ]
        return "  ".join([first, *mid, cells[5]]).rstrip()

    out.write(fmt(header) + "\n")
    for r, cells in zip(rows, body, strict=True):
        out.write(fmt(cells, r.verdict.status) + "\n")


def write_csv(rows: Sequence[Row], out: TextIO) -> None:
    """One CSV row per file, with a header."""
    fields = list(Row("", Measurement(path=""), Verdict(Status.OK, "")).as_dict())
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r.as_dict())


def write_json(rows: Sequence[Row], out: TextIO, thresholds: Thresholds, analysis: dict[str, Any]) -> None:
    """A JSON document with the settings used and one object per file."""
    doc = {
        "trueband": __version__,
        "settings": {
            "analysis": analysis,
            "suspect_below_hz": thresholds.suspect_below_hz,
            "borderline_below_hz": thresholds.borderline_below_hz,
            "sharp_cliff_db": thresholds.sharp_cliff_db,
        },
        "summary": {s.value: counts(rows)[s] for s in Status},
        "files": [r.as_dict() for r in rows],
    }
    json.dump(doc, out, indent=2)
    out.write("\n")
