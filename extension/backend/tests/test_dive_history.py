"""Tests for the Previous Dives history aggregation (services/storage.py).

Covers the metadata the Previous Dives card relies on: the on-mission
duration (time since the depth-gate crossing), the measured max depth from
the log, and the separate start/end positions.
"""

from __future__ import annotations

import json
from pathlib import Path

from doris.services.storage import build_dive_history_list


def _make_dive(root: Path, **fields: object) -> None:
    dives = root / "dives"
    dives.mkdir(parents=True, exist_ok=True)
    rec = {
        "dive_name": "Long Beach Test",
        "status": "completed",
        "started_at": "2026-06-25T17:59:00+00:00",
        "ended_at": "2026-06-25T18:47:00+00:00",
    }
    rec.update(fields)
    (dives / "dive_0001.json").write_text(json.dumps(rec, indent=2))


def test_duration_prefers_mission_duration_over_wall_clock(tmp_path: Path) -> None:
    # Wall clock (started->ended) is 48m, but the on-mission time from the
    # depth gate is only 30m -> the card should show 30m.
    _make_dive(tmp_path, mission_duration_s=1800)
    entries = build_dive_history_list(tmp_path)
    assert len(entries) == 1
    assert entries[0].duration == "30m"


def test_duration_falls_back_to_wall_clock(tmp_path: Path) -> None:
    _make_dive(tmp_path)  # no mission_duration_s
    entries = build_dive_history_list(tmp_path)
    assert entries[0].duration == "48m"


def test_max_depth_prefers_log_over_estimate(tmp_path: Path) -> None:
    _make_dive(tmp_path, estimated_depth="3", log_max_depth_m=27.4)
    entry = build_dive_history_list(tmp_path)[0]
    assert entry.max_depth == 27.4
    assert entry.log_max_depth_m == 27.4
    assert entry.estimated_depth_m == 3.0


def test_start_and_end_location_populated(tmp_path: Path) -> None:
    _make_dive(
        tmp_path,
        latitude=33.7261,
        longitude=-118.2754,
        end_latitude=33.7301,
        end_longitude=-118.2700,
    )
    entry = build_dive_history_list(tmp_path)[0]
    assert entry.start_location == "33.7261° N, 118.2754° W"
    assert entry.end_location == "33.7301° N, 118.2700° W"
    # location stays populated (start fix) for back-compat.
    assert entry.location == entry.start_location


def test_missing_locations_are_none(tmp_path: Path) -> None:
    _make_dive(tmp_path)
    entry = build_dive_history_list(tmp_path)[0]
    assert entry.start_location is None
    assert entry.end_location is None
