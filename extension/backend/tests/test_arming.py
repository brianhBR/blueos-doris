"""Tests for the vehicle arming-status service (issues #44, #8)."""

import time

from doris.services import arming
from doris.services.arming import ArmingService


def test_base_mode_bits_parsing() -> None:
    assert arming._base_mode_bits({"bits": 209}) == 209
    assert arming._base_mode_bits(81) == 81
    assert arming._base_mode_bits({"nope": 1}) is None
    assert arming._base_mode_bits(None) is None
    assert arming._base_mode_bits("x") is None


def test_decode_heartbeat_armed_accepts_missing_type() -> None:
    armed, known = arming._decode_heartbeat_armed({
        "message": {"base_mode": {"bits": 209}},
    })
    assert (armed, known) == (True, True)

    disarmed, known = arming._decode_heartbeat_armed({
        "message": {"type": "HEARTBEAT", "base_mode": {"bits": 81}},
    })
    assert (disarmed, known) == (False, True)


def test_decode_heartbeat_armed_rejects_gcs_and_wrong_type() -> None:
    assert arming._decode_heartbeat_armed({
        "message": {
            "type": "HEARTBEAT",
            "mavtype": {"type": "MAV_TYPE_GCS"},
            "base_mode": {"bits": 209},
        },
    }) == (False, False)
    assert arming._decode_heartbeat_armed({
        "message": {"type": "STATUSTEXT", "base_mode": {"bits": 209}},
    }) == (False, False)
    assert arming._decode_heartbeat_armed({"message": {}}) == (False, False)


def test_is_prearm_text() -> None:
    assert arming._is_prearm_text("PreArm: GPS horizontal error")
    assert arming._is_prearm_text("prearm: 3D fix required")
    assert arming._is_prearm_text("Arm: compass not calibrated")
    # Unrelated / success messages are ignored.
    assert not arming._is_prearm_text("EKF3 IMU0 is using GPS")
    assert not arming._is_prearm_text("Throttle armed")
    assert not arming._is_prearm_text("")


def test_decode_text_joins_char_list() -> None:
    chars = list("PreArm: bad") + ["\x00", "\x00"]
    assert arming._decode_text(chars) == "PreArm: bad"


async def _no_network(service: ArmingService) -> None:
    # Stop get_status from spawning the WS subscriber during tests.
    service._ensure_statustext_subscriber = lambda: None  # type: ignore[method-assign]


async def test_disarmed_with_failures_reports_waiting() -> None:
    service = ArmingService()
    await _no_network(service)

    async def fake_read_armed():
        return False, True

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    await service._record_failure("PreArm: GPS horizontal error", 4)
    await service._record_failure("PreArm: 3D fix required", 4)

    status = await service.get_status()
    assert status["armed"] is False
    assert status["armed_known"] is True
    assert status["waiting_to_arm"] is True
    texts = {r["text"] for r in status["reasons"]}
    assert texts == {"PreArm: GPS horizontal error", "PreArm: 3D fix required"}


async def test_duplicate_reasons_are_deduped() -> None:
    service = ArmingService()
    await _no_network(service)

    async def fake_read_armed():
        return False, True

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    await service._record_failure("PreArm: GPS horizontal error", 4)
    await service._record_failure("PreArm: GPS horizontal error", 4)

    status = await service.get_status()
    assert len(status["reasons"]) == 1


async def test_armed_clears_failures() -> None:
    service = ArmingService()
    await _no_network(service)

    async def fake_read_armed():
        return True, True

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    await service._record_failure("PreArm: GPS horizontal error", 4)
    status = await service.get_status()
    assert status["armed"] is True
    assert status["waiting_to_arm"] is False
    assert status["reasons"] == []


async def test_unknown_armed_state_suppresses_banner() -> None:
    service = ArmingService()
    await _no_network(service)

    async def fake_read_armed():
        return False, False  # heartbeat unreadable

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    await service._record_failure("PreArm: GPS horizontal error", 4)
    status = await service.get_status()
    # We can't confirm the vehicle is disarmed, so don't claim "waiting".
    assert status["armed_known"] is False
    assert status["waiting_to_arm"] is False
    assert status["armed"] is False


async def test_unknown_preserves_last_confirmed_armed() -> None:
    """A dropped HEARTBEAT must not report disarmed after a confirmed arm."""
    service = ArmingService()
    await _no_network(service)
    reads = iter([(True, True), (False, False)])

    async def fake_read_armed():
        return next(reads)

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    armed = await service.get_status()
    assert armed["armed"] is True
    assert armed["armed_known"] is True

    dropped = await service.get_status()
    assert dropped["armed"] is True
    assert dropped["armed_known"] is False
    assert dropped["waiting_to_arm"] is False


async def test_unknown_preserves_last_confirmed_disarmed() -> None:
    service = ArmingService()
    await _no_network(service)
    reads = iter([(False, True), (False, False)])

    async def fake_read_armed():
        return next(reads)

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    disarmed = await service.get_status()
    assert disarmed["armed"] is False
    assert disarmed["armed_known"] is True

    dropped = await service.get_status()
    assert dropped["armed"] is False
    assert dropped["armed_known"] is False


async def test_stale_reasons_are_pruned() -> None:
    service = ArmingService()
    await _no_network(service)

    async def fake_read_armed():
        return False, True

    service._read_armed = fake_read_armed  # type: ignore[assignment]

    await service._record_failure("PreArm: old reason", 4)
    # Force the reason to look older than the TTL.
    key = "prearm: old reason"
    service._failures[key]["last_seen"] = time.monotonic() - arming.FAILURE_TTL_S - 1

    status = await service.get_status()
    assert status["reasons"] == []
    assert status["waiting_to_arm"] is False
