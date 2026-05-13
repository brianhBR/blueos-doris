"""Tests for `doris.services.mdns`.

Focused on the dynamic hotspot-gateway detection introduced in
bh-wifi-hardening. Previously :data:`HOTSPOT_GATEWAY` was hardcoded
to ``192.168.43.1`` while BlueOS 1.5.x assigns ``192.168.42.1`` to
``uap0`` - the mismatch made the auxiliary dnsmasq fail
``bind-interfaces`` every start, leaving ``doris.local`` unresolvable
for hotspot clients that lack mDNS.
"""

from __future__ import annotations

import pytest

from doris.services import mdns


class _RunHostCommandRecorder:
    """Stand-in for ``_run_host_command`` that records every call and
    returns a configurable result per pattern."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: list[tuple[bool, str]] = []

    def respond(self, ok: bool, out: str = "") -> None:
        self.responses.append((ok, out))

    async def __call__(
        self, command: str, timeout: float = 30.0
    ) -> tuple[bool, str]:
        self.calls.append(command)
        if self.responses:
            return self.responses.pop(0)
        return True, ""


# ---------------------------------------------------------------------
# _detect_hotspot_gateway: parses `ip -4 -o addr show uap0` output.
# ---------------------------------------------------------------------


async def test_detect_hotspot_gateway_parses_192_168_42_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The current BlueOS 1.5.x layout assigns 192.168.42.1 to uap0."""
    rec = _RunHostCommandRecorder()
    rec.respond(
        True,
        "5: uap0    inet 192.168.42.1/24 brd 192.168.42.255 "
        "scope global uap0\\       valid_lft forever preferred_lft forever\n",
    )
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    assert await mdns._detect_hotspot_gateway() == "192.168.42.1"
    assert rec.calls == [f"ip -4 -o addr show {mdns.HOTSPOT_INTERFACE}"]


async def test_detect_hotspot_gateway_parses_legacy_192_168_43_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older BlueOS 1.4.x builds used 192.168.43.1; live read must
    pick it up too - the whole point of going dynamic."""
    rec = _RunHostCommandRecorder()
    rec.respond(
        True,
        "5: uap0    inet 192.168.43.1/24 brd 192.168.43.255 scope global uap0\n",
    )
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    assert await mdns._detect_hotspot_gateway() == "192.168.43.1"


async def test_detect_hotspot_gateway_falls_back_when_no_ip_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During boot ``ip addr show uap0`` may return the link line but
    no ``inet`` line if create_ap hasn't finished. Must fall back to
    the constant rather than emit an empty/invalid address."""
    rec = _RunHostCommandRecorder()
    rec.respond(True, "5: uap0    <NO-CARRIER,BROADCAST,MULTICAST,UP>\n")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    assert (
        await mdns._detect_hotspot_gateway() == mdns.HOTSPOT_GATEWAY_FALLBACK
    )


async def test_detect_hotspot_gateway_falls_back_when_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the ``ip`` invocation itself errors (e.g. interface missing
    because the rename hasn't taken effect yet), fall back."""
    rec = _RunHostCommandRecorder()
    rec.respond(False, "Device \"uap0\" does not exist.\n")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    assert (
        await mdns._detect_hotspot_gateway() == mdns.HOTSPOT_GATEWAY_FALLBACK
    )


# ---------------------------------------------------------------------
# start_hotspot_dns: must use the live-detected gateway in the
# dnsmasq config and must target uap0 (not wlan1) on no-dhcp-interface.
# ---------------------------------------------------------------------


async def test_start_hotspot_dns_uses_detected_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config written must use whatever ``_detect_hotspot_gateway``
    returns, NOT the fallback constant when detection succeeds."""
    rec = _RunHostCommandRecorder()
    # First call is _detect_hotspot_gateway (ip addr show); second is
    # the actual dnsmasq setup tee+start command.
    rec.respond(
        True,
        "5: uap0    inet 192.168.42.1/24 brd 192.168.42.255 "
        "scope global uap0\n",
    )
    rec.respond(True, "")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    assert len(rec.calls) == 2
    dnsmasq_cmd = rec.calls[1]
    assert "listen-address=192.168.42.1" in dnsmasq_cmd, dnsmasq_cmd
    assert "address=/doris.local/192.168.42.1" in dnsmasq_cmd, dnsmasq_cmd
    assert "address=/blueos-wifi.local/192.168.42.1" in dnsmasq_cmd, dnsmasq_cmd
    # No-dhcp-interface MUST point at uap0 (the post-rename name), not
    # the upstream-default wlan1.
    assert "no-dhcp-interface=uap0" in dnsmasq_cmd, dnsmasq_cmd
    assert "no-dhcp-interface=wlan1" not in dnsmasq_cmd, dnsmasq_cmd


async def test_start_hotspot_dns_falls_back_when_uap0_has_no_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the AP isn't up yet, we still configure dnsmasq with the
    fallback so the next watchdog tick / restart can use it."""
    rec = _RunHostCommandRecorder()
    rec.respond(True, "5: uap0    <NO-CARRIER>\n")
    rec.respond(True, "")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    assert len(rec.calls) == 2
    dnsmasq_cmd = rec.calls[1]
    assert (
        f"listen-address={mdns.HOTSPOT_GATEWAY_FALLBACK}" in dnsmasq_cmd
    ), dnsmasq_cmd
