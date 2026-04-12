"""Unified BlueOS WiFi Manager client that abstracts v1 and v2 APIs.

Tries v2 first (multi-interface support), falls back to v1 automatically.
Callers use a single interface regardless of which API version the
BlueOS instance exposes.
"""

from __future__ import annotations

import logging
from typing import Any

from .v1 import NetworkV1Client
from .v2 import NetworkV2Client

logger = logging.getLogger(__name__)


class NetworkClient:
    """Unified WiFi Manager client supporting both v1 and v2 APIs.

    On first call, probes the v2 root endpoint. If it responds, all
    subsequent calls use v2. Otherwise, falls back to v1 for the
    lifetime of this client instance.

    Usage:
        client = NetworkClient("http://host.docker.internal:9000")
        status = await client.get_status()
        networks = await client.scan()
        await client.connect("MyNetwork", "password123")
        await client.close()
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base_url = base_url
        self._timeout = timeout
        self._v1 = NetworkV1Client(base_url, timeout=timeout)
        self._v2 = NetworkV2Client(base_url, timeout=timeout)
        self._use_v2: bool | None = None

    async def _detect_version(self) -> bool:
        """Detect whether the v2 API is available. Result is cached."""
        if self._use_v2 is not None:
            return self._use_v2
        try:
            await self._v2.list_interfaces()
            self._use_v2 = True
            logger.info("WiFi Manager v2.0 API detected")
        except Exception:
            self._use_v2 = False
            logger.info("WiFi Manager v2.0 not available, falling back to v1.0")
        return self._use_v2

    @property
    async def api_version(self) -> str:
        """Return the detected API version string."""
        v2 = await self._detect_version()
        return "v2.0" if v2 else "v1.0"

    # ── Connection status ────────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        """Get current WiFi connection status.

        Returns dict with keys like: state, ssid, ip_address, address,
        signallevel (varies by version).
        """
        if await self._detect_version():
            return await self._v2.get_status()
        return await self._v1.get_status()

    # ── Scanning ─────────────────────────────────────────────────────

    async def scan(self) -> list[dict[str, Any]]:
        """Scan for available WiFi networks.

        Returns list of {ssid, bssid, flags, frequency, signallevel}.
        """
        if await self._detect_version():
            return await self._v2.scan()
        return await self._v1.scan()

    async def scan_interface(self, interface_name: str) -> dict[str, Any]:
        """Scan for networks on a specific interface (v2 only).

        Returns {interface, networks: [...]}.
        Raises RuntimeError if v2 is not available.
        """
        if not await self._detect_version():
            raise RuntimeError(
                "Per-interface scanning requires WiFi Manager v2.0"
            )
        return await self._v2.wifi_scan_interface(interface_name)

    async def scan_all_interfaces(self) -> list[dict[str, Any]]:
        """Scan for networks across all interfaces (v2 only).

        Returns list of {interface, networks: [...]}.
        Falls back to legacy scan wrapped in a single-interface result.
        """
        if await self._detect_version():
            return await self._v2.wifi_scan_all()
        networks = await self._v1.scan()
        return [{"interface": "wlan0", "networks": networks}]

    # ── Saved networks ───────────────────────────────────────────────

    async def get_saved(self) -> list[dict[str, Any]]:
        """Get list of saved WiFi networks."""
        if await self._detect_version():
            return await self._v2.wifi_saved()
        return await self._v1.get_saved()

    async def forget_network(self, ssid: str) -> Any:
        """Remove/forget a saved WiFi network."""
        if await self._detect_version():
            return await self._v2.wifi_delete_saved(ssid)
        return await self._v1.remove(ssid)

    # ── Connect / Disconnect ─────────────────────────────────────────

    async def connect(
        self,
        ssid: str,
        password: str,
        *,
        hidden: bool = False,
        interface: str | None = None,
    ) -> Any:
        """Connect to a WiFi network.

        Args:
            ssid: Network SSID.
            password: Network password.
            hidden: Whether the network is hidden.
            interface: Specific interface to use (v2 only, ignored on v1).
        """
        if await self._detect_version():
            if interface:
                return await self._v2.wifi_connect(ssid, password, interface=interface)
            return await self._v2.connect(ssid, password, hidden=hidden)
        return await self._v1.connect(ssid, password, hidden=hidden)

    async def disconnect(self, *, interface: str | None = None) -> Any:
        """Disconnect from current WiFi network.

        Args:
            interface: Specific interface to disconnect (v2 only).
        """
        if await self._detect_version():
            if interface:
                return await self._v2.wifi_disconnect(interface=interface)
            return await self._v2.disconnect()
        return await self._v1.disconnect()

    # ── Interface management (v2 only, graceful on v1) ───────────────

    async def list_interfaces(self) -> dict[str, Any] | None:
        """List all WiFi interfaces (v2 only).

        Returns None if v2 is not available.
        """
        if await self._detect_version():
            return await self._v2.list_interfaces()
        return None

    async def get_interface(self, interface_name: str) -> dict[str, Any] | None:
        """Get specific interface status (v2 only)."""
        if await self._detect_version():
            return await self._v2.get_interface(interface_name)
        return None

    async def get_interface_status(
        self, interface_name: str | None = None
    ) -> Any:
        """Get WiFi status, optionally for a specific interface.

        On v2, returns per-interface status. On v1, returns legacy status.
        """
        if await self._detect_version():
            if interface_name:
                return await self._v2.wifi_status_interface(interface_name)
            return await self._v2.wifi_status_all()
        return await self._v1.get_status()

    # ── Hotspot ──────────────────────────────────────────────────────

    async def get_hotspot(self) -> dict[str, Any]:
        """Get hotspot state. Returns {supported, enabled}."""
        if await self._detect_version():
            return await self._v2.get_hotspot()
        return await self._v1.get_hotspot()

    async def set_hotspot(self, enable: bool, *, interface: str | None = None) -> Any:
        """Enable or disable the hotspot."""
        if await self._detect_version():
            if interface:
                if enable:
                    return await self._v2.wifi_hotspot_enable(interface)
                return await self._v2.wifi_hotspot_disable(interface)
            return await self._v2.set_hotspot(enable)
        return await self._v1.set_hotspot(enable)

    async def get_hotspot_credentials(self) -> dict[str, Any]:
        """Get hotspot credentials."""
        if await self._detect_version():
            return await self._v2.get_hotspot_credentials()
        return await self._v1.get_hotspot_credentials()

    async def set_hotspot_credentials(
        self, ssid: str, password: str, *, interface: str | None = None,
    ) -> Any:
        """Set hotspot credentials."""
        if await self._detect_version():
            return await self._v2.wifi_hotspot_set_credentials(
                ssid, password, interface=interface,
            )
        return await self._v1.set_hotspot_credentials(ssid, password)

    async def get_smart_hotspot(self) -> bool:
        """Check if smart-hotspot is enabled."""
        if await self._detect_version():
            return await self._v2.get_smart_hotspot()
        return await self._v1.get_smart_hotspot()

    async def set_smart_hotspot(self, enable: bool) -> Any:
        """Enable or disable smart-hotspot."""
        if await self._detect_version():
            return await self._v2.set_smart_hotspot(enable)
        return await self._v1.set_smart_hotspot(enable)

    # ── Interface mode (v2 only) ─────────────────────────────────────

    async def get_interface_mode(self, interface_name: str) -> dict[str, Any] | None:
        """Get interface mode and capabilities (v2 only).

        Returns {interface, supports_ap_mode, supports_dual_mode,
                 current_mode, available_modes} or None if v1.
        """
        if await self._detect_version():
            return await self._v2.wifi_get_mode(interface_name)
        return None

    async def set_interface_mode(
        self, interface: str, mode: str, timeout: float | None = None,
    ) -> Any:
        """Set interface mode: 'normal', 'hotspot', or 'dual' (v2 only).

        Args:
            timeout: Per-request timeout (seconds). Hotspot mode changes
                     can take 15+ seconds due to create_ap startup.

        Raises RuntimeError if v2 is not available.
        """
        if not await self._detect_version():
            raise RuntimeError("Interface mode requires WiFi Manager v2.0")
        return await self._v2.wifi_set_mode(interface, mode, timeout=timeout)

    # ── Lifecycle ────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close underlying HTTP clients."""
        await self._v1.close()
        await self._v2.close()
