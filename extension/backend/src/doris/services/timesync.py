"""MAVLink-based system clock synchronisation.

Reads SYSTEM_TIME from the Artemis Global Tracker (MAVLink component 191)
and sets the Linux system clock when drift exceeds a threshold.  The
Artemis derives its time from GPS, so this gives microsecond-accurate UTC
even when the Raspberry Pi has no RTC or NTP.

Falls back to a browser-supplied timestamp when the Artemis is unavailable.
"""

import asyncio
import logging
import subprocess
from datetime import datetime, timezone

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

ARTEMIS_COMPONENT_ID = 191
MIN_DRIFT_S = 30
POLL_INTERVAL_S = 30
_SANE_YEAR_LO = 2024
_SANE_YEAR_HI = 2030


def _clock_is_sane() -> bool:
    year = datetime.now(tz=timezone.utc).year
    return _SANE_YEAR_LO <= year <= _SANE_YEAR_HI


def _set_system_clock(dt: datetime) -> bool:
    """Set the Linux system clock via the ``date`` command."""
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        subprocess.run(
            ["date", "-u", "-s", date_str],
            check=True,
            capture_output=True,
            timeout=5,
        )
        logger.info("System clock set to %s UTC", date_str)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logger.warning("Failed to set system clock: %s", exc)
        return False


class TimeSyncService:
    """Periodically syncs the system clock from Artemis GPS time."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._synced = False
        self._last_drift: float | None = None
        self._source: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    @property
    def synced(self) -> bool:
        return self._synced

    @property
    def clock_sane(self) -> bool:
        return _clock_is_sane()

    def status(self) -> dict:
        return {
            "synced": self._synced,
            "clock_sane": _clock_is_sane(),
            "source": self._source,
            "last_drift_seconds": (
                round(self._last_drift, 1) if self._last_drift is not None else None
            ),
            "utc": datetime.now(tz=timezone.utc).isoformat(),
        }

    async def _artemis_has_gps_fix(self) -> bool:
        """Check GPS_INPUT from the Artemis to verify it has a valid fix."""
        base = blueos_services.mavlink2rest
        url = (
            f"{base}/mavlink/vehicles/1/components/"
            f"{ARTEMIS_COMPONENT_ID}/messages/GPS_INPUT"
        )
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            fix_type = msg.get("fix_type", 0)
            if isinstance(fix_type, dict):
                fix_type = fix_type.get("bits", 0)
            return int(fix_type) >= 2
        except Exception:
            return False

    async def try_sync_from_artemis(self) -> bool:
        """Read SYSTEM_TIME from the Artemis (component 191) and sync if needed.

        Only trusts the time when the Artemis has a GPS fix (fix_type >= 2).
        Without a fix, the Artemis reports garbage time_unix_usec.
        """
        has_fix = await self._artemis_has_gps_fix()
        if not has_fix:
            logger.debug("Artemis has no GPS fix — skipping time sync")
            return False

        base = blueos_services.mavlink2rest
        url = (
            f"{base}/mavlink/vehicles/1/components/"
            f"{ARTEMIS_COMPONENT_ID}/messages/SYSTEM_TIME"
        )
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                logger.debug("Artemis SYSTEM_TIME not available (404)")
                return False
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Could not read Artemis SYSTEM_TIME: %s", exc)
            return False

        msg = data.get("message", {})
        time_unix_usec = msg.get("time_unix_usec", 0)
        if not time_unix_usec or time_unix_usec < 0:
            logger.debug("Artemis SYSTEM_TIME has no valid time_unix_usec")
            return False

        gps_dt = datetime.fromtimestamp(
            time_unix_usec / 1_000_000, tz=timezone.utc
        )
        if not (_SANE_YEAR_LO <= gps_dt.year <= _SANE_YEAR_HI):
            logger.debug(
                "Artemis GPS time looks invalid (year %d)", gps_dt.year
            )
            return False

        now = datetime.now(tz=timezone.utc)
        drift = abs((now - gps_dt).total_seconds())
        self._last_drift = drift

        if drift <= MIN_DRIFT_S:
            self._synced = True
            self._source = "artemis-gps"
            return True

        logger.info(
            "System clock drift %.0fs — syncing from Artemis GPS time (%s)",
            drift,
            gps_dt.isoformat(),
        )
        if _set_system_clock(gps_dt):
            self._synced = True
            self._source = "artemis-gps"
            return True
        return False

    async def try_sync_from_client(self, client_dt: datetime) -> dict:
        """Sync from a browser-supplied timestamp (fallback when Artemis is down)."""
        if client_dt.tzinfo is None:
            client_dt = client_dt.replace(tzinfo=timezone.utc)
        if not (_SANE_YEAR_LO <= client_dt.year <= _SANE_YEAR_HI):
            return {"synced": False, "reason": "client time looks invalid"}

        now = datetime.now(tz=timezone.utc)
        drift = abs((now - client_dt).total_seconds())
        self._last_drift = drift

        if drift <= MIN_DRIFT_S:
            self._synced = True
            if not self._source:
                self._source = "client-browser"
            return {
                "synced": False,
                "reason": "drift within tolerance",
                "drift_seconds": round(drift, 1),
                "clock_sane": _clock_is_sane(),
            }

        if self._source == "artemis-gps" and self._synced:
            return {
                "synced": False,
                "reason": "already synced from GPS",
                "drift_seconds": round(drift, 1),
                "clock_sane": _clock_is_sane(),
            }

        ok = _set_system_clock(client_dt)
        if ok:
            self._synced = True
            self._source = "client-browser"
        return {
            "synced": ok,
            "drift_seconds": round(drift, 1),
            "source": "client-browser" if ok else None,
            "clock_sane": _clock_is_sane(),
            "new_utc": datetime.now(tz=timezone.utc).isoformat() if ok else None,
        }

    async def _poll_loop(self) -> None:
        """Background loop: try Artemis sync every POLL_INTERVAL_S."""
        while True:
            try:
                await self.try_sync_from_artemis()
            except Exception as exc:
                logger.debug("Time sync poll error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)

    def start_background_sync(self) -> None:
        """Start the background polling task (call once at app startup)."""
        if self._task is not None:
            return
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._poll_loop())
        logger.info(
            "Time sync background task started (Artemis component %d, "
            "interval %ds)",
            ARTEMIS_COMPONENT_ID,
            POLL_INTERVAL_S,
        )

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


timesync_service = TimeSyncService()
