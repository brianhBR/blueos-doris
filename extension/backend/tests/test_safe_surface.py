"""Tests for the AGT safe-surface MAVLink protocol."""

import asyncio
import json

from doris.services import safe_surface as module
from doris.services.safe_surface import (
    SafeSurfaceService,
    _named_float_payload,
    parse_named_value,
)


def _message(component: int, name: str, value: float, system: int = 1) -> str:
    return json.dumps({
        "header": {"system_id": system, "component_id": component},
        "message": {
            "type": "NAMED_VALUE_FLOAT",
            "name": list(name.ljust(10, "\x00")),
            "value": value,
        },
    })


COMPATIBLE_FIRMWARE = {
    "known": True,
    "compatible": True,
    "version": "v0.3.0",
    "min_required": "v0.3.0",
}
OLD_FIRMWARE = {
    "known": True,
    "compatible": False,
    "version": "v0.2.3",
    "min_required": "v0.3.0",
}


def _healthy_frame() -> dict:
    return {"frame_applied": True, "relay": {"ok": True}}


def _healthy_agt_service() -> SafeSurfaceService:
    service = SafeSurfaceService()
    service.process_named_value(
        1, 192, "AGT_CAP", float(module.REQUIRED_CAPABILITIES)
    )
    service.process_named_value(1, 192, "REL_STAT", 0.0)
    service.process_named_value(1, 1, "RELAY", 0.0)
    return service


def test_parse_named_value():
    assert parse_named_value(_message(192, "AGT_CAP", 3)) == (
        1, 192, "AGT_CAP", 3.0,
    )
    assert parse_named_value(json.dumps({"message": {"type": "STATUSTEXT"}})) is None


def test_parse_named_value_rejects_nonfinite_and_malformed_values():
    assert parse_named_value(_message(192, "AGT_CAP", float("nan"))) is None
    assert parse_named_value(_message(192, "AGT_CAP", float("inf"))) is None
    assert parse_named_value(_message(192, "AGT_CAP", float("-inf"))) is None
    malformed = json.loads(_message(192, "AGT_CAP", 3.0))
    malformed["message"]["value"] = "not-a-number"
    assert parse_named_value(json.dumps(malformed)) is None


def test_process_rejects_invalid_capability_values():
    service = SafeSurfaceService()
    for value in (float("nan"), float("inf"), -1.0, 3.5, 256.0, "bad"):
        service.process_named_value(1, 192, "AGT_CAP", value)
        assert service.state.capabilities is None
        assert service.state.last_capability_update_monotonic is None


def test_process_rejects_out_of_range_binary_values():
    service = SafeSurfaceService()
    for name, component in (("RELAY", 1), ("REL_STAT", 192), ("PWR_SHDN", 192)):
        for value in (float("nan"), float("inf"), -0.11, 0.11, 0.5, 0.89, 1.11):
            service.process_named_value(1, component, name, value)
        if name == "RELAY":
            assert service.state.release_requested is None
        elif name == "REL_STAT":
            assert service.state.release_actual is None
        else:
            assert service.state.power_shutdown_requested is False


def test_process_accepts_agt_binary_tolerance_boundaries():
    service = SafeSurfaceService()
    service.process_named_value(1, 1, "RELAY", -0.1)
    service.process_named_value(1, 192, "REL_STAT", 0.9)
    assert service.state.release_requested is False
    assert service.state.release_actual is True


def test_status_unknown_is_not_compatible():
    service = SafeSurfaceService()
    status = service.status({
        "known": False,
        "compatible": None,
    })
    assert status["capabilities_known"] is False
    assert status["capabilities_compatible"] is False
    assert status["compatible"] is False


def test_release_request_and_actual_mismatch():
    service = SafeSurfaceService()
    service.process_named_value(1, 1, "RELAY", 1.0)
    service.process_named_value(1, 192, "REL_STAT", 0.0)
    assert service.status()["release_mismatch"] is True


def test_wrong_source_cannot_change_shutdown_state():
    service = SafeSurfaceService()
    service.process_named_value(1, 191, "PWR_SHDN", 1.0)
    service.process_named_value(255, 192, "PWR_SHDN", 1.0)
    assert service.state.shutdown_state in {"disabled", "idle"}
    assert service.state.power_shutdown_requested is False
    assert service._shutdown_task is None


def test_capability_and_firmware_are_both_required():
    service = SafeSurfaceService()
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    assert service.status({"compatible": False})["compatible"] is False
    assert service.status({"compatible": True})["compatible"] is True


def test_ack_payload_uses_agt_accepted_source():
    payload = _named_float_payload("PWR_ACK", 1.0)
    assert payload["header"]["system_id"] == 1
    assert payload["header"]["component_id"] == 191
    assert payload["message"]["value"] == 1.0


def test_shutdown_requires_verified_firmware(monkeypatch):
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    service = SafeSurfaceService()
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.process_named_value(1, 192, "PWR_SHDN", 1.0)
    assert service._shutdown_task is None
    assert service.state.shutdown_error == "AGT firmware compatibility is not verified"


def test_shutdown_requires_fresh_capability(monkeypatch):
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    service = SafeSurfaceService()
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.status({"compatible": True})

    monkeypatch.setattr(module.time, "monotonic", lambda: 106.0)
    service.process_named_value(1, 192, "PWR_SHDN", 1.0)

    assert service._shutdown_task is None
    assert service.state.shutdown_error == "AGT capability advertisement is stale"


async def test_repeated_shutdown_request_spawns_one_task(monkeypatch):
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    service = SafeSurfaceService()
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.status({"compatible": True})
    started = []
    release = asyncio.Event()

    async def sequence():
        started.append(True)
        await release.wait()

    monkeypatch.setattr(service, "_shutdown_sequence", sequence)
    service.process_named_value(1, 192, "PWR_SHDN", 1.0)
    service.process_named_value(1, 192, "PWR_SHDN", 1.0)
    await asyncio.sleep(0)
    assert len(started) == 1

    release.set()
    await service._shutdown_task
    service.process_named_value(1, 192, "PWR_SHDN", 0.0)
    assert service._shutdown_request_latched is False
    assert service.state.power_shutdown_requested is False
    assert service.state.shutdown_state == "idle"


async def test_failed_shutdown_allows_retry(monkeypatch):
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    service = SafeSurfaceService()
    service._shutdown_request_latched = True

    async def fail_flush():
        raise RuntimeError("flush failed")

    monkeypatch.setattr(service, "_flush_and_finalize", fail_flush)
    await service._shutdown_sequence()
    assert service.state.shutdown_state == "error"
    assert service._shutdown_request_latched is False

    retries = []

    async def retry():
        retries.append(True)

    monkeypatch.setattr(service, "_shutdown_sequence", retry)
    service.state.firmware_compatible = True
    service.process_named_value(
        1, 192, "AGT_CAP", float(module.REQUIRED_CAPABILITIES)
    )
    service.process_named_value(1, 192, "PWR_SHDN", 1.0)
    await service._shutdown_task
    assert retries == [True]


def test_agt_status_staleness(monkeypatch):
    service = SafeSurfaceService()
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.process_named_value(1, 192, "REL_STAT", 0.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 104.9)
    assert service.status()["agt_status_stale"] is False
    monkeypatch.setattr(module.time, "monotonic", lambda: 105.1)
    assert service.status()["agt_status_stale"] is True


def test_release_request_freshness_is_independent(monkeypatch):
    service = SafeSurfaceService()
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    service.process_named_value(1, 1, "RELAY", 0.0)
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.process_named_value(1, 192, "REL_STAT", 0.0)

    monkeypatch.setattr(module.time, "monotonic", lambda: 106.0)
    service.process_named_value(1, 192, "AGT_CAP", 3.0)
    service.process_named_value(1, 192, "REL_STAT", 0.0)
    status = service.status()
    assert status["agt_status_stale"] is False
    assert status["release_request_stale"] is True
    assert status["release_request_known"] is False


def test_navigator_only_config_can_start_mission():
    service = SafeSurfaceService()
    readiness = service.evaluate_release_readiness(OLD_FIRMWARE, _healthy_frame())
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert readiness["navigator_release_available"] is True
    assert readiness["agt_release_available"] is False
    assert readiness["warnings"]


def test_agt_only_config_can_start_mission():
    service = _healthy_agt_service()
    frame = {"frame_applied": True, "relay": {"ok": False}}
    readiness = service.evaluate_release_readiness(COMPATIBLE_FIRMWARE, frame)
    assert readiness["ready"] is True
    assert readiness["agt_release_available"] is True
    assert readiness["navigator_release_available"] is False
    assert any("Navigator release output" in w for w in readiness["warnings"])


def test_losing_both_release_paths_blocks_mission():
    service = SafeSurfaceService()
    frame = {"frame_applied": False, "relay": {"ok": False}}
    readiness = service.evaluate_release_readiness(OLD_FIRMWARE, frame)
    assert readiness["ready"] is False
    assert (
        "Neither the Navigator nor the AGT release path is available"
        in readiness["blockers"]
    )


def test_release_mismatch_only_removes_the_agt_path():
    service = _healthy_agt_service()
    service.process_named_value(1, 1, "RELAY", 1.0)
    readiness = service.evaluate_release_readiness(
        COMPATIBLE_FIRMWARE, _healthy_frame()
    )
    assert readiness["agt_release_available"] is False
    assert readiness["ready"] is True
    assert any("disagree" in w for w in readiness["warnings"])


def test_enabled_shutdown_requires_the_agt_release_path(monkeypatch):
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    degraded = SafeSurfaceService().evaluate_release_readiness(
        OLD_FIRMWARE, _healthy_frame()
    )
    assert degraded["ready"] is False
    assert any("host shutdown is enabled" in b for b in degraded["blockers"])

    healthy = _healthy_agt_service().evaluate_release_readiness(
        COMPATIBLE_FIRMWARE, _healthy_frame()
    )
    assert healthy["ready"] is True
    assert healthy["blockers"] == []


async def test_ack_precedes_poweroff(monkeypatch):
    service = SafeSurfaceService()
    events = []

    async def flush():
        events.append("flush")

    async def command(command):
        events.append(command)
        return True

    async def send_ack(name, value):
        events.append(f"{name}={value:g}")
        return True

    monkeypatch.setattr(service, "_flush_and_finalize", flush)
    monkeypatch.setattr(service, "_run_host_command", command)
    monkeypatch.setattr(service, "_send_named_float", send_ack)

    await service._shutdown_sequence()

    assert events == [
        "flush",
        "sync",
        "PWR_ACK=1",
        "sudo systemctl poweroff",
    ]


def test_recovery_is_latched_from_the_lua_state():
    """A late UI poll needs proof the dive finished, not just that it stopped."""
    service = SafeSurfaceService()
    assert service.recovery_seen() is False

    service.process_named_value(1, 1, "STATE", 3.0)  # ASCENT
    assert service.recovery_seen() is False

    service.process_named_value(1, 1, "STATE", 4.0)  # RECOVERY
    assert service.recovery_seen() is True

    # Lua restarting in CONFIG after a deck power cycle must not clear it.
    service.process_named_value(1, 1, "STATE", -1.0)
    assert service.recovery_seen() is True


def test_recovery_latch_ignores_other_senders():
    service = SafeSurfaceService()
    service.process_named_value(1, 192, "STATE", 4.0)
    service.process_named_value(2, 1, "STATE", 4.0)
    assert service.recovery_seen() is False


async def test_shutdown_defers_heavy_processing(monkeypatch, tmp_path):
    """The AGT holds power up while it waits, so this path must stay cheap."""
    monkeypatch.setattr(module.settings, "agt_shutdown_enabled", True)
    service = SafeSurfaceService()
    service._shutdown_request_latched = True

    from doris.services import dive_processing

    monkeypatch.setattr(dive_processing, "_data_root", lambda: tmp_path)

    def _explode(*args, **kwargs):
        raise AssertionError("shutdown must not run post-processing")

    monkeypatch.setattr(dive_processing.binlog, "archive_dive_bin_logs", _explode)
    monkeypatch.setattr(
        dive_processing.dive_csv_export, "export_dive_csv_to_usb", _explode
    )

    events: list[str] = []

    async def command(command):
        events.append(command)
        return True

    async def send_ack(name, value):
        events.append(f"{name}={value:g}")
        return True

    monkeypatch.setattr(service, "_run_host_command", command)
    monkeypatch.setattr(service, "_send_named_float", send_ack)

    await service._shutdown_sequence()

    assert events == ["sync", "PWR_ACK=1", "sudo systemctl poweroff"]
