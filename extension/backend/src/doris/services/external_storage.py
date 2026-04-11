"""DORIS external storage setup.

On startup, checks if /dev/sda exists on the host. If it does:
  1. Finds the first partition and its UUID via lsblk/blkid
  2. Mounts the partition to /mnt (if not already mounted)
  3. Adds persistent fstab entries for the device mount and a bind
     mount that overlays /usr/blueos/userdata/recorder
  4. Activates the bind mount so the current session works immediately

On subsequent boots the fstab entries handle everything before Docker
starts, so the container sees the external drive content transparently.

If the recorder directory is a broken symlink (left over from the
previous symlink-based approach), it is repaired directly inside the
container via the bind-mounted userdata directory.

All host commands run via the Commander API.
Frontend polls ``get_migration_status()`` to show progress.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

DISK = "/dev/sda"
MOUNT_POINT = "/mnt"
FSTAB = "/etc/fstab"

RECORDER_HOST_DIR = "/usr/blueos/userdata/recorder"
RECORDER_USB_DIR = f"{MOUNT_POINT}/recorder"
RECORDER_CONTAINER_PATH = Path("/tmp/storage/userdata/recorder")


class MigrationState(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    MOUNTING = "mounting"
    CONFIGURING = "configuring"
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

    ok, out = await _run_host_command(
        f"lsblk -lnpo FSTYPE {DISK} 2>/dev/null | head -1"
    )
    if ok and out.strip():
        return DISK

    return None


async def _get_partition_info(partition: str) -> tuple[str | None, str | None]:
    """Return (UUID, fstype) for a partition via blkid."""
    ok, out = await _run_host_command(
        f"sudo blkid -s UUID -o value {partition}"
    )
    uuid = out.strip() if ok and out.strip() else None

    ok, out = await _run_host_command(
        f"sudo blkid -s TYPE -o value {partition}"
    )
    fstype = out.strip() if ok and out.strip() else None

    return uuid, fstype


def _fix_broken_recorder_symlink() -> bool:
    """If the recorder path inside the container is a broken symlink, fix it.

    Returns True if a fix was applied.
    """
    p = RECORDER_CONTAINER_PATH
    if p.is_symlink() and not p.exists():
        logger.warning("Broken symlink at %s, replacing with directory", p)
        p.unlink()
        p.mkdir(parents=True, exist_ok=True)
        return True
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    return False


async def _add_fstab_entries(uuid: str, fstype: str) -> tuple[bool, str]:
    """Add device mount and bind mount entries to /etc/fstab (idempotent).

    Backs up fstab before writing, then verifies with ``mount -a --fake``.
    On verification failure the backup is restored.
    """
    device_entry = f"UUID={uuid} {MOUNT_POINT} {fstype} defaults,nofail 0 2"
    bind_entry = (
        f"{MOUNT_POINT}/recorder {RECORDER_HOST_DIR} none "
        f"bind,nofail,x-systemd.requires-mounts-for={MOUNT_POINT} 0 0"
    )

    ok, current_fstab = await _run_host_command(f"cat {FSTAB}")
    if not ok:
        return False, f"Failed to read {FSTAB}"

    lines_to_add: list[str] = []
    if f"UUID={uuid}" not in current_fstab:
        lines_to_add.append(device_entry)
    if f"{MOUNT_POINT}/recorder" not in current_fstab:
        lines_to_add.append(bind_entry)

    if not lines_to_add:
        logger.info("fstab entries already present")
        return True, "already configured"

    ok, err = await _run_host_command(f"sudo cp {FSTAB} {FSTAB}.doris.bak")
    if not ok:
        return False, f"Failed to backup fstab: {err}"

    append_text = "\\n".join(lines_to_add)
    ok, err = await _run_host_command(
        f"printf '\\n{append_text}\\n' | sudo tee -a {FSTAB} > /dev/null"
    )
    if not ok:
        await _run_host_command(f"sudo cp {FSTAB}.doris.bak {FSTAB}")
        return False, f"Failed to write fstab: {err}"

    ok, err = await _run_host_command("sudo mount -a --fake --verbose")
    if not ok:
        logger.error("fstab verification failed, restoring backup: %s", err)
        await _run_host_command(f"sudo cp {FSTAB}.doris.bak {FSTAB}")
        return False, f"fstab verification failed: {err}"

    logger.info("fstab entries added and verified")
    return True, "configured"


async def _do_setup() -> None:
    """Mount external drive and configure fstab for persistent bind mount.

    Idempotent -- skips steps that are already done.
    Updates ``_status`` at each stage so the frontend can report progress.
    """
    global _status

    _status = MigrationStatus(MigrationState.CHECKING, "Checking for external storage device")

    _fix_broken_recorder_symlink()

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

    uuid, fstype = await _get_partition_info(partition)
    if not uuid or not fstype:
        logger.error("Could not determine UUID/fstype for %s", partition)
        _status = MigrationStatus(MigrationState.ERROR, error=f"Cannot identify {partition}")
        return

    logger.info("Partition %s: UUID=%s fstype=%s", partition, uuid, fstype)

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

    # Tree on the USB filesystem for BlueOS recorder userdata
    ok, err = await _run_host_command(f"sudo mkdir -p {RECORDER_USB_DIR}")
    if not ok:
        _status = MigrationStatus(
            MigrationState.ERROR,
            error=f"Failed to create {RECORDER_USB_DIR}: {err}",
        )
        return

    # Already bind-mounted from our USB-side tree?
    ok, src = await _run_host_command(
        f"findmnt -n -o SOURCE --target {RECORDER_HOST_DIR} 2>/dev/null | head -1"
    )
    bound = (src or "").strip()
    if ok and bound == RECORDER_USB_DIR:
        logger.info("%s already bind-mounted from %s", RECORDER_HOST_DIR, RECORDER_USB_DIR)
        _status = MigrationStatus(MigrationState.CONFIGURING, "Ensuring fstab entries")
        ok, err = await _add_fstab_entries(uuid, fstype)
        if not ok:
            _status = MigrationStatus(MigrationState.ERROR, error=err)
            return
        _status = MigrationStatus(MigrationState.DONE, "External storage already active")
        return

    ok, _ = await _run_host_command(f"sudo mkdir -p {RECORDER_HOST_DIR}")

    # One-time copy: internal recorder dir has data and USB tree is empty
    ok_nonempty, _ = await _run_host_command(
        f"test -d {RECORDER_HOST_DIR} && test -n \"$(ls -A {RECORDER_HOST_DIR} 2>/dev/null)\""
    )
    ok_usb_empty, _ = await _run_host_command(
        f"test -z \"$(ls -A {RECORDER_USB_DIR} 2>/dev/null)\""
    )
    is_mountpoint, _ = await _run_host_command(f"mountpoint -q {RECORDER_HOST_DIR}")

    if ok_nonempty and ok_usb_empty and not is_mountpoint:
        _status = MigrationStatus(
            MigrationState.CONFIGURING,
            "Copying recorder data to external drive — this may take several minutes",
        )
        logger.info("Syncing %s → %s", RECORDER_HOST_DIR, RECORDER_USB_DIR)
        ok, err = await _run_host_command(
            "sudo rsync -a --no-perms --no-owner --no-group --no-links "
            f"{RECORDER_HOST_DIR}/ {RECORDER_USB_DIR}/",
            timeout=1800.0,
        )
        if not ok:
            logger.error("rsync failed: %s", err)
            _status = MigrationStatus(MigrationState.ERROR, error=f"Copy failed: {err}")
            return

    _status = MigrationStatus(MigrationState.CONFIGURING, "Configuring persistent mount")

    ok, err = await _add_fstab_entries(uuid, fstype)
    if not ok:
        _status = MigrationStatus(MigrationState.ERROR, error=err)
        return

    ok, _ = await _run_host_command(f"mountpoint -q {RECORDER_HOST_DIR}")
    if not ok:
        logger.info("Activating bind mount: %s -> %s", RECORDER_USB_DIR, RECORDER_HOST_DIR)
        ok, err = await _run_host_command(
            f"sudo mount --bind {RECORDER_USB_DIR} {RECORDER_HOST_DIR}"
        )
        if not ok:
            logger.warning(
                "Bind mount activation failed: %s (will take effect on reboot)",
                err,
            )
    else:
        logger.info("Bind mount already active at %s", RECORDER_HOST_DIR)

    logger.info("External storage configured: %s -> %s", RECORDER_HOST_DIR, MOUNT_POINT)
    _status = MigrationStatus(MigrationState.DONE, "External storage configured")


async def _run_setup_safe() -> None:
    """Wrapper that catches unexpected exceptions so the background task never crashes."""
    global _status
    try:
        await _do_setup()
    except Exception as e:
        logger.exception("Unexpected error in external storage setup")
        _status = MigrationStatus(MigrationState.ERROR, error=str(e))


def start_external_storage_setup() -> None:
    """Launch the storage setup as a fire-and-forget background task."""
    global _task
    _task = asyncio.ensure_future(_run_setup_safe())
