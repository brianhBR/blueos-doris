"""Artemis Global Tracker detection and GPS data service.

Detects the SparkFun Artemis Global Tracker by checking for its
HEARTBEAT on MAVLink component 192 (MAV_COMP_ID_ONBOARD_COMPUTER2).
Reads GPS_RAW_INT from the same component for position data.

Note: the AGT must NOT use component 191 (MAV_COMP_ID_ONBOARD_COMPUTER)
because BlueOS's mavlink-server router advertises itself on 191, and
the resulting identity collision makes the autopilot mis-route
commands (e.g. the Iridium test) away from the real AGT.

Iridium test support
--------------------
The AGT emits ``IRIDIUM: …`` STATUSTEXT messages while running an
Iridium SBD test, but the test takes 2-10 minutes and the AGT emits
unrelated STATUSTEXT messages (``GPS: PVT watchdog reinit`` etc.) in
between.  mavlink2rest only caches the *latest* STATUSTEXT, so HTTP
polling regularly loses the ``IRIDIUM: …PASSED/FAILED`` message.

To fix this we subscribe to the mavlink2rest STATUSTEXT WebSocket on
first use, filter to component 192, and buffer the last 100 messages
with a monotonic id.  The frontend polls with ``?since_id=<n>`` and
scans every new message for the test outcome — no STATUSTEXT can be
missed even if the AGT emits dozens of messages between polls.
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import websockets

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

ARTEMIS_COMPONENT_ID = 192
GPS_FIX_NAMES = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}

GPS_FIX_TYPE_MAP: dict[str, int] = {
    "GPS_FIX_TYPE_NO_GPS": 0,
    "GPS_FIX_TYPE_NO_FIX": 1,
    "GPS_FIX_TYPE_2D_FIX": 2,
    "GPS_FIX_TYPE_3D_FIX": 3,
    "GPS_FIX_TYPE_DGPS": 4,
    "GPS_FIX_TYPE_RTK_FLOAT": 5,
    "GPS_FIX_TYPE_RTK_FIXED": 6,
}

SEVERITY_MAP: dict[str, int] = {
    "MAV_SEVERITY_EMERGENCY": 0, "MAV_SEVERITY_ALERT": 1,
    "MAV_SEVERITY_CRITICAL": 2, "MAV_SEVERITY_ERROR": 3,
    "MAV_SEVERITY_WARNING": 4, "MAV_SEVERITY_NOTICE": 5,
    "MAV_SEVERITY_INFO": 6, "MAV_SEVERITY_DEBUG": 7,
}

STATUSTEXT_BUFFER_SIZE = 100
WS_RECONNECT_DELAY_S = 2.0
WS_FIRST_CONNECT_TIMEOUT_S = 5.0  # max wait before sending first MAV_CMD_USER_4
TRIGGER_POST_TIMEOUT_S = 15.0  # mavlink2rest POST commonly takes 4-5s

# ── AGT firmware version / identity ──────────────────────────────
# The AGT firmware reports its identity as STATUSTEXT lines prefixed
# with "Doris AGT" — its git-describe version ("Doris AGT v0.2.0") and
# the RockBlock modem IMEI ("Doris AGT IMEI:300234061234567") — once at
# boot and ~3x early in loop().  Because our STATUSTEXT subscriber
# connects lazily (typically long after the AGT booted) we cannot rely
# on catching that boot broadcast, so we also request it explicitly.
#
# The request uses AGT_DEBUG = MAV_CMD_USER_3 (31012), which makes the
# AGT dump its version + IMEI + GPS diagnostics as STATUSTEXT.  This is
# the ONLY command we send to fetch the version.
#
# AGT command map — MAVLink only defines MAV_CMD_USER_1..5, so the old
# version command on USER_6 (31015) was never valid (mavlink2rest 404s
# it) and has been retired:
#   31010 USER_1  LED_CONTROL
#   31011 USER_2  MISSION_STATUS
#   31012 USER_3  AGT_DEBUG      ← version + IMEI + GPS diag dump
#   31013 USER_4  IRIDIUM_TEST
#   31014 USER_5  REBOOT         ← DANGER: soft-reboots the AGT. Never
#                                  send this as a background/poll action.
AGT_VERSION_PREFIX = "Doris AGT"
AGT_DEBUG_CMD = "MAV_CMD_USER_3"
# Minimum AGT firmware this extension requires.  v0.3.0 introduces the
# safe-surface release status, capability handshake, and guarded shutdown
# protocol.  Older firmware must never be reported as compatible.
# Bump this in lockstep with breaking AGT protocol changes.
MIN_AGT_FIRMWARE_VERSION = "v0.3.0"
# Re-request the version at most this often while it's still unknown.
VERSION_REQUEST_INTERVAL_S = 30.0

_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
# RockBlock/Iridium 9603 IMEIs are 15 digits.  Accept an optional
# "IMEI:"/"IMEI=" label so version and IMEI can share a line or arrive
# as separate "Doris AGT ..." STATUSTEXT lines.
_IMEI_RE = re.compile(r"IMEI[:=\s]*(\d{15})", re.IGNORECASE)


def _parse_semver(text: str) -> tuple[int, int, int] | None:
    """Extract a (major, minor, patch) tuple from a version string.

    Handles git-describe output like ``v0.1.0``, ``v0.1.0-dirty`` and
    ``v0.1.0-3-gabc123`` by matching only the leading numeric core.
    """
    match = _SEMVER_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

# Keep reporting the tracker (and therefore the Iridium button) for this
# long after the last good HEARTBEAT.  mavlink2rest's per-message
# frequency estimate is noisy and regularly dips below the detection
# threshold for a poll or two even while the AGT is heartbeating at 1 Hz,
# which made the tracker tile randomly vanish from the sensors page
# (issue #29).  A grace window rides out those dips without keeping a
# truly disconnected tracker visible forever.
TRACKER_STICKY_GRACE_S = 60.0

# A HEARTBEAT is considered "live" when mavlink2rest last received it
# within this window.  Detection is gated on message *recency*
# (status.time.last_update age), NOT on mavlink2rest's per-message
# frequency estimate: that estimate is a noisy EMA that sits below any
# fixed threshold for long stretches even while the AGT heartbeats
# steadily, which dropped the tile despite a live link (issue #55).
# timesync.py trusts last_update age for the same component; we mirror it.
HEARTBEAT_MAX_AGE_S = 30.0


def _m2r_base() -> str:
    return blueos_services.mavlink2rest


def _statustext_ws_url() -> str:
    base = blueos_services.mavlink2rest
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/ws/mavlink?filter=STATUSTEXT"


def _decode_text(raw_text) -> str:
    """STATUSTEXT.text is sent as a list of single-char strings padded with \\x00."""
    if isinstance(raw_text, list):
        raw_text = "".join(c for c in raw_text if c != "\x00")
    return (raw_text or "").strip()


def _decode_severity(raw_severity) -> int:
    if isinstance(raw_severity, dict):
        return SEVERITY_MAP.get(raw_severity.get("type", ""), 6)
    if isinstance(raw_severity, int):
        return raw_severity
    return 6


def _message_age_seconds(data: dict) -> float | None:
    """Age of a mavlink2rest message from its ``status.time.last_update``.

    Returns ``None`` when the timestamp is missing or unparseable.  Both
    ``last_update`` and ``now`` come from the same host clock, so the
    returned age is valid even when the absolute system clock is wrong
    (mirrors the same helper in ``timesync.py``).
    """
    last_update = data.get("status", {}).get("time", {}).get("last_update")
    if not last_update or not isinstance(last_update, str):
        return None
    s = last_update.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - dt).total_seconds()


class _StatusTextBuffer:
    """In-memory ring buffer of recent AGT STATUSTEXT messages."""

    def __init__(self, max_size: int = STATUSTEXT_BUFFER_SIZE) -> None:
        self._messages: deque[dict] = deque(maxlen=max_size)
        self._next_id: int = 1
        self._lock = asyncio.Lock()

    async def add(self, text: str, severity: int) -> None:
        async with self._lock:
            self._messages.append({
                "id": self._next_id,
                "text": text,
                "severity": severity,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._next_id += 1

    async def since(self, since_id: int) -> tuple[list[dict], int]:
        async with self._lock:
            new = [m for m in self._messages if m["id"] > since_id]
            return new, self._next_id - 1

    async def latest_id(self) -> int:
        async with self._lock:
            return self._next_id - 1


class ArtemisTrackerService:
    """Detects the Artemis tracker and reads its GPS data."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._statustext = _StatusTextBuffer()
        self._ws_task: asyncio.Task | None = None
        # Set when the WebSocket has connected to mavlink2rest at least once
        # this process. send_iridium_test() awaits this so the first
        # MAV_CMD_USER_4 isn't sent before we're listening — otherwise the
        # AGT's "IRIDIUM: Test starting" reply (and possibly PASSED/FAILED
        # if the test resolves quickly) gets dropped on the floor.
        self._ws_connected: asyncio.Event = asyncio.Event()
        # Monotonic timestamp of the most recent good HEARTBEAT, used to
        # keep the tracker tile sticky across mavlink2rest frequency dips.
        self._last_heartbeat_monotonic: float | None = None
        # Cached AGT firmware version ("v0.1.0"), parsed from the
        # "Doris AGT <version>" STATUSTEXT.  None until first seen/requested.
        self._agt_version: str | None = None
        # Cached RockBlock modem IMEI (15 digits), parsed from the
        # "Doris AGT IMEI:<imei>" STATUSTEXT.  None until first seen.
        self._agt_imei: str | None = None
        # Monotonic timestamp of the last version request, to rate-limit
        # the proactive re-request while the version is still unknown.
        self._version_requested_monotonic: float | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for the tracker if it's present or was seen recently.

        Detection is sticky: a single missed/low-frequency HEARTBEAT poll
        no longer drops the tile.  We only stop reporting the tracker once
        no heartbeat has been seen for ``TRACKER_STICKY_GRACE_S`` (issue #29).
        """
        hb = await self._get_heartbeat()
        now = time.monotonic()
        if hb is not None:
            self._last_heartbeat_monotonic = now
            # Tracker is present — make sure we learn its firmware version
            # (rate-limited; no-op once known).
            self._maybe_request_version()
        else:
            last = self._last_heartbeat_monotonic
            if last is None or (now - last) > TRACKER_STICKY_GRACE_S:
                return []
            # Within the grace window — ride out a transient frequency dip
            # and keep the tile (with whatever GPS data is still available).

        gps = await self.get_gps_data()

        if gps and gps.get("fix_type", 0) >= 2:
            status_text = (
                f"{gps['fix_type_name']} | "
                f"{gps['lat']:.6f}, {gps['lon']:.6f} | "
                f"{gps['satellites']} sats"
            )
        elif gps:
            status_text = f"{gps['fix_type_name']} | {gps['satellites']} sats"
        else:
            status_text = "Connected (no GPS data)"

        return [
            ModuleInfo(
                id="artemis-tracker",
                name="Artemis Global Tracker",
                type="tracker",
                status="connected",
                module_status=status_text,
                last_reading=datetime.now().isoformat(),
                firmware_version=self._agt_version,
            )
        ]

    async def get_gps_data(self) -> dict | None:
        """Read the latest GPS_RAW_INT from the Artemis (component 192)."""
        base = _m2r_base()
        url = f"{base}/mavlink/vehicles/1/components/{ARTEMIS_COMPONENT_ID}/messages/GPS_RAW_INT"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            if msg.get("type") != "GPS_RAW_INT":
                return None

            raw_fix = msg.get("fix_type", 0)
            if isinstance(raw_fix, dict):
                fix_type = GPS_FIX_TYPE_MAP.get(raw_fix.get("type", ""), 0)
            elif isinstance(raw_fix, int):
                fix_type = raw_fix
            else:
                fix_type = 0

            lat = msg.get("lat", 0) / 1e7
            lon = msg.get("lon", 0) / 1e7
            alt = msg.get("alt", 0) / 1000.0
            satellites = msg.get("satellites_visible", 0)
            hdop = msg.get("eph", 65535)
            if hdop != 65535:
                hdop = hdop / 100.0
            else:
                hdop = None
            speed = msg.get("vel", 0) / 100.0
            course = msg.get("cog", 0) / 100.0

            time_info = data.get("status", {}).get("time", {})
            last_update = time_info.get("last_update")

            return {
                "fix_type": fix_type,
                "fix_type_name": GPS_FIX_NAMES.get(fix_type, f"Unknown ({fix_type})"),
                "lat": lat,
                "lon": lon,
                "alt_m": alt,
                "satellites": satellites,
                "hdop": hdop,
                "speed_mps": speed,
                "course_deg": course,
                "last_update": last_update,
            }
        except httpx.HTTPStatusError:
            return None
        except Exception as e:
            logger.debug("Failed to read Artemis GPS: %s", e)
            return None

    async def _get_heartbeat(self) -> dict | None:
        """Return the AGT HEARTBEAT message if one was received recently.

        Presence is judged by message *recency* (``status.time.last_update``
        age), NOT by mavlink2rest's per-message frequency estimate.  That
        estimate is a noisy EMA that sits below any fixed threshold for
        long stretches even while the AGT heartbeats steadily, which made
        the tracker tile vanish despite a live link (issue #55).
        ``timesync.py`` already trusts ``last_update`` age for the same
        component; we mirror that here.

        A stale cached HEARTBEAT (mavlink2rest keeps the last message
        forever) is rejected once it ages past ``HEARTBEAT_MAX_AGE_S``.
        When the timestamp is missing/unparseable we accept the message's
        presence rather than risk a false negative (the bug being fixed).
        """
        base = _m2r_base()
        url = f"{base}/mavlink/vehicles/1/components/{ARTEMIS_COMPONENT_ID}/messages/HEARTBEAT"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("No Artemis heartbeat: %s", e)
            return None

        msg = data.get("message")
        if not isinstance(msg, dict) or msg.get("type") != "HEARTBEAT":
            return None

        age = _message_age_seconds(data)
        if age is not None and age > HEARTBEAT_MAX_AGE_S:
            logger.debug("Artemis HEARTBEAT is stale (%.0fs old) — ignoring", age)
            return None
        return msg

    # ── STATUSTEXT subscriber ───────────────────────────────────────

    def _ensure_statustext_subscriber(self) -> None:
        """Lazily start the background WebSocket subscriber."""
        if self._ws_task is None or self._ws_task.done():
            # Clear so callers waiting on the event don't get stale True
            # from a previous subscriber that has since crashed.  The new
            # subscriber will set() it again as soon as it connects.
            self._ws_connected.clear()
            self._ws_task = asyncio.create_task(
                self._statustext_ws_loop(),
                name="agt-statustext-subscriber",
            )

    async def _statustext_ws_loop(self) -> None:
        """Forever-loop that streams STATUSTEXT into the buffer.

        Reconnects on disconnect with a fixed backoff so a transient
        mavlink2rest restart can't permanently break the iridium UI.
        """
        url = _statustext_ws_url()
        logger.info("AGT STATUSTEXT subscriber starting (%s)", url)
        while True:
            try:
                async with websockets.connect(url, close_timeout=5) as ws:
                    # Signal callers waiting on first connect that we're now
                    # listening.  Stays set across reconnects so transient
                    # mavlink2rest blips don't re-stall send_iridium_test().
                    self._ws_connected.set()
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            try:
                                raw = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                continue
                        if not raw.startswith("{"):
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        header = data.get("header", {}) or {}
                        if header.get("component_id") != ARTEMIS_COMPONENT_ID:
                            continue

                        msg = data.get("message", {}) or {}
                        if msg.get("type") != "STATUSTEXT":
                            continue

                        text = _decode_text(msg.get("text", ""))
                        if not text:
                            continue
                        sev = _decode_severity(msg.get("severity"))
                        self._capture_version(text)
                        await self._statustext.add(text, sev)
            except asyncio.CancelledError:
                logger.info("AGT STATUSTEXT subscriber cancelled")
                raise
            except Exception as e:
                logger.debug(
                    "AGT STATUSTEXT WS disconnected (%s); reconnecting in %.1fs",
                    e, WS_RECONNECT_DELAY_S,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY_S)

    # ── AGT commands (fire-and-trigger) ─────────────────────────────

    async def _send_agt_command(self, cmd_type: str, label: str) -> dict:
        """Send a fire-and-trigger COMMAND_LONG to the AGT (component 192).

        Ensures the STATUSTEXT subscriber is connected first, otherwise on
        the very first invocation per process the command goes out before
        the WS handshake completes and the AGT's near-immediate reply burst
        (e.g. "IRIDIUM: Test starting" or the AGT_DEBUG dump) arrives at
        mavlink2rest with no listener and is lost.

        Returns ``{accepted, error, latest_id}`` where ``latest_id`` is the
        buffer id at send time so the frontend can poll for newer messages.
        """
        self._ensure_statustext_subscriber()
        try:
            await asyncio.wait_for(
                self._ws_connected.wait(), timeout=WS_FIRST_CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "STATUSTEXT subscriber did not connect within %.1fs; %s "
                "will be sent anyway but reply may be lost",
                WS_FIRST_CONNECT_TIMEOUT_S, label,
            )
        baseline_id = await self._statustext.latest_id()

        base = _m2r_base()
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "COMMAND_LONG",
                "target_system": 1,
                "target_component": ARTEMIS_COMPONENT_ID,
                "command": {"type": cmd_type},
                "confirmation": 0,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "param5": 0.0,
                "param6": 0.0,
                "param7": 0.0,
            },
        }
        try:
            resp = await self.client.post(
                f"{base}/mavlink", json=payload, timeout=TRIGGER_POST_TIMEOUT_S,
            )
            resp.raise_for_status()
            logger.info("%s command sent to AGT (baseline_id=%d)", label, baseline_id)
            return {"accepted": True, "error": None, "latest_id": baseline_id}
        except Exception as e:
            logger.warning("Failed to send %s command: %s", label, e)
            return {"accepted": False, "error": str(e), "latest_id": baseline_id}

    async def send_iridium_test(self) -> dict:
        """Trigger a one-off Iridium test (MAV_CMD_USER_4)."""
        return await self._send_agt_command("MAV_CMD_USER_4", "Iridium test")

    async def send_debug(self) -> dict:
        """Trigger AGT_DEBUG (MAV_CMD_USER_3).

        The AGT dumps its version, IMEI (if the modem has been powered at
        least once this boot) and a burst of ``GPS: ...`` diagnostics as
        STATUSTEXT.  All of it lands in the shared STATUSTEXT buffer, so the
        frontend polls ``get_iridium_status`` from the returned baseline id.
        """
        return await self._send_agt_command(AGT_DEBUG_CMD, "AGT debug")

    async def get_iridium_status(self, since_id: int = 0) -> dict:
        """Return all AGT STATUSTEXT messages newer than ``since_id``.

        Response shape::

            {
              "messages": [
                {"id": 7, "text": "IRIDIUM: Test starting", "severity": 6,
                 "timestamp": "..."},
                ...
              ],
              "latest_id": 7
            }
        """
        self._ensure_statustext_subscriber()
        messages, latest_id = await self._statustext.since(since_id)
        return {"messages": messages, "latest_id": latest_id}

    # ── Firmware version ────────────────────────────────────────────

    def _capture_version(self, text: str) -> None:
        """Parse and cache AGT identity from a ``Doris AGT ...`` STATUSTEXT.

        The firmware reports its version and RockBlock IMEI as "Doris AGT"
        lines — either combined ("Doris AGT v0.2.0 IMEI:300234061234567")
        or as separate lines.  Each field is captured independently so
        neither clobbers a value that isn't present on a given line.
        """
        if not text.startswith(AGT_VERSION_PREFIX):
            return

        imei_match = _IMEI_RE.search(text)
        if imei_match:
            imei = imei_match.group(1)
            if imei != self._agt_imei:
                logger.info("AGT RockBlock IMEI: %s", imei)
            self._agt_imei = imei

        # Strip the prefix and any IMEI clause, then treat the remainder
        # as the version.  Only accept it when it carries a semver core so
        # a bare "Doris AGT IMEI:..." line doesn't overwrite the version.
        remainder = _IMEI_RE.sub("", text[len(AGT_VERSION_PREFIX):]).strip()
        if remainder and _parse_semver(remainder) is not None:
            if remainder != self._agt_version:
                logger.info("AGT firmware version: %s", remainder)
            self._agt_version = remainder

    async def request_version(self) -> bool:
        """Ask the AGT to dump its identity via AGT_DEBUG (MAV_CMD_USER_3).

        AGT_DEBUG makes the AGT emit its version, IMEI (if known) and GPS
        diagnostics as ``Doris AGT ...``/``GPS: ...`` STATUSTEXT lines.
        The reply arrives asynchronously and is picked up by the WebSocket
        subscriber, so this only dispatches the request and reports whether
        the POST succeeded.
        """
        self._ensure_statustext_subscriber()
        try:
            await asyncio.wait_for(
                self._ws_connected.wait(), timeout=WS_FIRST_CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "STATUSTEXT subscriber did not connect within %.1fs; "
                "version reply may be lost", WS_FIRST_CONNECT_TIMEOUT_S,
            )
        self._version_requested_monotonic = time.monotonic()

        base = _m2r_base()
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "COMMAND_LONG",
                "target_system": 1,
                "target_component": ARTEMIS_COMPONENT_ID,
                "command": {"type": AGT_DEBUG_CMD},
                "confirmation": 0,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "param5": 0.0,
                "param6": 0.0,
                "param7": 0.0,
            },
        }
        try:
            resp = await self.client.post(
                f"{base}/mavlink", json=payload, timeout=TRIGGER_POST_TIMEOUT_S,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Failed to request AGT version: %s", e)
            return False

    def _maybe_request_version(self) -> None:
        """Fire a one-off, rate-limited version request while it's unknown.

        Called from the (frequently polled) module scan so the tracker
        tile eventually shows a firmware version without any user action,
        without spamming a request on every poll.
        """
        if self._agt_version is not None:
            return
        last = self._version_requested_monotonic
        if last is not None and (time.monotonic() - last) < VERSION_REQUEST_INTERVAL_S:
            return
        self._version_requested_monotonic = time.monotonic()
        try:
            asyncio.create_task(self.request_version(), name="agt-version-request")
        except RuntimeError:
            # No running event loop (e.g. called from a sync context); the
            # next poll will retry once a loop is available.
            pass

    async def get_version(self, request: bool = True) -> dict:
        """Return the AGT firmware version and its compatibility status.

        Response shape::

            {"version": "v0.2.0", "imei": "300234061234567",
             "min_required": "v0.2.0", "compatible": true, "known": true}

        When ``request`` is true and the version isn't cached yet, this
        sends an AGT_DEBUG (MAV_CMD_USER_3) request and waits briefly for
        the reply.
        """
        if self._agt_version is None and request:
            await self.request_version()
            for _ in range(10):  # ~3s total
                if self._agt_version is not None:
                    break
                await asyncio.sleep(0.3)

        version = self._agt_version
        compatible: bool | None = None
        current = _parse_semver(version) if version else None
        minimum = _parse_semver(MIN_AGT_FIRMWARE_VERSION)
        if current is not None and minimum is not None:
            compatible = current >= minimum
        return {
            "version": version,
            "imei": self._agt_imei,
            "min_required": MIN_AGT_FIRMWARE_VERSION,
            "compatible": compatible,
            "known": version is not None,
        }

    async def close(self) -> None:
        if self._ws_task is not None and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


tracker_service = ArtemisTrackerService()
