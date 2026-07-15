"""Tests for Artemis tracker detection (services/tracker.py).

Regression coverage for two issues:

* #29 — the tracker tile (and the Iridium button on it) used to vanish
  whenever a single HEARTBEAT poll reported a low/zero frequency from
  mavlink2rest.  ``get_modules`` keeps the tracker visible for
  ``TRACKER_STICKY_GRACE_S`` after the last good heartbeat so transient
  dips don't drop the tile.
* #55 — even with the grace window the tile vanished while the AGT was
  clearly alive, because ``_get_heartbeat`` gated on mavlink2rest's
  noisy per-message frequency estimate.  Detection now gates on message
  *recency* (``status.time.last_update`` age), so a steadily-heartbeating
  AGT keeps the tile up regardless of the frequency estimate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from doris.services import tracker as tracker_mod
from doris.services.tracker import ArtemisTrackerService


@pytest.fixture
def svc(monkeypatch) -> ArtemisTrackerService:
    s = ArtemisTrackerService()
    # GPS read is irrelevant to detection; keep it cheap and deterministic.
    monkeypatch.setattr(s, "get_gps_data", _async_return(None))
    # Detection tests don't exercise versioning; suppress the proactive
    # (network) version request so these stay hermetic.
    monkeypatch.setattr(s, "_maybe_request_version", lambda: None)
    return s


def _async_return(value):
    async def _coro(*_args, **_kwargs):
        return value
    return _coro


def _set_heartbeat(monkeypatch, svc: ArtemisTrackerService, present: bool) -> None:
    monkeypatch.setattr(
        svc, "_get_heartbeat", _async_return({"type": "HEARTBEAT"} if present else None)
    )


def _set_clock(monkeypatch, value: float) -> None:
    monkeypatch.setattr(tracker_mod.time, "monotonic", lambda: value)


# ── recency-gate (issue #55) helpers ────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient returning a fixed response."""

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.is_closed = False
        self.last_post: dict | None = None

    async def get(self, _url, *_a, **_k) -> _FakeResp:
        return self._resp

    async def post(self, url, *_a, json=None, **_k) -> _FakeResp:
        self.last_post = {"url": url, "json": json}
        return self._resp


def _hb_payload(age_s: float = 0.0, freq: float = 0.0,
                mtype: str = "HEARTBEAT") -> dict:
    last = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {
        "message": {"type": mtype},
        "status": {"time": {"last_update": last, "frequency": freq}},
    }


def _install_client(svc: ArtemisTrackerService, status_code: int = 200,
                    payload: dict | None = None) -> None:
    svc._client = _FakeClient(_FakeResp(status_code, payload))


async def test_get_heartbeat_fresh_low_frequency_is_present():
    # The core #55 regression: a fresh heartbeat with frequency well below
    # the old 0.05 gate must still be detected.
    s = ArtemisTrackerService()
    _install_client(s, payload=_hb_payload(age_s=1.0, freq=0.0))
    assert await s._get_heartbeat() is not None


async def test_get_heartbeat_stale_message_ignored():
    s = ArtemisTrackerService()
    _install_client(
        s, payload=_hb_payload(age_s=tracker_mod.HEARTBEAT_MAX_AGE_S + 30.0)
    )
    assert await s._get_heartbeat() is None


async def test_get_heartbeat_404_returns_none():
    s = ArtemisTrackerService()
    _install_client(s, status_code=404)
    assert await s._get_heartbeat() is None


async def test_get_heartbeat_missing_timestamp_accepts_presence():
    s = ArtemisTrackerService()
    _install_client(
        s, payload={"message": {"type": "HEARTBEAT"}, "status": {"time": {}}}
    )
    assert await s._get_heartbeat() is not None


async def test_get_heartbeat_wrong_message_type_returns_none():
    s = ArtemisTrackerService()
    _install_client(s, payload=_hb_payload(mtype="GPS_RAW_INT"))
    assert await s._get_heartbeat() is None


async def test_low_frequency_heartbeat_still_shows_tile(monkeypatch):
    # End-to-end through get_modules: a live but low-frequency heartbeat
    # keeps the tracker tile (the field symptom in #55).
    s = ArtemisTrackerService()
    monkeypatch.setattr(s, "get_gps_data", _async_return(None))
    monkeypatch.setattr(s, "_maybe_request_version", lambda: None)
    _install_client(s, payload=_hb_payload(age_s=2.0, freq=0.0))
    modules = await s.get_modules()
    assert len(modules) == 1
    assert modules[0].type == "tracker"


async def test_present_heartbeat_shows_tracker(svc, monkeypatch):
    _set_clock(monkeypatch, 100.0)
    _set_heartbeat(monkeypatch, svc, present=True)

    modules = await svc.get_modules()

    assert len(modules) == 1
    assert modules[0].type == "tracker"


async def test_never_seen_returns_empty(svc, monkeypatch):
    _set_clock(monkeypatch, 100.0)
    _set_heartbeat(monkeypatch, svc, present=False)

    assert await svc.get_modules() == []


async def test_transient_dip_within_grace_keeps_tile(svc, monkeypatch):
    # Seen at t=100 …
    _set_clock(monkeypatch, 100.0)
    _set_heartbeat(monkeypatch, svc, present=True)
    assert len(await svc.get_modules()) == 1

    # … missed heartbeat 10s later (well within the grace window).
    _set_clock(monkeypatch, 110.0)
    _set_heartbeat(monkeypatch, svc, present=False)
    assert len(await svc.get_modules()) == 1


async def test_grace_expiry_drops_tile(svc, monkeypatch):
    _set_clock(monkeypatch, 100.0)
    _set_heartbeat(monkeypatch, svc, present=True)
    assert len(await svc.get_modules()) == 1

    # Past the grace window with no heartbeat -> tracker gone.
    _set_clock(monkeypatch, 100.0 + tracker_mod.TRACKER_STICKY_GRACE_S + 1.0)
    _set_heartbeat(monkeypatch, svc, present=False)
    assert await svc.get_modules() == []


# ── firmware version reporting ──────────────────────────────────────


def test_parse_semver_variants():
    assert tracker_mod._parse_semver("v0.1.0") == (0, 1, 0)
    assert tracker_mod._parse_semver("0.1.0") == (0, 1, 0)
    # git-describe dev builds carry a suffix that must be ignored.
    assert tracker_mod._parse_semver("v0.1.0-dirty") == (0, 1, 0)
    assert tracker_mod._parse_semver("v1.2.3-4-gabc123") == (1, 2, 3)
    assert tracker_mod._parse_semver("dev") is None
    assert tracker_mod._parse_semver("") is None


def test_capture_version_from_statustext():
    s = ArtemisTrackerService()
    assert s._agt_version is None
    s._capture_version("Doris AGT v0.2.1")
    assert s._agt_version == "v0.2.1"
    # Non-version STATUSTEXT must not clobber the cached value.
    s._capture_version("IRIDIUM: Test starting")
    assert s._agt_version == "v0.2.1"


def test_capture_imei_from_separate_statustext():
    s = ArtemisTrackerService()
    s._capture_version("Doris AGT v0.3.0")
    s._capture_version("Doris AGT IMEI:300234061234567")
    assert s._agt_version == "v0.3.0"
    assert s._agt_imei == "300234061234567"


def test_capture_version_and_imei_combined_line():
    s = ArtemisTrackerService()
    s._capture_version("Doris AGT v0.3.0 IMEI:300234061234567")
    assert s._agt_version == "v0.3.0"
    assert s._agt_imei == "300234061234567"


def test_capture_imei_line_does_not_clobber_version():
    s = ArtemisTrackerService()
    s._agt_version = "v0.3.0"
    # An IMEI-only line has no semver core and must leave the version alone.
    s._capture_version("Doris AGT IMEI:300234061234567")
    assert s._agt_version == "v0.3.0"
    assert s._agt_imei == "300234061234567"


async def test_get_version_includes_imei():
    s = ArtemisTrackerService()
    s._agt_version = "v0.3.0"
    s._agt_imei = "300234061234567"
    result = await s.get_version(request=False)
    assert result["imei"] == "300234061234567"


# ── AGT command dispatch ────────────────────────────────────────────


def _prime_command_client(monkeypatch, s: ArtemisTrackerService) -> None:
    """Wire a fake client + a pre-connected subscriber for command sends."""
    _install_client(s, payload={})
    monkeypatch.setattr(s, "_ensure_statustext_subscriber", lambda: None)
    s._ws_connected.set()


async def test_send_iridium_test_sends_user4(monkeypatch):
    s = ArtemisTrackerService()
    _prime_command_client(monkeypatch, s)
    result = await s.send_iridium_test()
    assert result["accepted"] is True
    msg = s._client.last_post["json"]["message"]
    assert msg["command"]["type"] == "MAV_CMD_USER_4"
    assert msg["target_component"] == tracker_mod.ARTEMIS_COMPONENT_ID


async def test_send_debug_sends_user3_to_component_192(monkeypatch):
    s = ArtemisTrackerService()
    _prime_command_client(monkeypatch, s)
    result = await s.send_debug()
    assert result["accepted"] is True
    msg = s._client.last_post["json"]["message"]
    assert msg["command"]["type"] == "MAV_CMD_USER_3"
    assert msg["target_component"] == tracker_mod.ARTEMIS_COMPONENT_ID


def test_debug_command_is_user3_not_reboot():
    # Safety net: the version/debug fetch must never be USER_5 (REBOOT) or
    # USER_6 (invalid, mavlink2rest 404). It is AGT_DEBUG = USER_3.
    assert tracker_mod.AGT_DEBUG_CMD == "MAV_CMD_USER_3"


async def test_get_version_reports_compatible_without_request():
    s = ArtemisTrackerService()
    s._agt_version = tracker_mod.MIN_AGT_FIRMWARE_VERSION
    result = await s.get_version(request=False)
    assert result["version"] == tracker_mod.MIN_AGT_FIRMWARE_VERSION
    assert result["known"] is True
    assert result["compatible"] is True


async def test_get_version_flags_incompatible_old_firmware():
    s = ArtemisTrackerService()
    s._agt_version = "v0.0.1"  # below any realistic minimum
    result = await s.get_version(request=False)
    # Only meaningful if the minimum is above v0.0.1, which it is.
    assert result["compatible"] is (
        _parse_ge("v0.0.1", tracker_mod.MIN_AGT_FIRMWARE_VERSION)
    )


async def test_get_version_unknown_without_request():
    s = ArtemisTrackerService()
    result = await s.get_version(request=False)
    assert result["version"] is None
    assert result["known"] is False
    assert result["compatible"] is None


def _parse_ge(a: str, b: str) -> bool:
    return tracker_mod._parse_semver(a) >= tracker_mod._parse_semver(b)


async def test_tracker_tile_carries_firmware_version(monkeypatch):
    s = ArtemisTrackerService()
    monkeypatch.setattr(s, "get_gps_data", _async_return(None))
    # Avoid the proactive network request during this unit test.
    monkeypatch.setattr(s, "_maybe_request_version", lambda: None)
    _install_client(s, payload=_hb_payload(age_s=1.0))
    s._agt_version = "v0.3.0"
    modules = await s.get_modules()
    assert len(modules) == 1
    assert modules[0].firmware_version == "v0.3.0"


async def test_recovered_heartbeat_resets_grace(svc, monkeypatch):
    _set_clock(monkeypatch, 100.0)
    _set_heartbeat(monkeypatch, svc, present=True)
    await svc.get_modules()

    # Dip, then recover before grace expires; the recovery should refresh
    # the timestamp so the tile survives another full grace window.
    _set_clock(monkeypatch, 130.0)
    _set_heartbeat(monkeypatch, svc, present=False)
    assert len(await svc.get_modules()) == 1

    _set_clock(monkeypatch, 140.0)
    _set_heartbeat(monkeypatch, svc, present=True)
    assert len(await svc.get_modules()) == 1

    # 50s after the *recovery*: still within grace (would have expired if
    # measured from the original t=100 sighting).
    _set_clock(monkeypatch, 190.0)
    _set_heartbeat(monkeypatch, svc, present=False)
    assert len(await svc.get_modules()) == 1
