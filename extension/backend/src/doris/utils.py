"""DORIS Backend - Utility functions for script deployment."""

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

import httpx

from .config import blueos_services

FIRMWARE_DIR = Path("/tmp/storage/firmware")
SCRIPTS_DIR = FIRMWARE_DIR / "scripts"
ARTEMIS_SVL_DEST = Path("/usr/bin/artemis_svl.py")

SCRIPT_SEARCH_PATHS = [
    Path("/app/scripts"),
    Path(__file__).resolve().parents[3] / "scripts",
]


def _atomic_copy(src: Path, dest: Path) -> None:
    """Replace ``dest`` with ``src`` in a single step.

    ArduPilot reads this file on its own schedule, so it must never observe a
    partial one. Copying straight onto ``dest`` truncates it and then streams
    ~75 KB back in; a script load landing inside that window compiles whatever
    prefix happens to be on disk and reports a syntax error at an arbitrary
    line, or a bogus complaint about the local variable limit. Writing beside
    the destination and renaming means a reader sees either the whole old file
    or the whole new one.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".doris.lua.")
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb") as fh:
            fh.write(src.read_bytes())
            fh.flush()
            # The rename below only orders the directory entry. Without this the
            # contents could still be in flight when power is cut, leaving a
            # correctly named but empty script.
            os.fsync(fh.fileno())
        shutil.copystat(src, tmp)
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; ArduPilot runs as another user
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def deploy_lua_scripts(logger: logging.Logger) -> bool:
    """Copy doris.lua into the ArduPilot scripts folder if the firmware bind-mount exists.

    Returns True if the script was deployed (changed on disk), False otherwise.
    """
    if not FIRMWARE_DIR.is_dir():
        logger.info("Firmware directory %s not found, skipping Lua script deployment", FIRMWARE_DIR)
        return False

    src: Path | None = None
    for candidate in SCRIPT_SEARCH_PATHS:
        path = candidate / "doris.lua"
        if path.is_file():
            src = path
            break

    if src is None:
        logger.warning("doris.lua not found in any search path: %s", SCRIPT_SEARCH_PATHS)
        return False

    try:
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = SCRIPTS_DIR / "doris.lua"
        src_hash = hashlib.sha256(src.read_bytes()).hexdigest()
        if dest.is_file():
            dest_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
            if src_hash == dest_hash:
                logger.info("doris.lua already up to date (sha256=%s…)", src_hash[:12])
                return False
        _atomic_copy(src, dest)
        logger.info("Deployed %s -> %s", src, dest)
        return True
    except Exception as e:
        logger.warning("Failed to deploy doris.lua: %s", e)
        return False


async def restart_firmware(logger: logging.Logger) -> None:
    """Restart the autopilot firmware so it picks up new Lua scripts."""
    url = f"{blueos_services.autopilot_manager}/v1.0/restart"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url)
            resp.raise_for_status()
        logger.info("Firmware restart command sent successfully")
    except Exception as e:
        logger.warning("Failed to restart firmware: %s", e)


async def stop_autopilot(logger: logging.Logger) -> bool:
    """Stop the ArduPilot process via BlueOS autopilot-manager.

    This also tears down the MAVLink router, which is what actually holds
    the serial port open. Needed before flashing peripherals (e.g. an
    Artemis sensor module) that share a UART with the autopilot.

    Returns True on success.
    """
    url = f"{blueos_services.autopilot_manager}/v1.0/stop"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url)
            resp.raise_for_status()
        logger.info("Autopilot stop command sent successfully")
        return True
    except Exception as e:
        logger.warning("Failed to stop autopilot: %s", e)
        return False


async def start_autopilot(logger: logging.Logger) -> bool:
    """Start the ArduPilot process via BlueOS autopilot-manager.

    Counterpart to :func:`stop_autopilot`. Returns True on success.
    """
    url = f"{blueos_services.autopilot_manager}/v1.0/start"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url)
            resp.raise_for_status()
        logger.info("Autopilot start command sent successfully")
        return True
    except Exception as e:
        logger.warning("Failed to start autopilot: %s", e)
        return False


def disable_usb_autosuspend(logger: logging.Logger) -> None:
    """Set power/control to 'on' for all USB devices to prevent autosuspend."""
    usb_devices = Path("/sys/bus/usb/devices")
    if not usb_devices.is_dir():
        logger.info("USB sysfs not available, skipping autosuspend disable")
        return

    count = 0
    for device in usb_devices.iterdir():
        control = device / "power" / "control"
        if not control.is_file():
            continue
        try:
            current = control.read_text().strip()
            if current != "on":
                control.write_text("on")
                count += 1
        except OSError:
            pass

    logger.info("Disabled USB autosuspend on %d device(s)", count)


def deploy_artemis_svl(logger: logging.Logger) -> None:
    """Copy artemis_svl.py to /usr/bin if permissions allow."""
    src: Path | None = None
    for candidate in SCRIPT_SEARCH_PATHS:
        path = candidate / "artemis_svl.py"
        if path.is_file():
            src = path
            break

    if src is None:
        logger.warning("artemis_svl.py not found in any search path: %s", SCRIPT_SEARCH_PATHS)
        return

    try:
        shutil.copy2(src, ARTEMIS_SVL_DEST)
        ARTEMIS_SVL_DEST.chmod(0o755)
        logger.info("Deployed %s -> %s", src, ARTEMIS_SVL_DEST)
    except PermissionError:
        logger.warning("Insufficient permissions to copy artemis_svl.py to %s", ARTEMIS_SVL_DEST)
    except Exception as e:
        logger.warning("Failed to deploy artemis_svl.py: %s", e)
