"""Network/WiFi service."""

from ..config import blueos_services
from ..models.network import ConnectionStatus, NetworkCredentials, NetworkInfo, WifiNetwork
from .base import BlueOSClient


class NetworkService:
    """Service for managing network connections via BlueOS WiFi Manager."""

    def __init__(self):
        self.wifi_manager = BlueOSClient(blueos_services.wifi_manager)

    async def get_network_info(self) -> NetworkInfo:
        """Get current network information."""
        connection = await self.get_connection_status()
        networks = await self.scan_networks()

        return NetworkInfo(
            connection=connection,
            available_networks=networks,
            is_scanning=False,
        )

    async def get_connection_status(self) -> ConnectionStatus:
        """Get current connection status."""
        try:
            status = await self.wifi_manager.get("/v2.0/status")

            # BlueOS returns state: "connected" | "disconnected"
            is_connected = status.get("state") == "connected"

            return ConnectionStatus(
                is_connected=is_connected,
                ssid=status.get("ssid"),
                ip_address=status.get("ip_address"),
                mac_address=status.get("address"),
                signal_strength=status.get("signallevel"),
            )
        except Exception:
            # Return disconnected status
            return ConnectionStatus(
                is_connected=False,
                ssid=None,
            )

    async def scan_networks(self) -> list[WifiNetwork]:
        """Scan for available WiFi networks."""
        try:
            networks_data = await self.wifi_manager.get("/v2.0/scan")
            saved_networks = await self._get_saved_networks()
            connection_status = await self.get_connection_status()

            networks = []
            seen_ssids = set()  # Deduplicate networks with same SSID

            for net in networks_data:
                ssid = net.get("ssid", "")
                if not ssid:  # Skip hidden networks
                    continue
                if ssid in seen_ssids:  # Skip duplicates (same SSID, different BSSID)
                    continue
                seen_ssids.add(ssid)

                # Parse security from flags like "[WEP-WPA2-PSK-CCMP]"
                flags = net.get("flags", "")
                security = self._parse_security(flags)

                networks.append(
                    WifiNetwork(
                        ssid=ssid,
                        signal_strength=net.get("signallevel", 0),
                        security=security,
                        frequency=self._get_frequency_band(net.get("frequency", 2400)),
                        is_saved=ssid in saved_networks,
                        is_connected=(connection_status.is_connected and connection_status.ssid == ssid),
                    )
                )

            # Sort by signal strength
            networks.sort(key=lambda n: n.signal_strength, reverse=True)
            return networks

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to scan networks: {e}")
            # Return empty list on error
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
            await self.wifi_manager.post(
                "/v2.0/connect",
                json={
                    "ssid": credentials.ssid,
                    "password": credentials.password,
                },
            )
            # Wait a moment and return status
            return await self.get_connection_status()
        except Exception:
            return ConnectionStatus(
                is_connected=False,
                ssid=credentials.ssid,
            )

    async def disconnect(self) -> ConnectionStatus:
        """Disconnect from current network."""
        try:
            await self.wifi_manager.post("/v2.0/disconnect")
            return await self.get_connection_status()
        except Exception:
            return ConnectionStatus(is_connected=False)

    async def forget_network(self, ssid: str) -> bool:
        """Forget a saved network."""
        try:
            await self.wifi_manager.delete(f"/v2.0/saved/{ssid}")
            return True
        except Exception:
            return False

    async def _get_saved_networks(self) -> set[str]:
        """Get list of saved network SSIDs."""
        try:
            saved = await self.wifi_manager.get("/v2.0/saved")
            return {net.get("ssid", "") for net in saved}
        except Exception:
            return set()

    def _get_frequency_band(self, frequency_mhz: int) -> str:
        """Convert frequency to band string."""
        if frequency_mhz >= 5000:
            return "5GHz"
        return "2.4GHz"

    async def close(self) -> None:
        """Close HTTP client."""
        await self.wifi_manager.close()

