"""Network/WiFi service."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..config import blueos_services
from ..models.network import (
    ConnectionStatus,
    NetworkCredentials,
    NetworkInfo,
    WifiNetwork,
    WlanLastAttempt,
    WlanState,
)
from .base import BlueOSClient
from .blueos.network import NetworkClient
from .hotspot_radio import _run_host_command
from .storage import DATA_ROOT

logger = logging.getLogger(__name__)

AP_WATCHDOG_INTERVAL_S = 60
AP_WATCHDOG_SETTLE_S = 15

# Intent file lives under DATA_ROOT (bind-mounted /tmp/storage/userdata
# in production) so the last-attempt record survives container restarts
# and can be shown to the user when they reconnect to the hotspot after
# a failed switch attempt. The ``mode`` field is always reset to ``ap``
# on backend startup regardless of what was previously persisted.
WLAN_INTENT_FILE = DATA_ROOT / "network_intent.json"

# Total wall time we'll wait for a STA association after flipping the
# external radio out of hotspot mode. The mode change itself eats ~5-15s
# (driver re-init), then the supplicant + DHCP add another ~5-20s on a
# typical home router. 60s is a comfortable upper bound.
STA_CONNECT_TIMEOUT_S = 60.0
STA_POLL_INTERVAL_S = 2.0

# ── v1 fallback constants ────────────────────────────────────────────
#
# BlueOS WiFi Manager v1 has no per-interface mode API, so on v1 we
# bypass it: we briefly transfer ``uap0`` from create_ap (hostapd) over
# to NetworkManager + nmcli for the duration of the STA session, then
# hand it back. The unmanaged-devices conf file has to move out of the
# way for NM to be willing to drive the interface; we stash it in /tmp
# and put it back when we restore the hotspot.
V1_NM_UNMANAGED_CONF = "/etc/NetworkManager/conf.d/99-blueos-hotspot-uap0.conf"
V1_NM_UNMANAGED_STASH = "/tmp/doris-99-blueos-hotspot-uap0.conf.stash"
V1_UAP0_CONNECTION_PREFIX = "doris-uap0-sta-"
V1_DHCP_TIMEOUT_S = 30


class NetworkService:
    """Service for managing network connections via BlueOS WiFi Manager.

    Uses the unified NetworkClient which auto-detects v1/v2 API availability.
    Fetches MAC address from linux2rest /system/network.
    """

    def __init__(self):
        self._client = NetworkClient(blueos_services.wifi_manager)
        self._linux2rest = BlueOSClient(blueos_services.linux2rest)
        self._cached_mac: str | None = None
        self._cached_serial: str | None = None

        # WLAN AP<->STA switching state. Loaded lazily on first read so
        # tests don't touch the filesystem just by instantiating the
        # service. The lock guards against concurrent switch attempts —
        # the user could spam the Connect button or two clients could
        # race when both have the page open.
        self._wlan_state: WlanState | None = None
        self._wlan_switch_lock = asyncio.Lock()
        self._wlan_switch_task: asyncio.Task[None] | None = None

    async def get_network_info(self) -> NetworkInfo:
        """Get current network information including device identity."""
        connection = await self.get_connection_status()
        networks = await self.scan_networks()
        hotspot_ssid = await self._get_hotspot_ssid()
        serial = await self._get_serial_number()

        return NetworkInfo(
            connection=connection,
            available_networks=networks,
            is_scanning=False,
            serial_number=serial,
            hotspot_ssid=hotspot_ssid,
        )

    async def _resolve_hotspot_interface_name(self) -> str | None:
        """Interface BlueOS uses for the AP/hotspot (e.g. wlan1 or wifi1).

        Must match configure_hotspot(), which uses WiFi Manager v2's
        hotspot_interface hint or the second listed adapter.
        """
        try:
            data = await self._client.list_interfaces()
            if not data:
                return None
            hi = data.get("hotspot_interface")
            if isinstance(hi, str) and hi.strip():
                return hi.strip()
            interfaces = data.get("interfaces", [])
            if len(interfaces) >= 2:
                name = interfaces[1].get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        except Exception:
            pass
        return None

    async def _get_hotspot_ssid(self) -> str | None:
        """Get the hotspot SSID for the secondary / AP WiFi interface."""
        primary = await self._resolve_hotspot_interface_name()
        candidates: list[str] = []
        if primary:
            candidates.append(primary)
        for fb in ("wlan1", "wifi1"):
            if fb not in candidates:
                candidates.append(fb)
        for iface in candidates:
            try:
                status = await self._client._v2.wifi_hotspot_status(iface)
                ssid = status.get("ssid")
                if ssid:
                    return str(ssid)
            except Exception:
                continue
        try:
            creds = await self._client.get_hotspot_credentials()
            return creds.get("ssid")
        except Exception as e:
            logger.warning("Failed to get hotspot credentials: %s", e)
            return None

    async def _get_serial_number(self) -> str:
        """Derive DORIS serial number from the last 4 hex digits of the ethernet MAC."""
        if self._cached_serial:
            return self._cached_serial

        try:
            interfaces: list[dict[str, Any]] = await self._linux2rest.get(  # type: ignore[assignment]
                "/system/network"
            )
            for iface in interfaces:
                name = iface.get("name", "")
                if name.startswith("eth") or name.startswith("en"):
                    mac = iface.get("mac", "")
                    if mac:
                        suffix = mac.replace(":", "")[-4:].upper()
                        self._cached_serial = f"D-{suffix}"
                        return self._cached_serial
        except Exception as e:
            logger.warning("Failed to get ethernet MAC for serial number: %s", e)

        return "D-0000"

    async def _get_wlan_mac(self) -> str | None:
        """Get MAC of the secondary WiFi interface (same target as AP/hotspot)."""
        names: list[str] = []
        sec = await self._resolve_hotspot_interface_name()
        if sec:
            names.append(sec)
        for n in ("wlan1", "wifi1"):
            if n not in names:
                names.append(n)
        try:
            interfaces: list[dict[str, Any]] = await self._linux2rest.get(  # type: ignore[assignment]
                "/system/network"
            )
            by_name = {
                iface.get("name"): iface
                for iface in interfaces
                if iface.get("name")
            }
            for want in names:
                row = by_name.get(want)
                if not row:
                    continue
                mac = row.get("mac")
                if mac:
                    self._cached_mac = mac
                return mac
        except Exception as e:
            logger.warning(f"Failed to get MAC from linux2rest: {e}")
        return self._cached_mac

    async def get_connection_status(self) -> ConnectionStatus:
        """Get current connection status."""
        try:
            status = await self._client.get_status()
            is_connected = status.get("state") == "connected"
            mac_address = await self._get_wlan_mac()

            return ConnectionStatus(
                is_connected=is_connected,
                ssid=status.get("ssid"),
                ip_address=status.get("ip_address"),
                mac_address=mac_address,
                signal_strength=status.get("signallevel"),
            )
        except Exception:
            return ConnectionStatus(
                is_connected=False,
                ssid=None,
                mac_address=self._cached_mac,
            )

    async def scan_networks(self) -> list[WifiNetwork]:
        """Scan for available WiFi networks."""
        try:
            networks_data = await self._client.scan()
            saved_networks = await self._get_saved_networks()
            connection_status = await self.get_connection_status()

            networks = []
            seen_ssids: set[str] = set()

            for net in networks_data:
                ssid = net.get("ssid", "")
                if not ssid or ssid in seen_ssids:
                    continue
                seen_ssids.add(ssid)

                flags = net.get("flags", "")
                security = self._parse_security(flags)

                networks.append(
                    WifiNetwork(
                        ssid=ssid,
                        signal_strength=net.get("signallevel", 0),
                        security=security,
                        frequency=self._get_frequency_band(net.get("frequency", 2400)),
                        is_saved=ssid in saved_networks,
                        is_connected=(
                            connection_status.is_connected
                            and connection_status.ssid == ssid
                        ),
                    )
                )

            networks.sort(key=lambda n: n.signal_strength, reverse=True)
            return networks

        except Exception as e:
            logger.warning(f"Failed to scan networks: {e}")
            return []

    def _parse_security(self, flags: str) -> str:
        """Parse security type from flags string like '[WEP-WPA2-PSK-CCMP]'."""
        if not flags:
            return "Open"
        flags_lower = flags.lower()
        if "wpa3" in flags_lower:
            return "WPA3"
        if "wpa2" in flags_lower:
            return "WPA2"
        if "wpa" in flags_lower:
            return "WPA"
        if "wep" in flags_lower:
            return "WEP"
        return "Open"

    async def connect(self, credentials: NetworkCredentials) -> ConnectionStatus:
        """Connect to a WiFi network."""
        try:
            await self._client.connect(credentials.ssid, credentials.password)
            return await self.get_connection_status()
        except Exception:
            return ConnectionStatus(
                is_connected=False,
                ssid=credentials.ssid,
            )

    async def disconnect(self) -> ConnectionStatus:
        """Disconnect from current network."""
        try:
            await self._client.disconnect()
            return await self.get_connection_status()
        except Exception:
            return ConnectionStatus(is_connected=False)

    async def forget_network(self, ssid: str) -> bool:
        """Forget a saved network."""
        try:
            await self._client.forget_network(ssid)
            return True
        except Exception:
            return False

    async def _get_saved_networks(self) -> set[str]:
        """Get list of saved network SSIDs."""
        try:
            saved = await self._client.get_saved()
            return {net.get("ssid", "") for net in saved}
        except Exception:
            return set()

    def _get_frequency_band(self, frequency_mhz: int) -> str:
        """Convert frequency to band string."""
        if frequency_mhz >= 5000:
            return "5GHz"
        return "2.4GHz"

    async def configure_hotspot(
        self,
        ssid: str = "DORIS",
        password: str = "blueosap",
    ) -> None:
        """Configure a single DORIS AP on the secondary WiFi interface (wlan1).

        The v2 per-interface hotspot APIs (enable/disable/credentials) hang on
        current BlueOS builds, so this method uses a two-step approach:

        1. Kill all APs via the global (legacy) ``hotspot?enable=false``.
           This removes both the undesired uap0 virtual AP on wlan0 and any
           existing wlan1 AP.
        2. Set global hotspot credentials to the DORIS SSID/password.
        3. Bring up only wlan1 as a hotspot via the v2 mode API
           (``POST /wifi/mode``), which works reliably.

        Result: wlan0 stays in client-only mode, wlan1 runs the sole AP.
        """
        if ssid == "DORIS":
            serial = await self._get_serial_number()
            ssid = f"DORIS ({serial})"

        interfaces_data = await self._client.list_interfaces()
        if not interfaces_data:
            # BlueOS WiFi Manager v2 is not available on this build
            # (e.g. blueos-core 1.5.0-beta.36 ships v1 only). The v2 path
            # below uses per-interface mode/hotspot APIs that don't exist
            # in v1, so all we can do is rename the global hotspot SSID
            # and restart the AP via v1.
            await self._configure_hotspot_v1(ssid, password)
            return

        interfaces = interfaces_data.get("interfaces", [])
        if len(interfaces) < 2:
            logger.info(
                "Only %d WiFi interface(s) found, skipping hotspot config",
                len(interfaces),
            )
            return

        primary_name = interfaces[0].get("name") or ""
        iface_name = interfaces[1]["name"]
        logger.info(
            "Configuring hotspot: primary=%s (client), secondary=%s (AP)",
            primary_name,
            iface_name,
        )

        # -- smart-hotspot: only touch if currently enabled --
        try:
            if await self._client.get_smart_hotspot():
                await self._client.set_smart_hotspot(False)
                logger.info("Smart hotspot disabled")
            else:
                logger.info("Smart hotspot already disabled")
        except Exception as e:
            logger.warning("Could not check/disable smart hotspot: %s", e)

        # -- check whether the primary interface has an unwanted AP --
        primary_hotspot_active = False
        try:
            hs = await self._client._v2.wifi_hotspot_status(primary_name)
            primary_hotspot_active = hs.get("enabled", False)
        except Exception:
            pass

        # -- set credentials BEFORE disabling (API rejects creds when hotspot is off) --
        try:
            await self._client.set_hotspot_credentials(ssid, password)
            logger.info("Hotspot credentials set: SSID=%s", ssid)
        except Exception as e:
            logger.warning("Failed to set hotspot credentials: %s", e)

        if primary_hotspot_active:
            logger.info(
                "Primary %s has an active hotspot (uap0); disabling all APs first",
                primary_name,
            )
            try:
                await self._client.set_hotspot(False)
                logger.info("All hotspots disabled via global API")
            except Exception as e:
                logger.warning("Global hotspot disable failed: %s", e)

        # -- ensure the secondary is in hotspot (or dual) mode --
        await self._ensure_secondary_hotspot(iface_name)

    async def _configure_hotspot_v1(self, ssid: str, password: str) -> None:
        """Rename the global BlueOS hotspot via the v1 WiFi Manager API.

        Used when v2 is unavailable. v1 has no concept of per-interface
        hotspots, so we just retarget the single global hotspot. If the
        SSID already matches, we skip the toggle so we don't interrupt
        connected clients on every backend restart.
        """
        try:
            current = await self._client.get_hotspot_credentials()
            current_ssid = current.get("ssid") if isinstance(current, dict) else None
        except Exception as e:
            logger.warning("v1 hotspot: failed to read current credentials: %s", e)
            current_ssid = None

        if current_ssid == ssid:
            logger.info("Hotspot SSID already %r (v1 path), nothing to do", ssid)
            return

        logger.info(
            "Renaming hotspot via v1 API: %r -> %r (BlueOS v2 unavailable)",
            current_ssid, ssid,
        )

        try:
            await self._client.set_hotspot_credentials(ssid, password)
        except Exception as e:
            logger.warning("v1 hotspot: set_hotspot_credentials failed: %s", e)
            return

        try:
            if await self._client.get_smart_hotspot():
                await self._client.set_smart_hotspot(False)
                logger.info("Smart hotspot disabled (v1 path)")
        except Exception as e:
            logger.debug("v1 hotspot: smart_hotspot check failed: %s", e)

        try:
            await self._client.set_hotspot(False)
            await asyncio.sleep(3)
            await self._client.set_hotspot(True)
            logger.info("Hotspot restarted via v1; broadcasting %r", ssid)
        except Exception as e:
            logger.warning("v1 hotspot: toggle failed (creds set but not applied): %s", e)

    async def _is_hotspot_actually_running(self, iface_name: str) -> bool:
        """Check if the AP is genuinely serving, not just labelled 'hotspot'."""
        try:
            hs = await self._client._v2.wifi_hotspot_status(iface_name)
            return bool(hs.get("enabled"))
        except Exception:
            return False

    async def _ensure_secondary_hotspot(self, iface_name: str) -> bool:
        """Ensure *iface_name* is running as a hotspot. Returns True on success.

        Uses the v2 mode API with a generous timeout (create_ap takes ~15s).
        If the WiFi Manager reports the mode as "hotspot" but the AP isn't
        actually running (no hostapd / no IP), cycles through normal first
        to force create_ap to restart.
        """
        try:
            mode_info = await self._client.get_interface_mode(iface_name)
            if not mode_info:
                logger.warning("Could not query mode for %s", iface_name)
                return False

            available = mode_info.get("available_modes", [])
            current = mode_info.get("current_mode")

            modes_to_try = [m for m in ("hotspot", "dual") if m in available]
            if not modes_to_try:
                logger.warning(
                    "Interface %s supports neither hotspot nor dual mode (available: %s)",
                    iface_name,
                    available,
                )
                return False

            target = modes_to_try[0]

            if current == target and await self._is_hotspot_actually_running(iface_name):
                logger.info("Interface %s already in %s mode and AP is running", iface_name, target)
                return True

            if current == target:
                logger.info(
                    "Interface %s reports %s mode but AP is not running, cycling via normal",
                    iface_name, target,
                )
                try:
                    await self._client.set_interface_mode(iface_name, "normal", timeout=15.0)
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning("Failed to set normal mode on %s: %s", iface_name, e)

            try:
                await self._client.set_interface_mode(iface_name, target, timeout=30.0)
                logger.info("Interface %s set to %s mode", iface_name, target)
                return True
            except Exception as e:
                logger.warning("Failed to set %s mode on %s: %s", target, iface_name, e)

            logger.warning("All mode attempts failed for %s", iface_name)
        except Exception as e:
            logger.warning("Failed to configure mode for %s: %s", iface_name, e)
        return False

    async def _get_secondary_interface_name(self) -> str | None:
        """Return the name of the secondary (AP) WiFi interface, or None."""
        try:
            data = await self._client.list_interfaces()
            if not data:
                return None
            interfaces = data.get("interfaces", [])
            if len(interfaces) >= 2:
                return interfaces[1].get("name")
        except Exception:
            pass
        return None

    async def start_ap_watchdog(self) -> None:
        """Background loop that re-asserts the wlan1 hotspot if it drops.

        After a dive the vehicle loses all WiFi connections.  BlueOS /
        NetworkManager may not automatically restart the AP on wlan1.
        This watchdog detects that and brings it back.

        Suppressed while WLAN intent is ``sta_pending`` or
        ``sta_connected``: in those states the external radio is
        intentionally *not* hosting an AP (it's associated to a client
        WLAN), so re-asserting hotspot mode would tear down the user's
        chosen connection.
        """
        await asyncio.sleep(AP_WATCHDOG_SETTLE_S)
        while True:
            await asyncio.sleep(AP_WATCHDOG_INTERVAL_S)
            try:
                state = await self.get_wlan_state()
                if state.mode != "ap":
                    continue
                iface = await self._get_secondary_interface_name()
                if not iface:
                    continue
                hs = await self._client._v2.wifi_hotspot_status(iface)
                if hs.get("enabled"):
                    continue
                logger.warning(
                    "AP on %s is down, re-asserting hotspot mode", iface,
                )
                if await self._ensure_secondary_hotspot(iface):
                    logger.info("AP on %s recovered by watchdog", iface)
                else:
                    logger.warning("AP watchdog: failed to recover %s", iface)
            except Exception as e:
                logger.debug("AP watchdog check error: %s", e)

    # ── WLAN AP <-> STA mode switching ───────────────────────────────
    #
    # On this hardware the *only* reliable radio when the vehicle is
    # fully assembled is the external USB Realtek (renamed to ``uap0``
    # by udev). It can be either an AP (hotspot mode) or a client (STA
    # / "normal" mode) but not both at once. The user switches between
    # them from the Network Setup tab; the AP is the safe default and
    # is restored on every backend startup.

    def _load_wlan_state(self) -> WlanState:
        """Read the persisted intent file. ``mode`` is always force-reset
        to ``ap`` on load — only ``last_attempt`` is kept across boots
        for UX feedback."""
        try:
            raw = WLAN_INTENT_FILE.read_text()
            data = json.loads(raw)
            persisted = WlanState.model_validate(data)
            return WlanState(mode="ap", last_attempt=persisted.last_attempt)
        except FileNotFoundError:
            return WlanState(mode="ap")
        except Exception as e:
            logger.warning("Could not load WLAN intent (%s); defaulting to ap", e)
            return WlanState(mode="ap")

    def _save_wlan_state(self, state: WlanState) -> None:
        try:
            WLAN_INTENT_FILE.parent.mkdir(parents=True, exist_ok=True)
            WLAN_INTENT_FILE.write_text(state.model_dump_json(indent=2))
        except OSError as e:
            logger.warning("Could not persist WLAN intent: %s", e)

    async def get_wlan_state(self) -> WlanState:
        """Return the current AP/STA state, refreshing IP / liveness
        from the appropriate place for the active code path.

        v2 path: BlueOS WiFi Manager ``/status`` is interface-aware and
        reports the right thing.

        v1 path: ``/v1.0/status`` reflects whatever interface BlueOS
        treats as primary (typically ``wlan0``), which would lie to us
        about ``uap0``. We probe the host directly via ``ip``/``iw``
        instead.
        """
        if self._wlan_state is None:
            self._wlan_state = self._load_wlan_state()

        state = self._wlan_state
        if state.mode != "sta_connected":
            return state

        # v2 first.
        v2_iface = await self._resolve_hotspot_interface_name()
        if v2_iface:
            try:
                status = await self._client.get_status()
                if status.get("state") == "connected":
                    ip = status.get("ip_address")
                    ssid = status.get("ssid") or state.target_ssid
                    if ip != state.ip_address or ssid != state.target_ssid:
                        state = state.model_copy(
                            update={"ip_address": ip, "target_ssid": ssid}
                        )
                        self._wlan_state = state
                else:
                    logger.warning(
                        "STA connection lost (status=%s), treating as ap",
                        status.get("state"),
                    )
                    state = WlanState(mode="ap", last_attempt=state.last_attempt)
                    self._wlan_state = state
                    self._save_wlan_state(state)
            except Exception as e:
                logger.debug("get_wlan_state v2 status refresh failed: %s", e)
            return state

        # v1: probe host directly.
        v1_iface = await self._resolve_external_iface_v1()
        if not v1_iface:
            return state
        try:
            ok, ip_out = await _run_host_command(
                f"ip -4 -o addr show dev {v1_iface} 2>/dev/null "
                f"| awk '{{print $4}}' | cut -d/ -f1 | head -1"
            )
            ip = ip_out.strip() if ok else ""
            if not ip:
                logger.warning(
                    "[v1] STA on %s lost its IP, treating as ap", v1_iface,
                )
                state = WlanState(mode="ap", last_attempt=state.last_attempt)
                self._wlan_state = state
                self._save_wlan_state(state)
            elif ip != state.ip_address:
                state = state.model_copy(update={"ip_address": ip})
                self._wlan_state = state
        except Exception as e:
            logger.debug("get_wlan_state v1 IP probe failed: %s", e)

        return state

    async def reset_wlan_to_ap_on_boot(self) -> None:
        """Force WLAN intent back to ``ap`` and proactively disconnect
        any STA association *on the external interface only*. Called
        once at startup *after* ``configure_hotspot()``.

        Saved networks are *kept* (option (b)) so the user can pick a
        previously-used SSID from the scan list and reconnect with one
        click during the same session.

        IMPORTANT: This must NEVER use the legacy global disconnect
        endpoint. On a vehicle where the onboard radio (``wlan0``) is
        actively connected upstream — common during development and
        possible in production if the user wires it up — that endpoint
        can disconnect ``wlan0`` and silently nuke the only management
        link to the vehicle. Always pass an explicit interface so the
        scope is unambiguous.
        """
        last_attempt: WlanLastAttempt | None = None
        try:
            persisted = self._load_wlan_state()
            last_attempt = persisted.last_attempt
        except Exception:
            pass

        v2_iface = await self._resolve_hotspot_interface_name()
        if v2_iface:
            try:
                # Only disconnect if the external radio is actually
                # associated to a STA. configure_hotspot() ran just
                # before us so under normal startup it's already in
                # hotspot mode and there's nothing to do here — but
                # if a previous run left it in STA mode we want to
                # break that association cleanly.
                hs = await self._client._v2.wifi_hotspot_status(v2_iface)
                if not hs.get("enabled"):
                    await self._client.disconnect(interface=v2_iface)
                    logger.info("Boot-time STA disconnect issued on %s", v2_iface)
            except Exception as e:
                logger.debug(
                    "Boot-time WLAN disconnect skipped on %s: %s", v2_iface, e,
                )
        else:
            # v1 fallback: if a previous run crashed mid-switch we may
            # still have an unmanaged-conf stash sitting in /tmp and a
            # leftover doris-uap0-sta-* nmcli profile holding the
            # interface. Roll back so configure_hotspot() (which the
            # caller just ran) can actually drive create_ap.
            v1_iface = await self._resolve_external_iface_v1()
            if v1_iface:
                # Tear down any stale doris-uap0-sta-* profiles.
                ok, out = await _run_host_command(
                    "nmcli -t -f NAME connection show 2>/dev/null"
                )
                if ok:
                    for name in out.splitlines():
                        name = name.strip()
                        if name.startswith(V1_UAP0_CONNECTION_PREFIX):
                            await _run_host_command(
                                f"sudo nmcli connection down '{name}' 2>/dev/null; "
                                f"sudo nmcli connection delete '{name}' 2>/dev/null; "
                                f"true"
                            )
                            logger.info(
                                "[v1] cleaned up leftover STA profile %r", name,
                            )
                # Restore stashed unmanaged conf if present.
                ok, _ = await _run_host_command(
                    f"test -f {V1_NM_UNMANAGED_STASH} && "
                    f"sudo mv {V1_NM_UNMANAGED_STASH} {V1_NM_UNMANAGED_CONF} && "
                    f"sudo nmcli general reload conf"
                )
                if ok:
                    logger.info(
                        "[v1] restored stashed unmanaged conf for %s", v1_iface,
                    )

        self._wlan_state = WlanState(mode="ap", last_attempt=last_attempt)
        self._save_wlan_state(self._wlan_state)
        logger.info("WLAN intent reset to AP on startup")

    async def begin_switch_to_sta(
        self, ssid: str, password: str
    ) -> WlanState:
        """Initiate an asynchronous switch from AP mode to STA mode.

        Returns immediately with ``mode=sta_pending``. The user's browser
        will lose its AP connection within a few seconds of this call —
        the actual outcome (success / failure) is recorded in the
        persisted state file and surfaced via :meth:`get_wlan_state`.

        Concurrent switch requests are rejected (returns the existing
        in-flight state). To re-attempt after a failure, the previous
        task must have completed.
        """
        if not ssid:
            raise ValueError("ssid is required")

        async with self._wlan_switch_lock:
            current = await self.get_wlan_state()
            if current.mode == "sta_pending":
                return current
            if (
                self._wlan_switch_task is not None
                and not self._wlan_switch_task.done()
            ):
                return current

            new_state = WlanState(
                mode="sta_pending",
                target_ssid=ssid,
                last_attempt=current.last_attempt,
            )
            self._wlan_state = new_state
            self._save_wlan_state(new_state)
            self._wlan_switch_task = asyncio.create_task(
                self._run_switch_to_sta(ssid, password)
            )
            return new_state

    async def begin_switch_to_ap(self) -> WlanState:
        """Tear down any STA connection and put the external radio back
        into hotspot mode. Same async pattern as :meth:`begin_switch_to_sta`
        — the call returns immediately while the work runs in the
        background.

        Saved networks are kept (option (b)).
        """
        async with self._wlan_switch_lock:
            current = await self.get_wlan_state()
            if (
                self._wlan_switch_task is not None
                and not self._wlan_switch_task.done()
            ):
                return current
            self._wlan_switch_task = asyncio.create_task(
                self._run_switch_to_ap()
            )
            return current

    async def _run_switch_to_sta(self, ssid: str, password: str) -> None:
        """Background task that performs the AP -> STA flip.

        Dispatches between the v2 path (BlueOS WiFi Manager v2 with
        per-interface mode + connect APIs) and the v1 host-shell path
        (nmcli driving uap0 directly while the BlueOS hotspot is paused).
        """
        # Fast path: if v2 is available, use it.
        v2_iface = await self._resolve_hotspot_interface_name()
        if v2_iface:
            await self._v2_switch_to_sta(v2_iface, ssid, password)
            return

        # Slow path: v1 fallback driving uap0 ourselves via Commander.
        v1_iface = await self._resolve_external_iface_v1()
        if not v1_iface:
            logger.warning("No external WiFi interface (v1 probe); aborting STA switch")
            self._record_failure(ssid, "External WiFi interface not found")
            return
        await self._v1_switch_to_sta(v1_iface, ssid, password)

    async def _run_switch_to_ap(self) -> None:
        """Background task that performs the STA -> AP flip."""
        v2_iface = await self._resolve_hotspot_interface_name()
        if v2_iface:
            await self._v2_switch_to_ap(v2_iface)
            return

        v1_iface = await self._resolve_external_iface_v1()
        if not v1_iface:
            logger.warning("No external WiFi interface (v1 probe); cannot restore AP")
            return
        await self._v1_switch_to_ap(v1_iface)

    # ── v2 path (BlueOS WiFi Manager v2) ─────────────────────────────

    async def _v2_switch_to_sta(self, iface: str, ssid: str, password: str) -> None:
        logger.info("[v2] Switching %s from AP to STA, target SSID=%r", iface, ssid)

        try:
            await self._client.set_interface_mode(iface, "normal", timeout=30.0)
        except Exception as e:
            logger.warning("Failed to switch %s to normal mode: %s", iface, e)
            await self._restore_ap_after_failure(iface)
            self._record_failure(ssid, f"Could not disable hotspot: {e}")
            return

        await asyncio.sleep(2)

        try:
            await self._client.connect(ssid, password, interface=iface)
        except Exception as e:
            logger.warning("Connect call to %r failed: %s", ssid, e)
            await self._restore_ap_after_failure(iface)
            self._record_failure(ssid, f"Connect rejected: {e}")
            return

        deadline = asyncio.get_event_loop().time() + STA_CONNECT_TIMEOUT_S
        last_status: dict[str, Any] = {}
        while asyncio.get_event_loop().time() < deadline:
            try:
                last_status = await self._client.get_status()
                if (
                    last_status.get("state") == "connected"
                    and last_status.get("ssid") == ssid
                ):
                    break
            except Exception as e:
                logger.debug("STA poll error: %s", e)
            await asyncio.sleep(STA_POLL_INTERVAL_S)
        else:
            logger.warning(
                "STA association to %r timed out after %.0fs (last status=%s)",
                ssid, STA_CONNECT_TIMEOUT_S, last_status,
            )
            # Deliberately NOT calling forget_network(ssid) here. Saved
            # networks in BlueOS WiFi Manager are global, not per-
            # interface — so forgetting the SSID we just tried would
            # also clear it from wlan0's saved list, silently breaking
            # any lab-internet connection the user has set up there.
            # The user can clean stale credentials manually via the
            # BlueOS UI if they really need to.
            await self._restore_ap_after_failure(iface)
            self._record_failure(
                ssid,
                "Timed out waiting for association — check password / signal.",
            )
            return

        ip = last_status.get("ip_address")
        logger.info("[v2] STA connected to %r (ip=%s)", ssid, ip)
        success = WlanLastAttempt(
            ssid=ssid,
            status="success",
            timestamp=datetime.now(timezone.utc),
        )
        self._wlan_state = WlanState(
            mode="sta_connected",
            target_ssid=ssid,
            ip_address=ip,
            last_attempt=success,
        )
        self._save_wlan_state(self._wlan_state)

    async def _v2_switch_to_ap(self, iface: str) -> None:
        logger.info("[v2] Restoring %s to hotspot mode", iface)
        try:
            await self._client.disconnect(interface=iface)
        except Exception as e:
            logger.debug("Disconnect call failed (may already be disconnected): %s", e)

        await self._restore_ap_after_failure(iface)

        self._wlan_state = WlanState(
            mode="ap",
            last_attempt=self._wlan_state.last_attempt if self._wlan_state else None,
        )
        self._save_wlan_state(self._wlan_state)

    # ── v1 path (BlueOS WiFi Manager v1, nmcli on host) ──────────────

    async def _resolve_external_iface_v1(self) -> str | None:
        """Best-guess for the external WiFi interface on v1 systems.

        On DORIS hardware the Realtek RTL88x2BU is renamed to ``uap0``
        by udev (see services/hotspot_radio.py). On a stock setup it
        would be ``wlan1``. Probe both via /sys/class/net.
        """
        for cand in ("uap0", "wlan1"):
            ok, out = await _run_host_command(
                f"test -d /sys/class/net/{cand} && echo {cand}"
            )
            if ok and cand in out:
                return cand
        return None

    async def _v1_switch_to_sta(self, iface: str, ssid: str, password: str) -> None:
        """Drive *iface* from AP mode to STA mode without the v2 mode API.

        Sequence:

          1. Stash ``unmanaged-devices=uap0`` NM conf so NM can take
             ownership of the interface; reload NM.
          2. Disable BlueOS hotspot (legacy v1 toggle) — this kills
             ``create_ap`` / hostapd on the interface.
          3. Bring the iface link down/up to clear hostapd state.
          4. Create + activate an nmcli connection profile bound to
             *iface*, autoconnect off so it never auto-rejoins on its
             own.
          5. Poll ``ip addr show`` for a DHCP lease.

        Any failure step rolls back via :meth:`_v1_restore_hotspot`.
        """
        conn_name = f"{V1_UAP0_CONNECTION_PREFIX}{ssid}"
        logger.info(
            "[v1] Switching %s from AP to STA, target SSID=%r (conn=%r)",
            iface, ssid, conn_name,
        )

        # 1. Stash unmanaged conf and reload NM so NM is willing to drive uap0.
        await _run_host_command(
            f"if [ -f {V1_NM_UNMANAGED_CONF} ]; then "
            f"  sudo mv {V1_NM_UNMANAGED_CONF} {V1_NM_UNMANAGED_STASH}; "
            f"fi"
        )
        await _run_host_command("sudo nmcli general reload conf")
        await asyncio.sleep(1)

        # 2. Disable BlueOS hotspot (kills create_ap on uap0).
        try:
            await self._client.set_hotspot(False)
        except Exception as e:
            logger.warning("[v1] set_hotspot(False) failed: %s", e)
            await self._v1_restore_hotspot(iface, conn_name)
            self._record_failure(ssid, f"Could not disable hotspot: {e}")
            return

        await asyncio.sleep(3)

        # 3. Force the link down/up to flush hostapd state cleanly.
        await _run_host_command(
            f"sudo ip link set {iface} down; sleep 1; sudo ip link set {iface} up"
        )
        await asyncio.sleep(2)

        # 4. Create + activate the nmcli connection.
        # Single-quote-escape SSID/password by replacing ' with '\''
        safe_ssid = ssid.replace("'", "'\\''")
        safe_pwd = password.replace("'", "'\\''")

        # Clean any prior profile with the same name (e.g. from a
        # previous failed attempt to the same SSID).
        await _run_host_command(
            f"sudo nmcli connection delete '{conn_name}' 2>/dev/null; true"
        )

        if password:
            add_cmd = (
                f"sudo nmcli connection add type wifi ifname {iface} "
                f"con-name '{conn_name}' ssid '{safe_ssid}' "
                f"connection.autoconnect no "
                f"wifi-sec.key-mgmt wpa-psk wifi-sec.psk '{safe_pwd}'"
            )
        else:
            add_cmd = (
                f"sudo nmcli connection add type wifi ifname {iface} "
                f"con-name '{conn_name}' ssid '{safe_ssid}' "
                f"connection.autoconnect no"
            )
        ok, err = await _run_host_command(add_cmd)
        if not ok:
            logger.warning("[v1] nmcli connection add failed: %s", err)
            await self._v1_restore_hotspot(iface, conn_name)
            self._record_failure(ssid, f"nmcli add failed: {err[:200]}")
            return

        ok, err = await _run_host_command(
            f"sudo nmcli --wait 30 connection up '{conn_name}'", timeout=45.0,
        )
        if not ok:
            logger.warning("[v1] nmcli connection up failed: %s", err)
            await _run_host_command(
                f"sudo nmcli connection delete '{conn_name}' 2>/dev/null; true"
            )
            await self._v1_restore_hotspot(iface, conn_name)
            self._record_failure(
                ssid,
                f"Association failed — check password / signal: {err[:200]}",
            )
            return

        # 5. Poll for DHCP lease.
        ip: str | None = None
        deadline = asyncio.get_event_loop().time() + V1_DHCP_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            ok, ip_out = await _run_host_command(
                f"ip -4 -o addr show dev {iface} 2>/dev/null "
                f"| awk '{{print $4}}' | cut -d/ -f1 | head -1"
            )
            if ok and ip_out.strip():
                ip = ip_out.strip()
                break
            await asyncio.sleep(STA_POLL_INTERVAL_S)

        if not ip:
            logger.warning(
                "[v1] %s associated to %r but never got a DHCP lease",
                iface, ssid,
            )
            await _run_host_command(
                f"sudo nmcli connection down '{conn_name}' 2>/dev/null; true"
            )
            await _run_host_command(
                f"sudo nmcli connection delete '{conn_name}' 2>/dev/null; true"
            )
            await self._v1_restore_hotspot(iface, conn_name)
            self._record_failure(ssid, "Associated but no DHCP lease in 30s")
            return

        logger.info("[v1] STA on %s connected to %r (ip=%s)", iface, ssid, ip)
        success = WlanLastAttempt(
            ssid=ssid,
            status="success",
            timestamp=datetime.now(timezone.utc),
        )
        self._wlan_state = WlanState(
            mode="sta_connected",
            target_ssid=ssid,
            ip_address=ip,
            last_attempt=success,
        )
        self._save_wlan_state(self._wlan_state)

    async def _v1_switch_to_ap(self, iface: str) -> None:
        """Tear down the nmcli STA connection on *iface* and restart
        the BlueOS hotspot."""
        logger.info("[v1] Restoring %s to hotspot mode", iface)

        # Find any active nmcli connection on iface to bring down.
        # Format from `nmcli -t -f NAME,DEVICE connection show --active`
        # is one ``NAME:DEVICE`` per line.
        ok, out = await _run_host_command(
            "nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null"
        )
        conn_name = ""
        if ok:
            for line in out.splitlines():
                parts = line.rsplit(":", 1)
                if len(parts) == 2 and parts[1].strip() == iface:
                    conn_name = parts[0].strip()
                    break

        await self._v1_restore_hotspot(iface, conn_name)

        self._wlan_state = WlanState(
            mode="ap",
            last_attempt=self._wlan_state.last_attempt if self._wlan_state else None,
        )
        self._save_wlan_state(self._wlan_state)

    async def _v1_restore_hotspot(self, iface: str, conn_name: str = "") -> None:
        """Roll back any v1 STA state on *iface* and re-enable the
        BlueOS hotspot. Used both in failure paths and from
        :meth:`_v1_switch_to_ap`. Idempotent — safe to call when
        nothing was set up yet."""
        if conn_name:
            await _run_host_command(
                f"sudo nmcli connection down '{conn_name}' 2>/dev/null; true"
            )
            await _run_host_command(
                f"sudo nmcli connection delete '{conn_name}' 2>/dev/null; true"
            )

        # Restore the unmanaged-devices conf so future NM reloads
        # leave uap0 alone for create_ap to drive.
        await _run_host_command(
            f"if [ -f {V1_NM_UNMANAGED_STASH} ]; then "
            f"  sudo mv {V1_NM_UNMANAGED_STASH} {V1_NM_UNMANAGED_CONF}; "
            f"fi"
        )
        await _run_host_command("sudo nmcli general reload conf")
        await asyncio.sleep(1)

        # Bounce the link to clear any nmcli-supplicant state still
        # attached so create_ap can take over cleanly.
        await _run_host_command(
            f"sudo ip addr flush dev {iface} 2>/dev/null; "
            f"sudo ip link set {iface} down; sleep 1; "
            f"sudo ip link set {iface} up"
        )
        await asyncio.sleep(2)

        try:
            await self._client.set_hotspot(True)
            logger.info("[v1] BlueOS hotspot re-enabled on %s", iface)
        except Exception as e:
            logger.warning("[v1] set_hotspot(True) failed: %s", e)

    async def _restore_ap_after_failure(self, iface: str) -> None:
        """Best-effort: put ``iface`` back into hotspot mode. Used both
        on the failure path of a STA switch and on user-initiated
        switch-back."""
        try:
            ok = await self._ensure_secondary_hotspot(iface)
            if not ok:
                logger.warning(
                    "Could not restore hotspot on %s after failed switch", iface,
                )
        except Exception as e:
            logger.warning("Hotspot restore failed: %s", e)

    def _record_failure(self, ssid: str, error: str) -> None:
        """Persist a failure outcome so the UI can surface it once the
        user reconnects to the AP."""
        failure = WlanLastAttempt(
            ssid=ssid,
            status="failed",
            error=error,
            timestamp=datetime.now(timezone.utc),
        )
        self._wlan_state = WlanState(mode="ap", last_attempt=failure)
        self._save_wlan_state(self._wlan_state)

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._client.close()
        await self._linux2rest.close()


# ── Module-level singleton ──────────────────────────────────────────
#
# WLAN switch state (the in-memory lock, the task handle, the cached
# WlanState) lives on the ``NetworkService`` instance. Routes and the
# startup handler must therefore share the *same* instance, otherwise
# the watchdog thread and the HTTP handler disagree on whether a switch
# is in flight and could race into a double-switch. The persisted intent
# file mitigates the worst of it but the in-flight lock is needed too.

_NETWORK_SERVICE_SINGLETON: NetworkService | None = None


def get_network_service() -> NetworkService:
    """Return the process-wide :class:`NetworkService` instance."""
    global _NETWORK_SERVICE_SINGLETON
    if _NETWORK_SERVICE_SINGLETON is None:
        _NETWORK_SERVICE_SINGLETON = NetworkService()
    return _NETWORK_SERVICE_SINGLETON
