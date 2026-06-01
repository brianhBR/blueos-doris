"""Tests for Artemis tracker detection stickiness (services/tracker.py).

Regression coverage for issue #29: the tracker tile (and the Iridium
button on it) used to vanish whenever a single HEARTBEAT poll reported a
low/zero frequency from mavlink2rest.  ``get_modules`` now keeps the
tracker visible for ``TRACKER_STICKY_GRACE_S`` after the last good
heartbeat so transient frequency dips don't drop the tile.
"""

from __future__ import annotations

import pytest

from doris.services import tracker as tracker_mod
from doris.services.tracker import ArtemisTrackerService


@pytest.fixture
def svc(monkeypatch) -> ArtemisTrackerService:
    s = ArtemisTrackerService()
    # GPS read is irrelevant to detection; keep it cheap and deterministic.
    monkeypatch.setattr(s, "get_gps_data", _async_return(None))
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
