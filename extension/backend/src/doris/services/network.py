"""Network/WiFi service."""

import logging
from typing import Any

from ..config import blueos_services
from ..models.network import ConnectionStatus, NetworkCredentials, NetworkInfo, WifiNetwork
from .base import BlueOSClient
from .blueos.network import NetworkClient

logger = logging.getLogger(__name__)

DORIS_SERIAL_NUMBER = "D-BB-00"


class NetworkService:
    """Service for managing network connections via BlueOS WiFi Manager.

    Uses the unified NetworkClient which auto-detects v1/v2 API availability.
    Fetches MAC address from linux2rest /system/network.
    """

    def __init__(self):
        self._client = NetworkClient(blueos_services.wifi_manager)
        self._linux2rest = BlueOSClient(blueos_services.linux2rest)
        self._cached_mac: str | None = None

    async def get_network_info(self) -> NetworkInfo:
        """Get current network information including device identity."""
        connection = await self.get_connection_status()
        networks = await self.scan_networks()
        hotspot_ssid = await self._get_hotspot_ssid()

        return NetworkInfo(
            connection=connection,
            available_networks=networks,
            is_scanning=False,
            serial_number=DORIS_SERIAL_NUMBER,
            hotspot_ssid=hotspot_ssid,
        )

    async def _get_hotspot_ssid(self) -> str | None:
        """Get the hotspot SSID from the WiFi Manager hotspot credentials."""
        try:
            creds = await self._client.get_hotspot_credentials()
            return creds.get("ssid")
        except Exception as e:
            logger.warning(f"Failed to get hotspot credentials: {e}")
            return None

    async def _get_wlan_mac(self) -> str | None:
        """Get MAC address of the wlan0 interface from linux2rest."""
        try:
            interfaces: list[dict[str, Any]] = await self._linux2rest.get(  # type: ignore[assignment]
                "/system/network"
            )
            for iface in interfaces:
                if iface.get("name", "").startswith("wlan"):
                    mac = iface.get("mac")
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

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._client.close()
        await self._linux2rest.close()
