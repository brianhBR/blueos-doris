"""DORIS external storage setup.

On startup, checks if /dev/sda exists on the host. If it does:
  1. Mounts /dev/sda to /mnt
  2. Moves /usr/blueos/userdata/recorder to /mnt/recorder
  3. Creates a symlink from the old path to the new location

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

DEVICE = "/dev/sda"
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


async def _do_setup() -> None:
    """Mount /dev/sda and relocate the recorder directory to it.

    Idempotent — skips steps that are already done.
    Updates ``_status`` at each stage so the frontend can report progress.
    """
    global _status

    _status = MigrationStatus(MigrationState.CHECKING, "Checking for external storage device")

    ok, _ = await _run_host_command(f"test -b {DEVICE}")
    if not ok:
        logger.info("%s not found, skipping external storage setup", DEVICE)
        _status = MigrationStatus(MigrationState.SKIPPED, f"{DEVICE} not found")
        return

    _status = MigrationStatus(MigrationState.MOUNTING, f"Mounting {DEVICE}")

    ok, _ = await _run_host_command(f"mountpoint -q {MOUNT_POINT}")
    if not ok:
        logger.info("Mounting %s to %s", DEVICE, MOUNT_POINT)
        ok, err = await _run_host_command(f"sudo mount {DEVICE} {MOUNT_POINT}")
        if not ok:
            logger.error("Failed to mount %s: %s", DEVICE, err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Mount failed: {err}")
            return
    else:
        logger.info("%s already mounted", MOUNT_POINT)

    ok, _ = await _run_host_command(f"test -L {RECORDER_SRC}")
    if ok:
        logger.info("%s is already a symlink, nothing to do", RECORDER_SRC)
        _status = MigrationStatus(MigrationState.DONE, "Already migrated")
        return

    ok, _ = await _run_host_command(f"test -d {RECORDER_SRC}")
    if ok:
        _status = MigrationStatus(
            MigrationState.MIGRATING,
            "Moving recorder data to external drive — this may take several minutes",
        )
        logger.info("Moving %s to %s", RECORDER_SRC, RECORDER_DST)
        ok, err = await _run_host_command(
            f"sudo mv {RECORDER_SRC} {RECORDER_DST}",
            timeout=600.0,
        )
        if not ok:
            logger.error("Failed to move recorder: %s", err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Move failed: {err}")
            return
    else:
        ok, _ = await _run_host_command(f"sudo mkdir -p {RECORDER_DST}")
        if not ok:
            logger.error("Failed to create %s", RECORDER_DST)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Failed to create {RECORDER_DST}")
            return

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
