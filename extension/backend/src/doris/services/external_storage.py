"""DORIS external storage setup.

On startup, checks if /dev/sda exists on the host. If it does:
  1. Finds the first partition (e.g. /dev/sda1) via lsblk
  2. Mounts the partition to /mnt
  3. Copies recorder data to /mnt/recorder using rsync
  4. Creates a symlink from the old path to the new location

This offloads recording data to an external USB drive.
All operations run on the host via the Commander API.

The migration runs as a background task so it doesn't block startup.
Frontend polls ``get_migration_status()`` to show progress.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

DISK = "/dev/sda"
MOUNT_POINT = "/mnt"
RECORDER_SRC = "/usr/blueos/userdata/recorder"
RECORDER_DST = "/mnt/recorder"


class MigrationState(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    MOUNTING = "mounting"
    MIGRATING = "migrating"
    LINKING = "linking"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class MigrationStatus:
    state: MigrationState = MigrationState.IDLE
    message: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {"state": self.state.value, "message": self.message, "error": self.error}


_status = MigrationStatus()
_task: asyncio.Task | None = None


def get_migration_status() -> dict:
    """Return the current migration status as a plain dict (JSON-safe)."""
    return _status.to_dict()


async def _run_host_command(command: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Execute a command on the host via the Commander API."""
    url = f"{blueos_services.commander}/v1.0/command/host"
    params = {"command": command, "i_know_what_i_am_doing": "true"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rc = data.get("return_code", -1)
            out = data.get("stdout", "").strip("'\"").replace("\\n", "\n").strip()
            err = data.get("stderr", "").strip("'\"").replace("\\n", "\n").strip()
            if rc != 0:
                logger.warning("Command returned %d: %s %s", rc, out, err)
                return False, err or out
            return True, out
    except Exception as e:
        logger.warning("Commander command failed (%s): %s", command[:60], e)
        return False, str(e)


async def _find_partition() -> str | None:
    """Find the first partition on DISK (e.g. /dev/sda1).

    Falls back to DISK itself if it has a filesystem directly.
    """
    ok, out = await _run_host_command(
        f"lsblk -lnpo NAME,TYPE {DISK} 2>/dev/null | awk '$2==\"part\"{{print $1; exit}}'"
    )
    if ok and out:
        return out.strip()

    # No partition table — check if the disk itself has a filesystem
    ok, out = await _run_host_command(
        f"lsblk -lnpo FSTYPE {DISK} 2>/dev/null | head -1"
    )
    if ok and out.strip():
        return DISK

    return None


async def _do_setup() -> None:
    """Mount external drive and relocate the recorder directory to it.

    Idempotent — skips steps that are already done.
    Updates ``_status`` at each stage so the frontend can report progress.
    """
    global _status

    _status = MigrationStatus(MigrationState.CHECKING, "Checking for external storage device")

    # ── Fix dangling symlink ─────────────────────────────────────────
    # If the drive was migrated previously but then removed, the
    # symlink still exists yet points to nothing. Restore the
    # directory so the recorder can keep writing to the SD card.
    ok, _ = await _run_host_command(
        f"test -L {RECORDER_SRC} && ! test -e {RECORDER_SRC}"
    )
    if ok:
        logger.warning("Dangling symlink at %s, restoring directory", RECORDER_SRC)
        await _run_host_command(f"sudo rm -f {RECORDER_SRC}")
        await _run_host_command(f"sudo mkdir -p {RECORDER_SRC}")

    ok, _ = await _run_host_command(f"test -b {DISK}")
    if not ok:
        logger.info("%s not found, skipping external storage setup", DISK)
        _status = MigrationStatus(MigrationState.SKIPPED, f"{DISK} not found")
        return

    partition = await _find_partition()
    if not partition:
        logger.info("No usable partition found on %s", DISK)
        _status = MigrationStatus(MigrationState.SKIPPED, f"No partition found on {DISK}")
        return

    logger.info("Using partition %s", partition)

    # ── Mount ────────────────────────────────────────────────────────
    _status = MigrationStatus(MigrationState.MOUNTING, f"Mounting {partition}")

    ok, _ = await _run_host_command(f"mountpoint -q {MOUNT_POINT}")
    if not ok:
        logger.info("Mounting %s to %s", partition, MOUNT_POINT)
        ok, err = await _run_host_command(f"sudo mount {partition} {MOUNT_POINT}")
        if not ok:
            logger.error("Failed to mount %s: %s", partition, err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Mount failed: {err}")
            return
    else:
        logger.info("%s already mounted", MOUNT_POINT)

    # ── Already done? ────────────────────────────────────────────────
    ok, _ = await _run_host_command(f"test -L {RECORDER_SRC}")
    if ok:
        logger.info("%s is already a symlink, nothing to do", RECORDER_SRC)
        _status = MigrationStatus(MigrationState.DONE, "Already migrated")
        return

    # ── Copy data ────────────────────────────────────────────────────
    ok, _ = await _run_host_command(f"test -d {RECORDER_SRC}")
    if ok:
        _status = MigrationStatus(
            MigrationState.MIGRATING,
            "Copying recorder data to external drive — this may take several minutes",
        )
        # rsync is preferred over mv:
        #  - works across filesystems (ext4 → ntfs)
        #  - doesn't fail on permission/ownership differences
        #  - handles a pre-existing destination directory correctly
        logger.info("Syncing %s → %s", RECORDER_SRC, RECORDER_DST)
        ok, err = await _run_host_command(
            f"sudo rsync -a --no-perms --no-owner --no-group --no-links "
            f"{RECORDER_SRC}/ {RECORDER_DST}/",
            timeout=1800.0,
        )
        if not ok:
            logger.error("rsync failed: %s", err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Copy failed: {err}")
            return

        # Remove original directory so we can replace it with a symlink
        ok, err = await _run_host_command(
            f"sudo rm -rf {RECORDER_SRC}",
            timeout=120.0,
        )
        if not ok:
            logger.error("Failed to remove source: %s", err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Remove source failed: {err}")
            return
    else:
        ok, _ = await _run_host_command(f"sudo mkdir -p {RECORDER_DST}")
        if not ok:
            logger.error("Failed to create %s", RECORDER_DST)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Failed to create {RECORDER_DST}")
            return

    # ── Symlink ──────────────────────────────────────────────────────
    _status = MigrationStatus(MigrationState.LINKING, "Creating symlink")

    ok, err = await _run_host_command(f"sudo ln -sf {RECORDER_DST} {RECORDER_SRC}")
    if not ok:
        logger.error("Failed to create symlink: %s", err)
        _status = MigrationStatus(MigrationState.ERROR, error=f"Symlink failed: {err}")
        return

    logger.info("External storage ready: %s → %s", RECORDER_SRC, RECORDER_DST)
    _status = MigrationStatus(MigrationState.DONE, "Recorder data migrated to external drive")


async def _run_setup_safe() -> None:
    """Wrapper that catches unexpected exceptions so the background task never crashes."""
    global _status
    try:
        await _do_setup()
    except Exception as e:
        logger.exception("Unexpected error in external storage setup")
        _status = MigrationStatus(MigrationState.ERROR, error=str(e))


def start_external_storage_setup() -> None:
    """Launch the storage migration as a fire-and-forget background task."""
    global _task
    _task = asyncio.ensure_future(_run_setup_safe())
