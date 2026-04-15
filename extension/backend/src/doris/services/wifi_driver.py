"""DORIS WiFi driver setup.

On startup, installs the morrownr/88x2bu out-of-tree driver for the
RTL88x2BU USB WiFi adapter.

Steps:
  1. Blacklist conflicting in-kernel drivers (rtw88, rtl8xxxu, 8192cu)
  2. Unload in-kernel drivers if loaded
  3. Install and load 88x2bu.ko

The driver .ko is fingerprinted by SHA-256 hash.  If a loaded driver
does not match the bundled version, it is replaced and reloaded so that
field devices pick up fixes (e.g. WPA2 AP-mode beacon fix).

Note on sudo: the Commander API's shell PATH does not include /sbin or
/usr/sbin, so modprobe, rmmod, depmod etc. are only reachable through
sudo (which resets the PATH).
"""

import hashlib
import logging
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

DRIVER_MODULE = "88x2bu"
DRIVER_SRC = Path(f"/app/driver/{DRIVER_MODULE}.ko")
BLACKLIST_CONF = "blacklist-rtl88x2bu.conf"

# SHA-256 of the bundled 88x2bu.ko.  Built from morrownr/88x2bu-20210702
# (commit fecac34, 2026-01-08) against kernel 6.6.31+rpt-rpi-2712.
# Fixes: WPA2 RSN IE in AP-mode beacons (clients no longer see WEP).
DRIVER_SHA256 = "ce336377e9834b765c0f3255cf51d189b8ee80273ff75996dcd3b47db82f8b81"
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
                logger.warning("[WIFI] host command returned %d: cmd=%s out=%s err=%s", rc, command[:80], out[:200], err[:200])
                return False, err or out
            return True, out
    except Exception as e:
        logger.warning("[WIFI] Commander command failed (%s): %s", command[:80], e)
        return False, str(e)


async def _is_driver_loaded() -> bool:
    """Check if the out-of-tree 88x2bu driver is already loaded."""
    ok, _ = await _run_host_command(f"lsmod | grep -q '^{DRIVER_MODULE} '")
    return ok


async def _installed_driver_hash() -> str | None:
    """Return the SHA-256 of the installed .ko on the host, or None."""
    ok, kver = await _run_host_command("uname -r")
    if not ok:
        return None
    dest = f"/lib/modules/{kver}/kernel/drivers/net/wireless/{DRIVER_MODULE}.ko"
    ok, out = await _run_host_command(f"sha256sum {dest} 2>/dev/null | awk '{{print $1}}'")
    if ok and out:
        return out.strip()
    return None


def _bundled_driver_hash() -> str | None:
    """Return the SHA-256 of the .ko bundled inside the container."""
    if not DRIVER_SRC.is_file():
        return None
    h = hashlib.sha256(DRIVER_SRC.read_bytes()).hexdigest()
    return h


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

    await _run_host_command(
        f"echo 'options {DRIVER_MODULE} rtw_drv_log_level=0 rtw_led_ctrl=0"
        f" rtw_vht_enable=1 rtw_power_mgnt=1 rtw_switch_usb_mode=1'"
        f" | sudo tee /etc/modprobe.d/{DRIVER_MODULE}.conf > /dev/null"
    )

    ok, _ = await _run_host_command(f"sudo modprobe {DRIVER_MODULE}")
    if not ok:
        logger.error("Failed to load %s module", DRIVER_MODULE)
        return False

    logger.info("%s driver installed and loaded", DRIVER_MODULE)
    return True


async def _upgrade_driver() -> bool:
    """Replace the installed driver with the bundled version and reload."""
    logger.info("Upgrading %s driver to bundled version (hash %s…)", DRIVER_MODULE, DRIVER_SHA256[:12])

    ok, _ = await _run_host_command(
        f"sudo pkill -f 'create_ap.*wlan1' 2>/dev/null; sleep 1; sudo rmmod {DRIVER_MODULE} 2>/dev/null; true"
    )

    if not await _install_driver():
        return False

    logger.info("%s driver upgraded and reloaded", DRIVER_MODULE)
    return True


async def setup_wifi_driver() -> None:
    """Install or upgrade the 88x2bu driver as needed.

    Called once during DORIS backend startup.

    - If the driver is not loaded at all, do a fresh install (blacklist
      conflicting modules, copy .ko, modprobe).
    - If the driver IS loaded but its SHA-256 doesn't match the bundled
      .ko, replace and reload it so field devices pick up fixes.
    - If the driver is loaded AND matches, skip everything.
    """
    if not DRIVER_SRC.is_file():
        logger.info("[WIFI] No %s.ko found at %s, skipping driver setup", DRIVER_MODULE, DRIVER_SRC)
        return

    if await _is_driver_loaded():
        installed = await _installed_driver_hash()
        bundled = _bundled_driver_hash()
        if installed and bundled and installed == bundled:
            logger.info(
                "[WIFI] %s driver loaded and up-to-date (hash %s…)", DRIVER_MODULE, installed[:12],
            )
            return
        logger.info(
            "[WIFI] %s driver loaded but outdated (installed=%s… bundled=%s…), upgrading",
            DRIVER_MODULE,
            (installed or "?")[:12],
            (bundled or "?")[:12],
        )
        await _upgrade_driver()
        return

    logger.info("[WIFI] Installing %s driver (first boot or driver missing)", DRIVER_MODULE)
    await _blacklist_conflicting_drivers()
    await _unload_conflicting_drivers()
    ok = await _install_driver()
    if ok:
        logger.info("[WIFI] driver setup completed successfully")
    else:
        logger.error("[WIFI] driver setup FAILED — WiFi adapter may not work")
