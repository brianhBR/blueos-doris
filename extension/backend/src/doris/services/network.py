"""Network/WiFi service."""

import asyncio
import logging
from typing import Any

from ..config import blueos_services
from ..models.network import ConnectionStatus, NetworkCredentials, NetworkInfo, WifiNetwork
from .base import BlueOSClient
from .blueos.network import NetworkClient

logger = logging.getLogger(__name__)

AP_WATCHDOG_INTERVAL_S = 60
AP_WATCHDOG_SETTLE_S = 15


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
            logger.warning("No WiFi interfaces found (v2 API unavailable)")
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
        This watchdog detects that and brings it back.  It also ensures
        the hotspot DNS server (dnsmasq) is running when the AP is up.
        """
        from .mdns import start_hotspot_dns, is_hotspot_dns_running

        await asyncio.sleep(AP_WATCHDOG_SETTLE_S)
        while True:
            await asyncio.sleep(AP_WATCHDOG_INTERVAL_S)
            try:
                iface = await self._get_secondary_interface_name()
                if not iface:
                    continue
                hs = await self._client._v2.wifi_hotspot_status(iface)
                if hs.get("enabled"):
                    if not await is_hotspot_dns_running():
                        logger.info("AP is up but hotspot DNS is not running, starting it")
                        await start_hotspot_dns()
                    continue
                logger.warning(
                    "[HOTSPOT] AP on %s is down, re-asserting hotspot mode", iface,
                )
                if await self._ensure_secondary_hotspot(iface):
                    logger.info("[HOTSPOT] AP on %s recovered by watchdog", iface)
                else:
                    logger.error("[HOTSPOT] watchdog failed to recover AP on %s", iface)
            except Exception as e:
                logger.debug("AP watchdog check error: %s", e)

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._client.close()
        await self._linux2rest.close()
