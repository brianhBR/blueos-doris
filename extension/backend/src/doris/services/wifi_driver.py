"""DORIS WiFi driver setup.

On startup, installs the morrownr/88x2bu out-of-tree driver for the
RTL88x2BU USB WiFi adapter.

Steps:
  1. Blacklist conflicting in-kernel drivers (rtw88, rtl8xxxu, 8192cu)
  2. Unload in-kernel drivers if loaded
  3. Install and load 88x2bu.ko

Note on sudo: the Commander API's shell PATH does not include /sbin or
/usr/sbin, so modprobe, rmmod, depmod etc. are only reachable through
sudo (which resets the PATH).
"""

import logging
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

DRIVER_MODULE = "88x2bu"
DRIVER_SRC = Path(f"/app/driver/{DRIVER_MODULE}.ko")
BLACKLIST_CONF = "blacklist-rtl88x2bu.conf"
OLD_BLACKLIST_FILES = [
    "blacklist-rtw88.conf",
    "blacklist-8192cu.conf",
    "rtl8xxxu.conf",
]

CONFLICTING_MODULES = [
    "rtw88_8822bu",
    "rtw88_8822b",
    "rtw88_usb",
    "rtw88_core",
    "rtl8xxxu",
    "8192cu",
    "8812bu",
]


async def _run_host_command(command: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Execute a command on the host via the Commander API.

    Returns (success, stdout_or_stderr).
    """
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


async def _is_driver_loaded() -> bool:
    """Check if the out-of-tree 88x2bu driver is already loaded."""
    ok, _ = await _run_host_command(f"lsmod | grep -q '^{DRIVER_MODULE} '")
    return ok


async def _get_doris_container_name() -> str | None:
    """Find the DORIS container name on the host."""
    ok, out = await _run_host_command(
        "docker ps --filter 'name=doris' --format '{{.Names}}' | head -1"
    )
    if ok and out:
        return out
    return None


async def _blacklist_conflicting_drivers() -> None:
    """Write a modprobe blacklist file for all conflicting in-kernel drivers."""
    for old in OLD_BLACKLIST_FILES:
        await _run_host_command(f"sudo rm -f /etc/modprobe.d/{old}")

    lines = "\\n".join(f"blacklist {m}" for m in CONFLICTING_MODULES)
    cmd = f"echo -e '{lines}' | sudo tee /etc/modprobe.d/{BLACKLIST_CONF} > /dev/null"
    await _run_host_command(cmd)
    logger.info("Blacklisted conflicting drivers in %s", BLACKLIST_CONF)


async def _unload_conflicting_drivers() -> None:
    """Unload in-kernel drivers that conflict with 8812bu (failures are fine)."""
    modules = " ".join(CONFLICTING_MODULES)
    await _run_host_command(f"sudo rmmod {modules} 2>/dev/null; true")


async def _install_driver() -> bool:
    """Copy and load the out-of-tree 88x2bu module on the host."""
    ok, kver = await _run_host_command("uname -r")
    if not ok:
        logger.error("Failed to get kernel version")
        return False

    container_name = await _get_doris_container_name()
    if not container_name:
        logger.error("Could not determine DORIS container name")
        return False

    ko = f"{DRIVER_MODULE}.ko"
    dest = f"/lib/modules/{kver}/kernel/drivers/net/wireless/{ko}"
    copy_cmd = (
        f"docker cp {container_name}:/app/driver/{ko} /tmp/{ko}"
        f" && sudo mkdir -p $(dirname {dest})"
        f" && sudo mv /tmp/{ko} {dest}"
        f" && sudo depmod -a"
    )
    ok, _ = await _run_host_command(copy_cmd, timeout=30.0)
    if not ok:
        logger.error("Failed to copy driver to host")
        return False

    ok, _ = await _run_host_command(f"sudo modprobe {DRIVER_MODULE}")
    if not ok:
        logger.error("Failed to load %s module", DRIVER_MODULE)
        return False

    logger.info("%s driver installed and loaded", DRIVER_MODULE)
    return True


async def setup_wifi_driver() -> None:
    """Install the 88x2bu driver if not already loaded.

    Called once during DORIS backend startup. Idempotent.
    """
    if not DRIVER_SRC.is_file():
        logger.info("No %s.ko found at %s, skipping driver setup", DRIVER_MODULE, DRIVER_SRC)
        return

    await _blacklist_conflicting_drivers()

    if await _is_driver_loaded():
        logger.info("%s driver already loaded, nothing to do", DRIVER_MODULE)
        return

    logger.info("Installing %s driver", DRIVER_MODULE)
    await _unload_conflicting_drivers()
    await _install_driver()
