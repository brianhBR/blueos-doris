"""Tests for Pydantic models."""

from datetime import datetime
import pytest

from doris.models.system import SystemStatus, BatteryInfo, StorageInfo, LocationInfo
from doris.models.network import WifiNetwork, ConnectionStatus
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
