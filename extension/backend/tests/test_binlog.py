"""Tests for the BIN log archiver (services/binlog.py)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from doris.services import binlog

# ── helpers ────────────────────────────────────────────────────────


def _make_bin(logs_dir: Path, num: int, *, mtime: datetime, size: int = 64) -> Path:
    p = logs_dir / f"{num:08d}.BIN"
    p.write_bytes(b"X" * size)
    ts = mtime.timestamp()
    os.utime(p, (ts, ts))
    return p


def _write_lastlog(logs_dir: Path, num: int) -> None:
    (logs_dir / "LASTLOG.TXT").write_text(f"{num}\n")


def _make_dive(
    dive_dir: Path,
    *,
    name: str = "Test Dive",
    start: datetime,
    end: datetime | None,
    bin_log_start_num: int | None = 100,
    dive_id: str = "dive_0001",
) -> Path:
    record: dict = {
        "dive_name": name,
        "started_at": start.isoformat(),
        "status": "completed",
        "profile_id": 7,
    }
    if end is not None:
        record["ended_at"] = end.isoformat()
    if bin_log_start_num is not None:
        record["bin_log_start_num"] = bin_log_start_num
    p = dive_dir / f"{dive_id}.json"
    p.write_text(json.dumps(record, indent=2))
    return p


# ── LASTLOG / iter_bin_logs ────────────────────────────────────────


def test_read_lastlog_num(tmp_path: Path) -> None:
    _write_lastlog(tmp_path, 42)
    assert binlog.read_lastlog_num(tmp_path) == 42


def test_read_lastlog_num_missing(tmp_path: Path) -> None:
    assert binlog.read_lastlog_num(tmp_path) is None


def test_read_lastlog_num_garbage(tmp_path: Path) -> None:
    (tmp_path / "LASTLOG.TXT").write_text("not a number\n")
    assert binlog.read_lastlog_num(tmp_path) is None


def test_iter_bin_logs_filters_and_sorts(tmp_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)
    for n in (10, 11, 12, 13):
        _make_bin(tmp_path, n, mtime=now)
    (tmp_path / "LASTLOG.TXT").write_text("13")
    (tmp_path / "junk.txt").write_text("ignore me")

    nums = sorted(n for n, _ in binlog.iter_bin_logs(logs_dir=tmp_path))
    assert nums == [10, 11, 12, 13]

    nums_range = sorted(
        n for n, _ in binlog.iter_bin_logs(min_num=11, max_num=12, logs_dir=tmp_path)
    )
    assert nums_range == [11, 12]


# ── selection ──────────────────────────────────────────────────────


def test_select_primary_range(tmp_path: Path) -> None:
    """When ``bin_log_start_num`` + LASTLOG bracket the dive, that range wins."""
    now = datetime.now(tz=timezone.utc)
    # Pre-existing log (before dive)
    _make_bin(tmp_path, 100, mtime=now - timedelta(hours=2))
    # The dive's logs
    _make_bin(tmp_path, 101, mtime=now - timedelta(minutes=10))
    _make_bin(tmp_path, 102, mtime=now - timedelta(minutes=2))
    _write_lastlog(tmp_path, 102)

    record = {
        "started_at": (now - timedelta(minutes=15)).isoformat(),
        "ended_at": now.isoformat(),
        "bin_log_start_num": 100,
    }
    paths = binlog.select_bin_logs_for_dive(record, logs_dir=tmp_path)
    assert [p.name for p in paths] == ["00000101.BIN", "00000102.BIN"]


def test_select_skips_zero_byte(tmp_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)
    _make_bin(tmp_path, 50, mtime=now, size=0)  # empty
    _make_bin(tmp_path, 51, mtime=now, size=128)
    _write_lastlog(tmp_path, 51)
    record = {
        "started_at": (now - timedelta(minutes=5)).isoformat(),
        "ended_at": now.isoformat(),
        "bin_log_start_num": 49,
    }
    paths = binlog.select_bin_logs_for_dive(record, logs_dir=tmp_path)
    assert [p.name for p in paths] == ["00000051.BIN"]


def test_select_fallback_window(tmp_path: Path) -> None:
    """No bin_log_start_num -> fall back to mtime overlap with dive window."""
    now = datetime.now(tz=timezone.utc)
    _make_bin(tmp_path, 70, mtime=now - timedelta(hours=3))  # before window
    _make_bin(tmp_path, 71, mtime=now - timedelta(minutes=5))  # in window
    _make_bin(tmp_path, 72, mtime=now - timedelta(minutes=1))  # in window
    _make_bin(tmp_path, 73, mtime=now + timedelta(minutes=10))  # after window+grace

    record = {
        "started_at": (now - timedelta(minutes=10)).isoformat(),
        "ended_at": now.isoformat(),
        # No bin_log_start_num, no LASTLOG -> fallback path
    }
    paths = binlog.select_bin_logs_for_dive(record, logs_dir=tmp_path)
    assert [p.name for p in paths] == ["00000071.BIN", "00000072.BIN"]


def test_select_no_match(tmp_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)
    record = {
        "started_at": now.isoformat(),
        "ended_at": now.isoformat(),
        "bin_log_start_num": 5,
    }
    assert binlog.select_bin_logs_for_dive(record, logs_dir=tmp_path) == []


# ── slug / naming ──────────────────────────────────────────────────


def test_slug_for_dive_uses_dive_name(tmp_path: Path) -> None:
    p = tmp_path / "dive_0042.json"
    p.write_text("{}")
    s = binlog.slug_for_dive({"dive_name": "  Hello, World!  "}, p)
    assert s == "hello_world"


def test_slug_for_dive_falls_back_to_id(tmp_path: Path) -> None:
    p = tmp_path / "dive_0042.json"
    p.write_text("{}")
    s = binlog.slug_for_dive({"dive_name": ""}, p)
    assert s == "dive_0042"


def test_dest_filename_always_suffix() -> None:
    name = binlog._dest_filename("mydive", 364, "00000364.BIN")
    assert name == "mydive_00000364.BIN"


# ── archive integration ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_skipped_no_usb(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    dives = tmp_path / "dives"
    dives.mkdir()

    now = datetime.now(tz=timezone.utc)
    _make_bin(logs, 11, mtime=now)
    _write_lastlog(logs, 11)
    dive_file = _make_dive(
        dives,
        start=now - timedelta(minutes=2),
        end=now,
        bin_log_start_num=10,
    )

    # Force USB unavailable
    monkeypatch.setattr(
        binlog.usb_storage, "get_recording_dir_if_available", lambda _sub: None
    )

    result = await binlog.archive_dive_bin_logs(dive_file, logs_dir=logs)
    assert result["status"] == "skipped_no_usb"
    record = json.loads(dive_file.read_text())
    assert record["bin_log_status"] == "skipped_no_usb"
    assert record["bin_log_files"] == []


@pytest.mark.asyncio
async def test_archive_no_match(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    dives = tmp_path / "dives"
    dives.mkdir()

    now = datetime.now(tz=timezone.utc)
    dive_file = _make_dive(
        dives,
        start=now - timedelta(minutes=10),
        end=now,
        bin_log_start_num=999,  # no logs after this number exist
    )
    # USB returned but unused since no files match
    monkeypatch.setattr(
        binlog.usb_storage,
        "get_recording_dir_if_available",
        lambda _sub: str(tmp_path / "usb"),
    )

    result = await binlog.archive_dive_bin_logs(dive_file, logs_dir=logs)
    assert result["status"] == "no_match"
    record = json.loads(dive_file.read_text())
    assert record["bin_log_status"] == "no_match"


@pytest.mark.asyncio
async def test_archive_copies_to_usb_with_dive_name_slug(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    dives = tmp_path / "dives"
    dives.mkdir()
    usb = tmp_path / "usb_root"
    usb.mkdir()

    now = datetime.now(tz=timezone.utc)
    src1 = _make_bin(logs, 200, mtime=now - timedelta(minutes=5), size=512)
    src2 = _make_bin(logs, 201, mtime=now - timedelta(minutes=1), size=1024)
    _write_lastlog(logs, 201)
    dive_file = _make_dive(
        dives,
        name="Reef Survey #1",
        start=now - timedelta(minutes=15),
        end=now,
        bin_log_start_num=199,
    )

    monkeypatch.setattr(
        binlog.usb_storage,
        "get_recording_dir_if_available",
        lambda _sub: str(usb),
    )
    # Skip the quiescence wait inside copy
    async def _instant_quiescent(*_args, **_kwargs):
        return True
    monkeypatch.setattr(binlog, "wait_until_quiescent", _instant_quiescent)

    result = await binlog.archive_dive_bin_logs(dive_file, logs_dir=logs)
    assert result["status"] == "ok"
    written = sorted(Path(p).name for p in result["files"])
    assert written == ["reef_survey_1_00000200.BIN", "reef_survey_1_00000201.BIN"]

    # Mtime preserved from source
    src_mtime = src1.stat().st_mtime
    dst_mtime = (usb / "reef_survey_1_00000200.BIN").stat().st_mtime
    assert abs(src_mtime - dst_mtime) < 2.0
    # Size preserved
    assert (usb / "reef_survey_1_00000201.BIN").stat().st_size == src2.stat().st_size

    record = json.loads(dive_file.read_text())
    assert record["bin_log_status"] == "ok"
    assert len(record["bin_log_files"]) == 2


@pytest.mark.asyncio
async def test_archive_uses_dive_id_when_name_blank(
    tmp_path: Path, monkeypatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    dives = tmp_path / "dives"
    dives.mkdir()
    usb = tmp_path / "usb"
    usb.mkdir()

    now = datetime.now(tz=timezone.utc)
    _make_bin(logs, 10, mtime=now)
    _write_lastlog(logs, 10)
    dive_file = _make_dive(
        dives,
        name="",
        start=now - timedelta(minutes=2),
        end=now,
        bin_log_start_num=9,
        dive_id="dive_0066",
    )

    monkeypatch.setattr(
        binlog.usb_storage,
        "get_recording_dir_if_available",
        lambda _sub: str(usb),
    )

    async def _instant_quiescent(*_args, **_kwargs):
        return True
    monkeypatch.setattr(binlog, "wait_until_quiescent", _instant_quiescent)

    result = await binlog.archive_dive_bin_logs(dive_file, logs_dir=logs)
    assert result["status"] == "ok"
    assert (usb / "dive_0066_00000010.BIN").exists()
