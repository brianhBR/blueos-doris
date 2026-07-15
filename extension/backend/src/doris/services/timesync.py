"""MAVLink-based system clock synchronisation.

Reads SYSTEM_TIME from the Artemis Global Tracker (MAVLink component 192)
and sets the Linux system clock when drift exceeds a threshold.  The
Artemis derives its time from GPS, so this gives microsecond-accurate UTC
even when the Raspberry Pi has no RTC or NTP.

Falls back to a browser-supplied timestamp when the Artemis is unavailable.

Mission profile notes (DORIS):
  * GPS is only available at the surface (deploy + recovery); the vehicle
    runs blind for the whole dive.  We therefore want to grab a fix and
    discipline the clock *quickly* during the brief surface window, then
    let the Pi free-run for the dive.  The poll loop runs fast until the
    first successful sync, then backs off.
  * BlueOS has no ``fake-hwclock``; it relies on systemd-timesyncd, which
    only persists the clock lazily (periodic + clean shutdown).  Our
    vehicles get power-cut, so a GPS-synced time can be lost on the next
    boot.  We therefore persist the last-good UTC to our own storage and,
    on startup, bump the clock forward to that floor so logs never fall
    back to 1970 mid-mission.
  * mavlink2rest runs on the same host as DORIS, so its
    ``status.time.last_update`` shares our (possibly wrong) system clock.
    Relative message *age* is therefore reliable even when absolute time
    is wrong, which lets us reject a stale cached SYSTEM_TIME that would
    otherwise step the clock backwards underwater.
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

ARTEMIS_COMPONENT_ID = 192
MIN_DRIFT_S = 30
POLL_INTERVAL_S = 30
# Poll quickly until the first successful sync so we catch the brief
# surface GPS window before the vehicle submerges.
FAST_POLL_INTERVAL_S = 3
# Reject mavlink2rest messages older than this; a stale cached SYSTEM_TIME
# must never be used to step the clock (it would jump us backwards).
MAX_MSG_AGE_S = 15
_SANE_YEAR_LO = 2024
_SANE_YEAR_HI = 2030

# Persist the last-good UTC here so a power-cut reboot can floor the clock
# forward instead of dropping to the epoch.  ``configurations`` is a
# persistent bind mount that survives container restarts and reboots.
_DATA_ROOT = Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))
_STATE_FILE = _DATA_ROOT / "configurations" / "timesync_state.json"


def _clock_is_sane() -> bool:
    year = datetime.now(tz=timezone.utc).year
    return _SANE_YEAR_LO <= year <= _SANE_YEAR_HI


def _set_system_clock(dt: datetime) -> bool:
    """Set the Linux system clock via the ``date`` command.

    Logs the step explicitly (old -> new) so pre-sync log timestamps can be
    reconciled after the fact.
    """
    old = datetime.now(tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        subprocess.run(
            ["date", "-u", "-s", date_str],
            check=True,
            capture_output=True,
            timeout=5,
        )
        logger.info(
            "System clock stepped: %s -> %s UTC (delta %+.1fs)",
            old.isoformat(),
            dt.isoformat(),
            (dt - old).total_seconds(),
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logger.warning("Failed to set system clock: %s", exc)
        return False


def _message_age_seconds(data: dict) -> float | None:
    """Age of a mavlink2rest message from its ``status.time.last_update``.

    Returns ``None`` when the timestamp is missing or unparseable.  Both
    ``last_update`` and ``now`` come from the same host clock, so the
    returned age is valid even when the absolute clock is wrong.
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


def _read_state() -> datetime | None:
    """Read the persisted last-good UTC, or ``None`` if absent/invalid."""
    try:
        raw = _STATE_FILE.read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        value = json.loads(raw).get("last_good_utc")
    except (ValueError, AttributeError):
        return None
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if not (_SANE_YEAR_LO <= dt.year <= _SANE_YEAR_HI):
        return None
    return dt


def _write_state(dt: datetime) -> None:
    """Persist the last-good UTC so a reboot can floor the clock forward."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(
                {
                    "last_good_utc": dt.astimezone(timezone.utc).isoformat(),
                    "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            )
        )
    except OSError as exc:
        logger.debug("Could not persist timesync state: %s", exc)


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

    def _record_good_time(self, dt: datetime, source: str) -> None:
        """Mark synced, remember the source, and persist the floor."""
        self._synced = True
        self._source = source
        _write_state(dt)

    def apply_boot_floor(self) -> bool:
        """Bump the clock forward to the last-good UTC if it is behind.

        Only ever moves the clock *forward* — this is a floor, not a sync,
        so it never marks the service as synced and the poll loop keeps
        looking for authoritative GPS time.
        """
        floor = _read_state()
        if floor is None:
            return False
        now = datetime.now(tz=timezone.utc)
        if now >= floor:
            return False
        logger.info(
            "Applying boot time floor: clock %s is behind last-good %s",
            now.isoformat(),
            floor.isoformat(),
        )
        if _set_system_clock(floor):
            self._source = "restored-floor"
            return True
        return False

    async def _artemis_has_fresh_gps_fix(self) -> bool:
        """Check GPS_INPUT from the Artemis for a *fresh* valid fix."""
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
        except Exception:
            return False

        age = _message_age_seconds(data)
        if age is not None and age > MAX_MSG_AGE_S:
            logger.debug("Artemis GPS_INPUT is stale (%.0fs old) — ignoring", age)
            return False

        msg = data.get("message", {})
        fix_type = msg.get("fix_type", 0)
        if isinstance(fix_type, dict):
            fix_type = fix_type.get("bits", 0)
        try:
            return int(fix_type) >= 2
        except (TypeError, ValueError):
            return False

    async def try_sync_from_artemis(self) -> bool:
        """Read SYSTEM_TIME from the Artemis (component 192) and sync if needed.

        Only trusts the time when the Artemis has a fresh GPS fix
        (fix_type >= 2) and the SYSTEM_TIME message itself is fresh.
        Without a fix the Artemis reports garbage time_unix_usec, and a
        stale cached message would otherwise step the clock backwards.
        """
        if not await self._artemis_has_fresh_gps_fix():
            logger.debug("Artemis has no fresh GPS fix — skipping time sync")
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

        age = _message_age_seconds(data)
        if age is not None and age > MAX_MSG_AGE_S:
            logger.debug(
                "Artemis SYSTEM_TIME is stale (%.0fs old) — skipping sync", age
            )
            return False

        msg = data.get("message", {})
        time_unix_usec = msg.get("time_unix_usec", 0)
        if not time_unix_usec or time_unix_usec < 0:
            logger.debug("Artemis SYSTEM_TIME has no valid time_unix_usec")
            return False

        gps_dt = datetime.fromtimestamp(time_unix_usec / 1_000_000, tz=timezone.utc)
        # Compensate for the message's transport age so we set the clock to
        # *now*, not to when the message was received.  ``age`` is measured
        # on the consistent host clock, so this holds even if absolute time
        # is currently wrong.
        if age is not None and 0 <= age <= MAX_MSG_AGE_S:
            gps_dt = gps_dt + timedelta(seconds=age)

        if not (_SANE_YEAR_LO <= gps_dt.year <= _SANE_YEAR_HI):
            logger.debug("Artemis GPS time looks invalid (year %d)", gps_dt.year)
            return False

        now = datetime.now(tz=timezone.utc)
        drift = abs((now - gps_dt).total_seconds())
        self._last_drift = drift

        if drift <= MIN_DRIFT_S:
            self._record_good_time(gps_dt, "artemis-gps")
            return True

        logger.info(
            "System clock drift %.0fs — syncing from Artemis GPS time (%s)",
            drift,
            gps_dt.isoformat(),
        )
        if _set_system_clock(gps_dt):
            self._record_good_time(gps_dt, "artemis-gps")
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
            _write_state(client_dt)
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
            self._record_good_time(client_dt, "client-browser")
        return {
            "synced": ok,
            "drift_seconds": round(drift, 1),
            "source": "client-browser" if ok else None,
            "clock_sane": _clock_is_sane(),
            "new_utc": datetime.now(tz=timezone.utc).isoformat() if ok else None,
        }

    async def _poll_loop(self) -> None:
        """Background loop: poll fast until first sync, then back off."""
        while True:
            synced_now = False
            try:
                synced_now = await self.try_sync_from_artemis()
            except Exception as exc:
                logger.debug("Time sync poll error: %s", exc)
            # Stay in fast-poll until we've locked the clock once, so we
            # don't miss the brief surface GPS window at deployment.
            interval = POLL_INTERVAL_S if self._synced else FAST_POLL_INTERVAL_S
            await asyncio.sleep(interval)
            _ = synced_now

    def start_background_sync(self) -> None:
        """Start the background polling task (call once at app startup)."""
        if self._task is not None:
            return
        # Floor the clock forward from the last-good time before anything
        # else logs, so early-boot timestamps are sane even with no GPS yet.
        try:
            self.apply_boot_floor()
        except Exception as exc:
            logger.debug("Boot time floor skipped: %s", exc)
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._poll_loop())
        logger.info(
            "Time sync background task started (Artemis component %d, "
            "fast %ds until first sync then %ds)",
            ARTEMIS_COMPONENT_ID,
            FAST_POLL_INTERVAL_S,
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
