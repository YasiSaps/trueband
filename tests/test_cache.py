from __future__ import annotations

import json
import os
from pathlib import Path

from trueband.scan import ResultCache
from trueband.track import AnalysisParams, Measurement


def make_file(tmp_path: Path, name: str = "a.mp3", data: bytes = b"abc") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_roundtrip(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    cache_path = tmp_path / "cache.json"
    c = ResultCache(cache_path, AnalysisParams())
    m = Measurement(path=str(f), cutoff_hz=16000.0, codec="mp3", warnings=["w"])
    c.put(f, m)
    c.save(force=True)

    c2 = ResultCache(cache_path, AnalysisParams())
    c2.load()
    assert c2.get(f) == m


def test_changed_file_is_a_miss(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    c = ResultCache(tmp_path / "c.json", AnalysisParams())
    c.put(f, Measurement(path=str(f), cutoff_hz=1.0))
    f.write_bytes(b"different length")
    assert c.get(f) is None


def test_touched_file_is_a_miss(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    c = ResultCache(tmp_path / "c.json", AnalysisParams())
    c.put(f, Measurement(path=str(f), cutoff_hz=1.0))
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    assert c.get(f) is None


def test_different_analysis_settings_discard_cache(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    cache_path = tmp_path / "c.json"
    c = ResultCache(cache_path, AnalysisParams(margin_db=45))
    c.put(f, Measurement(path=str(f), cutoff_hz=1.0))
    c.save(force=True)
    c2 = ResultCache(cache_path, AnalysisParams(margin_db=50))
    c2.load()
    assert c2.get(f) is None


def test_errors_are_not_cached(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    c = ResultCache(tmp_path / "c.json", AnalysisParams())
    c.put(f, Measurement(path=str(f), error="boom"))
    assert c.get(f) is None


def test_corrupt_cache_file_is_ignored(tmp_path: Path) -> None:
    cache_path = tmp_path / "c.json"
    cache_path.write_text("{not json")
    c = ResultCache(cache_path, AnalysisParams())
    c.load()
    assert c.entries == {}


def test_save_is_atomic_and_valid_json(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    cache_path = tmp_path / "nested" / "c.json"
    c = ResultCache(cache_path, AnalysisParams())
    c.put(f, Measurement(path=str(f), cutoff_hz=2.0))
    c.save(force=True)
    data = json.loads(cache_path.read_text())
    assert data["format"] == 1 and str(f) in data["entries"]
    assert [p.name for p in cache_path.parent.iterdir()] == ["c.json"]  # no temp files left


def test_save_batches_writes(tmp_path: Path) -> None:
    f = make_file(tmp_path)
    cache_path = tmp_path / "c.json"
    c = ResultCache(cache_path, AnalysisParams())
    c.put(f, Measurement(path=str(f), cutoff_hz=2.0))
    c.save(every=25)
    assert not cache_path.exists()
    c.save(force=True)
    assert cache_path.exists()
