"""Network/WiFi service."""

import logging
from typing import Any

from ..config import blueos_services
from ..models.network import ConnectionStatus, NetworkCredentials, NetworkInfo, WifiNetwork
from .base import BlueOSClient
from .blueos.network import NetworkClient

logger = logging.getLogger(__name__)


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
        """Configure the secondary WiFi interface (typically wlan1) as a hotspot.

        Prefers dual mode (client + hotspot) when supported, otherwise
        hotspot-only. Forces the primary (typically wlan0) to normal/client-only
        mode: disables smart-hotspot, turns off any AP on that radio, and sets
        mode to ``normal`` when the API allows — avoids two APs and NM fighting
        the DORIS AP (often mistaken for RF interference).
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
            logger.info("Only %d WiFi interface(s) found, skipping hotspot config", len(interfaces))
            return

        primary = interfaces[0]
        secondary = interfaces[1]
        primary_name = primary.get("name") or ""
        iface_name = secondary["name"]
        logger.info("Configuring secondary WiFi interface: %s", iface_name)

        # BlueOS often keeps a default "BlueOS" AP on the primary radio and/or uses
        # smart-hotspot to toggle it — that yields two SSIDs and can look like the
        # hotspot is constantly restarting. Prefer a single DORIS AP on wlan1.
        try:
            await self._client.set_smart_hotspot(False)
            logger.info("Smart hotspot disabled (avoids fighting DORIS wlan1 AP)")
        except Exception as e:
            logger.warning("Could not disable smart hotspot: %s", e)

        if primary_name:
            try:
                primary_mode = await self._client.get_interface_mode(primary_name)
                current_p = primary_mode.get("current_mode") if primary_mode else None
                available_p = primary_mode.get("available_modes", []) if primary_mode else []

                if current_p == "normal":
                    logger.info(
                        "Primary %s already in normal (client only) mode, leaving untouched",
                        primary_name,
                    )
                elif "normal" in available_p:
                    # Only disable hotspot and change mode if not already in client mode.
                    # Calling wifi_hotspot_disable on an interface that's already in
                    # normal mode can reset it and break autoconnect to saved networks.
                    try:
                        await self._client.set_hotspot(False, interface=primary_name)
                        logger.info("Hotspot disabled on primary interface %s", primary_name)
                    except Exception as e:
                        logger.warning("Could not disable hotspot on %s: %s", primary_name, e)

                    await self._client.set_interface_mode(primary_name, "normal")
                    logger.info("Primary %s set to normal mode (client only)", primary_name)
            except Exception as e:
                logger.warning(
                    "Could not configure primary %s: %s", primary_name, e,
                )

        try:
            await self._client.set_hotspot_credentials(ssid, password, interface=iface_name)
            logger.info("Hotspot credentials set: SSID=%s on %s", ssid, iface_name)
        except Exception as e:
            logger.warning("Failed to set hotspot credentials: %s", e)

        try:
            mode_info = await self._client.get_interface_mode(iface_name)
            if not mode_info:
                logger.warning("Could not query mode for %s", iface_name)
                return

            available = mode_info.get("available_modes", [])
            current = mode_info.get("current_mode")

            # Build ordered list of modes to try: prefer dual, fall back to hotspot
            modes_to_try = [m for m in ("dual", "hotspot") if m in available]
            if not modes_to_try:
                logger.warning("Interface %s supports neither dual nor hotspot mode (available: %s)", iface_name, available)
                return

            for mode in modes_to_try:
                if current == mode:
                    logger.info("Interface %s already in %s mode", iface_name, mode)
                    return
                try:
                    await self._client.set_interface_mode(iface_name, mode)
                    logger.info("Interface %s set to %s mode", iface_name, mode)
                    return
                except Exception as e:
                    logger.warning("Failed to set %s mode on %s: %s", mode, iface_name, e)

            logger.warning("All mode attempts failed for %s", iface_name)
        except Exception as e:
            logger.warning("Failed to configure mode for %s: %s", iface_name, e)

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._client.close()
        await self._linux2rest.close()
