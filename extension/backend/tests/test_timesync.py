"""Tests for the GPS time-sync service (services/timesync.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from doris.services import timesync
from doris.services.timesync import TimeSyncService


def _iso_ago(seconds: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _usec(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Routes mavlink2rest GETs by message-type substring in the URL."""

    is_closed = False

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def get(self, url: str) -> _FakeResponse:
        for key, resp in self._responses.items():
            if key in url:
                return resp
        return _FakeResponse({}, status_code=404)


def _make_service(responses: dict[str, _FakeResponse]) -> TimeSyncService:
    svc = TimeSyncService()
    svc._client = _FakeClient(responses)
    return svc


# ── _message_age_seconds ───────────────────────────────────────────


def test_message_age_fresh():
    data = {"status": {"time": {"last_update": _iso_ago(2)}}}
    age = timesync._message_age_seconds(data)
    assert age is not None and 1 <= age <= 5


def test_message_age_missing_returns_none():
    assert timesync._message_age_seconds({"status": {"time": {}}}) is None
    assert timesync._message_age_seconds({}) is None


def test_message_age_unparseable_returns_none():
    data = {"status": {"time": {"last_update": "not-a-date"}}}
    assert timesync._message_age_seconds(data) is None


# ── persistence / boot floor ───────────────────────────────────────


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    dt = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    timesync._write_state(dt)
    assert timesync._read_state() == dt


def test_read_state_rejects_insane_year(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    timesync._write_state(datetime(1999, 1, 1, tzinfo=timezone.utc))
    assert timesync._read_state() is None


def test_read_state_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "nope.json")
    assert timesync._read_state() is None


def test_apply_boot_floor_steps_forward_when_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    future = datetime.now(tz=timezone.utc) + timedelta(days=365)
    timesync._write_state(future)

    calls: list[datetime] = []
    monkeypatch.setattr(
        timesync, "_set_system_clock", lambda dt: calls.append(dt) or True
    )

    svc = TimeSyncService()
    assert svc.apply_boot_floor() is True
    assert calls and calls[0] == future
    assert svc._source == "restored-floor"
    # Floor is not an authoritative sync — keep polling for real GPS.
    assert svc.synced is False


def test_apply_boot_floor_noop_when_clock_ahead(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    past = datetime.now(tz=timezone.utc) - timedelta(days=365)
    timesync._write_state(past)

    calls: list[datetime] = []
    monkeypatch.setattr(
        timesync, "_set_system_clock", lambda dt: calls.append(dt) or True
    )

    svc = TimeSyncService()
    assert svc.apply_boot_floor() is False
    assert not calls


def test_apply_boot_floor_noop_without_state(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "missing.json")
    svc = TimeSyncService()
    assert svc.apply_boot_floor() is False


# ── try_sync_from_artemis: freshness gates ─────────────────────────


@pytest.mark.asyncio
async def test_sync_skips_when_no_fix(monkeypatch):
    svc = _make_service(
        {
            "GPS_INPUT": _FakeResponse(
                {"message": {"fix_type": 1}, "status": {"time": {"last_update": _iso_ago(1)}}}
            ),
        }
    )
    monkeypatch.setattr(timesync, "_set_system_clock", lambda dt: True)
    assert await svc.try_sync_from_artemis() is False


@pytest.mark.asyncio
async def test_sync_skips_when_fix_is_stale(monkeypatch):
    svc = _make_service(
        {
            "GPS_INPUT": _FakeResponse(
                {
                    "message": {"fix_type": 3},
                    "status": {"time": {"last_update": _iso_ago(120)}},
                }
            ),
        }
    )
    monkeypatch.setattr(timesync, "_set_system_clock", lambda dt: True)
    assert await svc.try_sync_from_artemis() is False


@pytest.mark.asyncio
async def test_sync_skips_when_system_time_is_stale(monkeypatch):
    # Fresh fix, but the SYSTEM_TIME message itself is stale: must not be
    # used to step the clock (this is the backward-jump guard).
    stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    svc = _make_service(
        {
            "GPS_INPUT": _FakeResponse(
                {"message": {"fix_type": 3}, "status": {"time": {"last_update": _iso_ago(1)}}}
            ),
            "SYSTEM_TIME": _FakeResponse(
                {
                    "message": {"time_unix_usec": _usec(stale_time)},
                    "status": {"time": {"last_update": _iso_ago(120)}},
                }
            ),
        }
    )
    calls: list[datetime] = []
    monkeypatch.setattr(
        timesync, "_set_system_clock", lambda dt: calls.append(dt) or True
    )
    assert await svc.try_sync_from_artemis() is False
    assert not calls


@pytest.mark.asyncio
async def test_sync_steps_clock_on_fresh_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    # GPS says it is ~10 minutes ahead of the current clock -> drift > 30s.
    gps_time = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
    svc = _make_service(
        {
            "GPS_INPUT": _FakeResponse(
                {"message": {"fix_type": 3}, "status": {"time": {"last_update": _iso_ago(1)}}}
            ),
            "SYSTEM_TIME": _FakeResponse(
                {
                    "message": {"time_unix_usec": _usec(gps_time)},
                    "status": {"time": {"last_update": _iso_ago(1)}},
                }
            ),
        }
    )
    calls: list[datetime] = []
    monkeypatch.setattr(
        timesync, "_set_system_clock", lambda dt: calls.append(dt) or True
    )
    assert await svc.try_sync_from_artemis() is True
    assert calls and abs((calls[0] - gps_time).total_seconds()) < 5
    assert svc.synced is True
    assert svc._source == "artemis-gps"
    # The good time was persisted as a floor.
    assert timesync._read_state() is not None


@pytest.mark.asyncio
async def test_sync_within_tolerance_marks_synced_without_stepping(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "_STATE_FILE", tmp_path / "timesync_state.json")
    gps_time = datetime.now(tz=timezone.utc) + timedelta(seconds=2)
    svc = _make_service(
        {
            "GPS_INPUT": _FakeResponse(
                {"message": {"fix_type": 3}, "status": {"time": {"last_update": _iso_ago(1)}}}
            ),
            "SYSTEM_TIME": _FakeResponse(
                {
                    "message": {"time_unix_usec": _usec(gps_time)},
                    "status": {"time": {"last_update": _iso_ago(1)}},
                }
            ),
        }
    )
    calls: list[datetime] = []
    monkeypatch.setattr(
        timesync, "_set_system_clock", lambda dt: calls.append(dt) or True
    )
    assert await svc.try_sync_from_artemis() is True
    assert not calls  # within MIN_DRIFT_S, no step needed
    assert svc.synced is True
