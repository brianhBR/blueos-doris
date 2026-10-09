"""Tests for the WiFi Manager v1 AP -> STA switch on the external radio."""

from __future__ import annotations

import base64

import pytest

from doris.services import network


class _FakeWifiClient:
    def __init__(self) -> None:
        self.hotspot_calls: list[bool] = []

    async def get_smart_hotspot(self) -> bool:
        return False

    async def set_smart_hotspot(self, enable: bool) -> None:
        pass

    async def set_hotspot(self, enable: bool) -> None:
        self.hotspot_calls.append(enable)


class _FakeHost:
    def __init__(self, *, create_ap_exits: bool = True) -> None:
        self.commands: list[str] = []
        self.create_ap_exits = create_ap_exits

    async def __call__(self, command: str, timeout: float = 30.0) -> tuple[bool, str]:
        self.commands.append(command)
        if "pgrep" in command:
            return True, "" if self.create_ap_exits else "running"
        if "-f UUID,TYPE connection show" in command:
            return True, "uuid-eth:802-3-ethernet\nuuid-home:802-11-wireless"
        if "802-11-wireless.ssid connection show uuid-home" in command:
            return True, "HomeNet"
        if "802-11-wireless-security.psk connection show uuid-home" in command:
            return True, "s3cret"
        if command.startswith("ip -4 -o addr show"):
            return True, "10.0.0.5"
        return True, ""

    def find(self, needle: str) -> list[str]:
        return [c for c in self.commands if needle in c]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path) -> network.NetworkService:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(network, "WLAN_INTENT_FILE", tmp_path / "intent.json")
    monkeypatch.setattr(network.asyncio, "sleep", no_sleep)
    svc = network.NetworkService()
    svc._client = _FakeWifiClient()  # type: ignore[assignment]
    return svc


async def test_saved_network_reuses_saved_psk(
    service: network.NetworkService, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _FakeHost()
    monkeypatch.setattr(network, "_run_host_command", host)

    await service._v1_switch_to_sta("uap0", "HomeNet", "")

    [add_cmd] = host.find("nmcli connection add")
    assert "wifi-sec.key-mgmt wpa-psk wifi-sec.psk 's3cret'" in add_cmd
    [up_cmd] = host.find("nmcli --wait 30 connection up")
    assert f"passwd-file {network.V1_PSK_FILE}" in up_cmd
    [stage_cmd] = host.find("base64 -d")
    encoded = stage_cmd.split("'", 2)[1]
    assert base64.b64decode(encoded) == b"802-11-wireless-security.psk:s3cret\n"
    assert host.find(f"sudo rm -f {network.V1_PSK_FILE}")
    state = await service.get_wlan_state()
    assert state.mode == "sta_connected"
    assert state.ip_address == "10.0.0.5"


async def test_stops_create_ap_without_hotspot_api(
    service: network.NetworkService, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _FakeHost()
    monkeypatch.setattr(network, "_run_host_command", host)

    await service._v1_switch_to_sta("uap0", "HomeNet", "pw")

    assert service._client.hotspot_calls == []  # type: ignore[attr-defined]
    assert host.find(f"pkill -INT -f '{network.V1_CREATE_AP_PATTERN}'")
    assert host.find("nmcli device set uap0 managed yes")
    stop_idx = host.commands.index(host.find("pkill -INT")[0])
    managed_idx = host.commands.index(host.find("managed yes")[0])
    assert stop_idx < managed_idx


async def test_restores_hotspot_when_create_ap_will_not_stop(
    service: network.NetworkService, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _FakeHost(create_ap_exits=False)
    monkeypatch.setattr(network, "_run_host_command", host)

    await service._v1_switch_to_sta("uap0", "HomeNet", "pw")

    assert host.find("pkill -KILL")
    assert not host.find("nmcli connection add")
    assert service._client.hotspot_calls == [True]  # type: ignore[attr-defined]
    assert host.find("nmcli device set uap0 managed no")
    state = await service.get_wlan_state()
    assert state.mode == "ap"
    assert state.last_attempt is not None
    assert state.last_attempt.status == "failed"


class _V1HotspotClient:
    def __init__(self) -> None:
        self.hotspot_calls: list[bool] = []

    async def get_hotspot_credentials(self) -> dict[str, str]:
        return {"ssid": "DORIS (D-0F58)", "password": "blueosap"}

    async def get_smart_hotspot(self) -> bool:
        return False

    async def set_smart_hotspot(self, enable: bool) -> None:
        pass

    async def get_hotspot(self) -> bool:
        return True

    async def set_hotspot(self, enable: bool) -> None:
        self.hotspot_calls.append(enable)


class _RadioHost:
    def __init__(self, *, ap: bool) -> None:
        self.ap = ap
        self.commands: list[str] = []

    async def __call__(self, command: str, timeout: float = 30.0) -> tuple[bool, str]:
        self.commands.append(command)
        if "test -d /sys/class/net/uap0" in command:
            return True, "uap0"
        if "iw dev uap0 info" in command:
            if self.ap:
                return True, "Interface uap0\n\ttype AP\n\tchannel 6 (2437 MHz)"
            return True, "Interface uap0\n\ttype managed\n\ttxpower -100.00 dBm"
        if "ip -br link show dev uap0" in command:
            return True, "UP" if self.ap else "DOWN"
        if "pgrep" in command:
            return True, ""
        return True, ""


async def test_v1_hotspot_left_alone_when_radio_is_an_ap(
    service: network.NetworkService, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _V1HotspotClient()
    service._client = client  # type: ignore[assignment]
    monkeypatch.setattr(network, "_run_host_command", _RadioHost(ap=True))

    await service._configure_hotspot_v1("DORIS (D-0F58)", "blueosap")

    assert client.hotspot_calls == []


async def test_v1_hotspot_restarts_when_radio_is_down(
    service: network.NetworkService, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _V1HotspotClient()
    service._client = client  # type: ignore[assignment]
    host = _RadioHost(ap=False)
    monkeypatch.setattr(network, "_run_host_command", host)

    await service._configure_hotspot_v1("DORIS (D-0F58)", "blueosap")

    assert client.hotspot_calls == [True]
    assert host.commands
    assert any("pkill -INT" in cmd for cmd in host.commands)
    assert not any("hotspot?enable=false" in cmd for cmd in host.commands)

