"""Network-related models."""

from datetime import datetime
from typing import Literal

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
    serial_number: str | None = None
    hotspot_ssid: str | None = None


class NetworkCredentials(BaseModel):
    """Credentials for connecting to a network."""

    ssid: str
    password: str | None = None


# ── WLAN mode switching (single external radio AP <-> STA) ──────────

WlanMode = Literal["ap", "sta_pending", "sta_connected"]
WlanAttemptStatus = Literal["success", "failed"]


class WlanLastAttempt(BaseModel):
    """Outcome of the most recent AP <-> STA switch attempt.

    Surfaced in the UI when the user reconnects to the AP after a failed
    STA switch — without this they would have no way of knowing why their
    browser session went dead and what to fix.
    """

    ssid: str
    status: WlanAttemptStatus
    error: str | None = None
    timestamp: datetime


class WlanState(BaseModel):
    """Current state of the external radio (uap0) for AP/STA switching.

    ``mode`` is the *intent* recorded by the DORIS extension:
      - ``ap``: external radio is broadcasting the DORIS hotspot
      - ``sta_pending``: switch initiated, connection attempt in flight
      - ``sta_connected``: external radio is associated to a client WLAN
    The intent always resets to ``ap`` on backend startup so a power
    cycle reliably reverts to the hotspot.
    """

    mode: WlanMode
    target_ssid: str | None = None
    ip_address: str | None = None
    last_attempt: WlanLastAttempt | None = None

