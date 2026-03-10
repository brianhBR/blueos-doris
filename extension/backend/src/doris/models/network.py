"""Network-related models."""

from pydantic import BaseModel


class WifiNetwork(BaseModel):
    """WiFi network information."""

    ssid: str
    signal_strength: int  # percentage
    security: str  # WPA2, WPA3, Open, etc.
    frequency: str  # 2.4GHz, 5GHz
    is_saved: bool = False
    is_connected: bool = False


class ConnectionStatus(BaseModel):
    """Network connection status."""

    is_connected: bool
    ssid: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    signal_strength: int | None = None


class NetworkInfo(BaseModel):
    """Complete network information."""

    connection: ConnectionStatus
    available_networks: list[WifiNetwork] = []
    is_scanning: bool = False


class NetworkCredentials(BaseModel):
    """Credentials for connecting to a network."""

    ssid: str
    password: str | None = None

