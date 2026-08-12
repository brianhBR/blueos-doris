"""Keep the AGT's USB serial adapter mapped to an autopilot serial port.

The AGT reaches ArduPilot as an ordinary MAVLink serial link.  ``ardupilot-
manager`` hands the device to the autopilot as port ``G``, ArduPilot's Linux
HAL maps that to SERIAL6, and the DORIS frame sets ``SERIAL6_PROTOCOL=2``
(MAVLink2) and ``SERIAL6_BAUD=57`` to match.  Because the frame also sets
``GPS_TYPE=14`` (the MAV backend), position arrives as ``GPS_INPUT`` over that
same link, so losing the port costs the vehicle its GPS, the release named
floats, and the safe-surface handshake together.

Mapping the port used to be a manual step in the BlueOS web UI, and it does not
survive the cable being moved.  ``ardupilot-manager`` stores the device under
its ``/dev/serial/by-path`` name, which encodes the physical USB socket; a Pi 5
puts its USB-2 and USB-3 sockets behind different host controllers, so moving
the plug renames the device.  The saved entry then names something that does
not exist, ``ardupilot-manager`` drops it silently, and the autopilot starts
without the port.  Nothing reports an error — the only symptom is the AGT tile
going blank, which is how one vehicle flew a whole dive with no AGT telemetry.

This module makes the mapping the extension's business instead: on startup it
checks that port G resolves to the AGT and repairs it when it does not, writing
the ``/dev/serial/by-id`` name, which follows the adapter rather than the
socket.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import serial.tools.list_ports as list_ports

from ..config import blueos_services

logger = logging.getLogger(__name__)

# The AGT presents to the Pi as a CH340 USB-serial bridge.
AGT_USB_VID = 0x1A86
AGT_USB_PID = 0x7523

# ArduPilot's Linux HAL maps -G to SERIAL6, which is the port the DORIS frame
# configures for the AGT.  Changing this means changing the frame's SERIAL6_*
# parameters to match.
AGT_PORT_LETTER = "G"

BY_ID_DIR = Path("/dev/serial/by-id")

SERIALS_PATH = "/v1.0/serials"
HEARTBEAT_PATH = "/mavlink/vehicles/1/components/1/messages/HEARTBEAT"
ARMED_FLAG = 128  # MAV_MODE_FLAG_SAFETY_ARMED

REQUEST_TIMEOUT_S = 30.0


# Indirected so tests can describe a /dev tree without creating one.
def _realpath(path: str) -> str:
    return os.path.realpath(path)


def _exists(path: str) -> bool:
    return os.path.exists(path)


def _by_id_links() -> list[str]:
    try:
        return sorted(str(link) for link in BY_ID_DIR.iterdir())
    except OSError:
        return []


def find_agt_device() -> str | None:
    """Return the AGT's device node, or None if it cannot be identified."""
    matches = sorted(
        p.device
        for p in list_ports.comports()
        if p.vid == AGT_USB_VID and p.pid == AGT_USB_PID
    )
    if not matches:
        return None
    if len(matches) > 1:
        # This adapter reports no serial number, so two of them are
        # indistinguishable.  Reassigning the autopilot's port to the wrong one
        # would be worse than leaving a human to sort it out.
        logger.warning(
            "%d CH340 adapters present (%s); not guessing which one is the AGT",
            len(matches),
            ", ".join(matches),
        )
        return None
    return matches[0]


def stable_name_for(device: str) -> str:
    """Return the by-id name for ``device``, falling back to the node itself.

    by-id is keyed on the adapter, so it follows the AGT between USB sockets;
    by-path is keyed on the socket and is what broke in the first place.  The
    raw ``/dev/ttyUSBn`` node is the last resort because it depends on
    enumeration order.
    """
    for link in _by_id_links():
        if _realpath(link) == _realpath(device):
            return link
    return device


def entry_points_at(configured: str, device: str) -> bool:
    """True when a configured endpoint currently resolves to ``device``.

    Compared after resolution rather than as strings, so an entry already
    working under a by-path or raw name is left alone.  Rewriting it would buy
    a tidier name at the cost of an autopilot restart.
    """
    if not configured or not _exists(configured):
        return False
    return _realpath(configured) == _realpath(device)


def plan_serial_update(
    serials: list[dict], device: str
) -> list[dict] | None:
    """Return the serial list to write, or None when nothing needs changing.

    Every port other than the AGT's is passed through untouched; which UARTs
    the Navigator exposes is BlueOS's business, not ours.
    """
    current = next(
        (s for s in serials if s.get("port") == AGT_PORT_LETTER), None
    )
    if current is not None and entry_points_at(
        str(current.get("endpoint", "")), device
    ):
        return None

    others = [s for s in serials if s.get("port") != AGT_PORT_LETTER]
    return others + [
        {"port": AGT_PORT_LETTER, "endpoint": stable_name_for(device)}
    ]


async def _vehicle_is_armed(client: httpx.AsyncClient) -> bool | None:
    """Armed state from the autopilot HEARTBEAT, or None if unreadable."""
    url = f"{blueos_services.mavlink2rest}{HEARTBEAT_PATH}"
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        message = resp.json().get("message", {})
        base_mode = message.get("base_mode")
        bits = (
            base_mode.get("bits")
            if isinstance(base_mode, dict)
            else base_mode
        )
        if not isinstance(bits, (int, float)):
            return None
        return bool(int(bits) & ARMED_FLAG)
    except Exception as e:
        logger.debug("Could not read HEARTBEAT for arming state: %s", e)
        return None


async def reconcile_agt_serial(dive_in_progress: bool = False) -> bool:
    """Ensure port G points at the AGT.  Returns True if the config changed.

    Writing the serial configuration restarts the autopilot, so this only
    writes when the mapping is actually broken, and never during a mission.
    """
    device = find_agt_device()
    if device is None:
        # Absent rather than misconfigured: the operator may simply have it
        # unplugged.  Dropping the saved entry would turn a temporary state
        # into a configuration change.
        logger.info("AGT USB adapter not present; leaving serial config alone")
        return False

    base = blueos_services.autopilot_manager
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_S, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(f"{base}{SERIALS_PATH}")
            resp.raise_for_status()
            serials = resp.json()
        except Exception as e:
            logger.warning("Could not read autopilot serial ports: %s", e)
            return False

        if not isinstance(serials, list) or not serials:
            # An empty list means the autopilot has no UARTs either, which we
            # cannot reconstruct.  Writing just the AGT here would leave the
            # Navigator's own ports out.
            logger.warning(
                "Autopilot reports no serial ports at all; refusing to write "
                "a configuration that would leave the Navigator UARTs out"
            )
            return False

        planned = plan_serial_update(serials, device)
        if planned is None:
            logger.info("AGT already mapped to autopilot port %s", AGT_PORT_LETTER)
            return False

        if dive_in_progress:
            logger.warning(
                "AGT is not on port %s but a dive is in progress; leaving the "
                "serial configuration alone rather than restarting the autopilot",
                AGT_PORT_LETTER,
            )
            return False

        if await _vehicle_is_armed(client):
            logger.warning(
                "AGT is not on port %s but the vehicle is armed; not "
                "restarting the autopilot to fix it",
                AGT_PORT_LETTER,
            )
            return False

        endpoint = planned[-1]["endpoint"]
        try:
            resp = await client.put(f"{base}{SERIALS_PATH}", json=planned)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Failed to map AGT to port %s: %s", AGT_PORT_LETTER, e)
            return False

        logger.info(
            "Mapped AGT to autopilot port %s (%s); the autopilot will restart",
            AGT_PORT_LETTER,
            endpoint,
        )
        return True
