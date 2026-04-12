"""BlueOS WiFi Manager v2.0 API client.

v2.0 includes all v1.0 endpoints at /v2.0/* AND new interface-aware endpoints:

Interface management:
  GET  /interfaces/                     - List all WiFi interfaces
  GET  /interfaces/{interface_name}     - Get specific interface status

Per-interface WiFi operations:
  GET  /wifi/scan/{interface_name}      - Scan on specific interface
  GET  /wifi/scan                       - Scan on all interfaces
  GET  /wifi/status/{interface_name}    - Status for specific interface
  GET  /wifi/status                     - Status for all interfaces
  POST /wifi/connect                    - Connect (body: {ssid, password, interface?})
  POST /wifi/disconnect                 - Disconnect (body: {interface?})
  GET  /wifi/saved                      - List saved networks
  DELETE /wifi/saved/{ssid}             - Delete saved network

Hotspot management:
  GET  /wifi/hotspot/{interface_name}   - Get hotspot status for interface
  POST /wifi/hotspot/enable             - Enable hotspot
  POST /wifi/hotspot/disable            - Disable hotspot
  POST /wifi/hotspot/credentials        - Set hotspot credentials

Interface mode:
  GET  /wifi/mode/{interface_name}      - Get interface mode (normal/hotspot/dual)
  POST /wifi/mode                       - Set interface mode
"""

from __future__ import annotations

from typing import Any

from ...base import BlueOSClient


class NetworkV2Client:
    """Client for BlueOS WiFi Manager v2.0 API.

    Extends v1 with multi-interface support: each WiFi adapter can be
    managed independently, scanned separately, and placed into different
    modes (normal, hotspot, dual).
    """

    API_VERSION = "v2.0"

    def __init__(self, base_url: str, timeout: float = 10.0):
        self._client = BlueOSClient(base_url, timeout=timeout)

    def _path(self, endpoint: str) -> str:
        return f"/{self.API_VERSION}{endpoint}"

    # ── Legacy-compatible endpoints (same as v1 but under /v2.0) ─────

    async def get_status(self) -> dict[str, Any]:
        """Get current connection status (legacy endpoint)."""
        return await self._client.get(self._path("/status"))

    async def scan(self) -> list[dict[str, Any]]:
        """Scan for available WiFi networks (legacy endpoint)."""
        return await self._client.get(self._path("/scan"))

    async def get_saved(self) -> list[dict[str, Any]]:
        """Get saved WiFi networks (legacy endpoint)."""
        return await self._client.get(self._path("/saved"))

    async def connect(self, ssid: str, password: str, *, hidden: bool = False) -> Any:
        """Connect to a WiFi network (legacy endpoint)."""
        return await self._client.post(
            self._path(f"/connect?hidden={str(hidden).lower()}"),
            json={"ssid": ssid, "password": password},
        )

    async def remove(self, ssid: str) -> Any:
        """Remove a saved WiFi network (legacy endpoint)."""
        return await self._client.post(
            self._path(f"/remove?ssid={ssid}"),
        )

    async def disconnect(self) -> Any:
        """Disconnect from current WiFi network (legacy endpoint)."""
        return await self._client.get(self._path("/disconnect"))

    async def get_hotspot(self) -> dict[str, Any]:
        """Get hotspot state (legacy endpoint)."""
        return await self._client.get(self._path("/hotspot"))

    async def set_hotspot(self, enable: bool) -> Any:
        """Enable or disable hotspot (legacy endpoint)."""
        return await self._client.post(
            self._path(f"/hotspot?enable={str(enable).lower()}"),
        )

    async def get_hotspot_extended_status(self) -> dict[str, Any]:
        """Get extended hotspot status."""
        return await self._client.get(self._path("/hotspot_extended_status"))

    async def get_smart_hotspot(self) -> bool:
        """Check if smart-hotspot is enabled."""
        return await self._client.get(self._path("/smart_hotspot"))

    async def set_smart_hotspot(self, enable: bool) -> Any:
        """Enable or disable smart-hotspot."""
        return await self._client.post(
            self._path(f"/smart_hotspot?enable={str(enable).lower()}"),
        )

    async def get_hotspot_credentials(self) -> dict[str, Any]:
        """Get hotspot credentials."""
        return await self._client.get(self._path("/hotspot_credentials"))

    async def set_hotspot_credentials(self, ssid: str, password: str) -> Any:
        """Set hotspot credentials (legacy endpoint)."""
        return await self._client.post(
            self._path("/hotspot_credentials"),
            json={"ssid": ssid, "password": password},
        )

    # ── v2-only: Interface management ────────────────────────────────

    async def list_interfaces(self) -> dict[str, Any]:
        """List all WiFi interfaces.

        Returns WifiInterfaceList:
          {interfaces: [{name, connected, ssid?, signal_strength?,
                         ip_address?, mac_address?, mode, supports_hotspot,
                         supports_dual_mode}],
           hotspot_interface?: str}
        """
        return await self._client.get(self._path("/interfaces/"))

    async def get_interface(self, interface_name: str) -> dict[str, Any]:
        """Get detailed status for a specific WiFi interface.

        Returns WifiInterface:
          {name, connected, ssid?, signal_strength?, ip_address?,
           mac_address?, mode, supports_hotspot, supports_dual_mode}
        """
        return await self._client.get(self._path(f"/interfaces/{interface_name}"))

    # ── v2-only: Per-interface WiFi operations ───────────────────────

    async def wifi_scan_interface(self, interface_name: str) -> dict[str, Any]:
        """Scan for networks on a specific interface.

        Returns WifiInterfaceScanResult:
          {interface: str, networks: [{ssid, bssid, flags, frequency, signallevel}]}
        """
        return await self._client.get(self._path(f"/wifi/scan/{interface_name}"))

    async def wifi_scan_all(self) -> list[dict[str, Any]]:
        """Scan for networks on all interfaces.

        Returns list of WifiInterfaceScanResult.
        """
        return await self._client.get(self._path("/wifi/scan"))

    async def wifi_status_interface(self, interface_name: str) -> dict[str, Any]:
        """Get connection status for a specific interface.

        Returns WifiInterfaceStatus:
          {interface, state, ssid?, bssid?, ip_address?,
           signal_strength?, frequency?, key_mgmt?}
        """
        return await self._client.get(self._path(f"/wifi/status/{interface_name}"))

    async def wifi_status_all(self) -> list[dict[str, Any]]:
        """Get connection status for all interfaces.

        Returns list of WifiInterfaceStatus.
        """
        return await self._client.get(self._path("/wifi/status"))

    async def wifi_connect(
        self, ssid: str, password: str, *, interface: str | None = None
    ) -> Any:
        """Connect to a WiFi network (v2 endpoint, interface-aware)."""
        payload: dict[str, Any] = {"ssid": ssid, "password": password}
        if interface:
            payload["interface"] = interface
        return await self._client.post(self._path("/wifi/connect"), json=payload)

    async def wifi_disconnect(self, *, interface: str | None = None) -> Any:
        """Disconnect from WiFi (v2 endpoint, interface-aware)."""
        payload: dict[str, Any] = {}
        if interface:
            payload["interface"] = interface
        return await self._client.post(self._path("/wifi/disconnect"), json=payload)

    async def wifi_saved(self) -> list[dict[str, Any]]:
        """List saved networks (v2 endpoint)."""
        return await self._client.get(self._path("/wifi/saved"))

    async def wifi_delete_saved(self, ssid: str) -> Any:
        """Delete a saved network by SSID (v2 endpoint)."""
        return await self._client.delete(self._path(f"/wifi/saved/{ssid}"))

    # ── v2-only: Hotspot per interface ───────────────────────────────

    async def wifi_hotspot_status(self, interface_name: str) -> dict[str, Any]:
        """Get hotspot status for a specific interface."""
        return await self._client.get(self._path(f"/wifi/hotspot/{interface_name}"))

    async def wifi_hotspot_enable(self, interface: str | None = None) -> Any:
        """Enable hotspot (v2 endpoint)."""
        payload: dict[str, Any] = {}
        if interface:
            payload["interface"] = interface
        return await self._client.post(self._path("/wifi/hotspot/enable"), json=payload)

    async def wifi_hotspot_disable(self, interface: str | None = None) -> Any:
        """Disable hotspot (v2 endpoint)."""
        payload: dict[str, Any] = {}
        if interface:
            payload["interface"] = interface
        return await self._client.post(
            self._path("/wifi/hotspot/disable"), json=payload
        )

    async def wifi_hotspot_set_credentials(
        self, ssid: str, password: str, *, interface: str | None = None,
    ) -> Any:
        """Set hotspot credentials (v2 endpoint)."""
        payload: dict[str, Any] = {
            "credentials": {"ssid": ssid, "password": password},
        }
        if interface:
            payload["interface"] = interface
        return await self._client.post(
            self._path("/wifi/hotspot/credentials"), json=payload,
        )

    # ── v2-only: Interface mode ──────────────────────────────────────

    async def wifi_get_mode(self, interface_name: str) -> dict[str, Any]:
        """Get interface mode and capabilities.

        Returns WifiInterfaceCapabilities:
          {interface, supports_ap_mode, supports_dual_mode,
           current_mode (normal|hotspot|dual), available_modes}
        """
        return await self._client.get(self._path(f"/wifi/mode/{interface_name}"))

    async def wifi_set_mode(
        self, interface: str, mode: str, timeout: float | None = None,
    ) -> Any:
        """Set interface mode (normal, hotspot, or dual).

        Starting a hotspot via create_ap can take 15+ seconds; callers
        should pass a generous ``timeout`` when switching to hotspot mode.
        """
        return await self._client.post(
            self._path("/wifi/mode"),
            json={"interface": interface, "mode": mode},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.close()
