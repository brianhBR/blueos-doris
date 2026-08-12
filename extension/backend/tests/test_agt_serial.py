"""Tests for mapping the AGT's USB adapter to an autopilot serial port.

The AGT talks to ArduPilot over a plain serial link that ``ardupilot-manager``
records by its ``/dev/serial/by-path`` name.  That name encodes the physical
USB socket, and a Pi 5 puts its USB-2 and USB-3 sockets behind different host
controllers, so moving the plug renames the device.  The saved entry then
points at nothing, ``ardupilot-manager`` drops it without logging anything, and
the autopilot comes up without the port -- taking the AGT's GPS, its release
named floats and the safe-surface handshake with it.  A vehicle flew an entire
dive that way before anyone noticed the tile was blank.
"""

from __future__ import annotations

import pytest

from doris.services import agt_serial
from doris.services.agt_serial import (
    AGT_PORT_LETTER,
    AGT_USB_PID,
    AGT_USB_VID,
    entry_points_at,
    find_agt_device,
    plan_serial_update,
    reconcile_agt_serial,
    stable_name_for,
)

BY_ID = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
BY_PATH_USB3 = "/dev/serial/by-path/platform-xhci-hcd.1-usb-0:2:1.0-port0"
BY_PATH_USB2 = "/dev/serial/by-path/platform-xhci-hcd.0-usb-0:2:1.0-port0"
NODE = "/dev/ttyUSB0"

NAVIGATOR_UARTS = [
    {"port": "C", "endpoint": "/dev/ttyAMA0"},
    {"port": "B", "endpoint": "/dev/ttyAMA2"},
    {"port": "E", "endpoint": "/dev/ttyAMA3"},
    {"port": "F", "endpoint": "/dev/ttyAMA4"},
]


class _Port:
    def __init__(self, device, vid, pid):
        self.device, self.vid, self.pid = device, vid, pid


@pytest.fixture
def dev_tree(monkeypatch):
    """Describe a /dev where the AGT is plugged into the USB-3 socket."""
    present = {BY_ID: NODE, BY_PATH_USB3: NODE, NODE: NODE}

    monkeypatch.setattr(agt_serial, "_exists", lambda p: p in present)
    monkeypatch.setattr(agt_serial, "_realpath", lambda p: present.get(p, p))
    monkeypatch.setattr(agt_serial, "_by_id_links", lambda: [BY_ID])
    return present


def _comports(monkeypatch, ports):
    monkeypatch.setattr(agt_serial.list_ports, "comports", lambda: ports)


# ── identifying the adapter ─────────────────────────────────────────


def test_detects_the_agt_by_its_usb_identity(monkeypatch):
    _comports(monkeypatch, [
        _Port("/dev/ttyAMA0", None, None),
        _Port(NODE, AGT_USB_VID, AGT_USB_PID),
    ])
    assert find_agt_device() == NODE


def test_an_unplugged_agt_is_not_invented(monkeypatch):
    _comports(monkeypatch, [_Port("/dev/ttyAMA0", None, None)])
    assert find_agt_device() is None


def test_refuses_to_choose_between_two_identical_adapters(monkeypatch):
    """This CH340 reports no serial number, so two of them are the same.

    Picking one at random could hand the autopilot a different device
    entirely, which is worse than leaving the port as it is.
    """
    _comports(monkeypatch, [
        _Port("/dev/ttyUSB0", AGT_USB_VID, AGT_USB_PID),
        _Port("/dev/ttyUSB1", AGT_USB_VID, AGT_USB_PID),
    ])
    assert find_agt_device() is None


# ── choosing the name to record ─────────────────────────────────────


def test_records_the_by_id_name_which_follows_the_adapter(dev_tree):
    assert stable_name_for(NODE) == BY_ID


def test_falls_back_to_the_device_node_when_there_is_no_by_id(monkeypatch):
    monkeypatch.setattr(agt_serial, "_realpath", lambda p: p)
    monkeypatch.setattr(agt_serial, "_by_id_links", lambda: [])
    assert stable_name_for(NODE) == NODE


# ── deciding whether to touch anything ──────────────────────────────


def test_a_working_entry_is_left_alone_whatever_it_is_called(dev_tree):
    """Renaming a working entry would cost an autopilot restart for nothing."""
    for name in (BY_PATH_USB3, BY_ID, NODE):
        serials = NAVIGATOR_UARTS + [{"port": AGT_PORT_LETTER, "endpoint": name}]
        assert plan_serial_update(serials, NODE) is None, name


def test_an_entry_naming_a_vanished_socket_is_repaired(dev_tree):
    """The actual failure: the cable moved and the saved by-path went stale."""
    serials = NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ]
    planned = plan_serial_update(serials, NODE)

    assert planned is not None
    agt = [s for s in planned if s["port"] == AGT_PORT_LETTER]
    assert agt == [{"port": AGT_PORT_LETTER, "endpoint": BY_ID}]


def test_a_missing_entry_is_added(dev_tree):
    planned = plan_serial_update(list(NAVIGATOR_UARTS), NODE)
    assert planned is not None
    assert {"port": AGT_PORT_LETTER, "endpoint": BY_ID} in planned


def test_the_navigator_uarts_are_carried_through_untouched(dev_tree):
    """Which UARTs the Navigator exposes is BlueOS's business, not ours."""
    serials = NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ]
    planned = plan_serial_update(serials, NODE)

    assert [s for s in planned if s["port"] != AGT_PORT_LETTER] == NAVIGATOR_UARTS
    assert len([s for s in planned if s["port"] == AGT_PORT_LETTER]) == 1


def test_an_entry_pointing_somewhere_real_but_wrong_is_repaired(dev_tree):
    serials = NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": "/dev/ttyAMA0"}
    ]
    assert plan_serial_update(serials, NODE) is not None


def test_entry_points_at_is_not_a_string_comparison(dev_tree):
    assert entry_points_at(BY_PATH_USB3, NODE)
    assert not entry_points_at(BY_PATH_USB2, NODE)
    assert not entry_points_at("", NODE)


# ── the write path ──────────────────────────────────────────────────


class _Response:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """Stands in for the autopilot-manager and mavlink2rest HTTP APIs."""

    written: list | None = None

    def __init__(self, serials, armed=False, heartbeat_known=True):
        self.serials = serials
        self.armed = armed
        self.heartbeat_known = heartbeat_known
        _Client.written = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url):
        if url.endswith(agt_serial.SERIALS_PATH):
            return _Response(self.serials)
        if not self.heartbeat_known:
            return _Response(None, status_code=404)
        bits = agt_serial.ARMED_FLAG if self.armed else 0
        return _Response({"message": {"base_mode": {"bits": bits}}})

    async def put(self, url, json):
        _Client.written = json
        return _Response(None)


def _install(monkeypatch, serials, **kwargs):
    monkeypatch.setattr(
        agt_serial.httpx,
        "AsyncClient",
        lambda *a, **kw: _Client(serials, **kwargs),
    )


@pytest.fixture
def agt_plugged_in(monkeypatch, dev_tree):
    _comports(monkeypatch, [_Port(NODE, AGT_USB_VID, AGT_USB_PID)])


@pytest.mark.asyncio
async def test_repairs_a_stale_mapping(monkeypatch, agt_plugged_in):
    _install(monkeypatch, NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ])
    assert await reconcile_agt_serial() is True
    assert {"port": AGT_PORT_LETTER, "endpoint": BY_ID} in _Client.written


@pytest.mark.asyncio
async def test_a_correct_mapping_is_not_rewritten(monkeypatch, agt_plugged_in):
    """Writing the config restarts the autopilot, so this must be idempotent."""
    _install(monkeypatch, NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB3}
    ])
    assert await reconcile_agt_serial() is False
    assert _Client.written is None


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_adapter_is_absent(monkeypatch, dev_tree):
    """An unplugged AGT is a temporary state, not a configuration change."""
    _comports(monkeypatch, [])
    _install(monkeypatch, list(NAVIGATOR_UARTS))
    assert await reconcile_agt_serial() is False
    assert _Client.written is None


@pytest.mark.asyncio
async def test_an_empty_port_list_is_never_completed_from_here(
    monkeypatch, agt_plugged_in
):
    """Writing just the AGT into an empty list would drop the Navigator UARTs.

    An empty list means something else has already gone wrong, and this module
    has no way to reconstruct the autopilot's own ports.
    """
    _install(monkeypatch, [])
    assert await reconcile_agt_serial() is False
    assert _Client.written is None


@pytest.mark.asyncio
async def test_a_dive_in_progress_is_never_interrupted(monkeypatch, agt_plugged_in):
    """Writing the config restarts the autopilot, which would end the mission."""
    _install(monkeypatch, NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ])
    assert await reconcile_agt_serial(dive_in_progress=True) is False
    assert _Client.written is None


@pytest.mark.asyncio
async def test_an_armed_vehicle_is_never_interrupted(monkeypatch, agt_plugged_in):
    _install(monkeypatch, NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ], armed=True)
    assert await reconcile_agt_serial() is False
    assert _Client.written is None


@pytest.mark.asyncio
async def test_an_unreadable_heartbeat_does_not_block_the_repair(
    monkeypatch, agt_plugged_in
):
    """On a fresh boot mavlink2rest may have no heartbeat cached yet.

    The dive record is the guard that matters for a mission in progress; the
    armed check is a second line of defence and must not stop the repair on a
    vehicle that has only just started up.
    """
    _install(monkeypatch, NAVIGATOR_UARTS + [
        {"port": AGT_PORT_LETTER, "endpoint": BY_PATH_USB2}
    ], heartbeat_known=False)
    assert await reconcile_agt_serial() is True
