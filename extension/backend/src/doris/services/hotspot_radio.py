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

    "-c", "<auto-picked: 1, 6 or 11>",
    "--ieee80211n",
    "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40][LDPC][RX-STBC1]"
                  "[MAX-AMSDU-7935][DSSS_CCK-40]",
    "--country", "US",

right before the SSID/password arguments. The patched module is written
to ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` on the host
and bind-mounted into the container by adding an entry to
``/root/.config/blueos/bootstrap/startup.json``. ``blueos-bootstrap``
re-reads ``startup.json`` on every boot, so the override survives
``blueos-core`` recreations and BlueOS upgrades that don't touch the
target file.

Even though the ht_capab line asks for ``[HT40+]``, hostapd is allowed
(and required by 802.11n on 2.4 GHz) to fall back to HT20 if its OBSS
coexistence scan finds neighbouring BSSes overlapping the would-be 40
MHz band - this gives us "try HT40, fall back to HT20" for free. In
practice on the bundled ``88x2bu`` driver HT40 never actually sticks on
2.4 GHz (see deferred-work note below), so the asked-for HT40 always
downgrades and we run HT20-with-full-caps - still about 3x stock
throughput because the rich ``ht_capab`` flags add SHORT-GI, LDPC,
RX-STBC, MAX-AMSDU and DSSS/CCK aggregation on top of the bare HT20
``wifi-manager`` ships.

Channel auto-selection
~~~~~~~~~~~~~~~~~~~~~~
At patch-install time :func:`_pick_24ghz_channel` reads the kernel's
cached ``iw dev wlan0 scan dump`` (refreshed every few seconds by
``wifi-manager``'s ``_autoscan``), scores each of the three non-
overlapping 2.4 GHz primaries (1, 6, 11) by the total linear-power
signal of neighbour BSSes inside its HT20 footprint, and picks the
quietest. Ties (or an empty scan) default to channel 6. The picker
runs each time :func:`setup_hotspot_radio` runs, so a vehicle that
boots in a new RF environment ends up on a sensible channel without
manual intervention.

Measurements (channel 6, this radio + AC1200 Techkey antenna)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Stock ``wifi-manager`` (no ``ht_capab``):     ~25-30 Mbps real TCP.
This patch (HT20 + full caps, channel 6):     ~82 Mbps down / 77 Mbps up
(``iperf3 -c 192.168.42.1 -p 5201 -t 20 -P 4``, May 2026, indoor
2-3 m, 1 AP neighbour on ch 5). PHY ceiling for 2-stream HT20 + SGI
is 144 Mbps and real-world TCP typically caps at 55-65% of PHY, so
~80 Mbps is at the achievable ceiling - the link is clean, the
remaining headroom would require a driver that can keep HT40 alive.

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

The 5 GHz attempt has since been replaced by the 2.4 GHz HT20-with-
full-caps patch described above (:func:`_install_create_ap_speed_patch`)
which delivers ~3x stock throughput on the same driver and *does* keep
the AP stable. The findings here are preserved for whenever the driver
is updated or a different 5 GHz-capable radio is dropped in.

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

Result: hotspot broadcast from the Realtek's external antenna at 2.4
GHz HT20 with the full set of HT capabilities the radio supports;
~80 Mbps real-world TCP throughput, ~3x what stock ``wifi-manager``
delivers. Onboard Broadcom is STA-only on ``wlan0``; in our testing
its retransmit count dropped from ~14,900/min to ~30/min.

Note on ``sudo``: the BlueOS Commander API's shell PATH does not include
``/sbin`` or ``/usr/sbin``, so ``udevadm`` and friends are only reachable
through ``sudo`` (which resets the PATH).
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
_CREATE_AP_ANCHOR = (
    '            "--redirect-to-localhost",'
    "  # Redirect all traffic to localhost, captive-portal style\n"
)

# Maxed-out 2.4 GHz HT20 configuration. The ``{channel}`` placeholder is
# filled at install time by :func:`_pick_24ghz_channel` so the AP lands
# on whichever of channels 1/6/11 is least crowded.
#
# Why HT20 and not HT40?  The ``morrownr/88x2bu-20210702`` driver
# advertises HT40 in ``iw phy phy1 info`` but its AP-mode firmware path
# silently downgrades any HT40 BSS to HT20.  Confirmed on May 2026 with
# a deliberately-cleaned RF environment (test owner moved all 2.4 GHz
# neighbours out of ch 11 HT40-'s affected band of 2442-2482 MHz, then
# hot-deployed ``-c 11 --ht_capab [HT40-]`` straight into the live
# wifi-manager).  hostapd reached ``state=ENABLED`` cleanly but reported
# ``secondary_channel=0`` and ``iw dev uap0 info`` showed
# ``width: 20 MHz`` - i.e. the downgrade is happening below hostapd's
# OBSS coexistence check, in the driver itself.  Same class of issue as
# the 5 GHz failure documented below.
#
# We still ask for ``[HT40+]`` in the ht_capab line because (a) it
# advertises the wider capability to clients that might benefit if a
# future driver fixes this, and (b) hostapd's standard "try HT40, fall
# back to HT20 if OBSS coexistence scan finds neighbours" logic remains
# in effect for free.  The other capabilities (LDPC, both SGI rates,
# RX-STBC1, MAX-AMSDU-7935, DSSS/CCK in HT40) all stick at HT20 and
# lift real TCP throughput from ~25 Mbps (stock) to ~80 Mbps.
_CREATE_AP_24GHZ_FLAGS_TEMPLATE = (
    '            "-c", "{channel}",\n'
    '            "--ieee80211n",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40]'
    '[LDPC][RX-STBC1][MAX-AMSDU-7935][DSSS_CCK-40]",\n'
    '            "--country", "US",\n'
)

# Matches *any* DORIS create_ap insertion that may already follow
# ``_CREATE_AP_ANCHOR`` - either the legacy 5 GHz attempt (with
# ``--freq-band 5`` and ``--vht_capab``) or the current 2.4 GHz patch
# with any auto-picked channel. Used at install time so re-running the
# patch on an upgraded install converges to the current canonical block
# instead of stacking flags. Anchored via ``re.match`` from the position
# right after ``_CREATE_AP_ANCHOR``, never used as a free search, so
# false positives outside our managed region are impossible.
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


async def _pick_24ghz_channel() -> int:
    """Pick the cleanest 2.4 GHz primary channel from {1, 6, 11}.

    Uses the kernel's cached scan results on ``wlan0`` (``wifi-manager``
    refreshes them every ~10 s via ``_autoscan``) to score each
    candidate channel by total neighbour-BSS signal power inside its
    HT20 footprint (primary ± 2 channels). Returns the candidate with
    the lowest score; ties and an empty scan default to channel 6
    (centre of band, conventionally cleanest).

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
    # then 1, then 11.  We include the candidate's list index in the sort
    # tuple so that equal scores pick the earlier-listed channel.
    candidates = [6, 1, 11]
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
    follows :data:`_CREATE_AP_ANCHOR`. No-op if no such block is present.

    Anchored to the position right after the anchor line so we can never
    accidentally strip something else that happens to look like our
    flags elsewhere in the file.
    """
    anchor_idx = source.find(_CREATE_AP_ANCHOR)
    if anchor_idx < 0:
        return source
    end_of_anchor = anchor_idx + len(_CREATE_AP_ANCHOR)
    m = _DORIS_PATCH_BLOCK_RE.match(source, end_of_anchor)
    if not m:
        return source
    return source[:end_of_anchor] + source[m.end():]


async def _install_create_ap_speed_patch() -> None:
    """Patch wifi-manager's ``create_ap`` invocation for max 2.4 GHz HT20.

    Reads the in-container ``networkmanager.py``, picks the cleanest
    2.4 GHz primary via :func:`_pick_24ghz_channel`, inserts the
    HT20-with-full-caps flags after the ``--redirect-to-localhost``
    anchor, and writes the result to
    ``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` so the
    bind mount in ``startup.json`` picks it up on the next
    ``blueos-core`` start.

    Upgrade-safe and re-run-safe: any prior DORIS patch (legacy 5 GHz
    attempt, or current 2.4 GHz patch with a different auto-picked
    channel) is stripped first via :func:`_strip_doris_patch`, then
    the current canonical block is applied. :func:`_write_host_file`
    short-circuits when the host file already matches.
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
        return

    if _CREATE_AP_ANCHOR not in source:
        logger.warning(
            "create_ap anchor not found in %s; BlueOS upstream may have "
            "changed - skipping create_ap speed patch (hotspot will still "
            "work, at default 2.4 GHz / 802.11g speeds)",
            WIFI_OVERRIDE_CONTAINER_PATH,
        )
        return

    stripped = _strip_doris_patch(source)
    channel = await _pick_24ghz_channel()
    flags = _CREATE_AP_24GHZ_FLAGS_TEMPLATE.format(channel=channel)
    patched = stripped.replace(
        _CREATE_AP_ANCHOR, _CREATE_AP_ANCHOR + flags, 1
    )

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
    *and* makes the AP come up on 2.4 GHz HT20 with the full set of HT
    capabilities the radio supports.

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

    await _install_create_ap_speed_patch()
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
