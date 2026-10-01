"""Find audio files, analyse them in parallel, and cache results for resuming."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trueband import __version__
from trueband.ffmpeg import Tools
from trueband.track import AnalysisParams, Measurement, measure

log = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".mp4",
    ".flac",
    ".wav",
    ".aif",
    ".aiff",
    ".ogg",
    ".oga",
    ".opus",
)

CACHE_FORMAT = 1


@dataclass(frozen=True)
class Target:
    """A file to analyse.

    Attributes:
        path: Absolute path.
        display: Path shown in reports (relative to the scanned folder, or as
            given on the command line for individual files).
    """

    path: Path
    display: str


@dataclass(frozen=True)
class DiscoveryProblem:
    """A folder that could not be listed."""

    path: str
    reason: str


def _matches(rel: str, name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(name, p) for p in patterns)


def discover(
    inputs: Iterable[Path],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    extensions: Sequence[str] = DEFAULT_EXTENSIONS,
    include_hidden: bool = False,
) -> tuple[list[Target], list[DiscoveryProblem]]:
    """Expand files and folders into a sorted, de-duplicated list of targets.

    Folders are walked recursively. Inside folders, files are kept when their
    extension is in ``extensions``, they match an ``include`` glob (if any),
    and they match no ``exclude`` glob. Globs are matched case-sensitively
    against both the path relative to the folder (``/``-separated) and the bare
    file or folder name, so ``--exclude STEMS`` prunes any folder called STEMS
    and ``--exclude '*/Samples/*'`` prunes by path.

    Files named explicitly are always kept (the user asked for them), except
    that excludes still apply. Hidden files and folders (dot-names, including
    macOS ``._`` resource-fork files) are skipped unless ``include_hidden``.

    Returns:
        ``(targets, problems)`` where problems are unreadable folders.
    """
    exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in extensions}
    seen: set[Path] = set()
    targets: list[Target] = []
    problems: list[DiscoveryProblem] = []

    def add(path: Path, display: str) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            targets.append(Target(resolved, display))

    for raw in inputs:
        if raw.is_dir():
            root = raw

            def on_error(err: OSError) -> None:
                problems.append(DiscoveryProblem(str(err.filename), err.strerror or str(err)))

            for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
                here = Path(dirpath)
                rel_dir = here.relative_to(root).as_posix()
                rel_dir = "" if rel_dir == "." else rel_dir + "/"
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if (include_hidden or not d.startswith(".")) and not _matches(rel_dir + d, d, exclude)
                )
                for name in sorted(filenames):
                    if not include_hidden and name.startswith("."):
                        continue
                    if Path(name).suffix.lower() not in exts:
                        continue
                    rel = rel_dir + name
                    if exclude and _matches(rel, name, exclude):
                        continue
                    if include and not _matches(rel, name, include):
                        continue
                    add(here / name, str(Path(raw) / rel) if str(raw) not in (".", "") else rel)
        elif raw.exists() or raw.is_symlink():
            if exclude and _matches(raw.as_posix(), raw.name, exclude):
                continue
            add(raw, str(raw))
        else:
            problems.append(DiscoveryProblem(str(raw), "no such file or directory"))

    targets.sort(key=lambda t: t.display.lower())
    return targets, problems


class ResultCache:
    """JSON file of measurements, keyed by path, size and modification time.

    A cache written with different :class:`AnalysisParams` is ignored, since
    its cutoffs were measured differently. Thresholds are *not* part of the
    key: cached measurements are simply re-classified.
    """

    def __init__(self, path: Path, params: AnalysisParams) -> None:
        self.path = path
        self.signature = params.signature()
        self.entries: dict[str, dict[str, Any]] = {}
        self._dirty = 0

    def load(self) -> None:
        """Load the cache file if present and compatible; otherwise start empty."""
        try:
            with self.path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable cache %s: %s", self.path, exc)
            return
        if (
            not isinstance(data, dict)
            or data.get("format") != CACHE_FORMAT
            or data.get("analysis") != self.signature
        ):
            log.warning("cache %s was made with different settings; starting fresh", self.path)
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self.entries = entries

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def get(self, path: Path) -> Measurement | None:
        """Return the cached measurement if the file is unchanged."""
        entry = self.entries.get(str(path))
        stamp = self._stamp(path)
        if entry is None or stamp is None:
            return None
        if [entry.get("size"), entry.get("mtime_ns")] != list(stamp):
            return None
        try:
            return Measurement.from_dict(entry["measurement"])
        except (KeyError, TypeError):
            return None

    def put(self, path: Path, m: Measurement) -> None:
        """Record a successful measurement. Errors are not cached, so they retry."""
        if m.error is not None:
            return
        stamp = self._stamp(path)
        if stamp is None:
            return
        self.entries[str(path)] = {"size": stamp[0], "mtime_ns": stamp[1], "measurement": m.to_dict()}
        self._dirty += 1

    def save(self, force: bool = False, every: int = 25) -> None:
        """Atomically write the cache (every ``every`` new entries, or if forced)."""
        if not self._dirty or (not force and self._dirty < every):
            return
        payload = {
            "format": CACHE_FORMAT,
            "trueband": __version__,
            "analysis": self.signature,
            "entries": self.entries,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".trueband-cache-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            Path(tmp).replace(self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._dirty = 0


def run_scan(
    targets: Sequence[Target],
    tools: Tools,
    params: AnalysisParams,
    *,
    workers: int,
    cache: ResultCache | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[Path, Measurement]:
    """Measure every target (cached results first), using up to ``workers`` threads.

    Work is mostly ffmpeg subprocesses and numpy FFTs, both of which release
    the GIL, so threads parallelise well here. On ``KeyboardInterrupt`` the
    remaining work is cancelled, the cache is saved and the interrupt re-raised.
    """
    results: dict[Path, Measurement] = {}
    for t in targets:
        cached = cache.get(t.path) if cache else None
        if cached is not None:
            results[t.path] = cached
    todo = [t for t in targets if t.path not in results]
    if results:
        log.info("%d file(s) loaded from cache, %d to analyse", len(results), len(todo))
    if progress:
        progress(len(results), len(targets))

    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="trueband")
    try:
        futures = {pool.submit(measure, t.path, tools, params): t for t in todo}
        for fut in as_completed(futures):
            t = futures[fut]
            m = results[t.path] = fut.result()
            if m.error:
                log.info("%s: %s", t.display, m.error)
            if cache:
                cache.put(t.path, m)
                cache.save()
            if progress:
                progress(len(results), len(targets))
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if cache:
            cache.save(force=True)
    pool.shutdown()
    return results


class Progress:
    """Minimal single-line progress bar on a TTY stream (no dependencies)."""

    def __init__(self, stream: Any, width: int = 28) -> None:
        self.stream = stream
        self.width = width
        self.start = time.monotonic()
        self._last = 0.0

    def __call__(self, done: int, total: int) -> None:
        """Redraw the bar (rate-limited to ~10 updates per second)."""
        now = time.monotonic()
        if done < total and now - self._last < 0.1:
            return
        self._last = now
        frac = done / total if total else 1.0
        filled = int(self.width * frac)
        elapsed = now - self.start
        eta = ""
        if 0 < done < total:
            remaining = elapsed / done * (total - done)
            eta = f"  eta {int(remaining // 60)}m{int(remaining % 60):02d}s"
        bar = "#" * filled + "-" * (self.width - filled)
        self.stream.write(f"\r[{bar}] {done}/{total} {frac:6.1%}{eta}   ")
        self.stream.flush()

    def close(self) -> None:
        """Clear the progress line."""
        self.stream.write("\r" + " " * (self.width + 50) + "\r")
        self.stream.flush()
