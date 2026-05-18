"""Pin the BlueOS hotspot to any supported USB Wi-Fi radio at full speed.

Why this exists
---------------
On a Pi 5 with the onboard Broadcom radio + an external USB Wi-Fi
dongle, out of the box BlueOS:

  1. **Layers the hotspot on the wrong radio.** ``wifi-manager`` creates a
     virtual ``__ap`` interface called ``uap0`` *on top of* the onboard
     Broadcom radio (``brcmfmac`` supports concurrent STA + AP), so the
     Broadcom chip is forced to be both client and AP at the same time and
     the USB Wi-Fi adapter (with its external antenna) sits idle.

  2. **Brings the AP up at 802.11g / 2.4 GHz / channel 1**, which caps real
     throughput around 15-25 Mbps even on hardware capable of much more.

This module fixes both. We can't simply re-parent ``uap0`` onto the
USB radio for every chipset - some drivers (notably ``rtl88x2bu``)
don't implement ``iw dev <X> interface add testap type __ap`` at all,
others (MediaTek mt76 family) allow it on paper but the firmware
rejects two AP-capable ifaces on the same phy. So instead:

Supported chipsets
------------------
The dispatch is table-driven (see :data:`SUPPORTED_HOTSPOT_RADIOS`).
Each row pairs a USB ``vendor:product`` ID with the chipset's
hostapd-safe ``ht_capab`` set and any extra ``create_ap`` flags it
needs (e.g. ``--ieee80211ax`` for HE-capable MT7921U). Currently:

  * **Realtek RTL88x2BU** (``0bda:b812``) - the dongle DORIS originally
    shipped, driven via the out-of-tree morrownr/88x2bu module (the
    in-kernel ``rtw88_8822bu`` is not stable enough on the Pi 5 USB
    controllers for AP mode). HT20 only (see "Why HT20 on Realtek but
    not on others" below). The :mod:`doris.services.wifi_driver`
    bootstrap only runs the morrownr install when this chip is on
    the bus.
  * **MediaTek MT7612U** (``0e8d:7612``) - in-kernel ``mt76x2u``,
    2x2 802.11ac on a single chip. HT40 works in AP mode without
    the firmware-path issue Realtek has.
  * **MediaTek MT7921AU** (``0e8d:7961``) - in-kernel ``mt7921u``,
    Wi-Fi 6 with ``HE Iftypes: AP`` on 2.4 GHz. Needs
    ``--ieee80211ax``.
  * **Atheros AR9271** (``0cf3:9271``) - in-kernel ``ath9k_htc``.
    802.11n only, but historically the most stable USB Wi-Fi AP
    chip on Linux. Useful as a known-good baseline when other USB
    chipsets misbehave on Pi USB controllers. ``ht_capab`` for
    this chip omits ``[LDPC]`` and ``[MAX-AMSDU-7935]`` because
    the driver doesn't advertise them and hostapd refuses to start
    with ``Driver does not support configured HT capability ...``.

Part 1: rename the radio to ``uap0`` via udev
----------------------------------------------
BlueOS's ``_create_virtual_interface()`` short-circuits if ``uap0``
already exists at startup. We make it exist *before* BlueOS sees it
by renaming whichever supported USB Wi-Fi adapter is plugged in -
straight from the kernel, by matching every supported
``idVendor:idProduct`` pair:

    SUBSYSTEM=="net", ACTION=="add", ATTRS{idVendor}=="0bda",
        ATTRS{idProduct}=="b812", NAME="uap0"          # Realtek
    SUBSYSTEM=="net", ACTION=="add", ATTRS{idVendor}=="0e8d",
        ATTRS{idProduct}=="7612", NAME="uap0"          # MediaTek MT7612U
    ... and so on for every entry in :data:`SUPPORTED_HOTSPOT_RADIOS`.

We also tell NetworkManager to leave ``uap0`` alone so hostapd /
``create_ap`` can drive it:

    [keyfile]
    unmanaged-devices=interface-name:uap0

Part 2: tell ``create_ap`` to max out 2.4 GHz with chip-tuned flags
-------------------------------------------------------------------
``wifi-manager`` invokes ``create_ap`` with no band/channel/HT/VHT flags,
so hostapd defaults to ``hw_mode=g, channel=1`` with no HT capabilities
advertised - real-world TCP throughput tops out around 25 Mbps in that
mode even on hardware capable of much more. We patch the wifi-handler
module inside ``blueos-core`` to add a chip-tuned argv block right
before the SSID/password arguments. The exact flags depend on which
USB Wi-Fi chip is on the bus (see :func:`_detect_hotspot_radio` for
matching against :data:`SUPPORTED_HOTSPOT_RADIOS`); a worked Realtek
example is::

    "-c", "<auto-picked: 1 or 6>",
    "--no-virt",
    "--ieee80211n",
    "--ht_capab", "[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",
    "--country", "<from system regdomain, fallback US>",

and an MT7921U (Wi-Fi 6) example with HE40 enabled::

    "-c", "<auto-picked: 1 or 6>",
    "--no-virt",
    "--ieee80211n",
    "--ieee80211ax",
    "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40][LDPC]"
                  "[TX-STBC][RX-STBC1][MAX-AMSDU-7935]",
    "--country", "<from system regdomain, fallback US>",

The patched module is written to
``/usr/blueos/userdata/wifi-overrides/networkmanager.py`` on the host
and bind-mounted into the container by adding an entry to
``/root/.config/blueos/bootstrap/startup.json``. ``blueos-bootstrap``
re-reads ``startup.json`` on every boot, so the override survives
``blueos-core`` recreations and BlueOS upgrades that don't touch the
target file.

Why HT20 on Realtek but HT40+ on every other supported chipset
``````````````````````````````````````````````````````````````
The :data:`SUPPORTED_HOTSPOT_RADIOS` table picks the appropriate
``ht_capab`` per chip. For Realtek RTL88x2BU the value is HT20-only
on purpose: the bundled ``morrownr/88x2bu-20210702`` driver's
AP-mode firmware path cannot keep an HT40 BSS up. In a busy 2.4 GHz
environment hostapd's OBSS scan downgrades to HT20 before the driver
ever sees the request and things look fine, but in a clean RF
environment hostapd actually issues the HT40 BSS request, the
driver rejects it, and the BSS cycles ``INTERFACE-ENABLED`` →
``INTERFACE-DISABLED`` (end-user symptom: "AP shows up, you connect,
it disappears, you lose connection"; ``iw dev uap0 info`` shows
``txpower -100.00 dBm`` and no ``channel/width/center`` line).
Confirmed in field reports May 2026 on a unit with no 2.4 GHz
neighbours on the picker-chosen channel. Asking for HT20 directly
removes the failure mode at the cost of nothing - the driver was
downgrading to HT20 every time it actually completed a BSS
bring-up anyway. The HT20-valid caps we keep (LDPC, SHORT-GI-20,
RX-STBC1, MAX-AMSDU-7935) still lift real TCP throughput from
~25 Mbps (stock) to ~80 Mbps on this radio.

MediaTek (mt76 family) and Atheros (ath9k_htc) don't have this
problem: their AP-mode firmware/driver paths bring HT40 BSSes up
cleanly, so their rows in the table keep ``[HT40+]`` and the
HT40-specific companion caps the driver claims in ``iw phy``.

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
from dataclasses import dataclass

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Supported USB Wi-Fi radios.
#
# Each entry describes one chipset we can drive as the hotspot AP. The
# table is the single source of truth for:
#
#   * udev: which USB ``idVendor:idProduct`` pairs get renamed to ``uap0``
#     (see :data:`UDEV_RULE_CONTENT`).
#   * runtime detection: :func:`_detect_hotspot_radio` reads ``lsusb`` on
#     the host and returns the matching entry (or ``None`` for an
#     unrecognised chip).
#   * ``create_ap`` argv: the per-chip ``ht_capab`` string and any extra
#     argv tokens (e.g. ``--ieee80211ax``) get baked into the patched
#     ``networkmanager.py`` so hostapd starts cleanly on whichever radio
#     is actually plugged in.
#   * out-of-tree-driver install: :mod:`doris.services.wifi_driver` only
#     runs the morrownr/88x2bu install when an entry with
#     ``needs_out_of_tree_driver=True`` is currently present on the bus.
#
# Why one table and not 4 parallel branches sprinkled around the file:
# the previous Realtek-only version had the chip's quirks (LDPC support,
# MAX-AMSDU=7935, "AP-mode firmware can't keep HT40 up") baked into
# constants and module text. Adding a second chipset that way meant
# duplicating every constant and every comment, and the third chipset
# would have been intractable. With the table, supporting a new chipset
# is just an extra row.
#
# Why per-chip ``ht_capab`` strings instead of one "universal" string:
# hostapd hard-fails (``Driver does not support configured HT capability
# [LDPC]``) on capabilities the driver doesn't advertise. So an
# ``ht_capab`` set tuned for Realtek (``[LDPC][MAX-AMSDU-7935]``)
# breaks Atheros AR9271 outright (driver doesn't claim LDPC, max AMSDU
# is 3839 not 7935), and an AR9271-safe set under-uses the Realtek. Per
# chip is the only correct shape.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class HotspotRadio:
    """One row in the supported-USB-Wi-Fi-radio table.

    Attributes:
        label: Human-readable name, used in logs only.
        vendor_id: USB ``idVendor`` in lowercase 4-hex form.
        product_id: USB ``idProduct`` in lowercase 4-hex form.
        kernel_module: Name of the kernel module that binds to the
            device (informational; used for log messages).
        needs_out_of_tree_driver: ``True`` only for the Realtek
            RTL88x2BU, which has no usable in-kernel driver on the
            Pi 5 kernels we ship - :mod:`doris.services.wifi_driver`
            checks this flag at startup and only installs
            ``morrownr/88x2bu`` when a chip with this flag is on the
            bus. Every other entry should be ``False`` (their drivers
            are mainline and already in the kernel).
        is_mt76_family: ``True`` for chips driven by the ``mt76`` USB
            stack (MT7612U, MT7921U). When set, we install
            ``options mt76_usb disable_usb_sg=1`` via modprobe.d - it
            mitigates ``mt76x02u_mcu_wait_resp failed with -110`` MCU
            timeouts that the morrownr/USB-WiFi project has documented
            under sustained USB load (especially on Pi 4 VL805 ports,
            harmless on Pi 5).
        ht_capab: Exact value passed to ``create_ap --ht_capab`` for
            this chip. *Only* include capabilities the driver
            advertises in ``iw phy phy<N> info`` - hostapd refuses to
            start if any are unsupported. HT40 caps are kept only on
            chips whose AP-mode firmware path can actually sustain a
            40 MHz BSS (excludes morrownr 88x2bu, see module
            docstring).
        extra_create_ap_argv_tokens: Bare argv tokens (without value)
            inserted between ``--ieee80211n`` and ``--ht_capab``. Use
            ``("--ieee80211ax",)`` for chips with HE/802.11ax AP
            support (MT7921U), empty for n-only chips. ``--ieee80211ax``
            requires ``--ieee80211n`` to also be set, which is always
            emitted - upstream ``create_ap`` only writes
            ``ieee80211n=1`` + ``ht_capab=`` into hostapd.conf when
            ``IEEE80211N=1``, so the ``ht_capab`` line is silently
            dropped without it.
    """

    label: str
    vendor_id: str
    product_id: str
    kernel_module: str
    needs_out_of_tree_driver: bool
    is_mt76_family: bool
    ht_capab: str
    extra_create_ap_argv_tokens: tuple[str, ...] = ()


# Order in the tuple is iteration order in ``lsusb`` matching: if a
# host had two supported chips on the bus we'd take the first one
# listed here. In practice DORIS only has one external USB port for
# the hotspot radio so this never matters, but the deterministic
# order keeps the udev rule output stable across regenerations.
SUPPORTED_HOTSPOT_RADIOS: tuple[HotspotRadio, ...] = (
    # Realtek RTL88x2BU (AC1200) - the dongle DORIS originally shipped.
    # Drives via the out-of-tree morrownr/88x2bu module bundled in the
    # extension. The driver's AP-mode firmware path cannot keep an
    # HT40 BSS up on this chip (May 2026 field report), so the
    # ``ht_capab`` here is HT20-only - see the module docstring's
    # "Why HT20 explicitly and not HT40 on Realtek" section for the
    # full diagnosis. The HT20 caps we keep still lift real TCP
    # throughput from ~25 Mbps (stock hostapd defaults) to ~80 Mbps.
    HotspotRadio(
        label="Realtek RTL88x2BU",
        vendor_id="0bda",
        product_id="b812",
        kernel_module="88x2bu",
        needs_out_of_tree_driver=True,
        is_mt76_family=False,
        ht_capab="[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",
        extra_create_ap_argv_tokens=(),
    ),
    # MediaTek MT7612U (AC1200) - in-kernel ``mt76x2u``. Common 2x2
    # USB module (e.g. Alfa AWUS036ACM, SparkLAN WUBM-273ACN). HT40
    # works cleanly in AP mode on this chip - the mt76 driver
    # consistently brings up 40 MHz BSSes without the firmware-path
    # cycling we see on morrownr 88x2bu. We advertise the full
    # HT40 cap set the chip claims in ``iw phy``.
    HotspotRadio(
        label="MediaTek MT7612U",
        vendor_id="0e8d",
        product_id="7612",
        kernel_module="mt76x2u",
        needs_out_of_tree_driver=False,
        is_mt76_family=True,
        ht_capab=(
            "[HT40+][SHORT-GI-20][SHORT-GI-40][LDPC]"
            "[RX-STBC1][MAX-AMSDU-7935][DSSS_CCK-40]"
        ),
        extra_create_ap_argv_tokens=(),
    ),
    # MediaTek MT7921AU - in-kernel ``mt7921u``. Wi-Fi 6 (HE) in AP
    # mode on 2.4 GHz (``HE Iftypes: AP`` with ``HE40/2.4GHz``).
    # Common single-chip Wi-Fi 6 USB module. ``--ieee80211ax`` is
    # required to actually emit ``ieee80211ax=1`` into hostapd.conf
    # and unlock HE rates. The ``ht_capab`` deliberately omits
    # ``[DSSS_CCK-40]`` because this chip's ``iw phy`` reports ``No
    # DSSS/CCK HT40`` and hostapd refuses to start with ``Driver does
    # not support configured HT capability [DSSS_CCK-40]`` - tested
    # and confirmed against MT7921U firmware May 2026.
    HotspotRadio(
        label="MediaTek MT7921AU",
        vendor_id="0e8d",
        product_id="7961",
        kernel_module="mt7921u",
        needs_out_of_tree_driver=False,
        is_mt76_family=True,
        ht_capab=(
            "[HT40+][SHORT-GI-20][SHORT-GI-40][LDPC]"
            "[TX-STBC][RX-STBC1][MAX-AMSDU-7935]"
        ),
        extra_create_ap_argv_tokens=("--ieee80211ax",),
    ),
    # Atheros AR9271 (Alfa AWUS036NHA and many compatible OEMs) -
    # in-kernel ``ath9k_htc``. 802.11n single-stream only (no AC, no
    # AX, no MU-MIMO). The most stable USB Wi-Fi AP chip on Linux,
    # bar none - chosen as a known-good baseline when other USB
    # chipsets misbehave on the Pi's USB controller. ``ht_capab``
    # deliberately omits ``[LDPC]`` (driver does NOT claim it -
    # tested against ath9k_htc May 2026, hostapd hard-fails with
    # ``Driver does not support configured HT capability [LDPC]``)
    # and ``[MAX-AMSDU-7935]`` (AR9271 max AMSDU is 3839 not 7935;
    # asking for 7935 also triggers the same hostapd unsupported-
    # capability hard fail). ``[DSSS_CCK-40]`` is kept because the
    # chip does claim DSSS/CCK in HT40 and it's a real throughput
    # win in mixed-rate environments.
    HotspotRadio(
        label="Atheros AR9271",
        vendor_id="0cf3",
        product_id="9271",
        kernel_module="ath9k_htc",
        needs_out_of_tree_driver=False,
        is_mt76_family=False,
        ht_capab="[HT40+][SHORT-GI-20][SHORT-GI-40][RX-STBC1][DSSS_CCK-40]",
        extra_create_ap_argv_tokens=(),
    ),
)


# Conservative HT20 set used when ``lsusb`` detection fails or returns
# an unrecognised chip. Both capabilities are advertised by every
# 802.11n driver we've checked (Realtek, MediaTek, Atheros, Broadcom),
# so this lets the AP at least come up with HT20 + SGI-20 instead of
# falling back to plain ``hw_mode=g`` (~25 Mbps stock). Real-world
# units shouldn't hit this path - it's a belt-and-suspenders fallback
# for "we know the rest of the patch wants to apply, we just can't
# tell *which* chip".
_FALLBACK_HT_CAPAB = "[SHORT-GI-20][RX-STBC1]"


# Back-compat aliases for the Realtek IDs - some older call sites and
# tests import ``USB_VENDOR_ID`` / ``USB_PRODUCT_ID`` by name. New code
# should iterate over :data:`SUPPORTED_HOTSPOT_RADIOS` instead.
USB_VENDOR_ID = SUPPORTED_HOTSPOT_RADIOS[0].vendor_id
USB_PRODUCT_ID = SUPPORTED_HOTSPOT_RADIOS[0].product_id

UDEV_RULE_PATH = "/etc/udev/rules.d/72-blueos-hotspot.rules"
NM_CONF_PATH = "/etc/NetworkManager/conf.d/99-blueos-hotspot-uap0.conf"
MT76_MODPROBE_PATH = "/etc/modprobe.d/blueos-mt76.conf"

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

# How we build the create_ap argv flags we splice in after the
# ``--redirect-to-localhost`` anchor. Flag order in the generated
# block, from top to bottom:
#
#   "-c", "<auto-picked: 1 or 6>",   # always present
#   "--no-virt",                     # always present (universal-safe)
#   "--ieee80211n",                  # always present
#   "--ieee80211ax",                 # only for HE-capable chips (MT7921U)
#   "--ht_capab", "<per-chip>",      # always present, value per-chip
#   "--country", "<live regdomain>", # always present
#
# Why ``--no-virt`` is universal-safe (and required on some chips):
#
#   * **MediaTek MT7921U** rejects ``create_ap``'s default two-iface
#     dance outright. Its interface combination matrix only allows
#     ``#{ AP, P2P-GO } <= 1`` per phy, so when ``create_ap`` tries
#     to add a virtual ``__ap0`` on top of the existing managed
#     ``uap0`` the firmware refuses with ``Maybe your WiFi adapter
#     does not fully support virtual interfaces`` and the AP never
#     starts.
#   * **MediaTek MT7612U** is in the same family; its driver also
#     refuses concurrent managed+AP on one phy.
#   * **Realtek RTL88x2BU** ``rtl88x2bu`` famously does not implement
#     ``iw dev <X> interface add testap type __ap`` at all (returns
#     ``-ENODEV``). We rename ``wlan1 -> uap0`` via udev so the
#     first-iface-already-exists fast path is taken in ``wifi-manager``,
#     but ``--no-virt`` is an additional safety net.
#   * **Atheros AR9271** ``ath9k_htc`` does support virtual ifaces,
#     so ``--no-virt`` here is a no-op rather than a fix - included
#     for uniformity.
#
# Why ``--ieee80211n`` AND ``--ieee80211ax`` for HE chips:
# upstream ``create_ap`` only emits ``ieee80211n=1`` + ``ht_capab=...``
# into ``hostapd.conf`` when ``IEEE80211N=1``. Without
# ``--ieee80211n`` hostapd silently falls back to ``hw_mode=g`` and
# ignores the ``--ht_capab`` line entirely, so we always emit n.
#
# Why ``--country`` is sourced live (not hardcoded ``US``): hostapd
# clamps TX power to 20 dBm and refuses 40 MHz channel widths on
# 2.4 GHz when no country code is set, both of which silently undo
# other parts of this patch. See :func:`_get_country_code`.
#
# ``{indent}`` is filled with the whitespace captured by
# :data:`_CREATE_AP_ANCHOR_RE` so the inserted lines line up with
# the surrounding argv entries regardless of upstream indentation
# choice (BlueOS reformat passes have changed it in the past).
def _build_create_ap_flags(
    *,
    indent: str,
    channel: int,
    country: str,
    radio: HotspotRadio | None,
) -> str:
    """Return the indented create_ap argv-flag block for *radio*.

    Pass *radio*\\ =\\ ``None`` when :func:`_detect_hotspot_radio` could
    not match the bus contents to any entry in
    :data:`SUPPORTED_HOTSPOT_RADIOS`. In that case we emit the
    conservative HT20 fallback - the AP still comes up, just at lower
    throughput than a chip-tuned profile would deliver. See
    :data:`_FALLBACK_HT_CAPAB` for the rationale.
    """
    ht_capab = radio.ht_capab if radio is not None else _FALLBACK_HT_CAPAB
    extra_tokens: tuple[str, ...] = (
        radio.extra_create_ap_argv_tokens if radio is not None else ()
    )
    lines = [
        f'{indent}"-c", "{channel}",\n',
        f'{indent}"--no-virt",\n',
        f'{indent}"--ieee80211n",\n',
    ]
    for token in extra_tokens:
        lines.append(f'{indent}"{token}",\n')
    lines.append(f'{indent}"--ht_capab", "{ht_capab}",\n')
    lines.append(f'{indent}"--country", "{country}",\n')
    return "".join(lines)


# Matches *any* DORIS create_ap insertion that may already follow
# the anchor matched by :data:`_CREATE_AP_ANCHOR_RE`. The regex is
# anchored via ``re.match`` from the position right after the
# anchor match, never used as a free search, so false positives
# outside our managed region are impossible.
#
# Recognised historical block shapes (every supported lineage must
# match here or re-running the patch on an upgrade stacks flags
# instead of replacing the prior block):
#
#   1. **Legacy 5 GHz attempt** (pre bh-0.4.x):
#      ``--freq-band 5 / -c / --ieee80211n / --ieee80211ac /
#      --ht_capab / --vht_capab / --country``
#   2. **Realtek HT20-only** (bh-0.4.x shipping):
#      ``-c / --ieee80211n / --ht_capab / --country``
#   3. **Current multi-chip** (this branch):
#      ``-c / --no-virt / --ieee80211n / [--ieee80211ax] /
#      --ht_capab / --country``
#
# Each line that's only in some lineages is wrapped in ``(?:...)?``;
# lines required across every lineage stay required.
_DORIS_PATCH_BLOCK_RE = re.compile(
    r'(?:[ \t]*"--freq-band", "[0-9]+",\n)?'        # legacy 5 GHz only
    r'[ \t]*"-c", "[0-9]+",\n'                      # always present
    r'(?:[ \t]*"--no-virt",\n)?'                    # current multi-chip only
    r'[ \t]*"--ieee80211n",\n'                      # always present
    r'(?:[ \t]*"--ieee80211ac",\n)?'                # legacy 5 GHz only
    r'(?:[ \t]*"--ieee80211ax",\n)?'                # MT7921U only
    r'[ \t]*"--ht_capab", "[^"]+",\n'               # always present
    r'(?:[ \t]*"--vht_capab", "[^"]+",\n)?'         # legacy 5 GHz only
    r'[ \t]*"--country", "[A-Z]+",\n'               # always present
)

def _build_udev_rule_content() -> str:
    """Build the udev-rule file content from :data:`SUPPORTED_HOTSPOT_RADIOS`.

    Emits one ``SUBSYSTEM=="net" ... NAME="uap0"`` line per supported
    chipset, so plugging in any of the recognised USB Wi-Fi adapters
    causes the kernel to name its netdev ``uap0`` straight from the
    add event - before NetworkManager or BlueOS sees it. ``wifi-
    manager``'s ``_create_virtual_interface()`` short-circuits when
    ``uap0`` already exists, which is exactly what we want: no
    virtual-iface dance on top of a different radio, no chance of
    parenting the AP on the onboard Broadcom.

    Generated content is deterministic for a given table so re-runs
    of :func:`setup_hotspot_radio` short-circuit cleanly in
    :func:`_write_host_file`'s content-match check.
    """
    header = (
        "# Managed by DORIS extension (services/hotspot_radio.py).\n"
        "# Rename any supported USB Wi-Fi adapter's net device to uap0 so\n"
        "# BlueOS uses it as the hotspot AP without trying to create a virtual\n"
        "# __ap interface (several supported chipset drivers do not allow\n"
        "# more than one AP-capable iface per phy).\n"
    )
    rules = "".join(
        f"# {radio.label}\n"
        f'SUBSYSTEM=="net", ACTION=="add", ATTRS{{idVendor}}=="{radio.vendor_id}",'
        f' ATTRS{{idProduct}}=="{radio.product_id}", NAME="uap0"\n'
        for radio in SUPPORTED_HOTSPOT_RADIOS
    )
    return header + rules


UDEV_RULE_CONTENT = _build_udev_rule_content()

NM_CONF_CONTENT = (
    "# Managed by DORIS extension (services/hotspot_radio.py).\n"
    "# Keep NetworkManager from grabbing uap0; hostapd / create_ap drives it.\n"
    "[keyfile]\n"
    "unmanaged-devices=interface-name:uap0\n"
)

# Install for every boot regardless of which chip is currently plugged
# in: harmless on Realtek/Atheros (no mt76_usb module loads, so the
# option is just ignored) and saves a reboot dance when swapping in an
# MT chip later. The option mitigates ``mt76x02u_mcu_wait_resp failed
# with -110`` MCU timeouts the morrownr/USB-WiFi project has documented
# under sustained USB load on certain xhci controllers (VIA VL805 on
# Pi 4, harmless on Pi 5's RP1 DWC3). See
# https://github.com/morrownr/USB-WiFi for the upstream tracking.
MT76_MODPROBE_CONTENT = (
    "# Managed by DORIS extension (services/hotspot_radio.py).\n"
    "# Disable scatter-gather USB transfers in the mt76_usb bus driver.\n"
    "# Mitigates `mt76x02u_mcu_wait_resp failed with -110` MCU timeouts on\n"
    "# MT7612U/MT7921U under sustained USB load. Harmless when no MT76\n"
    "# chip is on the bus (option is parsed only when the module loads).\n"
    "options mt76_usb disable_usb_sg=1\n"
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


# ``lsusb`` lines look like:
#   Bus 003 Device 002: ID 0bda:b812 Realtek Semiconductor Corp. RTL88x2bu ...
# We just need the ``id <V>:<P>`` token (lowercased) so we can match
# against the table. Don't try to parse the descriptive tail - vendor
# strings vary across firmware/EEPROM revisions of the same chip.
_LSUSB_ID_RE = re.compile(r"\bID\s+([0-9a-f]{4}):([0-9a-f]{4})\b", re.IGNORECASE)


async def _detect_hotspot_radio() -> HotspotRadio | None:
    """Return the :class:`HotspotRadio` matching what ``lsusb`` reports.

    Reads ``lsusb`` once on the host and walks
    :data:`SUPPORTED_HOTSPOT_RADIOS` in iteration order, returning the
    first matching entry (or ``None`` if no supported USB Wi-Fi
    adapter is on the bus, or ``lsusb`` itself failed).

    Read-only and idempotent. Callers can invoke this on every patch
    install without side effects on the live system.

    Returning ``None`` is *not* an error - it means "use the
    conservative fallback profile" in
    :func:`_build_create_ap_flags`. We deliberately do not crash or
    refuse to install the patch in that case; the worst-case shape
    of an unknown-chip patch is still better than stock
    ``wifi-manager`` defaults (``hw_mode=g`` no-HT).
    """
    ok, out = await _run_host_command("lsusb")
    if not ok or not out.strip():
        logger.warning(
            "Could not read lsusb to detect hotspot radio; "
            "falling back to conservative HT20 profile"
        )
        return None

    # Build a set of (vendor, product) pairs visible on the bus,
    # lowercased. Matching against the supported table by both
    # vendor *and* product avoids the false positive where a
    # MediaTek-vendor non-Wi-Fi device (e.g. a webcam) shares the
    # vendor ID 0x0e8d with the MT7921U entry below.
    bus_ids: set[tuple[str, str]] = set()
    for match in _LSUSB_ID_RE.finditer(out):
        bus_ids.add((match.group(1).lower(), match.group(2).lower()))

    for radio in SUPPORTED_HOTSPOT_RADIOS:
        if (radio.vendor_id, radio.product_id) in bus_ids:
            logger.info(
                "Detected hotspot radio: %s (USB %s:%s, driver %s)",
                radio.label,
                radio.vendor_id,
                radio.product_id,
                radio.kernel_module,
            )
            return radio

    logger.warning(
        "No supported USB Wi-Fi chip on the bus (lsusb showed %d ID pairs); "
        "falling back to conservative HT20 profile",
        len(bus_ids),
    )
    return None


# Reads the system regdomain so we can pass a real country code to
# create_ap; without one hostapd uses world regulatory which caps
# 2.4 GHz TX power to 20 dBm and refuses HT40 widths. ``iw reg get``
# prints one or more ``country <CC>: ...`` lines; we want the
# ``global`` block's value (first match in normal output).
_IW_REG_COUNTRY_RE = re.compile(r"^country\s+([A-Z]{2}|00):", re.MULTILINE)
_COUNTRY_CODE_FALLBACK = "US"


async def _get_country_code() -> str:
    """Return the active 2-letter country code, or ``US`` if unset.

    Reads ``iw reg get`` on the host and parses the first
    ``country <CC>:`` line. ``country 00`` is the kernel's
    "world regulatory" sentinel meaning "no country has been set" -
    we treat it the same as a failure and fall back to ``US`` because
    a country-less hostapd config silently undoes other parts of this
    patch (HT40 refused, 20 dBm TX power cap).

    Falls back to ``US`` rather than refusing to install when ``iw``
    fails or returns garbage: the speed patch is still useful with a
    sane default country, just slightly less correct than reading the
    live regdomain.
    """
    ok, out = await _run_host_command("iw reg get")
    if not ok or not out.strip():
        logger.info(
            "Could not read system regdomain via `iw reg get`; "
            "defaulting create_ap --country to %s",
            _COUNTRY_CODE_FALLBACK,
        )
        return _COUNTRY_CODE_FALLBACK

    match = _IW_REG_COUNTRY_RE.search(out)
    if match is None:
        logger.info(
            "Could not parse country code from `iw reg get` output; "
            "defaulting create_ap --country to %s",
            _COUNTRY_CODE_FALLBACK,
        )
        return _COUNTRY_CODE_FALLBACK

    code = match.group(1)
    if code == "00":
        logger.info(
            "System regdomain is `country 00` (world default, unset); "
            "defaulting create_ap --country to %s",
            _COUNTRY_CODE_FALLBACK,
        )
        return _COUNTRY_CODE_FALLBACK

    return code


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
    country = await _get_country_code()
    radio = await _detect_hotspot_radio()
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
    flags = _build_create_ap_flags(
        indent=indent, channel=channel, country=country, radio=radio
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
    # Installed unconditionally - see :data:`MT76_MODPROBE_CONTENT` for
    # why a non-MT chip is unaffected.
    mt76_ok = await _write_host_file(MT76_MODPROBE_PATH, MT76_MODPROBE_CONTENT)

    if not (rule_ok and nm_ok):
        logger.warning(
            "Hotspot-radio host config not fully written (udev=%s, NM=%s, "
            "mt76=%s); AP may stay parented on the onboard radio",
            rule_ok,
            nm_ok,
            mt76_ok,
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
