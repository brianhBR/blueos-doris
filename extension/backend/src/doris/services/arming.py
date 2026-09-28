"""Vehicle arming-status service.

Surfaces the autopilot's arming state and any failing pre-arm checks so
the extension can show a "waiting to arm" banner that explains *why* the
vehicle won't arm (issues #44 and #8).

How ArduPilot reports this
──────────────────────────
While disarmed with failing checks, ArduPilot streams the failure reasons
as STATUSTEXT messages ("PreArm: <reason>") on a rotating ~30-60 s nag, and
emits "Arm: <reason>" when an arm attempt is rejected.  mavlink2rest's HTTP
message endpoint only caches the *latest* STATUSTEXT, so a single GET would
miss most of the rotating reasons.  We therefore subscribe to the
mavlink2rest STATUSTEXT WebSocket (mirroring ``ArtemisTrackerService``) and
keep a de-duplicated, TTL-bounded view of the most recent failing checks.

Armed state comes from the autopilot HEARTBEAT (``base_mode`` &
``MAV_MODE_FLAG_SAFETY_ARMED``); when the vehicle is armed the failing-check
list is cleared so the banner disappears.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import websockets

from ..config import blueos_services

logger = logging.getLogger(__name__)

AUTOPILOT_COMPONENT_ID = 1
ARMED_FLAG = 128  # MAV_MODE_FLAG_SAFETY_ARMED

# A failing pre-arm reason is considered "current" for this long after it
# was last reported.  ArduPilot re-nags roughly every 30-60 s, so this is
# generous enough to ride out a missed cycle without showing stale reasons.
FAILURE_TTL_S = 150.0

WS_RECONNECT_DELAY_S = 2.0
MAX_FAILURES = 20

# STATUSTEXT prefixes that indicate a failing arming / pre-arm check.
_PREARM_PREFIXES = ("prearm:", "arm:")

SEVERITY_MAP = {
    "MAV_SEVERITY_EMERGENCY": 0,
    "MAV_SEVERITY_ALERT": 1,
    "MAV_SEVERITY_CRITICAL": 2,
    "MAV_SEVERITY_ERROR": 3,
    "MAV_SEVERITY_WARNING": 4,
    "MAV_SEVERITY_NOTICE": 5,
    "MAV_SEVERITY_INFO": 6,
    "MAV_SEVERITY_DEBUG": 7,
}


def _m2r_base() -> str:
    return blueos_services.mavlink2rest


def _statustext_ws_url() -> str:
    base = blueos_services.mavlink2rest
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/ws/mavlink?filter=STATUSTEXT"


def _decode_text(raw_text) -> str:
    """STATUSTEXT.text is a list of single-char strings padded with \\x00."""
    if isinstance(raw_text, list):
        raw_text = "".join(c for c in raw_text if c != "\x00")
    return (raw_text or "").strip()


def _decode_severity(raw_severity) -> int:
    if isinstance(raw_severity, dict):
        return SEVERITY_MAP.get(raw_severity.get("type", ""), 6)
    if isinstance(raw_severity, int):
        return raw_severity
    return 6


def _is_prearm_text(text: str) -> bool:
    low = text.strip().lower()
    return any(low.startswith(p) for p in _PREARM_PREFIXES)


def _base_mode_bits(base_mode) -> int | None:
    """Extract the integer bitfield from a HEARTBEAT base_mode value.

    mavlink2rest serializes base_mode as ``{"bits": <int>}`` but older
    builds may send a bare int; handle both, returning None if unknown.
    """
    if isinstance(base_mode, dict):
        bits = base_mode.get("bits")
        return int(bits) if isinstance(bits, (int, float)) else None
    if isinstance(base_mode, (int, float)):
        return int(base_mode)
    return None


def _mavtype_name(mavtype) -> str | None:
    if isinstance(mavtype, dict):
        name = mavtype.get("type")
        return name if isinstance(name, str) else None
    if isinstance(mavtype, str):
        return mavtype
    return None


def _decode_heartbeat_armed(payload: object) -> tuple[bool, bool]:
    """Return ``(armed, known)`` from a mavlink2rest HEARTBEAT JSON body.

    ``known`` is False when the payload is missing, is not a vehicle
    heartbeat, or has no parseable ``base_mode``.  GCS heartbeats are
    ignored so a ground-station SAFETY_ARMED bit cannot flip the banner.
    The ``type`` field is optional: a parseable ``base_mode`` is enough,
    so a missing type is not treated as "disarmed".
    """
    if not isinstance(payload, dict):
        return False, False
    msg = payload.get("message", payload)
    if not isinstance(msg, dict):
        return False, False
    msg_type = msg.get("type")
    if msg_type is not None and msg_type != "HEARTBEAT":
        return False, False
    if _mavtype_name(msg.get("mavtype")) == "MAV_TYPE_GCS":
        return False, False
    bits = _base_mode_bits(msg.get("base_mode"))
    if bits is None:
        return False, False
    return bool(bits & ARMED_FLAG), True


class ArmingService:
    """Tracks vehicle armed state and failing pre-arm checks."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        # reason text -> {"text", "severity", "last_seen", "timestamp"}
        self._failures: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        # Last confirmed HEARTBEAT armed bit.  Held across transient
        # mavlink2rest misses so callers do not see armed flap false.
        self._last_armed: bool | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_status(self) -> dict:
        """Return armed state plus any current failing pre-arm checks.

        Response shape::

            {
              "armed": bool,
              "armed_known": bool,
              "waiting_to_arm": bool,
              "reasons": [{"text", "severity", "timestamp"}],
              "checked_at": "<iso8601>",
            }
        """
        self._ensure_statustext_subscriber()
        armed, armed_known = await self._read_armed()
        if armed_known:
            self._last_armed = armed
        elif self._last_armed is not None:
            # Keep the last confirmed bit.  Reporting False here is what
            # made the top banner flash red on every dropped HEARTBEAT.
            armed = self._last_armed

        async with self._lock:
            if armed:
                # Vehicle is armed: pre-arm reasons are no longer relevant.
                self._failures.clear()
            else:
                self._prune_locked()
            reasons = sorted(
                (
                    {
                        "text": f["text"],
                        "severity": f["severity"],
                        "timestamp": f["timestamp"],
                    }
                    for f in self._failures.values()
                ),
                key=lambda r: r["text"].lower(),
            )

        waiting = bool(armed_known and not armed and reasons)
        return {
            "armed": armed,
            "armed_known": armed_known,
            "waiting_to_arm": waiting,
            "reasons": reasons,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _read_armed(self) -> tuple[bool, bool]:
        """Read armed state from the autopilot HEARTBEAT.

        Returns ``(armed, armed_known)``; ``armed_known`` is False when the
        heartbeat or its base_mode couldn't be read, so the caller can avoid
        claiming the vehicle is disarmed on a transient mavlink2rest miss.
        """
        base = _m2r_base()
        url = (
            f"{base}/mavlink/vehicles/1/components/"
            f"{AUTOPILOT_COMPONENT_ID}/messages/HEARTBEAT"
        )
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return False, False
            resp.raise_for_status()
            return _decode_heartbeat_armed(resp.json())
        except Exception as e:
            logger.debug("Could not read HEARTBEAT for arming state: %s", e)
            return False, False

    def _prune_locked(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, f in self._failures.items()
            if now - f["last_seen"] > FAILURE_TTL_S
        ]
        for key in stale:
            del self._failures[key]

    async def _record_failure(self, text: str, severity: int) -> None:
        key = text.strip().lower()
        async with self._lock:
            self._failures[key] = {
                "text": text.strip(),
                "severity": severity,
                "last_seen": time.monotonic(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Bound memory: drop the oldest if we somehow accumulate many.
            if len(self._failures) > MAX_FAILURES:
                oldest = min(
                    self._failures.items(), key=lambda kv: kv[1]["last_seen"]
                )[0]
                del self._failures[oldest]

    # ── STATUSTEXT subscriber ───────────────────────────────────────

    def _ensure_statustext_subscriber(self) -> None:
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.create_task(
                self._statustext_ws_loop(),
                name="arming-statustext-subscriber",
            )

    async def _statustext_ws_loop(self) -> None:
        url = _statustext_ws_url()
        logger.info("Arming STATUSTEXT subscriber starting (%s)", url)
        while True:
            try:
                async with websockets.connect(url, close_timeout=5) as ws:
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
                        if header.get("component_id") != AUTOPILOT_COMPONENT_ID:
                            continue
                        msg = data.get("message", {}) or {}
                        if msg.get("type") != "STATUSTEXT":
                            continue

                        text = _decode_text(msg.get("text", ""))
                        if not text or not _is_prearm_text(text):
                            continue
                        await self._record_failure(
                            text, _decode_severity(msg.get("severity"))
                        )
            except asyncio.CancelledError:
                logger.info("Arming STATUSTEXT subscriber cancelled")
                raise
            except Exception as e:
                logger.debug(
                    "Arming STATUSTEXT WS disconnected (%s); reconnecting in %.1fs",
                    e, WS_RECONNECT_DELAY_S,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY_S)
