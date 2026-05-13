"""Tests for `doris.services.mdns`.

Focused on the self-healing additions that landed alongside the cherry-pick
from ``tony-wifi``: dynamic AP iface + IP discovery, idempotent
``start_hotspot_dns`` (so the watchdog can re-call it cheaply), and the
``docker exec``-based NGINX redirect installer that replaces the Docker
TCP API path (BlueOS 1.5.x no longer exposes 2375).
"""

from __future__ import annotations

import pytest

from doris.services import mdns


class _RunHostCommandRecorder:
    """Stand-in for ``_run_host_command`` (returns ``bool``) that records
    every call and returns a configurable result per call."""

    def __init__(self, default: bool = True) -> None:
        self.calls: list[str] = []
        self.responses: list[bool] = []
        self.default = default

    def respond(self, ok: bool) -> None:
        self.responses.append(ok)

    async def __call__(self, command: str, timeout: float = 30.0) -> bool:
        self.calls.append(command)
        if self.responses:
            return self.responses.pop(0)
        return self.default


class _RunHostCommandCaptureRecorder:
    """Stand-in for ``_run_host_command_capture`` (returns ``(bool, str)``)
    that records every call and returns a configurable result per call."""

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
# _discover_hotspot_iface_and_ip: walks HOTSPOT_IFACE_CANDIDATES and
# returns the first iface that has an IPv4. The whole point of this
# function is to keep working across BlueOS renames (wlan1 -> uap0)
# and subnet renumbers (192.168.43 -> 192.168.42) at runtime.
# ---------------------------------------------------------------------


async def test_discover_picks_first_candidate_with_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First candidate in HOTSPOT_IFACE_CANDIDATES that has an IPv4 wins."""
    rec = _RunHostCommandCaptureRecorder()
    rec.respond(True, "192.168.42.1")
    monkeypatch.setattr(mdns, "_run_host_command_capture", rec)

    assert await mdns._discover_hotspot_iface_and_ip() == (
        mdns.HOTSPOT_IFACE_CANDIDATES[0], "192.168.42.1",
    )
    assert len(rec.calls) == 1
    assert mdns.HOTSPOT_IFACE_CANDIDATES[0] in rec.calls[0]


async def test_discover_falls_through_to_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the first candidate has no IP, fall through and try the next."""
    rec = _RunHostCommandCaptureRecorder()
    rec.respond(True, "")             # uap0: command ok but no IP
    rec.respond(True, "192.168.43.1") # wlan1: legacy BlueOS
    monkeypatch.setattr(mdns, "_run_host_command_capture", rec)

    assert await mdns._discover_hotspot_iface_and_ip() == (
        mdns.HOTSPOT_IFACE_CANDIDATES[1], "192.168.43.1",
    )
    assert len(rec.calls) == 2


async def test_discover_returns_none_when_no_iface_has_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no candidate has an IP, return None so callers can early-exit
    (the watchdog will retry)."""
    rec = _RunHostCommandCaptureRecorder()
    for _ in mdns.HOTSPOT_IFACE_CANDIDATES:
        rec.respond(True, "")
    monkeypatch.setattr(mdns, "_run_host_command_capture", rec)

    assert await mdns._discover_hotspot_iface_and_ip() is None
    assert len(rec.calls) == len(mdns.HOTSPOT_IFACE_CANDIDATES)


# ---------------------------------------------------------------------
# _expected_dns_conf: pure renderer, easiest signal that the iface
# and gateway both make it into the config exactly once.
# ---------------------------------------------------------------------


def test_expected_dns_conf_uses_supplied_iface_and_gateway() -> None:
    conf = mdns._expected_dns_conf("uap0", "192.168.42.1")
    assert "listen-address=192.168.42.1\n" in conf
    assert "no-dhcp-interface=uap0\n" in conf
    assert "address=/doris.local/192.168.42.1\n" in conf
    assert "address=/blueos-wifi.local/192.168.42.1\n" in conf
    # No legacy hardcoded addresses must leak through.
    assert "192.168.43" not in conf
    assert "wlan1" not in conf


def test_expected_dns_conf_handles_legacy_blueos_too() -> None:
    """Whatever discovery returns (e.g. the legacy 192.168.43 subnet) must
    flow through into the conf verbatim — the conf renderer must not
    hardcode anything."""
    conf = mdns._expected_dns_conf("wlan1", "192.168.43.1")
    assert "listen-address=192.168.43.1\n" in conf
    assert "no-dhcp-interface=wlan1\n" in conf


# ---------------------------------------------------------------------
# start_hotspot_dns: must early-return when the AP iface isn't up yet,
# write the discovered conf when it IS up, and skip the spawn when the
# right dnsmasq is already running (idempotency for the watchdog).
# ---------------------------------------------------------------------


async def test_start_hotspot_dns_no_op_when_iface_not_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold-boot: AP iface hasn't gotten an IP yet, so discovery returns
    None. start_hotspot_dns must return cleanly without trying to spawn
    dnsmasq — the watchdog will pick it up on the next tick."""
    cap = _RunHostCommandCaptureRecorder()
    for _ in mdns.HOTSPOT_IFACE_CANDIDATES:
        cap.respond(True, "")
    monkeypatch.setattr(mdns, "_run_host_command_capture", cap)
    spawn = _RunHostCommandRecorder()
    monkeypatch.setattr(mdns, "_run_host_command", spawn)

    await mdns.start_hotspot_dns()

    assert spawn.calls == []  # never tried to spawn dnsmasq
    assert len(cap.calls) == len(mdns.HOTSPOT_IFACE_CANDIDATES)


async def test_start_hotspot_dns_writes_conf_and_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: AP iface up with IP -> render conf -> spawn dnsmasq
    via the PID-file-based kill/launch path (no pkill -f, which would
    self-terminate the wrapper shell)."""
    cap = _RunHostCommandCaptureRecorder()
    cap.respond(True, "192.168.42.1")  # discovery
    cap.respond(True, "")              # _hotspot_dns_already_running:
                                       #   reads HOTSPOT_DNS_CONF; empty
                                       #   means "no on-disk conf yet"
    monkeypatch.setattr(mdns, "_run_host_command_capture", cap)

    spawn = _RunHostCommandRecorder()
    spawn.respond(False)  # alive-check fails (no PID file): not running
    spawn.respond(True)   # the actual write+spawn command succeeds
    monkeypatch.setattr(mdns, "_run_host_command", spawn)

    await mdns.start_hotspot_dns()

    # The write+spawn command must:
    #  - tee the rendered conf to HOTSPOT_DNS_CONF (with the live gateway)
    #  - kill any prior daemon via the PID file (NOT pkill -f, which
    #    would self-terminate the wrapper shell)
    #  - spawn dnsmasq with --conf-file and --pid-file pointing at our paths
    spawn_cmd = spawn.calls[-1]
    assert "192.168.42.1" in spawn_cmd
    assert "no-dhcp-interface=uap0" in spawn_cmd
    assert mdns.HOTSPOT_DNS_CONF in spawn_cmd
    assert mdns.HOTSPOT_DNS_PID in spawn_cmd
    assert "pkill -f" not in spawn_cmd, (
        "must not pkill -f against the conf path: that matches our own "
        "wrapper shell (its argv contains both 'dnsmasq' and the conf "
        "path) and self-terminates with exit 255 before dnsmasq launches"
    )


async def test_start_hotspot_dns_idempotent_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If our dnsmasq is already alive AND the on-disk conf already
    matches what we'd write, start_hotspot_dns must skip the spawn so
    the watchdog can call it cheaply every 30 s without churning the
    daemon."""
    expected_conf = mdns._expected_dns_conf("uap0", "192.168.42.1")
    cap = _RunHostCommandCaptureRecorder()
    cap.respond(True, "192.168.42.1")  # discovery
    cap.respond(True, expected_conf)   # on-disk conf matches
    monkeypatch.setattr(mdns, "_run_host_command_capture", cap)

    spawn = _RunHostCommandRecorder()
    spawn.respond(True)  # alive-check via PID file passes
    monkeypatch.setattr(mdns, "_run_host_command", spawn)

    await mdns.start_hotspot_dns()

    # Only the alive-check ran via _run_host_command; no spawn issued.
    assert len(spawn.calls) == 1
    assert "kill -0" in spawn.calls[0]


# ---------------------------------------------------------------------
# NGINX redirect: must use `docker exec` (not the old TCP 2375 archive
# PUT) because BlueOS 1.5.x stopped exposing the Docker daemon on TCP.
# ---------------------------------------------------------------------


async def test_nginx_redirect_exists_uses_docker_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existence probe must run `docker exec test -f` against
    blueos-core via the Commander shell — not Docker TCP."""
    rec = _RunHostCommandRecorder()
    rec.respond(True)
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    assert await mdns._nginx_redirect_exists() is True
    assert len(rec.calls) == 1
    cmd = rec.calls[0]
    assert "docker exec" in cmd
    assert mdns.CORE_CONTAINER in cmd
    assert "test -f" in cmd
    assert mdns.NGINX_CONF_DST in cmd
