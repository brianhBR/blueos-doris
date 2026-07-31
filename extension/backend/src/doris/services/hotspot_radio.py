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

Part 2: tell ``create_ap`` to max out 2.4 GHz HT20
--------------------------------------------------
``wifi-manager`` invokes ``create_ap`` with no band/channel/HT/VHT flags,
so hostapd defaults to ``hw_mode=g, channel=1`` with no HT capabilities
advertised - real-world TCP throughput tops out around 25 Mbps in that
mode even on hardware capable of much more. We patch the wifi-handler
module inside ``blueos-core`` to add::

    "-c", "<auto-picked: 1 or 6>",
    "--ieee80211n",
    "--ht_capab", "[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",
    "--country", "US",

right before the SSID/password arguments. The patched module is written
to ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` on the host
and bind-mounted into the container by adding an entry to
``/root/.config/blueos/bootstrap/startup.json``. ``blueos-bootstrap``
re-reads ``startup.json`` on every boot, so the override survives
``blueos-core`` recreations and BlueOS upgrades that don't touch the
target file.

The ht_capab line explicitly declares HT20-only capabilities. An
earlier revision of this patch asked for ``[HT40+]`` on the theory
that hostapd's "try HT40, OBSS-coex fall back to HT20" logic would
get us the wider channel for free in clean RF environments and a
graceful downgrade otherwise. In practice the bundled
``morrownr/88x2bu-20210702`` driver's AP-mode firmware path cannot
keep an HT40 BSS up: in a busy 2.4 GHz environment hostapd's OBSS
scan downgrades to HT20 before the driver ever sees the request and
things look fine, but in a clean RF environment hostapd actually
issues the HT40 BSS request, the driver rejects it, and the BSS
cycles ``INTERFACE-ENABLED`` → ``INTERFACE-DISABLED`` (end-user
symptom: "AP shows up, you connect, it disappears, you lose
connection"; ``iw dev uap0 info`` shows ``txpower -100.00 dBm`` and
no ``channel/width/center`` line). Confirmed in field reports May
2026 on a unit with no 2.4 GHz neighbours on the picker-chosen
channel. Asking for HT20 directly removes the failure mode at the
cost of nothing - the driver was downgrading to HT20 every time it
actually completed a BSS bring-up anyway. The other capabilities
(LDPC, SHORT-GI-20, RX-STBC1, MAX-AMSDU-7935) still lift real TCP
throughput from ~25 Mbps (stock) to ~80 Mbps on this radio.

Channel auto-selection
~~~~~~~~~~~~~~~~~~~~~~
At patch-install time :func:`_pick_24ghz_channel` reads the kernel's
cached ``iw dev wlan0 scan dump`` (refreshed every few seconds by
``wifi-manager``'s ``_autoscan``), scores the two non-overlapping
2.4 GHz primaries (1, 6) by the total linear-power signal of neighbour
BSSes inside each one's HT20 footprint, and picks the quietest. Ties
(or an empty scan) default to channel 6. Channel 11 is intentionally
*not* a candidate: the DORIS potted external antenna is tuned for the
lower half of the 2.4 GHz band (roughly 2412-2437 MHz), so ch 11 at
2462 MHz sits noticeably outside its SWR sweet spot - the resulting
mismatch loss is likely to exceed any interference benefit from being
on a quieter primary. The picker runs each time :func:`setup_hotspot_
radio` runs, so a vehicle that boots in a new RF environment still
gets the cleaner of the two antenna-compatible primaries.

Measurements (channel 6, this radio + DORIS potted external antenna)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Stock ``wifi-manager`` (no ``ht_capab``):     ~25-30 Mbps real TCP.
This patch (HT20 + full caps, channel 6):     ~82 Mbps down / 77 Mbps up
(``iperf3 -c 192.168.42.1 -p 5201 -t 20 -P 4``, May 2026, indoor
2-3 m, 1 AP neighbour on ch 5). PHY ceiling for 2-stream HT20 + SGI
is 144 Mbps and real-world TCP typically caps at 55-65% of PHY, so
~80 Mbps is at the achievable ceiling - the link is clean, the
remaining headroom would require a driver that can keep HT40 alive.

Why no 5 GHz mode
~~~~~~~~~~~~~~~~~
5 GHz is intentionally not attempted. Two independent blockers:

  1. **The DORIS potted external antenna is single-band 2.4 GHz.** Even
     if the software produced a valid 5 GHz beacon the RF would not
     radiate efficiently, so usable 5 GHz coverage is impossible
     without an antenna swap.
  2. **The bundled ``88x2bu`` driver doesn't reliably do 5 GHz AP
     anyway.** May 2026 testing on ``morrownr/88x2bu-20210702`` showed
     UNII-1 (ch 36-48) hard-rejected by the driver before any RF
     transmit (``Failed to set beacon parameters`` / ``INTERFACE-
     DISABLED``), and UNII-3 (ch 149) brought hostapd up but the AP
     dropped within ~45 s in repeat runs. Same class of AP-mode
     firmware-path issue as the 2.4 GHz HT40 downgrade documented at
     :data:`_CREATE_AP_24GHZ_FLAGS_TEMPLATE` below.

Anyone resuming 5 GHz work needs both (a) a dual-band antenna with
characterised 5 GHz SWR and (b) a different driver or radio that
keeps an AP-mode 5 GHz BSS up under sustained load. Until then,
2.4 GHz HT20 with the full ``ht_capab`` set above is the achievable
ceiling on this hardware. The legacy-patch regex
:data:`_DORIS_PATCH_BLOCK_RE` still recognises and strips the old
``--freq-band 5`` / ``--ieee80211ac`` / ``--vht_capab`` flags so users
upgrading from a previous extension that wrote a 5 GHz patch are
cleanly migrated to the 2.4 GHz config.

Part 3: raise the Pi 5 USB current budget
------------------------------------------
A Pi 5 caps *total* USB draw at 600 mA unless
``usb_max_current_enable=1`` is set in ``/boot/firmware/config.txt``,
which lifts the budget to 1600 mA. The Realtek dongle declares 500 mA
and a USB flash drive can declare up to 896 mA (essentially the USB 3
ceiling), so the stock cap leaves no headroom at all.

When the rail trips over-current protection the dongle is dropped off
the bus and re-enumerated as a *new* phy, which leaves ``hostapd``
bound to the interface that no longer exists. The end state is a
zombie AP: ``create_ap`` and ``hostapd`` are still running and
``wifi-manager`` still reports ``hotspot=true``, but ``uap0`` has no
SSID, no channel, no address, and ``txpower -100.00 dBm`` - so nothing
beacons and the vehicle silently loses its access point. Note this is
the same ``txpower -100.00 dBm`` signature as the HT40 failure
documented at :data:`_CREATE_AP_24GHZ_FLAGS_TEMPLATE`; the
distinguishing evidence is the over-current/re-enumeration pair in
``dmesg``. Observed on the vehicle in July 2026 about nine minutes
into a boot::

    usb usb1-port1: over-current change #1
    usb 1-1: USB disconnect, device number 2
    rtl88x2bu 1-1:1.0: Runtime PM usage count underflow!
    usb 1-1: new high-speed USB device number 4 using xhci-hcd
    rtl88x2bu 1-1:1.0 uap0: renamed from wlan1

The over-current notification arrives on every root-hub port at once
because the Pi shares one sense circuit across the whole USB rail, so
the logs cannot attribute the trip to an individual device.

:func:`_ensure_usb_max_current` stages the setting for the next boot.
It is deliberately append-only and never rewrites ``config.txt`` - see
that function for why.

Boot order
----------
  1. Kernel + USB enumeration -> ``rtl88x2bu`` loads -> udev rule renames
     the net device to ``uap0``.
  2. NetworkManager starts, reads ``conf.d``, marks ``uap0`` unmanaged.
  3. ``blueos-bootstrap`` reads ``startup.json``, mounts the patched
     ``networkmanager.py`` over the stock one, then starts ``blueos-core``.
  4. ``wifi-manager`` sees ``uap0`` already exists -> runs ``create_ap``
     on it with the 2.4 GHz HT20-with-full-caps flags installed at the
     previous DORIS extension start -> hostapd brings the AP up on the
     auto-picked channel (1 or 6) with the rich ``ht_capab`` set.

Result: hotspot broadcast from the Realtek's external antenna at 2.4
GHz HT20 with the full set of HT capabilities the radio supports;
~80 Mbps real-world TCP throughput, ~3x what stock ``wifi-manager``
delivers. Onboard Broadcom is STA-only on ``wlan0``; in our testing
its retransmit count dropped from ~14,900/min to ~30/min.

Note on ``sudo``: the BlueOS Commander API's shell PATH does not include
``/sbin`` or ``/usr/sbin``, so ``udevadm`` and friends are only reachable
through ``sudo`` (which resets the PATH).

Note on the bind-mount + missing-source booby trap: Docker silently
creates an empty *directory* at any bind-mount source path that doesn't
exist on the host. So a transient state where ``startup.json`` lists
``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` as a bind
source while the host file is missing is fatal - the next
``blueos-core`` restart turns the missing source into a directory, then
container init fails with "not a directory" because the target is a
file, and ``blueos-bootstrap`` falls back to the factory image. We hit
this on the vehicle in May 2026 after :func:`_install_create_ap_speed
_patch`'s self-heal ``rm``'d a corrupt override without also removing
the bind entry. The fix - and the contract every caller in this module
must honour - is: **never delete the override file while its bind entry
is still in startup.json**. The self-heal path now removes the bind
*first*, then the file; :func:`_remove_startup_bind` is the safe
counterpart to :func:`_ensure_startup_bind` for that purpose.
"""

import ast
import base64
import json
import logging
import re

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
# We insert our HT20-with-full-caps flags between the anchor and the SSID
# line.
#
# Anchor strategy is *regex on the argv token* rather than exact match
# on the line including its trailing comment. The previous
# implementation pinned the anchor to ``            "--redirect-to-
# localhost",  # Redirect all traffic ...`` - exactly 12 leading
# spaces, exactly that comment, exactly that wording. Any upstream
# reformat (reindent, comment edit, comma-then-trailing-comment, comma
# alone, no comma at end of list) silently broke the patch with no
# loud signal and the install fell through to stock ``create_ap``
# args. We now match on just the argv-token line and capture the
# leading whitespace so the inserted flags re-emit with the same
# indentation regardless of upstream formatting changes.
#
# The regex requires *some* leading whitespace because a top-level
# occurrence of the string ``"--redirect-to-localhost"`` (e.g. in a
# docstring or comment) would not be inside the argv list and would
# be the wrong place to inject flags. We accept both ``,`` and a bare
# end of line after the token so a future upstream that drops the
# trailing comma still matches.
_CREATE_AP_ANCHOR_RE = re.compile(
    r'^(?P<indent>[ \t]+)"--redirect-to-localhost",?(?:[ \t]*#[^\n]*)?\n',
    re.MULTILINE,
)

# Maxed-out 2.4 GHz HT20 configuration. ``{channel}`` is filled at
# install time by :func:`_pick_24ghz_channel` so the AP lands on
# whichever of channels 1/6 is least crowded.  Channel 11 is left
# out because the DORIS potted external antenna's SWR sweet spot is
# the lower half of the 2.4 GHz band; see :func:`_pick_24ghz_channel`.
# ``{indent}`` is filled with the whitespace captured by
# :data:`_CREATE_AP_ANCHOR_RE` so the inserted lines line up with the
# surrounding argv entries regardless of upstream indentation choice.
#
# Why HT20 explicitly and not HT40?  The ``morrownr/88x2bu-20210702``
# driver advertises HT40 capability but its AP-mode firmware path
# cannot keep an HT40 BSS up.  May 2026 lab testing on a deliberately
# cleaned RF environment (test owner moved all 2.4 GHz neighbours out
# of ch 11 HT40-'s affected 2442-2482 MHz band, then hot-deployed
# ``-c 11 --ht_capab [HT40-]`` into the live wifi-manager) showed
# hostapd reach ``state=ENABLED`` but report ``secondary_channel=0``
# and ``iw dev uap0 info`` come back with ``width: 20 MHz`` - i.e.
# when things "worked" the driver was silently dropping us to HT20
# below hostapd's state machine, inside the driver/firmware seam.
#
# An earlier revision of this patch asked for ``[HT40+]`` anyway,
# betting on (a) "the driver downgrades for us" and (b) "hostapd's
# OBSS coex scan downgrades to HT20 if any 2.4 GHz neighbour overlaps
# the secondary 40 MHz band, so HT40+ falls back gracefully for free."
# A field report in May 2026 broke that bet: a unit with no 2.4 GHz
# neighbours on the auto-picked channel saw hostapd actually issue
# the HT40 BSS request (no OBSS neighbours to coex-downgrade off of),
# the driver rejected it inside the firmware path, and the BSS
# entered an ``INTERFACE-ENABLED`` -> ``INTERFACE-DISABLED`` cycle
# on every client association attempt.  The user-visible signature
# is "AP appears, you connect, it disappears, you lose connection"
# and ``iw dev uap0 info`` shows ``txpower -100.00 dBm`` (the
# firmware-unset sentinel) with no ``channel/width/center`` line -
# distinct from the "stable HT20" signature where the same command
# reports ``channel <N>, width: 20 MHz, txpower 20.00 dBm`` cleanly.
#
# Same class of AP-mode firmware-path issue as the 5 GHz failure
# noted in the module docstring ("Why no 5 GHz mode" section): the
# driver advertises a capability it cannot actually sustain.
#
# We therefore advertise HT20 only.  This costs nothing: every unit
# that "worked" with the previous patch was running HT20 internally
# already (per the lab measurement above).  The ht_capab caps we
# keep - SHORT-GI-20, LDPC, RX-STBC1, MAX-AMSDU-7935 - are all
# HT20-valid and lift real TCP throughput from ~25 Mbps stock to
# ~80 Mbps.  The HT40-specific caps ([HT40+], SHORT-GI-40,
# DSSS_CCK-40) are removed both because they would be ignored at
# HT20 and to make sure hostapd has no path back to attempting an
# HT40 BSS.
_CREATE_AP_24GHZ_FLAGS_TEMPLATE = (
    '{indent}"-c", "{channel}",\n'
    '{indent}"--ieee80211n",\n'
    '{indent}"--ht_capab", "[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",\n'
    '{indent}"--country", "US",\n'
)

# Matches *any* DORIS create_ap insertion that may already follow
# the anchor matched by :data:`_CREATE_AP_ANCHOR_RE` - either the
# legacy 5 GHz attempt (with ``--freq-band 5`` and ``--vht_capab``)
# or the current 2.4 GHz patch with any auto-picked channel. Used at
# install time so re-running the patch on an upgraded install
# converges to the current canonical block instead of stacking flags.
# Anchored via ``re.match`` from the position right after the
# anchor match, never used as a free search, so false positives
# outside our managed region are impossible.
_DORIS_PATCH_BLOCK_RE = re.compile(
    r'(?:[ \t]*"--freq-band", "[0-9]+",\n)?'        # legacy 5 GHz only
    r'[ \t]*"-c", "[0-9]+",\n'                      # always present
    r'[ \t]*"--ieee80211n",\n'                      # always present
    r'(?:[ \t]*"--ieee80211ac",\n)?'                # legacy only
    r'[ \t]*"--ht_capab", "[^"]+",\n'               # always present
    r'(?:[ \t]*"--vht_capab", "[^"]+",\n)?'         # legacy only
    r'[ \t]*"--country", "[A-Z]+",\n'               # always present
)

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

# Firmware config that governs the Pi 5 USB current budget. See "Part 3"
# in the module docstring for the failure this prevents.
BOOT_CONFIG_PATH = "/boot/firmware/config.txt"
BOOT_CONFIG_BACKUP_PATH = "/boot/firmware/config.txt.doris-usb-current.bak"
USB_MAX_CURRENT_SETTING = "usb_max_current_enable=1"

# Appended verbatim to config.txt. Emitted through a single-quoted
# ``printf``, so this must stay free of single quotes and percent signs.
#
# The leading empty entry produces a blank separator line, and the
# explicit ``[all]`` header is load-bearing: config.txt settings are
# scoped by the most recent section header, and the file ships with
# model-conditional sections (``[cm4]``, ``[cm5]``, ``[pi5]``) that may
# appear last. Appending a bare ``usb_max_current_enable=1`` would
# inherit whichever section happened to be open at the end of the file,
# so on a vehicle whose config.txt ends inside ``[cm4]`` the setting
# would silently never apply.
USB_MAX_CURRENT_BLOCK_LINES = (
    "",
    "[all]",
    "# Managed by DORIS extension (services/hotspot_radio.py).",
    "# Raise the Pi 5 USB rail budget from 600 mA to 1600 mA. The Realtek AP",
    "# dongle declares 500 mA and USB storage up to 896 mA, so the stock cap",
    "# leaves no headroom: the rail trips over-current under load, drops the",
    "# dongle off the bus, and takes the hotspot down with it.",
    USB_MAX_CURRENT_SETTING,
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
    """Write *content* to *path* on the host, preserving the inode in place.

    Streams the payload as base64 in small chunks appended to a staging
    file, decodes the staging into a verified ``.doris.tmp`` binary,
    then writes the verified bytes *through* the destination path with
    a final ``cat tmp > path`` redirect. The redirect opens ``path``
    with ``O_WRONLY|O_CREAT|O_TRUNC``, which truncates and rewrites the
    existing inode in place when the file already exists - bind mounts
    that target this inode (notably the wifi override mount in
    ``blueos-core``) keep seeing the new content with no remount.

    Why not the obvious ``mv tmp path``?  ``mv`` always allocates a
    new inode; the old inode lingers as long as something else holds
    it open. ``blueos-core``'s bind mount is established at container
    start against the host file's inode at that moment, so after a
    ``mv`` the in-container view still points at the previous
    (now-orphaned) inode while the host filesystem path resolves to
    the fresh one. We hit this in May 2026 when ``setup_hotspot_radio``
    rewrote a corrupt override on the host but ``wifi-manager`` kept
    crash-looping for hours because its bind-mounted view was still
    the corrupt orphan inode. Symptom on the host:
    ``stat -c %i host_path != stat -c %i container_path`` even though
    the bind-mount config in ``startup.json`` is correct.

    The previous implementation used a single ``sudo tee <<HEREDOC``
    shell command sent through the Commander API's query string, which
    hit nginx's request URI length limit (around 8 KB) for anything
    bigger than a few KB and silently failed with HTTP 400 - notably
    the ~25 KB patched ``networkmanager.py``. Chunked base64 keeps each
    request well under the limit and works for any file size.

    Skips the write entirely if the destination already has the same
    content, avoiding unnecessary inotify churn.

    Atomicity caveat: trading ``mv`` for an in-place truncate+rewrite
    means a reader hitting *path* during the (sub-ms) ``cat`` window
    could observe a partially-written file. Acceptable for our
    consumers - ``wifi-manager`` reads its bind-mounted file once at
    process start; udev and NetworkManager configs are reloaded by
    explicit signals after this function returns.
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

    # Install in two steps under one sudo'd shell:
    #   1. base64 -d into a verified ``.doris.tmp`` (any decode error
    #      stops here, leaving ``path`` untouched).
    #   2. cat ``.doris.tmp > path``: ``>`` opens ``path`` with
    #      O_TRUNC, preserving the inode if the file exists - which
    #      is what keeps active bind mounts seeing the new content.
    final_cmd = (
        f"sudo bash -c 'base64 -d {_HOSTFILE_STAGING} > {path}.doris.tmp"
        f" && cat {path}.doris.tmp > {path}"
        f" && rm -f {path}.doris.tmp {_HOSTFILE_STAGING}'"
    )
    ok, _ = await _run_host_command(final_cmd)
    if ok:
        logger.info(
            "Wrote %s on host (%d bytes via %d base64 chunks, inode-preserving)",
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


async def _pick_24ghz_channel() -> int:
    """Pick the cleanest 2.4 GHz primary channel from {1, 6}.

    Uses the kernel's cached scan results on ``wlan0`` (``wifi-manager``
    refreshes them every ~10 s via ``_autoscan``) to score each
    candidate channel by total neighbour-BSS signal power inside its
    HT20 footprint (primary ± 2 channels). Returns the candidate with
    the lowest score; ties and an empty scan default to channel 6
    (centre of band, conventionally cleanest).

    Channel 11 is deliberately excluded from the candidate set: the
    DORIS potted external antenna is tuned for the lower half of the
    2.4 GHz band (roughly 2412-2437 MHz), so channel 11 at 2462 MHz
    sits outside its SWR sweet spot. Adding it back would gain nothing
    on average - the antenna mismatch loss at 2462 MHz is likely to
    exceed any interference advantage from being on a quieter primary.

    This is intentionally read-only: it consumes whatever the kernel
    happens to have cached and does *not* trigger a fresh scan, because
    a live ``scan trigger`` on a connected ``wlan0`` can briefly stall
    the STA link.
    """
    ok, dump = await _run_host_command(
        "sudo iw dev wlan0 scan dump 2>/dev/null"
    )
    if not ok or not dump.strip():
        logger.info(
            "2.4 GHz channel scan unavailable; defaulting to channel 6"
        )
        return 6

    # ``iw scan dump`` groups fields per-BSS with a ``BSS <bssid>`` header
    # then a block of ``\tkey: value`` lines.  Field order within a block
    # is driver-dependent (typically ``freq:`` comes before ``signal:``,
    # but we don't rely on that), so we accumulate per-BSS state and
    # emit one entry into ``neighbours_by_channel`` at each new BSS header
    # / end of file.  We skip our own AP (SSID prefix ``DORIS``) so a
    # vehicle that booted on channel X last time doesn't get pushed off
    # X this time just because its own beacon is in the scan cache.
    neighbours_by_channel: dict[int, list[float]] = {}
    current: dict[str, object] = {}

    def _flush() -> None:
        ssid = current.get("ssid")
        if isinstance(ssid, str) and ssid.startswith("DORIS"):
            return
        ch = current.get("channel")
        sig = current.get("signal")
        if isinstance(ch, int) and isinstance(sig, float):
            neighbours_by_channel.setdefault(ch, []).append(sig)

    for raw in dump.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            _flush()
            current = {}
            continue
        if line.startswith("signal:"):
            try:
                current["signal"] = float(line.split()[1])
            except (IndexError, ValueError):
                pass
            continue
        if line.startswith("freq:"):
            try:
                freq = int(line.split()[1])
            except (IndexError, ValueError):
                continue
            # 2.4 GHz channels 1-13 sit at 2412 + 5*(ch-1) MHz.
            if 2412 <= freq <= 2472:
                current["channel"] = (freq - 2407) // 5
            continue
        if line.startswith("SSID:"):
            # ``iw`` prints ``SSID: <name>`` (no quotes); empty SSID is a
            # hidden AP, which we still want to count as a neighbour.
            current["ssid"] = line[len("SSID:"):].strip()
    _flush()

    if not neighbours_by_channel:
        logger.info(
            "No 2.4 GHz neighbours visible; defaulting to channel 6"
        )
        return 6

    def linear_power(dbm: float) -> float:
        # dBm -> linear mW. Summing in linear space gives a correct
        # "total interfering energy" score; summing in dB would be wrong.
        return 10.0 ** (dbm / 10.0)

    def score(primary: int) -> float:
        total = 0.0
        for ch, signals in neighbours_by_channel.items():
            if abs(ch - primary) <= 2:  # HT20 footprint ~ primary +/- 2
                total += sum(linear_power(s) for s in signals)
        return total

    # Candidate order also breaks ties: prefer 6 (middle of band) first,
    # then 1.  We include the candidate's list index in the sort tuple
    # so that equal scores pick the earlier-listed channel.  Channel 11
    # is excluded - see function docstring for the antenna-tuning
    # rationale.
    candidates = [6, 1]
    scored = sorted(
        (score(c), idx, c) for idx, c in enumerate(candidates)
    )
    chosen = scored[0][2]
    logger.info(
        "2.4 GHz channel scores (lower = cleaner): %s -> chose ch %d",
        ", ".join(f"ch{c}={s:.2e}" for s, _, c in scored),
        chosen,
    )
    return chosen


def _strip_doris_patch(source: str) -> str:
    """Remove any prior DORIS ``create_ap`` insertion that immediately
    follows the anchor line matched by :data:`_CREATE_AP_ANCHOR_RE`.
    No-op if no such block is present.

    Anchored to the position right after the anchor line so we can never
    accidentally strip something else that happens to look like our
    flags elsewhere in the file.
    """
    anchor_match = _CREATE_AP_ANCHOR_RE.search(source)
    if anchor_match is None:
        return source
    end_of_anchor = anchor_match.end()
    block_match = _DORIS_PATCH_BLOCK_RE.match(source, end_of_anchor)
    if not block_match:
        return source
    return source[:end_of_anchor] + source[block_match.end():]


async def _install_create_ap_speed_patch() -> bool:
    """Patch wifi-manager's ``create_ap`` invocation for max 2.4 GHz HT20.

    Reads the in-container ``networkmanager.py``, picks the cleanest
    2.4 GHz primary via :func:`_pick_24ghz_channel`, inserts the
    HT20-with-full-caps flags after the ``--redirect-to-localhost``
    anchor, and writes the result to
    ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` so the
    bind mount in ``startup.json`` picks it up on the next
    ``blueos-core`` start.

    Returns ``True`` iff the host override file is in place with the
    canonical patch applied (either because we just wrote it, or
    because :func:`_write_host_file` short-circuited a no-op when the
    on-disk content already matched). Returns ``False`` on every
    failure path: source unreadable, source corrupt (self-heal path),
    anchor missing, or chunked write failed. Callers MUST treat
    ``False`` as "do not add or keep the bind entry in
    ``startup.json``" because pointing a bind at a missing/un-patched
    source is the factory-revert footgun documented in the module
    docstring - and the self-heal path here actively removes the
    entry, which gets undone if a subsequent
    :func:`_ensure_startup_bind` re-adds it.

    Upgrade-safe and re-run-safe: any prior DORIS patch (legacy 5 GHz
    attempt, or current 2.4 GHz patch with a different auto-picked
    channel) is stripped first via :func:`_strip_doris_patch`, then
    the current canonical block is applied. :func:`_write_host_file`
    short-circuits when the host file already matches.

    Self-healing on existing corruption: if the bind-mounted source we
    read here is not valid Python, an earlier DORIS extension version
    (or an interrupted write) must have left a corrupt file on the host.
    Patching a corrupt source and writing it back would just re-stamp
    the corruption on every boot - which is exactly how we ended up in
    a ``SyntaxError`` crash loop for ``wifi-manager`` in May 2026. So
    we detect that case with :func:`ast.parse`, delete the host
    override outright, and bail without writing. The next
    ``blueos-core`` restart re-binds to the stock file from the image,
    and the *next* :func:`setup_hotspot_radio` reads a clean baseline
    and re-installs the patch correctly.
    """
    source = await _read_container_file(
        BLUEOS_CORE_CONTAINER, WIFI_OVERRIDE_CONTAINER_PATH
    )
    if source is None:
        logger.warning(
            "Could not read %s from %s; skipping create_ap speed patch",
            WIFI_OVERRIDE_CONTAINER_PATH,
            BLUEOS_CORE_CONTAINER,
        )
        return False

    try:
        ast.parse(source)
    except SyntaxError as exc:
        logger.warning(
            "Bind-mounted %s is not valid Python (line %s: %s); a prior "
            "DORIS write was corrupted. Removing host override %s AND its "
            "bind-mount entry from %s so blueos-core falls back to stock "
            "wifi-manager networkmanager.py on next restart - the next "
            "DORIS run will re-patch from the clean baseline and "
            "re-install the bind.",
            WIFI_OVERRIDE_CONTAINER_PATH,
            exc.lineno,
            exc.msg,
            WIFI_OVERRIDE_HOST_PATH,
            BOOTSTRAP_STARTUP_JSON,
        )
        # Order matters: REMOVE THE BIND ENTRY FIRST, then delete the
        # file. If we did it the other way and a blueos-core restart
        # happened in between, Docker would silently create the missing
        # source as a directory (default behavior for absent bind
        # sources), then fail the next container start with
        # "Are you trying to mount a directory onto a file" and
        # blueos-bootstrap would fall back to the factory image. We hit
        # exactly this on the vehicle in May 2026, which is why both
        # steps are non-negotiable here.
        await _remove_startup_bind()
        await _run_host_command(f"sudo rm -f {WIFI_OVERRIDE_HOST_PATH}")
        return False

    if _CREATE_AP_ANCHOR_RE.search(source) is None:
        logger.warning(
            "create_ap anchor not found in %s; BlueOS upstream may have "
            "changed - skipping create_ap speed patch (hotspot will still "
            "work, at default 2.4 GHz / 802.11g speeds). Bind-mount install "
            "is also skipped this run; an un-patched override file is the "
            "same factory-revert footgun as a missing one.",
            WIFI_OVERRIDE_CONTAINER_PATH,
        )
        return False

    stripped = _strip_doris_patch(source)
    channel = await _pick_24ghz_channel()
    anchor_match = _CREATE_AP_ANCHOR_RE.search(stripped)
    # We re-search post-strip because the strip can shift offsets, but
    # the anchor itself is never inside the stripped region; this match
    # is guaranteed to succeed here, since we already confirmed the
    # anchor exists pre-strip and strip only removes content *after* it.
    assert anchor_match is not None, (
        "anchor disappeared after _strip_doris_patch; this is a bug in "
        "the strip regex - the anchor should never be inside the "
        "stripped span"
    )
    indent = anchor_match.group("indent")
    flags = _CREATE_AP_24GHZ_FLAGS_TEMPLATE.format(
        indent=indent, channel=channel
    )
    insert_at = anchor_match.end()
    patched = stripped[:insert_at] + flags + stripped[insert_at:]

    await _run_host_command(f"sudo mkdir -p {WIFI_OVERRIDE_DIR}")
    return await _write_host_file(WIFI_OVERRIDE_HOST_PATH, patched)


def _binds_dict_or_none(data: object) -> dict | None:
    """Return the binds dict inside a parsed ``startup.json`` payload,
    or ``None`` if the expected ``data["core"]["binds"]`` shape isn't
    there.

    Defensive helper for both :func:`_ensure_startup_bind` and
    :func:`_remove_startup_bind`. ``blueos-bootstrap`` major-version
    upgrades have historically re-templated this file in place; if it
    ever renames the schema (e.g. ``core`` → ``containers/core``) or
    changes the bind structure from a dict to a list, blindly calling
    ``data.setdefault("core", {}).setdefault("binds", {})`` would
    create a parallel orphan key that bootstrap never reads while
    silently looking like success. Returning ``None`` here lets the
    callers log loudly and skip the write rather than corrupt the
    file with an unrecognised top-level key.
    """
    if not isinstance(data, dict):
        return None
    core = data.get("core")
    if not isinstance(core, dict):
        return None
    binds = core.get("binds")
    if not isinstance(binds, dict):
        return None
    return binds


async def _remove_startup_bind() -> None:
    """Remove the wifi-override bind from ``startup.json`` if present.

    Counterpart to :func:`_ensure_startup_bind`, used by the self-heal
    path in :func:`_install_create_ap_speed_patch` when we have to
    delete the host override.

    Why this exists: Docker silently creates an empty *directory* at
    any bind-mount source that doesn't exist on the host. So if we
    delete the host override while ``startup.json`` still lists it as a
    bind source, the next ``blueos-core`` restart triggers Docker to
    create a directory at that path, then container init fails with
    ``not a directory: Are you trying to mount a directory onto a
    file (or vice-versa)?`` and ``blueos-bootstrap`` falls back to the
    factory image (silently re-pinning the user away from their
    chosen blueos-core tag). Removing the bind entry first means the
    next ``blueos-core`` restart simply doesn't try to mount anything
    at that path; on a subsequent DORIS run we add the bind back and
    install a fresh override.

    No-op if the entry isn't present, the JSON can't be parsed, or
    the schema doesn't match the shape we know how to walk - matches
    the conservative shape of :func:`_ensure_startup_bind`.
    """
    raw = await _read_host_file(BOOTSTRAP_STARTUP_JSON)
    if raw is None:
        logger.warning(
            "Could not read %s; skipping bind-mount removal",
            BOOTSTRAP_STARTUP_JSON,
        )
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(
            "Bootstrap %s is not valid JSON: %s", BOOTSTRAP_STARTUP_JSON, e
        )
        return

    binds = _binds_dict_or_none(data)
    if binds is None:
        logger.warning(
            "Bootstrap %s does not contain a 'core.binds' dict; bootstrap "
            "schema may have migrated. Skipping bind removal to avoid "
            "corrupting the file with an unrecognised top-level key.",
            BOOTSTRAP_STARTUP_JSON,
        )
        return

    if WIFI_OVERRIDE_HOST_PATH not in binds:
        logger.info(
            "No wifi-override bind in %s; nothing to remove",
            BOOTSTRAP_STARTUP_JSON,
        )
        return

    del binds[WIFI_OVERRIDE_HOST_PATH]
    new_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if await _write_host_file(BOOTSTRAP_STARTUP_JSON, new_content):
        logger.info(
            "Removed wifi-override bind from %s; restart blueos-core to "
            "drop the stale mount",
            BOOTSTRAP_STARTUP_JSON,
        )


async def _ensure_startup_bind() -> None:
    """Add the wifi-override bind to ``startup.json`` if not already present.

    ``blueos-bootstrap`` reads this file on every boot and uses it to build
    the ``docker run`` arguments for ``blueos-core``. The new entry takes
    effect at the next reboot (or after manually restarting
    ``blueos-bootstrap``); we do not auto-restart anything to avoid an
    unexpected outage.

    Refuses to install when the on-disk schema doesn't match the
    shape we know how to safely modify (``core.binds`` is a dict).
    The previous implementation used :func:`dict.setdefault` to
    silently create those keys if missing - which would survive a
    schema migration that renamed ``core`` (or restructured ``binds``)
    but would write our entry into a section bootstrap never reads,
    so the bind would never become active and we'd have no signal.
    The explicit guard surfaces a migration via a loud warning, then
    leaves the file untouched.
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

    binds = _binds_dict_or_none(data)
    if binds is None:
        logger.warning(
            "Bootstrap %s does not contain a 'core.binds' dict; bootstrap "
            "schema may have migrated. Skipping bind install rather than "
            "creating a parallel orphan key bootstrap won't read.",
            BOOTSTRAP_STARTUP_JSON,
        )
        return

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


async def _ensure_usb_max_current() -> bool:
    """Stage ``usb_max_current_enable=1`` in the Pi 5 boot config.

    Lifts the total USB current budget from 600 mA to 1600 mA so the
    Realtek AP dongle and USB storage can draw their declared maximum
    without tripping the rail. See "Part 3" in the module docstring for
    the over-current failure this prevents.

    Returns ``True`` when the setting is active or staged, ``False``
    when we skipped or could not apply it. Nothing is gated on the
    result - a vehicle with the stock budget still boots and still
    brings up an AP, it is just liable to lose the dongle under load.

    **Only takes effect on the next reboot.** Firmware reads
    ``config.txt`` at boot and there is no runtime equivalent, so this
    stages the change and logs it, consistent with how the udev rename
    and the bind-mounted wifi override are handled.

    Append-only by design, and this is the important part. Every other
    host file in this module goes through :func:`_write_host_file`,
    which does a read-modify-rewrite of the whole file. That is fine
    for files we own outright and can regenerate from constants, but
    ``config.txt`` is neither: it is owned by the OS image, carries
    board-specific overlay and GPIO configuration we did not write, and
    a truncated or mis-decoded rewrite leaves a Pi that will not boot -
    recoverable only by pulling the card. :func:`_read_host_file` routes
    file contents through Commander, whose ``repr()``-encoded transport
    has silently corrupted content before (see
    :func:`_decode_commander_field`, which exists because of exactly
    that bug). Appending via ``tee -a`` keeps the existing bytes
    untouched no matter how badly a read goes, so the worst case is a
    duplicated stanza rather than an unbootable vehicle.

    Guards, in order: skip non-Pi-5 boards, where the setting is
    meaningless; short-circuit if the firmware already reports the
    raised budget; leave the file alone if *any*
    ``usb_max_current_enable`` line already exists, including
    ``=0``, since an explicit operator choice should not be silently
    overridden and a second line would make the effective value depend
    on parse order; and refuse to touch the file at all if the one-time
    backup cannot be taken.
    """
    ok, model = await _run_host_command(
        "cat /proc/device-tree/model 2>/dev/null | tr -d '\\0'"
    )
    if not ok or "Raspberry Pi 5" not in model:
        logger.info(
            "USB current limit: board reports %r, not a Pi 5 - skipping",
            model.strip() or "unknown",
        )
        return False

    ok, active = await _run_host_command(
        "vcgencmd get_config usb_max_current_enable 2>/dev/null"
    )
    if ok and USB_MAX_CURRENT_SETTING in active:
        logger.info("USB rail already running at the raised 1600 mA budget")
        return True

    ok, declared = await _run_host_command(
        f"grep -n '^[[:space:]]*usb_max_current_enable' {BOOT_CONFIG_PATH}"
        " 2>/dev/null; true"
    )
    if ok and declared.strip():
        logger.info(
            "USB current limit already declared in %s (%s); reboot to activate",
            BOOT_CONFIG_PATH,
            "; ".join(declared.split()),
        )
        return True

    ok, _ = await _run_host_command(
        f"test -f {BOOT_CONFIG_BACKUP_PATH} ||"
        f" sudo cp -p {BOOT_CONFIG_PATH} {BOOT_CONFIG_BACKUP_PATH}"
    )
    if not ok:
        logger.warning(
            "Could not back up %s; refusing to modify the boot config",
            BOOT_CONFIG_PATH,
        )
        return False

    block = "\\n".join(USB_MAX_CURRENT_BLOCK_LINES) + "\\n"
    ok, _ = await _run_host_command(
        f"printf '{block}' | sudo tee -a {BOOT_CONFIG_PATH} >/dev/null"
    )
    if not ok:
        logger.warning(
            "Failed to append %s to %s (backup at %s)",
            USB_MAX_CURRENT_SETTING, BOOT_CONFIG_PATH, BOOT_CONFIG_BACKUP_PATH,
        )
        return False

    ok, count = await _run_host_command(
        f"grep -c '^{USB_MAX_CURRENT_SETTING}$' {BOOT_CONFIG_PATH} 2>/dev/null; true"
    )
    if not ok or count.strip() != "1":
        logger.warning(
            "Post-append check of %s expected exactly one %r line but counted "
            "%r; the boot config may now be inconsistent. Backup is at %s.",
            BOOT_CONFIG_PATH,
            USB_MAX_CURRENT_SETTING,
            count.strip(),
            BOOT_CONFIG_BACKUP_PATH,
        )
        return False

    logger.info(
        "Raised USB rail budget to 1600 mA in %s; reboot to activate "
        "(backup at %s)",
        BOOT_CONFIG_PATH,
        BOOT_CONFIG_BACKUP_PATH,
    )
    return True


async def setup_hotspot_radio() -> None:
    """Install the host-side config that pins ``uap0`` to the USB Realtek
    *and* makes the AP come up on 2.4 GHz HT20 with the full set of HT
    capabilities the radio supports.

    Also raises the Pi 5 USB current budget so the dongle cannot be
    dropped off the bus by an over-current trip; see
    :func:`_ensure_usb_max_current`.

    Idempotent: writes are skipped where existing host content already
    matches. The udev rename, the bind-mounted wifi override and the USB
    current budget all only take full effect on the next reboot, so this
    function never restarts services or kicks the running hotspot - it
    just stages everything for the next boot. The running AP keeps
    working at default settings in the meantime.
    """
    # Runs first and unconditionally: it is independent of the hotspot
    # config below, and the early return on write failure must not skip
    # it.
    await _ensure_usb_max_current()

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

    # Ordering contract: only add (or re-add) the bind entry in
    # startup.json AFTER we've confirmed the host override file is in
    # place and patched. Otherwise we re-arm exactly the May 2026
    # factory-revert trap that PR #18 closed - the self-heal path
    # inside _install_create_ap_speed_patch removes the bind entry
    # when it has to delete a corrupt override, and an unconditional
    # _ensure_startup_bind() right after would silently put it back,
    # leaving startup.json pointing at a missing source on next boot.
    patch_ok = await _install_create_ap_speed_patch()
    if patch_ok:
        await _ensure_startup_bind()
    else:
        logger.warning(
            "create_ap speed patch did not install this run; skipping "
            "startup.json bind install to avoid pointing a bind-mount at a "
            "missing or un-patched host file (would trigger Docker to auto-"
            "create a directory at the source path on next blueos-core "
            "restart, then 'not a directory' container init failure, then "
            "blueos-bootstrap factory revert)."
        )

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
