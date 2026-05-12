"""Tests for Pydantic models."""

from datetime import datetime, timezone
import pytest

from doris.models.system import SystemStatus, BatteryInfo, StorageInfo, LocationInfo
from doris.models.network import (
    ConnectionStatus,
    WifiNetwork,
    WlanLastAttempt,
    WlanState,
)
from doris.models.sensors import ModuleInfo
from doris.models.missions import Mission, MissionConfig, MissionStatus, TriggerConfig, TriggerType


def test_battery_info():
    """Test BatteryInfo model."""
    battery = BatteryInfo(
        level=87.0,
        voltage=14.2,
        time_remaining="12.5 hours",
    )
    assert battery.level == 87.0
    assert battery.voltage == 14.2
    assert battery.time_remaining == "12.5 hours"
    assert battery.charging is False


def test_storage_info():
    """Test StorageInfo model."""
    storage = StorageInfo(
        total_gb=500.0,
        used_gb=225.0,
        available_gb=275.0,
        used_percent=45.0,
    )
    assert storage.total_gb == 500.0
    assert storage.available_gb == 275.0
    assert storage.used_percent == 45.0


def test_location_info():
    """Test LocationInfo model."""
    location = LocationInfo(
        latitude=41.7128,
        longitude=-74.006,
        altitude=100.0,
        depth=0.0,
        heading=180.0,
        speed=0.5,
        satellites=12,
        fix_type="3d",
        last_update="Just now",
    )
    assert location.latitude == 41.7128
    assert location.satellites == 12
    assert location.fix_type == "3d"


def test_wifi_network():
    """Test WifiNetwork model."""
    network = WifiNetwork(
        ssid="DORIS_HotSpot",
        signal_strength=95,
        security="WPA2",
        frequency="2.4GHz",
        is_saved=True,
    )
    assert network.ssid == "DORIS_HotSpot"
    assert network.is_saved is True
    assert network.is_connected is False


def test_connection_status():
    """Test ConnectionStatus model."""
    status = ConnectionStatus(
        is_connected=True,
        ssid="TestNetwork",
        ip_address="192.168.1.100",
    )
    assert status.is_connected is True
    assert status.ssid == "TestNetwork"


def test_wlan_state_default_is_ap():
    """Default WlanState is the safe ``ap`` mode with no last attempt."""
    state = WlanState(mode="ap")
    assert state.mode == "ap"
    assert state.target_ssid is None
    assert state.ip_address is None
    assert state.last_attempt is None


def test_wlan_state_round_trips_through_json():
    """The persisted intent file is JSON; verify it survives a round-trip."""
    attempt = WlanLastAttempt(
        ssid="HomeWiFi",
        status="failed",
        error="Timed out waiting for association",
        timestamp=datetime(2026, 5, 11, 22, 30, tzinfo=timezone.utc),
    )
    state = WlanState(
        mode="sta_connected",
        target_ssid="HomeWiFi",
        ip_address="192.168.1.42",
        last_attempt=attempt,
    )
    blob = state.model_dump_json()
    restored = WlanState.model_validate_json(blob)
    assert restored == state
    assert restored.last_attempt is not None
    assert restored.last_attempt.status == "failed"


def test_wlan_state_rejects_unknown_mode():
    """Mode is a Literal; bad values must fail validation."""
    with pytest.raises(Exception):
        WlanState(mode="sideways")  # type: ignore[arg-type]


def test_module_info():
    """Test ModuleInfo model."""
    module = ModuleInfo(
        id="camera-1",
        name="Camera Module",
        type="camera",
        status="connected",
        module_status="Ready: Active",
        power_usage=95.0,
    )
    assert module.type == "camera"
    assert module.status == "connected"
    assert module.module_status == "Ready: Active"


def test_mission_config():
    """Test MissionConfig model."""
    config = MissionConfig(
        name="Test Mission",
        start_trigger=TriggerConfig(trigger_type=TriggerType.MANUAL),
        end_trigger=TriggerConfig(
            trigger_type=TriggerType.DURATION,
            value=3600,
            unit="seconds",
        ),
        timelapse_enabled=True,
        timelapse_interval=30,
    )
    assert config.name == "Test Mission"
    assert config.timelapse_enabled is True


def test_mission():
    """Test Mission model."""
    config = MissionConfig(
        name="Test Mission",
        start_trigger=TriggerConfig(trigger_type=TriggerType.MANUAL),
        end_trigger=TriggerConfig(trigger_type=TriggerType.DURATION, value=60),
    )

    mission = Mission(
        id="mission-001",
        name="Test Mission",
        status=MissionStatus.PENDING,
        config=config,
        created_at=datetime.now(),
    )

    assert mission.id == "mission-001"
    assert mission.status == MissionStatus.PENDING
    assert mission.started_at is None


def test_trigger_config():
    """Test TriggerConfig model."""
    trigger = TriggerConfig(
        trigger_type=TriggerType.DURATION,
        value=3600,
        unit="seconds",
    )
    assert trigger.trigger_type == TriggerType.DURATION
    assert trigger.value == 3600


def test_system_status():
    """Test SystemStatus model."""
    status = SystemStatus(
        connected=True,
        battery_level=85.0,
        battery_voltage=14.2,
        battery_time_remaining="10 hours",
        storage_used_percent=45.0,
        storage_used_gb=225.0,
        storage_total_gb=500.0,
        cpu_usage=25.0,
        memory_usage=40.0,
        temperature=45.0,
        uptime="12:30:00",
    )
    assert status.connected is True
    assert status.battery_level == 85.0
    assert status.storage_used_percent == 45.0
