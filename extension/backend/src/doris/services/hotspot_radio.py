"""Pin the BlueOS hotspot to the USB Realtek RTL88x2BU radio at full speed.

Why this exists
---------------
On a Pi 5 with the onboard Broadcom radio + a USB Realtek RTL88x2BU dongle,
out of the box BlueOS:

  1. **Layers the hotspot on the wrong radio.** ``wifi-manager`` creates a
     virtual ``__ap`` interface called ``uap0`` *on top of* the onboard
     Broadcom radio (``brcmfmac`` supports concurrent STA + AP), so the
     Broadcom chip is forced to be both client and AP at the same time and
     the Realtek (with its external antenna) sits idle.

  2. **Brings the AP up at 802.11g / 2.4 GHz / channel 1**, which caps real
     throughput around 15-25 Mbps even on hardware capable of ~400 Mbps.

This module fixes both. We can't simply re-parent ``uap0`` onto the
Realtek - the out-of-tree ``rtl88x2bu`` driver does not support virtual
interfaces (``iw dev wlan1 interface add testap type __ap`` returns
``-ENODEV``). So instead:

Part 1: rename the Realtek to ``uap0`` via udev
-----------------------------------------------
BlueOS's ``_create_virtual_interface()`` short-circuits if ``uap0``
already exists at startup. We make it exist *before* BlueOS sees it by
renaming the Realtek's net device from ``wlan1`` to ``uap0`` straight
from the kernel:

    SUBSYSTEM=="net", ACTION=="add", ATTRS{idVendor}=="0bda",
        ATTRS{idProduct}=="b812", NAME="uap0"

We also tell NetworkManager to leave ``uap0`` alone so hostapd /
``create_ap`` can drive it:

    [keyfile]
    unmanaged-devices=interface-name:uap0

Part 2: tell ``create_ap`` to use 5 GHz / 802.11ac
--------------------------------------------------
``wifi-manager`` invokes ``create_ap`` with no band/channel/HT/VHT flags,
so hostapd defaults to ``hw_mode=g, channel=1`` with no HT and no VHT.
We patch the wifi-handler module inside ``blueos-core`` to add::

    "--freq-band", "5",
    "-c", "36",
    "--ieee80211n",
    "--ieee80211ac",
    "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40]",
    "--vht_capab", "[SHORT-GI-80][RXLDPC]",
    "--country", "US",

right before the SSID/password arguments. The patched module is written
to ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` on the host
and bind-mounted into the container by adding an entry to
``/root/.config/blueos/bootstrap/startup.json``. ``blueos-bootstrap``
re-reads ``startup.json`` on every boot, so the override survives
``blueos-core`` recreations and BlueOS upgrades that don't touch the
target file.

VHT80 (80 MHz width) is *not* enabled - it is unstable in AP mode on
``rtl88x2bu`` (hostapd brings up and immediately disables the AP). HT40
on a non-DFS channel is the sweet spot.

5 GHz investigation notes (May 2026, deferred)
----------------------------------------------
The 5 GHz patch encoded above (``-c 36``) does *not* work on the
``88x2bu`` driver we ship (``morrownr/88x2bu-20210702`` @
``bd7c7eb9d``). Findings, in case we pick this back up:

* **Channel 36 / UNII-1** (this patch): hostapd never reaches
  ``AP-ENABLED``. The driver hard-rejects beacon programming with::

      uap0: INTERFACE-DISABLED
      Failed to set beacon parameters
      uap0: Could not connect to kernel driver
      Interface initialization failed

  This is a software-side ``nl80211`` rejection (no RF energy has been
  transmitted yet), consistent with the well-known FCC indoor-only /
  UNII-1 restriction baked into older Realtek out-of-tree drivers.
  ``--ht_capab`` / ``--vht_capab`` / 20 MHz fallback do *not* change the
  outcome on channel 36.

* **Channels 40, 44, 48 (UNII-1)**: not exhaustively tested but expected
  to share channel 36's fate (same regulatory regime).

* **Channel 149 / UNII-3** with the same HT40+VHT flags: hostapd reaches
  ``AP-ENABLED`` cleanly, the radio broadcasts at channel 149 / 40 MHz /
  13 dBm. Stability across longer runs is *not yet confirmed* - one
  90 s run dropped the AP around the 45 s mark, but a separate leftover
  test stayed up for several minutes. Needs a clean, longer repro before
  shipping. Channels 153, 157, 161, 165 untested.

* The radio itself (``iw phy phy1 info``) advertises 5 GHz AP capability,
  HT40, VHT80, RX LDPC, short-GI on all relevant 5 GHz frequencies, so
  this isn't a hardware limit - it's the driver's AP-mode firmware path.

Paths forward when we resume:

  1. Switch ``-c 36`` to ``-c 149`` and re-validate stability for >5 min.
  2. Or update the bundled ``88x2bu.ko`` to a newer ``morrownr`` commit
     (current build is 2021-07-02) and re-test channel 36.
  3. RF / antenna sanity check: even if the failure mode above is
     software, the AC1200 Techkey antenna may not be well tuned across
     the full 5 GHz range, so picking a channel that *also* matches the
     antenna's actual SWR sweet spot is worth doing.

Until then, ``_install_create_ap_5ghz_patch()`` is left in place because
the host I/O plumbing it exercises (chunked-base64 write, ``sudo cat``,
the ``_decode_commander_field`` decoder) is reused by other DORIS code.
The patch itself is a no-op on the live device: the resulting create_ap
argv simply fails to bring up the AP and wifi-manager falls back to its
default 2.4 GHz / channel 1 / HT20 configuration. Disable the call to
``_install_create_ap_5ghz_patch()`` from ``setup_hotspot_radio()`` if
the failure noise in ``wifi-manager`` logs becomes a problem.

Boot order
----------
  1. Kernel + USB enumeration -> ``rtl88x2bu`` loads -> udev rule renames
     the net device to ``uap0``.
  2. NetworkManager starts, reads ``conf.d``, marks ``uap0`` unmanaged.
  3. ``blueos-bootstrap`` reads ``startup.json``, mounts the patched
     ``networkmanager.py`` over the stock one, then starts ``blueos-core``.
  4. ``wifi-manager`` sees ``uap0`` already exists -> runs ``create_ap``
     on it with the 5 GHz / 802.11ac flags -> hostapd brings the AP up on
     channel 36, HT40, with VHT MCS rates available.

Result: hotspot broadcast from the Realtek's external antenna at 5 GHz
802.11ac; ~200-400 Mbps real-world throughput to 2x2 MIMO clients.
Onboard Broadcom is STA-only on ``wlan0``; in our testing its retransmit
count dropped from ~14,900/min to ~30/min.

Note on ``sudo``: the BlueOS Commander API's shell PATH does not include
``/sbin`` or ``/usr/sbin``, so ``udevadm`` and friends are only reachable
through ``sudo`` (which resets the PATH).
"""

import ast
import base64
import json
import logging

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)


# Realtek RTL88x2BU (AC1200) USB IDs - matches the dongle we ship.
USB_VENDOR_ID = "0bda"
USB_PRODUCT_ID = "b812"

UDEV_RULE_PATH = "/etc/udev/rules.d/72-blueos-hotspot.rules"
NM_CONF_PATH = "/etc/NetworkManager/conf.d/99-blueos-hotspot-uap0.conf"

# Path used as the *source* of the bind mount on the host, plus the
# corresponding path inside the blueos-core container.
WIFI_OVERRIDE_DIR = "/usr/blueos/userdata/wifi-overrides"
WIFI_OVERRIDE_HOST_PATH = f"{WIFI_OVERRIDE_DIR}/networkmanager.py"
WIFI_OVERRIDE_CONTAINER_PATH = (
    "/home/pi/services/wifi/wifi_handlers/networkmanager/networkmanager.py"
)
BLUEOS_CORE_CONTAINER = "blueos-core"
BOOTSTRAP_STARTUP_JSON = "/root/.config/blueos/bootstrap/startup.json"

# Anchor in the upstream wifi-manager source where the create_ap argv list
# ends ``--redirect-to-localhost`` and continues with the SSID/password.
# We insert the 5 GHz / 802.11ac flags between the anchor and the SSID line.
_CREATE_AP_ANCHOR = (
    '            "--redirect-to-localhost",'
    "  # Redirect all traffic to localhost, captive-portal style\n"
)
_CREATE_AP_5GHZ_FLAGS = (
    '            "--freq-band", "5",\n'
    '            "-c", "36",\n'
    '            "--ieee80211n",\n'
    '            "--ieee80211ac",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40]",\n'
    '            "--vht_capab", "[SHORT-GI-80][RXLDPC]",\n'
    '            "--country", "US",\n'
)
# Substring used to detect that the patch is already applied (idempotency).
_PATCH_MARKER = '"--freq-band", "5"'

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


def _decode_commander_field(value: str) -> str:
    """Decode a stdout/stderr field returned by BlueOS Commander.

    Commander captures the child's output via Python's ``subprocess`` and
    serialises it back as the ``repr()`` of the string, so the JSON value
    we receive looks like ``"'line1\\nline2\\n'"`` for a normal file and
    ``"'a\\'b'"`` for content containing a single quote. The previous
    decoder did ``out.strip("'\\"").replace("\\\\n", "\\n").strip()`` which
    only handled ``\\n`` - it left every embedded ``\\'`` and ``\\\\`` literal
    in the result, silently corrupting any file we read through
    ``docker exec cat`` or ``cat``. That bit us when the patched
    ``networkmanager.py`` we wrote to the host contained
    ``f"[{\\'-\\'.join(...)}]"`` and crashed wifi-manager with a
    ``SyntaxError: f-string expression part cannot include a backslash``
    on every boot.

    ``ast.literal_eval`` understands the full Python string-escape set in
    one shot, so we use it as the primary decoder and only fall back to
    the old strip/replace heuristic when the value isn't a quoted Python
    literal (e.g. some commander builds return raw text for empty
    output).
    """
    if not value:
        return ""
    text = value.strip()
    if not text:
        return ""
    if text[0] in ("'", '"') and text[-1] == text[0]:
        try:
            decoded = ast.literal_eval(text)
            if isinstance(decoded, str):
                return decoded.strip()
        except (ValueError, SyntaxError):
            pass
    return (
        text.strip("'\"")
        .replace("\\\\", "\x00")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\r", "\r")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\x00", "\\")
        .strip()
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
            out = _decode_commander_field(data.get("stdout", ""))
            err = _decode_commander_field(data.get("stderr", ""))
            if rc != 0:
                logger.warning("Command returned %d: %s %s", rc, out, err)
                return False, err or out
            return True, out
    except Exception as e:
        logger.warning("Commander command failed (%s): %s", command[:60], e)
        return False, str(e)


async def _read_host_file(path: str) -> str | None:
    """Return the contents of a host file, or None if it doesn't exist.

    Uses ``sudo cat`` so we can read root-owned config like
    ``/root/.config/blueos/bootstrap/startup.json`` - Commander runs as
    ``pi``, so a bare ``cat`` returns empty/EACCES on such files and we'd
    incorrectly conclude the file doesn't exist.
    """
    ok, out = await _run_host_command(f"sudo cat {path} 2>/dev/null")
    return out if ok else None


# Max number of base64 chars per chunk-append command. nginx in front of
# Commander caps the request URI around 8 KB; the chunk lives inside a
# ``printf %s '...' >> /tmp/...`` shell command plus the
# ``i_know_what_i_am_doing`` flag, both of which inflate further under
# URL-encoding. 2000 keeps comfortable headroom.
_HOSTFILE_CHUNK_SIZE = 2000
_HOSTFILE_STAGING = "/tmp/.doris_hostfile_stage"


async def _write_host_file(path: str, content: str) -> bool:
    """Write *content* to *path* on the host atomically.

    Streams the payload as base64 in small chunks appended to a staging
    file, then atomically renames into place under ``sudo``. The previous
    implementation used a single ``sudo tee <<HEREDOC`` shell command sent
    through the Commander API's query string, which hit nginx's request
    URI length limit (around 8 KB) for anything bigger than a few KB and
    silently failed with HTTP 400 - notably the ~25 KB patched
    ``networkmanager.py``. Chunked base64 keeps each request well under
    the limit and works for any file size.

    Skips the write entirely if the destination already has the same
    content, avoiding unnecessary inotify churn.
    """
    existing = await _read_host_file(path)
    if existing is not None and existing.strip() == content.strip():
        logger.info("Host file %s already up to date, skipping", path)
        return True

    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    total_chunks = (len(encoded) + _HOSTFILE_CHUNK_SIZE - 1) // _HOSTFILE_CHUNK_SIZE

    ok, _ = await _run_host_command(f"true > {_HOSTFILE_STAGING}")
    if not ok:
        logger.warning("Could not truncate staging file %s", _HOSTFILE_STAGING)
        return False

    for idx in range(total_chunks):
        chunk = encoded[idx * _HOSTFILE_CHUNK_SIZE : (idx + 1) * _HOSTFILE_CHUNK_SIZE]
        # Base64 alphabet is shell-safe, so single-quoting the chunk is
        # sufficient escaping; no embedded quotes possible.
        ok, _ = await _run_host_command(
            f"printf %s '{chunk}' >> {_HOSTFILE_STAGING}"
        )
        if not ok:
            logger.warning(
                "Chunk %d/%d failed while writing %s; aborting",
                idx + 1, total_chunks, path,
            )
            await _run_host_command(f"rm -f {_HOSTFILE_STAGING}")
            return False

    # Atomic install: decode-to-temp, rename, clean up staging. The
    # redirect must live inside the sudo'd shell so root owns the target.
    final_cmd = (
        f"sudo bash -c 'base64 -d {_HOSTFILE_STAGING} > {path}.doris.tmp"
        f" && mv {path}.doris.tmp {path}"
        f" && rm -f {_HOSTFILE_STAGING}'"
    )
    ok, _ = await _run_host_command(final_cmd)
    if ok:
        logger.info(
            "Wrote %s on host (%d bytes via %d base64 chunks)",
            path, len(content), total_chunks,
        )
    else:
        await _run_host_command(
            f"sudo rm -f {_HOSTFILE_STAGING} {path}.doris.tmp"
        )
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


async def _read_container_file(container: str, path: str) -> str | None:
    """Return the contents of *path* from inside *container*, or None on error."""
    ok, out = await _run_host_command(f"docker exec {container} cat {path}")
    return out if ok else None


async def _install_create_ap_5ghz_patch() -> None:
    """Patch wifi-manager's ``create_ap`` invocation for 5 GHz / 802.11ac.

    Reads the in-container ``networkmanager.py``, inserts the 5 GHz flags
    after the ``--redirect-to-localhost`` anchor, and writes the result to
    ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` so the
    bind mount in ``startup.json`` picks it up at next ``blueos-core``
    start. Idempotent: if the file already contains our marker, skip.
    """
    source = await _read_container_file(
        BLUEOS_CORE_CONTAINER, WIFI_OVERRIDE_CONTAINER_PATH
    )
    if source is None:
        logger.warning(
            "Could not read %s from %s; skipping 5 GHz patch",
            WIFI_OVERRIDE_CONTAINER_PATH,
            BLUEOS_CORE_CONTAINER,
        )
        return

    if _PATCH_MARKER in source:
        # Either the bind mount is already active and serving the patched
        # file, or the patch was applied previously. Either way we just
        # make sure the host file matches our current expected content.
        patched = source
    elif _CREATE_AP_ANCHOR in source:
        patched = source.replace(
            _CREATE_AP_ANCHOR,
            _CREATE_AP_ANCHOR + _CREATE_AP_5GHZ_FLAGS,
            1,
        )
    else:
        logger.warning(
            "create_ap anchor not found in %s; BlueOS upstream may have "
            "changed - skipping 5 GHz patch (hotspot will still work, just "
            "at default 2.4 GHz / 802.11g speeds)",
            WIFI_OVERRIDE_CONTAINER_PATH,
        )
        return

    await _run_host_command(f"sudo mkdir -p {WIFI_OVERRIDE_DIR}")
    await _write_host_file(WIFI_OVERRIDE_HOST_PATH, patched)


async def _ensure_startup_bind() -> None:
    """Add the wifi-override bind to ``startup.json`` if not already present.

    ``blueos-bootstrap`` reads this file on every boot and uses it to build
    the ``docker run`` arguments for ``blueos-core``. The new entry takes
    effect at the next reboot (or after manually restarting
    ``blueos-bootstrap``); we do not auto-restart anything to avoid an
    unexpected outage.
    """
    raw = await _read_host_file(BOOTSTRAP_STARTUP_JSON)
    if raw is None:
        logger.warning(
            "Could not read %s; skipping bind-mount install",
            BOOTSTRAP_STARTUP_JSON,
        )
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Bootstrap %s is not valid JSON: %s", BOOTSTRAP_STARTUP_JSON, e)
        return

    binds = data.setdefault("core", {}).setdefault("binds", {})
    existing = binds.get(WIFI_OVERRIDE_HOST_PATH)
    if (
        isinstance(existing, dict)
        and existing.get("bind") == WIFI_OVERRIDE_CONTAINER_PATH
    ):
        logger.info("Bootstrap bind for wifi override already present")
        return

    binds[WIFI_OVERRIDE_HOST_PATH] = {
        "bind": WIFI_OVERRIDE_CONTAINER_PATH,
        "mode": "ro",
    }

    new_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if await _write_host_file(BOOTSTRAP_STARTUP_JSON, new_content):
        logger.info(
            "Added wifi override bind to %s; reboot to activate",
            BOOTSTRAP_STARTUP_JSON,
        )


async def setup_hotspot_radio() -> None:
    """Install the host-side config that pins ``uap0`` to the USB Realtek
    *and* makes the AP come up on 5 GHz / 802.11ac.

    Idempotent: writes are skipped where existing host content already
    matches. Both the udev rename and the bind-mounted wifi override only
    take full effect on the next reboot, so this function never restarts
    services or kicks the running hotspot - it just stages everything for
    the next boot. The running AP keeps working at default settings in the
    meantime.
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

    await _install_create_ap_5ghz_patch()
    await _ensure_startup_bind()

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
