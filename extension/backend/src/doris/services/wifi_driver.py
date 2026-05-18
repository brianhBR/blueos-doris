"""DORIS WiFi driver setup.

On startup, installs the morrownr/88x2bu out-of-tree driver for the
Realtek RTL88x2BU USB WiFi adapter - **but only when that chip is
actually on the USB bus**. On units running a different supported
hotspot radio (MediaTek MT7612U/MT7921U, Atheros AR9271 - all of
which have working in-kernel drivers on the Pi 5 kernel we ship)
this module short-circuits and leaves the host alone: no
blacklist file written, no in-kernel modules unloaded, no modprobe
of the out-of-tree blob. The bundled ``88x2bu.ko`` stays on disk
inside the container so plugging in a Realtek later still works
- we just don't disturb a working in-kernel driver path on units
that don't need it.

The presence of the out-of-tree module on a unit that uses an
in-kernel driver chip would be benign on its own, but the
blacklist file we *also* install would silently break recovery if
that user ever ran a different Realtek chip on the same host
(the blacklist applies to broad ``rtw88_*`` families). Keeping
this whole code path conditional on the actual Realtek USB ID
keeps the host's modprobe state minimal.

Steps when a Realtek RTL88x2BU is detected:
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

# Realtek RTL88x2BU USB ID - matches the row in
# :data:`doris.services.hotspot_radio.SUPPORTED_HOTSPOT_RADIOS`. We
# duplicate the literal here rather than importing across modules to
# keep this bootstrap module free of cross-service dependencies; if
# the IDs ever change we update both call sites at once.
REALTEK_USB_ID = "0bda:b812"

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


async def _realtek_usb_present() -> bool:
    """Return ``True`` iff the Realtek RTL88x2BU is currently on the bus.

    Used to short-circuit ``setup_wifi_driver`` on units running a
    non-Realtek supported hotspot chip (MediaTek, Atheros) so we
    don't blacklist working in-kernel drivers or pollute modprobe
    state with the out-of-tree blob those units don't need.

    Failure to query ``lsusb`` returns ``False`` - if we can't tell,
    don't touch the host. The Realtek install path is intrusive
    (blacklist write, rmmod, depmod, modprobe) so erring on the side
    of "no" is the right default. Worst case: a Realtek user with a
    broken Commander has to retry once we can read the bus.
    """
    ok, out = await _run_host_command("lsusb")
    if not ok or not out:
        return False
    return REALTEK_USB_ID.lower() in out.lower()


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
    """Install the 88x2bu driver if a Realtek RTL88x2BU is on the bus.

    Called once during DORIS backend startup. Idempotent in three ways:

      1. **Hardware-gated.** If no Realtek RTL88x2BU is detected via
         ``lsusb``, the function returns immediately without
         touching the host. Units running an in-kernel-driver
         hotspot chip (MediaTek MT7612U/MT7921U, Atheros AR9271)
         take this path, so their working in-kernel drivers are
         never blacklisted.
      2. **Driver-loaded short-circuit.** If the out-of-tree
         ``88x2bu`` is already loaded, we skip every host write
         (blacklist file, rmmod, depmod, modprobe) so a healthy
         Realtek system never sees a transient USB reset just
         because the extension restarted.
      3. **Missing-bundle short-circuit.** If ``88x2bu.ko`` isn't in
         the container image (we don't currently ship it via every
         build flavour), there's nothing to install - log and
         return.
    """
    if not DRIVER_SRC.is_file():
        logger.info("No %s.ko found at %s, skipping driver setup", DRIVER_MODULE, DRIVER_SRC)
        return

    if not await _realtek_usb_present():
        logger.info(
            "No Realtek RTL88x2BU (%s) on the USB bus; skipping out-of-tree "
            "%s driver install. Other supported hotspot chips "
            "(MediaTek mt76 family, Atheros ath9k_htc) use in-kernel "
            "drivers and need no host-side install.",
            REALTEK_USB_ID,
            DRIVER_MODULE,
        )
        return

    if await _is_driver_loaded():
        logger.info("%s driver already loaded, skipping all driver setup", DRIVER_MODULE)
        return

    logger.info("Installing %s driver (first boot or driver missing)", DRIVER_MODULE)
    await _blacklist_conflicting_drivers()
    await _unload_conflicting_drivers()
    await _install_driver()
