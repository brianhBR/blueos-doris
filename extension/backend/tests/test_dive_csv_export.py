"""Tests for the post-dive CSV-to-USB export (services/dive_csv_export.py).

These exercise the background export path without touching the real
recorder/USB layout: the dive's .mcap mapping, the USB directory, and the
mcap quiescence wait are all stubbed so the test asserts the orchestration
(parse -> build -> atomic write -> record status) and the cached-file
lookup the download route relies on.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcap.writer import Writer

from doris.services import dive_csv_export as dce

BASE = datetime(2026, 5, 28, 16, 50, 58, tzinfo=timezone.utc)
BASE_NS = int(BASE.timestamp() * 1_000_000_000)


def _write_min_mcap(path: Path) -> None:
    """Two 1-second buckets of DORIS depth/state telemetry."""
    msgs = [
        ("NAMED_VALUE_FLOAT", {"type": "NAMED_VALUE_FLOAT", "name": "STATE", "value": 1.0}),
        ("NAMED_VALUE_FLOAT", {"type": "NAMED_VALUE_FLOAT", "name": "DEPTH", "value": 12.5}),
        ("SCALED_PRESSURE3", {"type": "SCALED_PRESSURE3", "press_abs": 0, "temperature": 540}),
    ]
    with path.open("wb") as f:
        writer = Writer(f)
        writer.start()
        channels: dict[str, int] = {}

        def channel_for(msg_type: str) -> int:
            topic = f"mavlink/1/1/{msg_type}"
            if topic not in channels:
                sid = writer.register_schema(
                    name=f"mavlink.1.1.{msg_type}", encoding="jsonschema", data=b"{}"
                )
                channels[topic] = writer.register_channel(
                    topic=topic, message_encoding="json", schema_id=sid
                )
            return channels[topic]

        for i in range(2):
            base = BASE_NS + i * 1_000_000_000
            for j, (msg_type, message) in enumerate(msgs):
                payload = {"header": {"system_id": 1, "component_id": 1}, "message": message}
                writer.add_message(
                    channel_id=channel_for(msg_type),
                    log_time=base + j,
                    data=json.dumps(payload).encode("utf-8"),
                    publish_time=base + j,
                )
        writer.finish()


def _make_dive(tmp_path: Path, name: str = "Reef Survey") -> Path:
    dives = tmp_path / "dives"
    dives.mkdir(parents=True, exist_ok=True)
    rec = {
        "dive_name": name,
        "username": "brian",
        "status": "completed",
        "started_at": BASE.isoformat(),
        "ended_at": "2026-05-28T17:30:00+00:00",
        "latitude": 33.7261,
        "longitude": -118.2754,
    }
    p = dives / "dive_0001.json"
    p.write_text(json.dumps(rec, indent=2))
    return p


async def _noop_quiescent(*_a, **_k) -> bool:
    return True


def _patch_common(monkeypatch, tmp_path: Path, mcap_path: Path | None) -> None:
    monkeypatch.setattr(dce, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(dce, "wait_until_quiescent", _noop_quiescent)
    monkeypatch.setattr(
        "doris.services.storage._load_dive_windows", lambda root: []
    )
    mapping = {"dive_0001": mcap_path} if mcap_path is not None else {}
    monkeypatch.setattr(
        "doris.services.mcap_telemetry.map_dive_stem_to_largest_mcap",
        lambda root, windows: mapping,
    )


def test_export_writes_csv_to_usb(tmp_path: Path, monkeypatch) -> None:
    rec_dir = tmp_path / "recorder"
    rec_dir.mkdir()
    mcap = rec_dir / "dive.mcap"
    _write_min_mcap(mcap)
    dive_file = _make_dive(tmp_path)

    usb = tmp_path / "usb" / "dive_data"
    usb.mkdir(parents=True)
    _patch_common(monkeypatch, tmp_path, mcap)
    monkeypatch.setattr(
        dce.usb_storage, "get_recording_dir_if_available", lambda sub: str(usb)
    )

    result = asyncio.run(dce.export_dive_csv_to_usb(dive_file))

    assert result["status"] == "ok"
    # Named by dive start time (YYYYMMDDtHHMMSS) then dive name.
    out = usb / "20260528t165058_reef_survey_dive_data.csv"
    assert out.is_file()
    assert result["file"] == str(out)
    assert result["rows"] == 2

    text = out.read_text(encoding="utf-8")
    assert "# DIVE DATA" in text
    assert "# TIME SERIES" in text
    assert "timestamp_utc" in text
    # No spurious carriage returns (the blank-row bug we previously fixed).
    assert "\r" not in text

    # Record was updated and the cache lookup finds the file.
    record = json.loads(dive_file.read_text())
    assert record["csv_export_status"] == "ok"
    assert record["csv_export_file"] == str(out)
    assert dce.find_cached_csv(record) == out


def test_export_skips_when_no_usb(tmp_path: Path, monkeypatch) -> None:
    rec_dir = tmp_path / "recorder"
    rec_dir.mkdir()
    mcap = rec_dir / "dive.mcap"
    _write_min_mcap(mcap)
    dive_file = _make_dive(tmp_path)

    _patch_common(monkeypatch, tmp_path, mcap)
    monkeypatch.setattr(
        dce.usb_storage, "get_recording_dir_if_available", lambda sub: None
    )

    result = asyncio.run(dce.export_dive_csv_to_usb(dive_file))

    assert result["status"] == "skipped_no_usb"
    record = json.loads(dive_file.read_text())
    assert record["csv_export_status"] == "skipped_no_usb"
    assert "csv_export_file" not in record
    assert dce.find_cached_csv(record) is None


def test_export_without_mcap_still_writes_header(tmp_path: Path, monkeypatch) -> None:
    dive_file = _make_dive(tmp_path)
    usb = tmp_path / "usb" / "dive_data"
    usb.mkdir(parents=True)
    _patch_common(monkeypatch, tmp_path, None)
    monkeypatch.setattr(
        dce.usb_storage, "get_recording_dir_if_available", lambda sub: str(usb)
    )

    result = asyncio.run(dce.export_dive_csv_to_usb(dive_file))

    assert result["status"] == "ok"
    assert result["rows"] == 0
    out = usb / "20260528t165058_reef_survey_dive_data.csv"
    assert out.is_file()
    assert "# DIVE DATA" in out.read_text(encoding="utf-8")


def test_find_cached_csv_handles_missing_file(tmp_path: Path) -> None:
    assert dce.find_cached_csv({}) is None
    assert dce.find_cached_csv({"csv_export_file": ""}) is None
    assert dce.find_cached_csv({"csv_export_file": str(tmp_path / "nope.csv")}) is None
    real = tmp_path / "ok.csv"
    real.write_text("data")
    assert dce.find_cached_csv({"csv_export_file": str(real)}) == real


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
