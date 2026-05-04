"""Pin the BlueOS hotspot to the USB Realtek RTL88x2BU radio.

Why this exists
---------------
On a Pi 5 with the onboard Broadcom radio + a USB Realtek RTL88x2BU dongle,
BlueOS's wifi-manager creates the hotspot as a virtual ``__ap`` interface
called ``uap0`` *layered on top of* the onboard Broadcom radio (Broadcom's
``brcmfmac`` driver supports concurrent STA + AP). The result:

  * The Broadcom chip is forced to be both client and AP at the same time;
    Cockpit/SSH over Wi-Fi suffer from heavy retransmits.
  * The Realtek dongle (with its external antenna) sits idle.

We can't simply re-parent ``uap0`` onto the Realtek: the out-of-tree
``rtl88x2bu`` driver doesn't support virtual interfaces -
``iw dev wlan1 interface add testap type __ap`` returns ``-ENODEV``.

The trick we exploit
--------------------
BlueOS's ``_create_virtual_interface()`` short-circuits if ``uap0`` already
exists at startup. So we make ``uap0`` exist *before* BlueOS sees it by
renaming the Realtek's net device from ``wlan1`` to ``uap0`` straight from
the kernel via a udev rule:

    SUBSYSTEM=="net", ACTION=="add", ATTRS{idVendor}=="0bda",
        ATTRS{idProduct}=="b812", NAME="uap0"

We also need to keep NetworkManager's hands off ``uap0`` (otherwise it
treats the AP-mode interface as a regular wifi device with no profile and
brings it down, fighting hostapd):

    [keyfile]
    unmanaged-devices=interface-name:uap0

Boot order then naturally works:

  1. Kernel + USB enumeration -> rtl88x2bu loads -> udev rule renames the
     net device to uap0.
  2. NetworkManager starts, reads conf.d, marks uap0 unmanaged.
  3. blueos-core starts -> wifi-manager sees uap0 already exists -> runs
     ``create_ap`` directly on it (auto-falls back to ``--no-virt`` because
     rtl88x2bu can't do simultaneous STA+AP).

Result: the hotspot is broadcast from the Realtek's external antenna; the
onboard Broadcom is free to do STA-only on wlan0. In our testing wlan0
retransmits dropped from ~14,900/min to ~30/min.

Note on ``sudo``: the BlueOS Commander API's shell PATH does not include
``/sbin`` or ``/usr/sbin``, so ``udevadm`` and friends are only reachable
through ``sudo`` (which resets the PATH).
"""

import logging

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)


# Realtek RTL88x2BU (AC1200) USB IDs - matches the dongle we ship.
USB_VENDOR_ID = "0bda"
USB_PRODUCT_ID = "b812"

UDEV_RULE_PATH = "/etc/udev/rules.d/72-blueos-hotspot.rules"
NM_CONF_PATH = "/etc/NetworkManager/conf.d/99-blueos-hotspot-uap0.conf"

UDEV_RULE_CONTENT = (
    "# Managed by DORIS extension (services/hotspot_radio.py).\n"
    "# Rename the Realtek RTL88x2BU USB Wi-Fi adapter's net device to uap0\n"
    "# so BlueOS uses it as the hotspot AP without trying to create a virtual\n"
    "# __ap interface (rtl88x2bu does not support virtual interfaces).\n"
    f'SUBSYSTEM=="net", ACTION=="add", ATTRS{{idVendor}}=="{USB_VENDOR_ID}",'
    f' ATTRS{{idProduct}}=="{USB_PRODUCT_ID}", NAME="uap0"\n'
)

NM_CONF_CONTENT = (
    "# Managed by DORIS extension (services/hotspot_radio.py).\n"
    "# Keep NetworkManager from grabbing uap0; hostapd / create_ap drives it.\n"
    "[keyfile]\n"
    "unmanaged-devices=interface-name:uap0\n"
)


async def _run_host_command(command: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Execute a command on the host via the BlueOS Commander API.

    Returns ``(success, stdout_or_stderr)``.
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


async def _read_host_file(path: str) -> str | None:
    """Return the contents of a host file, or None if it doesn't exist."""
    ok, out = await _run_host_command(f"cat {path} 2>/dev/null")
    return out if ok else None


async def _write_host_file(path: str, content: str) -> bool:
    """Write *content* to *path* on the host atomically.

    Skips the write if the existing file already has the same content,
    avoiding unnecessary inotify churn.
    """
    existing = await _read_host_file(path)
    if existing is not None and existing.strip() == content.strip():
        logger.info("Host file %s already up to date, skipping", path)
        return True

    # Use a heredoc with a unique sentinel to avoid quote-escaping headaches.
    sentinel = "DORIS_HOSTFILE_EOF"
    cmd = (
        f"sudo tee {path} > /dev/null <<'{sentinel}'\n"
        f"{content}"
        f"{sentinel}\n"
    )
    ok, _ = await _run_host_command(cmd)
    if ok:
        logger.info("Wrote %s on host (%d bytes)", path, len(content))
    return ok


async def _reload_udev() -> None:
    """Tell udev to reload its rules (so the new file takes effect on next add)."""
    await _run_host_command("sudo udevadm control --reload-rules")


async def _reload_network_manager() -> None:
    """Ask NetworkManager to re-read its configuration directory."""
    # ``nmcli general reload`` is the cleanest way; fall back to SIGHUP if the
    # binary is missing on a particular host.
    ok, _ = await _run_host_command("sudo nmcli general reload 2>/dev/null")
    if not ok:
        await _run_host_command(
            "sudo pkill -HUP NetworkManager 2>/dev/null; true"
        )


async def setup_hotspot_radio() -> None:
    """Install the host-side config that pins ``uap0`` to the USB Realtek.

    Idempotent: writes are skipped if existing files already match. The udev
    rule's ``NAME=`` directive is only honoured by the kernel on the initial
    ``add`` netlink event for an interface, so renaming an *already-present*
    ``wlan1`` requires either a USB unplug/replug or a reboot. If the
    Realtek dongle is currently enumerated as ``wlan1`` we log a notice
    asking for one reboot to apply the rename; the running hotspot keeps
    working in the meantime.
    """
    rule_ok = await _write_host_file(UDEV_RULE_PATH, UDEV_RULE_CONTENT)
    nm_ok = await _write_host_file(NM_CONF_PATH, NM_CONF_CONTENT)

    if not (rule_ok and nm_ok):
        logger.warning(
            "Hotspot-radio host config not fully written (udev=%s, NM=%s); "
            "AP may stay parented on the onboard radio",
            rule_ok,
            nm_ok,
        )
        return

    await _reload_udev()
    await _reload_network_manager()

    # Heads-up if the rename hasn't taken effect on this boot.
    ok, out = await _run_host_command(
        "ls /sys/class/net/wlan1 /sys/class/net/uap0 2>/dev/null; true"
    )
    has_wlan1 = ok and "/sys/class/net/wlan1" in out
    has_uap0 = ok and "/sys/class/net/uap0" in out
    if has_wlan1 and not has_uap0:
        logger.info(
            "Hotspot radio config installed; reboot once to rename wlan1 -> uap0",
        )
    elif has_uap0:
        logger.info("Hotspot radio config installed; uap0 already in place")
