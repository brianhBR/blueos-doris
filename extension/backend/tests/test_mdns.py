"""Tests for `doris.services.mdns`.

Focused on:
 - the dynamic hotspot-gateway detection introduced in bh-wifi-hardening
   (previously :data:`HOTSPOT_GATEWAY` was hardcoded to ``192.168.43.1``
   while BlueOS 1.5.x assigns ``192.168.42.1`` to ``uap0`` - the
   mismatch made the auxiliary dnsmasq fail ``bind-interfaces`` every
   start, leaving ``doris.local`` unresolvable for hotspot clients
   that lack mDNS), and
 - the four hardening fixes to ``_upload_nginx_redirect`` in
   bh-nginx-redirect-hardening (mkdir race, DEBUG->WARNING, reload
   rc check, post-PUT verification).
"""

from __future__ import annotations

import logging
from typing import Any

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
    returns, NOT the fallback constant when detection succeeds.

    start_hotspot_dns issues THREE Commander calls now:
      1. ``ip addr show uap0`` (detect gateway)
      2. write conf + pkill prior daemon
      3. launch dnsmasq + verify pid file
    """
    rec = _RunHostCommandRecorder()
    rec.respond(
        True,
        "5: uap0    inet 192.168.42.1/24 brd 192.168.42.255 "
        "scope global uap0\n",
    )
    rec.respond(True, "")          # write_cmd
    rec.respond(True, "running")   # start_cmd
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    assert len(rec.calls) == 3
    write_cmd = rec.calls[1]
    assert "listen-address=192.168.42.1" in write_cmd, write_cmd
    assert "address=/doris.local/192.168.42.1" in write_cmd, write_cmd
    assert "address=/blueos-wifi.local/192.168.42.1" in write_cmd, write_cmd
    # No-dhcp-interface MUST point at uap0 (the post-rename name), not
    # the upstream-default wlan1.
    assert "no-dhcp-interface=uap0" in write_cmd, write_cmd
    assert "no-dhcp-interface=wlan1" not in write_cmd, write_cmd


async def test_start_hotspot_dns_detaches_dnsmasq_from_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dnsmasq must be invoked in a way that lets Commander's ssh
    session return immediately after the daemon forks - inheriting
    open stdin/stdout/stderr keeps the ssh hanging until timeout
    (rc=255 with empty output), which we observed in live testing
    after fixing the gateway IP. The fix uses setsid + redirect to
    /dev/null + ``&`` to background the launcher."""
    rec = _RunHostCommandRecorder()
    rec.respond(
        True,
        "5: uap0    inet 192.168.42.1/24 brd 192.168.42.255 "
        "scope global uap0\n",
    )
    rec.respond(True, "")
    rec.respond(True, "running")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    start_cmd = rec.calls[2]
    assert "setsid" in start_cmd, start_cmd
    assert "< /dev/null" in start_cmd, start_cmd
    assert "> /dev/null" in start_cmd, start_cmd
    # Backgrounded so the shell exits even if the daemon hasn't
    # closed its fds in time.
    assert " & " in start_cmd, start_cmd
    # Real signal of success is the pid file existing, not the
    # launcher's rc.
    assert "test -f" in start_cmd and "running" in start_cmd, start_cmd


async def test_start_hotspot_dns_ignores_unreliable_commander_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commander's ssh transport returns rc=255 spuriously on short
    multi-step commands - we observed this in live testing where the
    underlying shell had run to completion. start_hotspot_dns must
    therefore NOT abort on a write_cmd / start_cmd rc=False; the
    real signal is "did the pid file get created"."""
    rec = _RunHostCommandRecorder()
    rec.respond(
        True,
        "5: uap0    inet 192.168.42.1/24 brd 192.168.42.255 "
        "scope global uap0\n",
    )
    # Both subsequent calls report False, but the second one's stdout
    # says ``running`` (because the shell-level pid-file check did
    # succeed on the host, even though ssh's wrapper rc was 255).
    rec.respond(False, "")
    rec.respond(False, "running\n")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    # Must still issue all three calls and not bail early.
    assert len(rec.calls) == 3


async def test_start_hotspot_dns_falls_back_when_uap0_has_no_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the AP isn't up yet, we still configure dnsmasq with the
    fallback so the next watchdog tick / restart can use it."""
    rec = _RunHostCommandRecorder()
    rec.respond(True, "5: uap0    <NO-CARRIER>\n")
    rec.respond(True, "")
    rec.respond(True, "running")
    monkeypatch.setattr(mdns, "_run_host_command", rec)

    await mdns.start_hotspot_dns()

    assert len(rec.calls) == 3
    write_cmd = rec.calls[1]
    assert (
        f"listen-address={mdns.HOTSPOT_GATEWAY_FALLBACK}" in write_cmd
    ), write_cmd


# ---------------------------------------------------------------------
# _upload_nginx_redirect: the four hardening fixes.
#
# Background: live testing on Tony's unit produced "nginx redirect
# attempt N/5 failed" logs with no exception text because the upload
# helper logged at DEBUG. The function also had a fire-and-forget
# mkdir (Detach=True) that raced the archive PUT, ignored the
# ``nginx -s reload`` return code outright, and never verified the
# PUT actually landed. These tests pin each of those down so they
# can't silently regress.
# ---------------------------------------------------------------------


class _FakeHttpResponse:
    """Minimal stand-in for ``httpx.Response`` for the surface we use."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.status_code} (faked raise_for_status)"
            )

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    """Records every HTTP call ``_upload_nginx_redirect`` makes and
    dispatches canned responses based on URL fragments.

    Records ``calls`` as a list of ``(method, url)`` tuples in the
    order issued so tests can assert ordering (mkdir-inspect MUST
    happen before the archive PUT - that's the race the new code
    closes).
    """

    def __init__(
        self,
        *,
        exec_create_status: int = 201,
        exec_inspect_exit_code: int = 0,
        archive_put_status: int = 200,
        archive_put_text: str = "",
    ) -> None:
        self.exec_create_status = exec_create_status
        self.exec_inspect_exit_code = exec_inspect_exit_code
        self.archive_put_status = archive_put_status
        self.archive_put_text = archive_put_text
        self.calls: list[tuple[str, str]] = []
        self.start_request_bodies: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeHttpResponse:
        self.calls.append(("POST", url))
        if url.endswith("/start"):
            self.start_request_bodies.append(kwargs.get("json") or {})
            return _FakeHttpResponse(200, {})
        if "/exec" in url:
            return _FakeHttpResponse(
                self.exec_create_status, {"Id": "execid000000"}
            )
        return _FakeHttpResponse(200, {})

    async def get(self, url: str, **_kwargs: Any) -> _FakeHttpResponse:
        self.calls.append(("GET", url))
        if "/exec/" in url and url.endswith("/json"):
            return _FakeHttpResponse(
                200, {"ExitCode": self.exec_inspect_exit_code}
            )
        return _FakeHttpResponse(200, {})

    async def put(self, url: str, **_kwargs: Any) -> _FakeHttpResponse:
        self.calls.append(("PUT", url))
        return _FakeHttpResponse(
            self.archive_put_status, text=self.archive_put_text
        )


def _install_nginx_redirect_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: _FakeAsyncClient,
    cid: str | None = "abcdef012345",
    exists_after_put: bool = True,
    reload_response: tuple[bool, str] = (True, ""),
) -> _RunHostCommandRecorder:
    """Install the standard set of monkeypatches for an
    ``_upload_nginx_redirect`` test. Returns the ``_run_host_command``
    recorder so tests can assert on the reload invocation."""

    def fake_async_client(*_a: Any, **_kw: Any) -> _FakeAsyncClient:
        return client

    async def fake_find_cid(_c: _FakeAsyncClient) -> str | None:
        return cid

    async def fake_exists() -> bool:
        return exists_after_put

    rec = _RunHostCommandRecorder()
    rec.respond(*reload_response)

    monkeypatch.setattr(mdns.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(mdns, "_find_core_container_id", fake_find_cid)
    monkeypatch.setattr(mdns, "_nginx_redirect_exists", fake_exists)
    monkeypatch.setattr(mdns, "_run_host_command", rec)
    return rec


async def test_upload_nginx_redirect_happy_path_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncClient()
    rec = _install_nginx_redirect_mocks(monkeypatch, client=client)

    assert await mdns._upload_nginx_redirect() is True

    # One ``nginx -s reload`` call must have been issued.
    assert len(rec.calls) == 1
    assert "nginx -s reload" in rec.calls[0]
    assert mdns.CORE_CONTAINER in rec.calls[0]


async def test_upload_nginx_redirect_returns_false_when_core_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Container not found is a WARNING, not a silent DEBUG."""
    client = _FakeAsyncClient()
    _install_nginx_redirect_mocks(monkeypatch, client=client, cid=None)

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    assert any(
        "container not found" in r.message for r in caplog.records
    ), caplog.records


async def test_upload_nginx_redirect_waits_for_mkdir_before_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mkdir exec must run with ``Detach=False`` and we must
    inspect the exec result BEFORE issuing the archive PUT.

    The original code used ``Detach=True`` (fire-and-forget) and then
    PUT'd immediately, meaning on a slow blueos-core boot the mkdir
    hadn't actually created the destination directory yet. This test
    pins the new ordering: POST /exec, POST /exec/.../start, GET
    /exec/.../json, *then* PUT /archive.
    """
    client = _FakeAsyncClient()
    _install_nginx_redirect_mocks(monkeypatch, client=client)

    await mdns._upload_nginx_redirect()

    method_order = [m for (m, _u) in client.calls]
    # Find the indices of the archive PUT vs the mkdir inspect GET.
    archive_put_idx = next(
        i for i, (m, u) in enumerate(client.calls)
        if m == "PUT" and "/archive" in u
    )
    inspect_idx = next(
        i for i, (m, u) in enumerate(client.calls)
        if m == "GET" and "/exec/" in u and u.endswith("/json")
    )
    assert inspect_idx < archive_put_idx, (
        f"archive PUT (idx={archive_put_idx}) must happen AFTER the "
        f"mkdir inspect GET (idx={inspect_idx}); otherwise we re-arm "
        f"the Detach=True race. Call sequence was {method_order}"
    )

    # And the exec start body must explicitly NOT detach - otherwise
    # the subsequent GET /exec/.../json returns a stale ExitCode.
    assert client.start_request_bodies, "no exec start call recorded"
    start_body = client.start_request_bodies[0]
    assert start_body.get("Detach") is False, start_body


async def test_upload_nginx_redirect_returns_false_when_mkdir_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mkdir reporting a non-zero exit code must abort the upload
    before the PUT (PUTting into a missing path would 404 anyway)."""
    client = _FakeAsyncClient(exec_inspect_exit_code=1)
    _install_nginx_redirect_mocks(monkeypatch, client=client)

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    # No PUT should have been issued.
    assert not any(m == "PUT" for (m, _u) in client.calls), client.calls
    assert any(
        "mkdir" in r.message and "exited" in r.message for r in caplog.records
    ), caplog.records


async def test_upload_nginx_redirect_returns_false_when_put_non_200(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Archive PUT failing must surface as a WARNING+False, not the
    silent DEBUG the old code used."""
    client = _FakeAsyncClient(
        archive_put_status=500, archive_put_text="internal error"
    )
    _install_nginx_redirect_mocks(monkeypatch, client=client)

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    assert any(
        "archive PUT" in r.message and "500" in r.message
        for r in caplog.records
    ), caplog.records


async def test_upload_nginx_redirect_returns_false_when_post_put_verify_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PUT can return 200 but the tar may not have landed where we
    expected (e.g. extraction quirk). Post-PUT verification via
    ``_nginx_redirect_exists`` catches that case."""
    client = _FakeAsyncClient()
    rec = _install_nginx_redirect_mocks(
        monkeypatch, client=client, exists_after_put=False
    )

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    # Reload MUST NOT be issued when verification failed - reloading
    # against a conf we couldn't observe is wasted effort.
    assert rec.calls == [], rec.calls
    assert any(
        "not observable" in r.message for r in caplog.records
    ), caplog.records


async def test_upload_nginx_redirect_returns_false_when_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Previously ``nginx -s reload``'s rc was explicitly ignored
    ('errors surface via 502 from the redirect itself'). That's
    user-visible failure instead of caller-visible failure; the new
    code checks the rc and returns False so the retry loop /
    watchdog can try again."""
    client = _FakeAsyncClient()
    _install_nginx_redirect_mocks(
        monkeypatch,
        client=client,
        reload_response=(False, "[emerg] open() ... failed"),
    )

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    assert any(
        "nginx -s reload" in r.message and "failed" in r.message
        for r in caplog.records
    ), caplog.records


async def test_upload_nginx_redirect_logs_exception_type_at_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Outer except must log at WARNING and include the exception's
    class name so that field reports like 'attempt N/5 failed'
    actually point at a cause instead of being information-free."""

    class _BoomClient:
        async def __aenter__(self) -> "_BoomClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, *_a: Any, **_kw: Any) -> _FakeHttpResponse:
            raise ConnectionError("kaboom")

    def fake_async_client(*_a: Any, **_kw: Any) -> _BoomClient:
        return _BoomClient()

    async def fake_find_cid(_c: object) -> str:
        return "abcdef012345"

    monkeypatch.setattr(mdns.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(mdns, "_find_core_container_id", fake_find_cid)

    with caplog.at_level(logging.WARNING, logger="doris.services.mdns"):
        result = await mdns._upload_nginx_redirect()

    assert result is False
    assert any(
        "ConnectionError" in r.message and "kaboom" in r.message
        for r in caplog.records
    ), caplog.records
