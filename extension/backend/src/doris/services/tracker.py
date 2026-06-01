"""Artemis Global Tracker detection and GPS data service.

Detects the SparkFun Artemis Global Tracker by checking for its
HEARTBEAT on MAVLink component 191 (MAV_COMP_ID_ONBOARD_COMPUTER).
Reads GPS_RAW_INT from the same component for position data.

Iridium test support
--------------------
The AGT emits ``IRIDIUM: …`` STATUSTEXT messages while running an
Iridium SBD test, but the test takes 2-10 minutes and the AGT emits
unrelated STATUSTEXT messages (``GPS: PVT watchdog reinit`` etc.) in
between.  mavlink2rest only caches the *latest* STATUSTEXT, so HTTP
polling regularly loses the ``IRIDIUM: …PASSED/FAILED`` message.

To fix this we subscribe to the mavlink2rest STATUSTEXT WebSocket on
first use, filter to component 191, and buffer the last 100 messages
with a monotonic id.  The frontend polls with ``?since_id=<n>`` and
scans every new message for the test outcome — no STATUSTEXT can be
missed even if the AGT emits dozens of messages between polls.
"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import websockets

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

ARTEMIS_COMPONENT_ID = 191
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

# Keep reporting the tracker (and therefore the Iridium button) for this
# long after the last good HEARTBEAT.  mavlink2rest's per-message
# frequency estimate is noisy and regularly dips below the detection
# threshold for a poll or two even while the AGT is heartbeating at 1 Hz,
# which made the tracker tile randomly vanish from the sensors page
# (issue #29).  A grace window rides out those dips without keeping a
# truly disconnected tracker visible forever.
TRACKER_STICKY_GRACE_S = 60.0


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
            )
        ]

    async def get_gps_data(self) -> dict | None:
        """Read the latest GPS_RAW_INT from the Artemis (component 191)."""
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
        """Check for a recent HEARTBEAT from the Artemis component."""
        base = _m2r_base()
        url = f"{base}/mavlink/vehicles/1/components/{ARTEMIS_COMPONENT_ID}/messages/HEARTBEAT"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            time_info = data.get("status", {}).get("time", {})
            freq = time_info.get("frequency", 0)
            if freq < 0.05:
                return None
            return data.get("message")
        except Exception as e:
            logger.debug("No Artemis heartbeat: %s", e)
            return None

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

    # ── Iridium test ────────────────────────────────────────────────

    async def send_iridium_test(self) -> dict:
        """Send COMMAND_LONG (MAV_CMD_USER_4) to trigger Iridium test.

        Returns ``{accepted, error, latest_id}`` where ``latest_id`` is
        the buffer id at the moment the command was sent so the frontend
        can poll for messages newer than that.
        """
        self._ensure_statustext_subscriber()
        # Wait until the STATUSTEXT WebSocket subscriber is actually
        # connected to mavlink2rest before dispatching the command.
        # Without this, on the very first invocation per process the
        # MAV_CMD_USER_4 goes out tens to hundreds of milliseconds before
        # the WS handshake completes, and the AGT's near-immediate reply
        # ("IRIDIUM: Test starting", and on a fast PASSED the result too)
        # arrives at mavlink2rest with no listener and is lost.
        try:
            await asyncio.wait_for(
                self._ws_connected.wait(), timeout=WS_FIRST_CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "STATUSTEXT subscriber did not connect within %.1fs; "
                "test command will be sent anyway but reply may be lost",
                WS_FIRST_CONNECT_TIMEOUT_S,
            )
        baseline_id = await self._statustext.latest_id()

        base = _m2r_base()
        post_url = f"{base}/mavlink"
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "COMMAND_LONG",
                "target_system": 1,
                "target_component": ARTEMIS_COMPONENT_ID,
                "command": {"type": "MAV_CMD_USER_4"},
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
                post_url, json=payload, timeout=TRIGGER_POST_TIMEOUT_S,
            )
            resp.raise_for_status()
            logger.info("Iridium test command sent to AGT (baseline_id=%d)", baseline_id)
            return {"accepted": True, "error": None, "latest_id": baseline_id}
        except Exception as e:
            logger.warning("Failed to send Iridium test command: %s", e)
            return {"accepted": False, "error": str(e), "latest_id": baseline_id}

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
