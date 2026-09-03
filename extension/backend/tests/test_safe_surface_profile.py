"""Static safety checks for the mirrored release profile."""

import json
from pathlib import Path

from doris.services import frame as frame_module
from doris.services.frame import FrameService


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_frame_keeps_the_navigator_release_output():
    frame = FrameService().load_frame_definition()
    assert frame is not None
    params = frame["parameters"]
    assert params["RELAY1_FUNCTION"] == 1
    assert params["SERVO14_FUNCTION"] == -1
    assert frame["post_reboot_parameters"] == {"RELAY1_PIN": 14}


def test_lua_mirrors_the_release_to_both_controllers():
    lua = (BACKEND_ROOT / "scripts" / "doris.lua").read_text()
    assert "relay:on(ch)" in lua
    assert "relay:off(ch)" in lua
    assert "gcs:send_named_float('RELAY',    relay_active and 1 or 0)" in lua
    # Which Navigator output fires is operator-configurable; assert the handle
    # exists and is what navigator_relay_channel() reads, not how it is declared.
    assert 'Parameter("DORIS_RELAY_CH")' in lua
    channel = lua.split("local function navigator_relay_channel()", 1)[1]
    assert "RELAY_CH:get()" in channel.split("\nend", 1)[0]
    assert lua.count("activate_relay()") >= 5


def test_lua_requests_agt_release_even_when_navigator_output_is_disabled():
    lua = (BACKEND_ROOT / "scripts" / "doris.lua").read_text()
    body = lua.split("local function set_release_outputs(on)", 1)[1].split("\nend", 1)[0]
    assert body.index("relay_active = on") < body.index("navigator_relay_channel()")
    # Every caller has to go through that helper or it guarantees nothing.
    for caller in ("activate_relay", "deactivate_relay", "update_release_test"):
        body = lua.split(f"local function {caller}(", 1)[1].split("\nend", 1)[0]
        assert "set_release_outputs(" in body
        assert "relay:on(" not in body and "relay:off(" not in body


def test_lua_release_test_cannot_fire_during_a_dive():
    """The on-deck test must be inert once the mission owns the release."""
    lua = (BACKEND_ROOT / "scripts" / "doris.lua").read_text()
    body = lua.split("local function update_release_test(now_ms)", 1)[1]
    body = body.split("\nend", 1)[0]
    assert "state == STATE_CONFIG" in body
    # And a real release clears the test's claim, so the test's own timeout
    # can never switch off a weight drop the mission asked for.
    activate = lua.split("local function activate_relay()", 1)[1].split("\nend", 1)[0]
    assert "rls_tst_start_ms = 0" in activate


async def test_changed_frame_version_reapplies_existing_install(monkeypatch, tmp_path):
    frame_version = FrameService().load_frame_definition()["version"]
    sentinel = tmp_path / ".doris_frame_applied"
    sentinel.write_text(
        json.dumps({"version": frame_version - 1, "applied_at": "earlier"})
    )
    monkeypatch.setattr(frame_module, "FRAME_SENTINEL", sentinel)

    service = FrameService()
    applied = []

    async def apply_frame(name):
        applied.append(name)
        return {"success": True, "succeeded": 3}

    monkeypatch.setattr(service, "apply_frame", apply_frame)
    assert await service.apply_frame_if_needed() is True
    assert applied == ["doris"]
    assert json.loads(sentinel.read_text())["version"] == frame_version


async def test_unchanged_frame_version_leaves_existing_install_alone(
    monkeypatch, tmp_path
):
    frame_version = FrameService().load_frame_definition()["version"]
    sentinel = tmp_path / ".doris_frame_applied"
    sentinel.write_text(
        json.dumps({"version": frame_version, "applied_at": "earlier"})
    )
    monkeypatch.setattr(frame_module, "FRAME_SENTINEL", sentinel)

    service = FrameService()

    async def apply_frame(name):
        raise AssertionError("frame must not be re-applied")

    monkeypatch.setattr(service, "apply_frame", apply_frame)
    assert await service.apply_frame_if_needed() is True
