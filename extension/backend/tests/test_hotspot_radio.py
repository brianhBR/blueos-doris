"""Tests for `doris.services.hotspot_radio`.

These exercise the units that have caused field bugs: the legacy-patch
stripper, the self-heal-on-corrupt-source path in
``_install_create_ap_speed_patch``, and the inode-preserving write
command shape in ``_write_host_file``.
"""

from __future__ import annotations

import pytest

from doris.services import hotspot_radio

# ---------------------------------------------------------------------
# _strip_doris_patch: pure function, easy to exercise across the
# legacy 5 GHz block and the current 2.4 GHz block.
# ---------------------------------------------------------------------


ANCHOR = hotspot_radio._CREATE_AP_ANCHOR


def _wrap(payload: str) -> str:
    """Wrap *payload* in a minimal `wifi-manager` argv list."""
    return (
        "argv = [\n"
        '            "create_ap",\n'
        f"{ANCHOR}"
        f"{payload}"
        '            ssid,\n'
        '            password,\n'
        "]\n"
    )


CURRENT_24GHZ_PATCH = (
    '            "-c", "6",\n'
    '            "--ieee80211n",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40]'
    '[LDPC][RX-STBC1][MAX-AMSDU-7935][DSSS_CCK-40]",\n'
    '            "--country", "US",\n'
)


LEGACY_5GHZ_PATCH = (
    '            "--freq-band", "5",\n'
    '            "-c", "36",\n'
    '            "--ieee80211n",\n'
    '            "--ieee80211ac",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40]",\n'
    '            "--vht_capab", "[SHORT-GI-80][MAX-MPDU-11454]",\n'
    '            "--country", "US",\n'
)


def test_strip_doris_patch_noop_on_unpatched_source() -> None:
    src = _wrap("")
    assert hotspot_radio._strip_doris_patch(src) == src


def test_strip_doris_patch_removes_current_24ghz_block() -> None:
    src = _wrap(CURRENT_24GHZ_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--ht_capab" not in stripped


def test_strip_doris_patch_removes_legacy_5ghz_block() -> None:
    src = _wrap(LEGACY_5GHZ_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--freq-band" not in stripped
    assert "--vht_capab" not in stripped


def test_strip_doris_patch_idempotent() -> None:
    src = _wrap(CURRENT_24GHZ_PATCH)
    once = hotspot_radio._strip_doris_patch(src)
    twice = hotspot_radio._strip_doris_patch(once)
    assert once == twice


def test_strip_doris_patch_does_not_eat_unrelated_args() -> None:
    """A non-DORIS argv entry after the anchor must be preserved."""
    unrelated = '            "--something-else", "value",\n'
    src = _wrap(unrelated)
    assert hotspot_radio._strip_doris_patch(src) == src


# ---------------------------------------------------------------------
# Self-heal path: when the bind-mounted source we read back is
# syntactically broken, we must NOT re-write it - we must delete the
# host override so the next blueos-core restart falls back to the
# stock baseline.
# ---------------------------------------------------------------------


class _RunHostCommandRecorder:
    """Stand-in for ``_run_host_command`` that records every call and
    returns a configurable result per pattern."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: list[tuple[bool, str]] = []

    def respond(self, ok: bool, out: str = "") -> None:
        self.responses.append((ok, out))

    async def __call__(self, command: str, timeout: float = 30.0) -> tuple[bool, str]:
        self.calls.append(command)
        if self.responses:
            return self.responses.pop(0)
        return True, ""


@pytest.fixture
def patched_command(monkeypatch: pytest.MonkeyPatch) -> _RunHostCommandRecorder:
    rec = _RunHostCommandRecorder()
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)
    return rec


CORRUPT_SOURCE = (
    "def scan_networks():\n"
    "    # Same shape of corruption that bit us in May 2026.\n"
    "    flag_str = f\"[{\\'-\\'.join(set(security_flags))}]\"\n"
    "    return flag_str\n"
)


CLEAN_SOURCE_WITH_ANCHOR = _wrap("")


CLEAN_SOURCE_NO_ANCHOR = (
    "def scan_networks():\n"
    "    return []\n"
)


async def test_install_patch_removes_override_when_source_is_corrupt(
    patched_command: _RunHostCommandRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(container: str, path: str) -> str:
        return CORRUPT_SOURCE

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)

    # If we reached _pick_24ghz_channel something is wrong - the
    # corruption check should bail before it. Trip the test loudly
    # if it ever does.
    async def must_not_run() -> int:
        raise AssertionError(
            "_pick_24ghz_channel must not run on corrupt source"
        )

    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", must_not_run)

    await hotspot_radio._install_create_ap_speed_patch()

    rm_cmds = [c for c in patched_command.calls if "rm -f" in c]
    assert any(
        hotspot_radio.WIFI_OVERRIDE_HOST_PATH in c for c in rm_cmds
    ), (
        f"expected `rm -f {hotspot_radio.WIFI_OVERRIDE_HOST_PATH}` to be "
        f"issued; got commands: {patched_command.calls}"
    )
    # And we must not have tried to install anything.
    assert not any(
        ".doris.tmp" in c for c in patched_command.calls
    ), "must not write to host while source is corrupt"


async def test_install_patch_skips_when_anchor_missing(
    patched_command: _RunHostCommandRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream BlueOS could re-name the create_ap call; we must not
    rm the override in that case, just skip."""

    async def fake_read(container: str, path: str) -> str:
        return CLEAN_SOURCE_NO_ANCHOR

    async def must_not_run() -> int:
        raise AssertionError("picker should not run if anchor is missing")

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", must_not_run)

    await hotspot_radio._install_create_ap_speed_patch()

    assert not any(
        hotspot_radio.WIFI_OVERRIDE_HOST_PATH in c and "rm" in c
        for c in patched_command.calls
    ), "anchor-missing must not delete the override file"


async def test_install_patch_writes_when_source_is_clean(
    patched_command: _RunHostCommandRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: clean parseable source + anchor present results in
    a write to the host override path."""

    async def fake_read(container: str, path: str) -> str:
        return CLEAN_SOURCE_WITH_ANCHOR

    write_calls: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        write_calls.append((path, content))
        return True

    async def fake_pick() -> int:
        return 6

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", fake_pick)

    await hotspot_radio._install_create_ap_speed_patch()

    assert len(write_calls) == 1
    path, content = write_calls[0]
    assert path == hotspot_radio.WIFI_OVERRIDE_HOST_PATH
    assert '"-c", "6"' in content
    assert "--ht_capab" in content


# ---------------------------------------------------------------------
# _write_host_file: must emit a final command that preserves the
# destination inode (cat ... > path) rather than the inode-replacing
# ``mv`` pattern.
# ---------------------------------------------------------------------


async def test_write_host_file_uses_inode_preserving_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _RunHostCommandRecorder()
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    async def fake_read(path: str) -> str | None:
        return None  # destination doesn't exist yet

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)

    ok = await hotspot_radio._write_host_file("/tmp/some-dest", "hello world\n")
    assert ok

    final_cmd = rec.calls[-1]
    # Must redirect through the destination (preserves existing inode)
    assert "> /tmp/some-dest" in final_cmd, final_cmd
    # Must NOT use the inode-breaking `mv` pattern for installing
    # into the destination.  ``rm -f .doris.tmp`` is fine; what we
    # are forbidding is a ``mv X /tmp/some-dest`` style install.
    assert "mv " not in final_cmd or "/tmp/some-dest" not in final_cmd.split("mv ", 1)[1].split()[:2], (
        f"_write_host_file must not install via `mv`; got: {final_cmd}"
    )


async def test_write_host_file_skips_when_existing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _RunHostCommandRecorder()
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    async def fake_read(path: str) -> str | None:
        return "hello world\n"

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)

    ok = await hotspot_radio._write_host_file("/tmp/some-dest", "hello world\n")
    assert ok
    # No host commands should have been issued at all - the
    # short-circuit must fire before chunked writes start.
    assert rec.calls == [], rec.calls
