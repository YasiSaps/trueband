from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from trueband.scan import discover


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    touch(
        tmp_path,
        "a.mp3",
        "b.FLAC",
        "notes.txt",
        "cover.jpg",
        "House/c.m4a",
        "House/d.wav",
        "House/STEMS/e.wav",
        "Samples/f.aiff",
        "Deep/er/g.opus",
        "Deep/er/h.ogg",
        "._a.mp3",
        ".hidden/i.mp3",
    )
    return tmp_path


def names(targets: list) -> list[str]:  # type: ignore[type-arg]
    return sorted(t.path.name for t in targets)


def test_walks_recursively_and_filters_extensions(tree: Path) -> None:
    targets, problems = discover([tree])
    assert names(targets) == ["a.mp3", "b.FLAC", "c.m4a", "d.wav", "e.wav", "f.aiff", "g.opus", "h.ogg"]
    assert problems == []


def test_display_paths_are_relative_to_given_folder(tree: Path) -> None:
    targets, _ = discover([tree])
    displays = {t.display for t in targets}
    assert str(tree / "House/c.m4a") in displays


def test_exclude_by_folder_name(tree: Path) -> None:
    targets, _ = discover([tree], exclude=["STEMS"])
    assert "e.wav" not in names(targets)
    assert "d.wav" in names(targets)


def test_exclude_by_path_glob(tree: Path) -> None:
    targets, _ = discover([tree], exclude=["Deep/*"])
    assert "g.opus" not in names(targets) and "h.ogg" not in names(targets)


def test_exclude_by_file_glob(tree: Path) -> None:
    targets, _ = discover([tree], exclude=["*.wav"])
    assert not [n for n in names(targets) if n.endswith(".wav")]


def test_include_glob(tree: Path) -> None:
    targets, _ = discover([tree], include=["House/*"])
    assert names(targets) == ["c.m4a", "d.wav", "e.wav"]


def test_custom_extensions(tree: Path) -> None:
    targets, _ = discover([tree], extensions=["opus", ".OGG"])
    assert names(targets) == ["g.opus", "h.ogg"]


def test_hidden_files_skipped_by_default(tree: Path) -> None:
    targets, _ = discover([tree])
    assert "._a.mp3" not in names(targets) and "i.mp3" not in names(targets)
    targets, _ = discover([tree], include_hidden=True)
    assert "._a.mp3" in names(targets) and "i.mp3" in names(targets)


def test_explicit_file_kept_regardless_of_extension(tree: Path) -> None:
    targets, _ = discover([tree / "notes.txt"])
    assert names(targets) == ["notes.txt"]


def test_duplicates_removed(tree: Path) -> None:
    targets, _ = discover([tree, tree / "a.mp3", tree / "House"])
    assert len(targets) == len({t.path for t in targets})


def test_missing_path_reported(tmp_path: Path) -> None:
    targets, problems = discover([tmp_path / "nope"])
    assert targets == []
    assert problems and "no such file" in problems[0].reason


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, non-root")
def test_unreadable_folder_reported_not_fatal(tree: Path) -> None:
    locked = tree / "House"
    locked.chmod(0)
    try:
        targets, problems = discover([tree])
    finally:
        locked.chmod(0o755)
    assert "a.mp3" in names(targets)
    assert any("House" in p.path for p in problems)
