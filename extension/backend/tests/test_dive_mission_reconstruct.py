"""Tests for active-dive config-name reconstruction (issue #38)."""

import json
from pathlib import Path

from doris.services.dive_records import find_latest_active_dive_record


def _write_record(dives_dir: Path, n: int, status: str, configuration: str) -> None:
    dives_dir.mkdir(parents=True, exist_ok=True)
    (dives_dir / f"dive_{n:04d}.json").write_text(
        json.dumps(
            {
                "dive_name": f"Dive {n}",
                "configuration": configuration,
                "status": status,
                "profile_id": n,
                "started_at": "2026-06-01T00:00:00+00:00",
            }
        )
    )


def test_finds_latest_active_record(tmp_path: Path) -> None:
    _write_record(tmp_path, 1, "completed", "Old Config")
    _write_record(tmp_path, 2, "active", "Reef Survey")
    _write_record(tmp_path, 3, "cancelled", "Aborted Config")

    record = find_latest_active_dive_record(tmp_path)
    assert record is not None
    assert record["configuration"] == "Reef Survey"
    assert record["_dive_file"] == "dive_0002.json"


def test_no_active_record_returns_none(tmp_path: Path) -> None:
    _write_record(tmp_path, 1, "completed", "Old Config")
    assert find_latest_active_dive_record(tmp_path) is None


def test_missing_dives_dir_returns_none(tmp_path: Path) -> None:
    assert find_latest_active_dive_record(tmp_path / "does_not_exist") is None


def test_picks_highest_numbered_active(tmp_path: Path) -> None:
    _write_record(tmp_path, 5, "active", "First Active")
    _write_record(tmp_path, 9, "active", "Latest Active")

    record = find_latest_active_dive_record(tmp_path)
    assert record is not None
    assert record["configuration"] == "Latest Active"
