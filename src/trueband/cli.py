"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from trueband import __version__
from trueband.classify import (
    DEFAULT_BORDERLINE_BELOW_HZ,
    DEFAULT_SHARP_CLIFF_DB,
    DEFAULT_SUSPECT_BELOW_HZ,
    Status,
    Thresholds,
    classify,
)
from trueband.explain import render_plot, render_text
from trueband.ffmpeg import FFmpegNotFoundError, Tools, find_tools
from trueband.report import Row, sort_rows, summary_line, write_csv, write_json, write_table
from trueband.scan import DEFAULT_EXTENSIONS, Progress, ResultCache, discover, run_scan
from trueband.track import (
    DEFAULT_SEGMENT_SECONDS,
    AnalysisParams,
    Measurement,
    measure_audio,
)

log = logging.getLogger("trueband")

EXIT_OK = 0
EXIT_FLAGGED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

EPILOG = """\
status meanings:
  suspect     cutoff below the suspect threshold: typical of a <=160 kbps lossy source
  borderline  cutoff between the thresholds: typical of a ~192 kbps source
  ok          content extends to (or near) the top of the audible band
  error       the file could not be analysed (reason given)

trueband is a heuristic. Treat flagged files as a list to check by ear or in a
spectrogram viewer, not as a verdict. See the README for what it can't detect.

examples:
  trueband ~/Music/DJ
  trueband ~/Music/DJ --exclude STEMS --exclude '*_to_delete*' --format csv -o report.csv
  trueband ~/Music/DJ --cache ~/.cache/trueband.json --show flagged
  trueband --explain "track.mp3" --plot track.png
"""


def parse_hz(text: str) -> float:
    """Parse ``18000``, ``18k``, ``18kHz`` or ``18.5 khz`` into Hz."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(k|khz|hz)?\s*", text, flags=re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(f"invalid frequency: {text!r} (try 18000 or 18k)")
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("k", "khz"):
        value *= 1000
    if value <= 0:
        raise argparse.ArgumentTypeError("frequency must be positive")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(
        prog="trueband",
        description=(
            "Scan audio files and flag ones whose real frequency content doesn't match their "
            "format: low-bitrate rips re-encoded at higher bitrates, or lossy files converted to "
            "FLAC/WAV/AIFF."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="*", type=Path, help="files and/or folders to scan (folders are recursive)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    out = p.add_argument_group("output")
    out.add_argument(
        "-f",
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="report format (default: table)",
    )
    out.add_argument(
        "-o", "--output", type=Path, metavar="FILE", help="write the report to FILE instead of stdout"
    )
    out.add_argument(
        "--show",
        choices=("all", "flagged", "suspect"),
        default="all",
        help="which files to include: all, flagged (suspect+borderline+error), or suspect only",
    )
    out.add_argument(
        "--sort",
        choices=("status", "cutoff", "path"),
        default="status",
        help="row order (default: status, worst first)",
    )
    out.add_argument(
        "--fail-on",
        choices=("suspect", "borderline"),
        help="exit with status 1 if any file is at least this bad (useful in scripts)",
    )
    out.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour the table's status column (default: auto)",
    )

    sel = p.add_argument_group("file selection")
    sel.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only scan files matching GLOB (repeatable)",
    )
    sel.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files/folders matching GLOB, e.g. STEMS or '*/Samples/*' (repeatable)",
    )
    sel.add_argument(
        "--extensions",
        default=",".join(e.lstrip(".") for e in DEFAULT_EXTENSIONS),
        metavar="LIST",
        help="comma-separated extensions to scan in folders (default: %(default)s)",
    )
    sel.add_argument(
        "--include-hidden",
        action="store_true",
        help="also scan dot-files and dot-folders (e.g. macOS '._' files, skipped by default)",
    )

    th = p.add_argument_group("thresholds")
    th.add_argument(
        "--suspect-below",
        type=parse_hz,
        default=DEFAULT_SUSPECT_BELOW_HZ,
        metavar="FREQ",
        help="cutoff below this is 'suspect' (default: 18k)",
    )
    th.add_argument(
        "--borderline-below",
        type=parse_hz,
        default=DEFAULT_BORDERLINE_BELOW_HZ,
        metavar="FREQ",
        help="cutoff below this is 'borderline' (default: 19.2k)",
    )
    th.add_argument(
        "--margin-db",
        type=_positive_float,
        default=AnalysisParams().margin_db,
        metavar="DB",
        help="content counts if within DB of the 1-5 kHz level (default: %(default)s)",
    )
    th.add_argument(
        "--sharp-cliff-db",
        type=_positive_float,
        default=DEFAULT_SHARP_CLIFF_DB,
        metavar="DB",
        help="drop across the cutoff that counts as an encoder-style cliff (default: %(default)s)",
    )
    th.add_argument(
        "--segment-seconds",
        type=_positive_float,
        default=DEFAULT_SEGMENT_SECONDS,
        metavar="S",
        help="length of each analysed excerpt (default: %(default)s)",
    )

    run = p.add_argument_group("execution")
    run.add_argument(
        "-j",
        "--workers",
        type=_positive_int,
        default=min(8, os.cpu_count() or 2),
        help="files analysed in parallel (default: %(default)s)",
    )
    run.add_argument(
        "--cache",
        type=Path,
        metavar="FILE",
        help="JSON file to store results in, so an interrupted or repeated scan resumes",
    )
    run.add_argument("--no-progress", action="store_true", help="don't show the progress bar")
    verbosity = run.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="log each problem file as it's found (-vv for debug output)",
    )
    verbosity.add_argument("-q", "--quiet", action="store_true", help="only print the report and errors")

    ex = p.add_argument_group("single-file inspection")
    ex.add_argument(
        "--explain",
        type=Path,
        metavar="FILE",
        help="show the spectrum and reasoning for one file instead of scanning",
    )
    ex.add_argument(
        "--plot",
        type=Path,
        metavar="PNG",
        help="with --explain, also save a spectrum plot (needs: pip install 'trueband[plot]')",
    )
    return p


def _setup_logging(verbose: int, quiet: bool) -> None:
    level = logging.WARNING
    if quiet:
        level = logging.ERROR
    elif verbose >= 2:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    logging.basicConfig(level=level, format="trueband: %(message)s", stream=sys.stderr)


def _explain(args: argparse.Namespace, tools: Tools, params: AnalysisParams, th: Thresholds) -> int:
    path: Path = args.explain
    m, est = measure_audio(path, tools, params)
    verdict = classify(m, th)
    render_text(m, verdict, est, th, params.margin_db, sys.stdout)
    if args.plot:
        if est is None:
            log.error("nothing to plot: %s", m.error)
            return EXIT_USAGE
        try:
            render_plot(m, est, th, params.margin_db, args.plot)
        except ImportError:
            log.error("--plot needs matplotlib: pip install 'trueband[plot]'")
            return EXIT_USAGE
        print(f"\nSaved plot to {args.plot}")
    return EXIT_OK if m.error is None else EXIT_FLAGGED


def _open_output(path: Path | None) -> TextIO:
    if path is None:
        return sys.stdout
    return path.open("w", encoding="utf-8", newline="")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``trueband`` command. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.quiet)

    if args.plot and not args.explain:
        parser.error("--plot only works together with --explain")
    if not args.explain and not args.paths:
        parser.error("give at least one file or folder to scan (or use --explain FILE)")
    if args.explain and args.paths:
        parser.error("--explain takes a single file; don't combine it with scan paths")
    try:
        th = Thresholds(args.suspect_below, args.borderline_below, args.sharp_cliff_db)
    except ValueError as exc:
        parser.error(str(exc))
    params = AnalysisParams(margin_db=args.margin_db, segment_seconds=args.segment_seconds)

    try:
        tools = find_tools()
    except FFmpegNotFoundError as exc:
        print(f"trueband: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.explain:
        if args.explain.is_dir():
            parser.error("--explain takes a single audio file, not a folder")
        return _explain(args, tools, params, th)

    extensions = [e.strip() for e in args.extensions.split(",") if e.strip()]
    targets, problems = discover(
        args.paths,
        include=args.include,
        exclude=args.exclude,
        extensions=extensions,
        include_hidden=args.include_hidden,
    )
    for prob in problems:
        log.warning("skipping %s: %s", prob.path, prob.reason)
    if not targets:
        log.error("no audio files found")
        return EXIT_USAGE if problems else EXIT_OK

    cache = None
    if args.cache:
        cache = ResultCache(args.cache.expanduser(), params)
        cache.load()

    show_progress = not (args.quiet or args.no_progress) and sys.stderr.isatty()
    progress = Progress(sys.stderr) if show_progress else None

    def on_result(target: object, m: Measurement) -> None:
        if m.error:
            log.info("%s: %s", m.path, m.error)

    started = time.monotonic()
    try:
        results = run_scan(
            targets, tools, params, workers=args.workers, cache=cache, progress=progress, on_result=on_result
        )
    except KeyboardInterrupt:
        if progress:
            progress.close()
        print("trueband: interrupted" + (" (progress saved to cache)" if cache else ""), file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        if progress:
            progress.close()
    elapsed = time.monotonic() - started

    rows = [Row(t.display, results[t.path], classify(results[t.path], th)) for t in targets]
    shown = rows
    if args.show == "flagged":
        shown = [r for r in rows if r.verdict.status is not Status.OK]
    elif args.show == "suspect":
        shown = [r for r in rows if r.verdict.status is Status.SUSPECT]
    shown = sort_rows(shown, args.sort)

    try:
        out = _open_output(args.output)
    except OSError as exc:
        log.error("cannot write %s: %s", args.output, exc.strerror or exc)
        return EXIT_USAGE
    try:
        if args.format == "csv":
            write_csv(shown, out)
        elif args.format == "json":
            write_json(shown, out, th, params.signature())
        else:
            color = args.color == "always" or (
                args.color == "auto" and args.output is None and out.isatty() and "NO_COLOR" not in os.environ
            )
            write_table(shown, out, color=color)
    finally:
        if out is not sys.stdout:
            out.close()

    sys.stdout.flush()  # keep the summary below the report when both go to a terminal
    if not args.quiet:
        print(summary_line(rows, elapsed), file=sys.stderr)
        if args.output:
            print(f"Report written to {args.output}", file=sys.stderr)

    statuses = {r.verdict.status for r in rows}
    if args.fail_on == "suspect" and Status.SUSPECT in statuses:
        return EXIT_FLAGGED
    if args.fail_on == "borderline" and statuses & {Status.SUSPECT, Status.BORDERLINE}:
        return EXIT_FLAGGED
    return EXIT_OK
