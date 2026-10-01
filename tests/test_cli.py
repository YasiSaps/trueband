from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import pytest
from conftest import HAVE_FFMPEG, write_audio
from signals import shaped_noise

import trueband.scan
from trueband import __version__
from trueband.cli import main, parse_hz

SR = 44100


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny library: one full-band FLAC, one fake FLAC, one 128k-sourced WAV, junk."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    root = tmp_path_factory.mktemp("lib")
    (root / "Good").mkdir()
    (root / "Bad").mkdir()
    (root / "STEMS").mkdir()
    write_audio(root / "Good/full.flac", shaped_noise(8, SR, channels=2), SR, ["-c:a", "flac"])
    write_audio(
        root / "Bad/fake.flac", shaped_noise(8, SR, cutoff_hz=16000, channels=2), SR, ["-c:a", "flac"]
    )
    write_audio(root / "Bad/mid.wav", shaped_noise(8, SR, cutoff_hz=18500), SR, ["-c:a", "pcm_s16le"])
    write_audio(root / "STEMS/stem.wav", shaped_noise(4, SR, cutoff_hz=12000), SR, ["-c:a", "pcm_s16le"])
    (root / "Bad/broken.mp3").write_text("not audio")
    (root / "readme.txt").write_text("ignored")
    return root


def run_cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(args)
    out, err = capsys.readouterr()
    return code, out, err


# ----------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("text", "hz"),
    [
        ("18000", 18000),
        ("18k", 18000),
        ("18.5k", 18500),
        ("19.2kHz", 19200),
        (" 16 KHZ ", 16000),
        ("17000hz", 17000),
    ],
)
def test_parse_hz(text: str, hz: float) -> None:
    assert parse_hz(text) == hz


@pytest.mark.parametrize("text", ["", "abc", "18 MHz", "-5k", "0"])
def test_parse_hz_rejects(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_hz(text)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_paths_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "give at least one file or folder" in capsys.readouterr().err


def test_bad_threshold_order_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([".", "--suspect-below", "20k", "--borderline-below", "19k"])
    assert exc.value.code == 2


def test_plot_without_explain_is_usage_error() -> None:
    with pytest.raises(SystemExit):
        main([".", "--plot", "x.png"])


def test_missing_ffmpeg_exits_2_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRUEBAND_FFPROBE", "no-such-ffprobe-xyz")
    code, _, err = run_cli([str(tmp_path)], capsys)
    assert code == 2
    assert "Could not find" in err and "Traceback" not in err


# ----------------------------------------------------------------- scanning


@pytest.mark.ffmpeg
def test_table_output(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run_cli([str(library), "--no-progress"], capsys)
    assert code == 0
    lines = out.strip().splitlines()
    assert lines[0].split()[:3] == ["STATUS", "CUTOFF", "CLIFF"]
    body = "\n".join(lines[1:])
    # Worst first: suspects, then borderline, then errors, then ok.
    order = [ln.split()[0] for ln in lines[1:]]
    assert order == ["suspect", "suspect", "borderline", "error", "ok"]
    assert "fake.flac" in body and "lossless file with a lossy-style cutoff" in body
    assert "readme.txt" not in body
    assert "Scanned 5 files: 2 suspect, 1 borderline, 1 ok, 1 error" in err


@pytest.mark.ffmpeg
def test_exclude(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run_cli([str(library), "--exclude", "STEMS", "-q"], capsys)
    assert "stem.wav" not in out


@pytest.mark.ffmpeg
def test_json_output(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli([str(library), "-f", "json", "-q"], capsys)
    assert code == 0
    doc = json.loads(out)
    assert doc["summary"] == {"suspect": 2, "borderline": 1, "ok": 1, "error": 1}
    assert doc["settings"]["suspect_below_hz"] == 18000
    by_name = {Path(f["path"]).name: f for f in doc["files"]}
    assert by_name["fake.flac"]["cutoff_hz"] == pytest.approx(16000, abs=150)
    assert by_name["broken.mp3"]["status"] == "error"


@pytest.mark.ffmpeg
def test_csv_output_to_file(library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "report.csv"
    code, out, err = run_cli([str(library), "-f", "csv", "-o", str(dest), "--sort", "path"], capsys)
    assert code == 0 and out == ""
    assert f"Report written to {dest}" in err
    rows = list(csv.DictReader(io.StringIO(dest.read_text())))
    assert len(rows) == 5
    assert {"path", "status", "cutoff_hz", "cliff_db", "note"} <= set(rows[0])
    assert [r["path"] for r in rows] == sorted((r["path"] for r in rows), key=str.lower)


@pytest.mark.ffmpeg
@pytest.mark.parametrize(("show", "expected"), [("flagged", 4), ("suspect", 2), ("all", 5)])
def test_show_filter(library: Path, capsys: pytest.CaptureFixture[str], show: str, expected: int) -> None:
    _, out, _ = run_cli([str(library), "--show", show, "-f", "json", "-q"], capsys)
    assert len(json.loads(out)["files"]) == expected


@pytest.mark.ffmpeg
def test_custom_thresholds_change_status(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run_cli(
        [str(library / "Bad/mid.wav"), "--borderline-below", "18k", "-f", "json", "-q"], capsys
    )
    assert json.loads(out)["files"][0]["status"] == "ok"


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    ("fail_on", "target", "code"),
    [
        ("suspect", "Good", 0),
        ("suspect", "Bad", 1),
        ("borderline", "Bad/mid.wav", 1),
        ("suspect", "Bad/mid.wav", 0),
    ],
)
def test_fail_on(
    library: Path, capsys: pytest.CaptureFixture[str], fail_on: str, target: str, code: int
) -> None:
    assert run_cli([str(library / target), "--fail-on", fail_on, "-q"], capsys)[0] == code


@pytest.mark.ffmpeg
def test_cache_resumes(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache.json"
    first = run_cli([str(library), "--cache", str(cache), "-f", "json", "-q"], capsys)[1]
    assert cache.exists()

    calls: list[Path] = []
    real = trueband.scan.measure

    def counting(path: Path, *a: object, **k: object) -> object:
        calls.append(path)
        return real(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(trueband.scan, "measure", counting)
    second = run_cli([str(library), "--cache", str(cache), "-f", "json", "-q"], capsys)[1]
    # Only the unreadable file is retried; everything else comes from the cache.
    assert [p.name for p in calls] == ["broken.mp3"]
    assert json.loads(first)["files"] == json.loads(second)["files"]


@pytest.mark.ffmpeg
def test_explain(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(["--explain", str(library / "Bad/fake.flac")], capsys)
    assert code == 0
    assert "Status:      SUSPECT" in out
    assert "Cutoff:      16.00 kHz" in out
    assert "<- cutoff" in out
    assert out.count(" dB") > 40  # one bar per 500 Hz band


@pytest.mark.ffmpeg
def test_explain_broken_file(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(["--explain", str(library / "Bad/broken.mp3")], capsys)
    assert code == 1
    assert "Status:      ERROR" in out


@pytest.mark.ffmpeg
def test_explain_plot(library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("matplotlib")
    png = tmp_path / "spec.png"
    code, _, _ = run_cli(["--explain", str(library / "Good/full.flac"), "--plot", str(png)], capsys)
    assert code == 0
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.ffmpeg
def test_module_entry_point(library: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "trueband", str(library / "Good"), "-f", "json", "-q"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["summary"]["ok"] == 1


@pytest.mark.ffmpeg
def test_explain_folder_is_usage_error(library: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--explain", str(library)])
    assert exc.value.code == 2
