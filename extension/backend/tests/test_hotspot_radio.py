"""Tests for `doris.services.hotspot_radio`.

These exercise the units that have caused field bugs: the legacy-patch
stripper, the self-heal-on-corrupt-source path in
``_install_create_ap_speed_patch``, the regex anchor's tolerance for
upstream reformatting, the inode-preserving write command shape in
``_write_host_file``, and the contract that
``setup_hotspot_radio`` only installs the startup.json bind entry
after a successful patch.
"""

from __future__ import annotations

import pytest

from doris.services import hotspot_radio

# ---------------------------------------------------------------------
# _strip_doris_patch: pure function, easy to exercise across the
# legacy 5 GHz block and the current 2.4 GHz block.
# ---------------------------------------------------------------------


# Canonical anchor line used by `_wrap` to build test fixtures. The
# production code finds this (and tolerated variations) via
# :data:`hotspot_radio._CREATE_AP_ANCHOR_RE`. Tests for the regex's
# tolerance to upstream reformatting live further down with their own
# variant anchors.
CANONICAL_ANCHOR_LINE = (
    '            "--redirect-to-localhost",'
    "  # Redirect all traffic to localhost, captive-portal style\n"
)


def _wrap(payload: str, *, anchor: str = CANONICAL_ANCHOR_LINE) -> str:
    """Wrap *payload* in a minimal `wifi-manager` argv list."""
    return (
        "argv = [\n"
        '            "create_ap",\n'
        f"{anchor}"
        f"{payload}"
        '            ssid,\n'
        '            password,\n'
        "]\n"
    )


# The first DORIS shipping shape: Realtek HT20 with no --no-virt.
LEGACY_REALTEK_NO_NOVIRT_PATCH = (
    '            "-c", "6",\n'
    '            "--ieee80211n",\n'
    '            "--ht_capab", "[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",\n'
    '            "--country", "US",\n'
)


# Current Realtek shape (this branch): HT20 caps + --no-virt + live country.
CURRENT_REALTEK_PATCH = (
    '            "-c", "6",\n'
    '            "--no-virt",\n'
    '            "--ieee80211n",\n'
    '            "--ht_capab", "[SHORT-GI-20][LDPC][RX-STBC1][MAX-AMSDU-7935]",\n'
    '            "--country", "US",\n'
)


# Current MT7921U shape: HT40 + HE + --no-virt + --ieee80211ax.
CURRENT_MT7921U_PATCH = (
    '            "-c", "6",\n'
    '            "--no-virt",\n'
    '            "--ieee80211n",\n'
    '            "--ieee80211ax",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40][LDPC]'
    '[TX-STBC][RX-STBC1][MAX-AMSDU-7935]",\n'
    '            "--country", "US",\n'
)


# Current AR9271 shape: HT40 without LDPC/MAX-AMSDU-7935 and no HE.
CURRENT_AR9271_PATCH = (
    '            "-c", "6",\n'
    '            "--no-virt",\n'
    '            "--ieee80211n",\n'
    '            "--ht_capab", "[HT40+][SHORT-GI-20][SHORT-GI-40][RX-STBC1][DSSS_CCK-40]",\n'
    '            "--country", "US",\n'
)


# Legacy HT40 attempt (pre-PR21): single Realtek-tuned ht_capab with
# HT40+. We keep this as test data so the strip path can prove it
# still cleans up old overrides on units that installed bh-0.4.x or
# earlier.
LEGACY_24GHZ_HT40_PATCH = (
    '            "-c", "1",\n'
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


# Older code referred to CURRENT_24GHZ_PATCH for the Realtek shape.
# Keep an alias so any helper or fixture importing the old name from
# outside this file keeps working. New tests should pick the
# specific *_PATCH constant for the lineage being exercised.
CURRENT_24GHZ_PATCH = LEGACY_REALTEK_NO_NOVIRT_PATCH


def test_strip_doris_patch_noop_on_unpatched_source() -> None:
    src = _wrap("")
    assert hotspot_radio._strip_doris_patch(src) == src


def test_strip_doris_patch_removes_current_realtek_block() -> None:
    src = _wrap(CURRENT_REALTEK_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--ht_capab" not in stripped
    assert "--no-virt" not in stripped


def test_strip_doris_patch_removes_current_mt7921u_block() -> None:
    """Re-running the patch on a unit that already had the MT7921U
    block installed must strip the prior insertion cleanly, including
    the --ieee80211ax line that's unique to that lineage."""
    src = _wrap(CURRENT_MT7921U_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--ht_capab" not in stripped
    assert "--ieee80211ax" not in stripped
    assert "--no-virt" not in stripped


def test_strip_doris_patch_removes_current_ar9271_block() -> None:
    src = _wrap(CURRENT_AR9271_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--ht_capab" not in stripped
    assert "--no-virt" not in stripped


def test_strip_doris_patch_removes_legacy_realtek_no_novirt_block() -> None:
    """The first shipping shape didn't include --no-virt. The strip
    pass must still recognise that lineage so an upgrade from
    bh-0.4.4 cleanly converges to the current shape."""
    src = _wrap(LEGACY_REALTEK_NO_NOVIRT_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--ht_capab" not in stripped


def test_strip_doris_patch_removes_legacy_ht40_block() -> None:
    """A unit upgrading from a previous bh-0.4.x release will still
    have an HT40+ override on disk. The strip pass at install time
    must recognise and remove it so the fresh HT20-only block is
    written cleanly on top, with no stacked flags."""
    src = _wrap(LEGACY_24GHZ_HT40_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "[HT40+]" not in stripped
    assert "SHORT-GI-40" not in stripped
    assert "DSSS_CCK-40" not in stripped


def test_strip_doris_patch_removes_legacy_5ghz_block() -> None:
    src = _wrap(LEGACY_5GHZ_PATCH)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert stripped == _wrap("")
    assert "--freq-band" not in stripped
    assert "--vht_capab" not in stripped


@pytest.mark.parametrize(
    "patch_block",
    [
        CURRENT_REALTEK_PATCH,
        CURRENT_MT7921U_PATCH,
        CURRENT_AR9271_PATCH,
        LEGACY_REALTEK_NO_NOVIRT_PATCH,
        LEGACY_24GHZ_HT40_PATCH,
        LEGACY_5GHZ_PATCH,
    ],
    ids=[
        "current-realtek",
        "current-mt7921u",
        "current-ar9271",
        "legacy-realtek-no-novirt",
        "legacy-24ghz-ht40",
        "legacy-5ghz",
    ],
)
def test_strip_doris_patch_idempotent_across_lineages(patch_block: str) -> None:
    """Strip must be idempotent for every supported lineage.

    Anyone re-running ``setup_hotspot_radio`` on a vehicle picks the
    chip's current canonical block; the strip step must converge in
    one pass regardless of which prior lineage the existing override
    came from."""
    src = _wrap(patch_block)
    once = hotspot_radio._strip_doris_patch(src)
    twice = hotspot_radio._strip_doris_patch(once)
    assert once == twice
    assert once == _wrap("")


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

    remove_bind_called = []

    async def fake_remove_bind() -> None:
        remove_bind_called.append(True)

    monkeypatch.setattr(hotspot_radio, "_remove_startup_bind", fake_remove_bind)

    result = await hotspot_radio._install_create_ap_speed_patch()
    # Self-heal path must report failure so the caller skips
    # _ensure_startup_bind and doesn't re-arm the bind-mount trap.
    assert result is False, (
        "self-heal path must return False so setup_hotspot_radio knows "
        "to skip _ensure_startup_bind; otherwise the bind gets re-added "
        "right after we removed it"
    )

    rm_cmds = [c for c in patched_command.calls if "rm -f" in c]
    assert any(
        hotspot_radio.WIFI_OVERRIDE_HOST_PATH in c for c in rm_cmds
    ), (
        f"expected `rm -f {hotspot_radio.WIFI_OVERRIDE_HOST_PATH}` to be "
        f"issued; got commands: {patched_command.calls}"
    )
    # The bind entry MUST be removed too - otherwise Docker creates
    # a directory at the missing source on next blueos-core restart
    # and bootstrap falls back to factory (the May 2026 incident).
    assert remove_bind_called, (
        "self-heal must also remove the bind from startup.json; "
        "deleting only the file leaves a booby trap for next "
        "blueos-core restart"
    )
    # And we must not have tried to install anything.
    assert not any(
        ".doris.tmp" in c for c in patched_command.calls
    ), "must not write to host while source is corrupt"


async def test_install_patch_removes_bind_BEFORE_file_on_corrupt_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering matters: removing the bind first prevents Docker from
    seeing a startup.json entry pointing at a missing source.  If the
    rm happens first and a blueos-core restart sneaks in before the
    bind removal lands, Docker auto-creates a directory at the missing
    source and the next start fails."""

    async def fake_read(container: str, path: str) -> str:
        return CORRUPT_SOURCE

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)

    order: list[str] = []

    async def fake_remove_bind() -> None:
        order.append("remove_bind")

    rec = _RunHostCommandRecorder()
    original_call = rec.__call__

    async def recording_run_host_command(command: str, timeout: float = 30.0):
        if "rm -f" in command and hotspot_radio.WIFI_OVERRIDE_HOST_PATH in command:
            order.append("rm_file")
        return await original_call(command, timeout)

    monkeypatch.setattr(hotspot_radio, "_remove_startup_bind", fake_remove_bind)
    monkeypatch.setattr(hotspot_radio, "_run_host_command", recording_run_host_command)

    await hotspot_radio._install_create_ap_speed_patch()

    assert order == ["remove_bind", "rm_file"], (
        f"expected bind-removal BEFORE file-removal; got: {order}"
    )


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

    result = await hotspot_radio._install_create_ap_speed_patch()
    # Anchor-missing must report False so setup_hotspot_radio skips
    # the bind install - a bind pointing at an un-patched (or
    # missing) source is the same factory-revert trap.
    assert result is False, (
        "anchor-missing path must return False so setup_hotspot_radio "
        "knows to skip _ensure_startup_bind"
    )

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

    result = await hotspot_radio._install_create_ap_speed_patch()
    # Happy path must report success so setup_hotspot_radio installs
    # the bind entry.
    assert result is True, (
        "happy path must return True so setup_hotspot_radio installs "
        "the startup.json bind"
    )

    assert len(write_calls) == 1
    path, content = write_calls[0]
    assert path == hotspot_radio.WIFI_OVERRIDE_HOST_PATH
    assert '"-c", "6"' in content
    assert "--ht_capab" in content


def _realtek_radio() -> hotspot_radio.HotspotRadio:
    """Return the Realtek RTL88x2BU entry from the supported-radio table.

    Pulled by ``vendor_id`` so a future re-ordering of
    :data:`SUPPORTED_HOTSPOT_RADIOS` doesn't silently change which
    chip these tests exercise.
    """
    return next(
        r for r in hotspot_radio.SUPPORTED_HOTSPOT_RADIOS
        if r.vendor_id == "0bda" and r.product_id == "b812"
    )


def _mt7921u_radio() -> hotspot_radio.HotspotRadio:
    return next(
        r for r in hotspot_radio.SUPPORTED_HOTSPOT_RADIOS
        if r.vendor_id == "0e8d" and r.product_id == "7961"
    )


def _ar9271_radio() -> hotspot_radio.HotspotRadio:
    return next(
        r for r in hotspot_radio.SUPPORTED_HOTSPOT_RADIOS
        if r.vendor_id == "0cf3" and r.product_id == "9271"
    )


def _mt7612u_radio() -> hotspot_radio.HotspotRadio:
    return next(
        r for r in hotspot_radio.SUPPORTED_HOTSPOT_RADIOS
        if r.vendor_id == "0e8d" and r.product_id == "7612"
    )


async def test_install_patch_emits_ht20_only_caps_on_realtek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the May 2026 field report: an earlier revision
    of this patch asked for ``[HT40+]`` and a field unit with a clean
    RF environment hit a morrownr 88x2bu AP-mode firmware bug, cycling
    the BSS INTERFACE-ENABLED/DISABLED on every client association.
    The fix is to advertise HT20 only so hostapd has no path to ever
    request an HT40 BSS. Asserts both the positive (HT20 caps present,
    correct order) and the negative (none of the HT40-specific caps
    are emitted, regardless of channel)."""
    captured: dict[str, str] = {}

    async def fake_read(_c: str, _p: str) -> str | None:
        return _wrap("")

    async def fake_write(_path: str, content: str) -> bool:
        captured["content"] = content
        return True

    async def fake_pick() -> int:
        # Channel 1 is the case that broke in the field - HT40+ on
        # ch 1 was the specific failure trigger - so we exercise it
        # here to make sure HT20-only is emitted for that channel
        # too, not just the docstring's example channel 6.
        return 1

    async def fake_country() -> str:
        return "US"

    async def fake_detect() -> hotspot_radio.HotspotRadio:
        return _realtek_radio()

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", fake_pick)
    monkeypatch.setattr(hotspot_radio, "_get_country_code", fake_country)
    monkeypatch.setattr(hotspot_radio, "_detect_hotspot_radio", fake_detect)

    result = await hotspot_radio._install_create_ap_speed_patch()
    assert result is True

    content = captured["content"]
    # HT20-valid caps we keep on Realtek
    assert "[SHORT-GI-20]" in content
    assert "[LDPC]" in content
    assert "[RX-STBC1]" in content
    assert "[MAX-AMSDU-7935]" in content
    # HT40-specific caps that broke a field unit's Realtek. Each
    # one is checked individually so a regression that brings back
    # just one of them fails with a specific message.
    assert "[HT40+]" not in content, (
        "HT40+ must not be re-introduced on Realtek - it caused "
        "INTERFACE-DISABLED cycling on the morrownr 88x2bu AP-mode "
        "firmware path on units with clean RF environments. See May "
        "2026 field report."
    )
    assert "[HT40-]" not in content, "HT40- has the same driver issue as HT40+"
    assert "SHORT-GI-40" not in content, (
        "SHORT-GI-40 only applies inside an HT40 BSS; emitting it "
        "implies an HT40 path we explicitly removed from Realtek."
    )
    assert "DSSS_CCK-40" not in content, (
        "DSSS_CCK-40 only applies inside an HT40 BSS; same reason."
    )
    # Channel and country must also be present and correct for the
    # picked channel.
    assert '"-c", "1"' in content
    assert '"--country", "US"' in content
    assert '"--ieee80211n"' in content
    # --no-virt is universal-safe and now always emitted, including
    # on Realtek (rtl88x2bu doesn't support virtual ifaces so the
    # flag is a no-op for the chip but still belt-and-suspenders).
    assert '"--no-virt"' in content
    # And nothing about ac/ax/VHT should sneak in on Realtek -
    # those are HE-chip / legacy-5GHz paths, not Realtek's.
    assert "--ieee80211ac" not in content
    assert "--ieee80211ax" not in content
    assert "--vht_capab" not in content
    assert "--freq-band" not in content


# ---------------------------------------------------------------------
# _CREATE_AP_ANCHOR_RE: must tolerate the kind of whitespace/comment
# variations that upstream BlueOS reformat passes inflict. The old
# exact-string anchor would silently miss any of these.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        # Canonical: 12 spaces + comma + trailing comment
        '            "--redirect-to-localhost",  # Redirect all traffic\n',
        # 8 spaces (shallower indent)
        '        "--redirect-to-localhost",  # comment\n',
        # 16 spaces (deeper indent)
        '                "--redirect-to-localhost",  # comment\n',
        # Comma but no trailing comment
        '            "--redirect-to-localhost",\n',
        # No comma and no comment (last item of a list with no trailing comma)
        '            "--redirect-to-localhost"\n',
        # Tab indent
        '\t\t\t"--redirect-to-localhost",\n',
        # Comment with different wording
        '            "--redirect-to-localhost",  # captive portal anchor\n',
        # Spaces between comma and comment vary
        '            "--redirect-to-localhost",   # extra spaces\n',
    ],
)
def test_create_ap_anchor_regex_tolerates_variant(variant: str) -> None:
    """Each tolerated upstream variant must match and capture its indent."""
    src = _wrap("", anchor=variant)
    m = hotspot_radio._CREATE_AP_ANCHOR_RE.search(src)
    assert m is not None, f"regex must match variant:\n{variant!r}"
    assert m.group("indent") == variant[: len(variant) - len(variant.lstrip(" \t"))]


def test_create_ap_anchor_regex_rejects_unindented_token() -> None:
    """A top-level mention of the token (e.g. in module docstring or
    inside a comment) must NOT match - injecting flags there would
    corrupt unrelated source."""
    src = (
        "# Discusses --redirect-to-localhost in a comment\n"
        '"--redirect-to-localhost",\n'  # zero indent
        "argv = [\n"
        '            "create_ap",\n'
        "]\n"
    )
    assert hotspot_radio._CREATE_AP_ANCHOR_RE.search(src) is None


def test_strip_doris_patch_works_with_shallow_indent_anchor() -> None:
    """Strip must still recognise the patch block when the anchor has a
    different indent than canonical, because the regex anchor accepts
    any indent and the patch block uses ``[ \\t]*`` per-line."""
    shallow_anchor = '        "--redirect-to-localhost",\n'
    shallow_patch = (
        '        "-c", "6",\n'
        '        "--ieee80211n",\n'
        '        "--ht_capab", "[HT40+]",\n'
        '        "--country", "US",\n'
    )
    src = _wrap(shallow_patch, anchor=shallow_anchor)
    stripped = hotspot_radio._strip_doris_patch(src)
    assert "--ht_capab" not in stripped
    assert "--ieee80211n" not in stripped
    assert "--redirect-to-localhost" in stripped


async def test_install_patch_uses_captured_indent_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inserted flag lines must match the indentation of the anchor
    line they follow, so the resulting file stays syntactically clean
    regardless of upstream indentation choices."""
    shallow_anchor = '        "--redirect-to-localhost",\n'
    src = _wrap("", anchor=shallow_anchor)

    async def fake_read(container: str, path: str) -> str:
        return src

    write_calls: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        write_calls.append((path, content))
        return True

    async def fake_pick() -> int:
        return 1

    async def fake_country() -> str:
        return "US"

    async def fake_detect() -> hotspot_radio.HotspotRadio:
        return _realtek_radio()

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", fake_pick)
    monkeypatch.setattr(hotspot_radio, "_get_country_code", fake_country)
    monkeypatch.setattr(hotspot_radio, "_detect_hotspot_radio", fake_detect)

    result = await hotspot_radio._install_create_ap_speed_patch()
    assert result is True

    assert len(write_calls) == 1
    _, content = write_calls[0]
    # Each inserted flag line must begin with the same 8-space indent
    # as the shallow anchor.
    assert '        "-c", "1",\n' in content
    assert '        "--ieee80211n",\n' in content
    assert '        "--no-virt",\n' in content
    # And must NOT have the canonical 12-space indent baked in.
    assert '            "-c", "1",\n' not in content


# ---------------------------------------------------------------------
# Bug 4: _install_create_ap_speed_patch must return False on write
# failure too, so setup_hotspot_radio doesn't add a bind entry that
# points at a missing (or stale) host file.
# ---------------------------------------------------------------------


async def test_install_patch_returns_false_on_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(container: str, path: str) -> str:
        return CLEAN_SOURCE_WITH_ANCHOR

    async def fake_write(path: str, content: str) -> bool:
        return False

    async def fake_pick() -> int:
        return 6

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_pick_24ghz_channel", fake_pick)

    rec = _RunHostCommandRecorder()
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    result = await hotspot_radio._install_create_ap_speed_patch()
    assert result is False, (
        "_write_host_file returning False must propagate as False so "
        "the caller doesn't add a bind entry that points at a host "
        "file we failed to actually write"
    )


async def test_install_patch_returns_false_on_unreadable_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(container: str, path: str) -> None:
        return None

    monkeypatch.setattr(hotspot_radio, "_read_container_file", fake_read)

    rec = _RunHostCommandRecorder()
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    result = await hotspot_radio._install_create_ap_speed_patch()
    assert result is False


# ---------------------------------------------------------------------
# Bug 4 (integration): setup_hotspot_radio must only call
# _ensure_startup_bind when _install_create_ap_speed_patch succeeds.
# This is the contract that closes the May 2026 factory-revert footgun
# at the *outer* level: the self-heal inside the patch function removes
# the bind, and the outer must NOT silently re-add it.
# ---------------------------------------------------------------------


async def test_setup_hotspot_radio_skips_ensure_bind_when_patch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_write(path: str, content: str) -> bool:
        return True

    async def noop_reload() -> None:
        return None

    async def patch_fails() -> bool:
        return False

    ensure_calls: list[bool] = []

    async def fake_ensure_bind() -> None:
        ensure_calls.append(True)

    rec = _RunHostCommandRecorder()

    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_reload_udev", noop_reload)
    monkeypatch.setattr(hotspot_radio, "_reload_network_manager", noop_reload)
    monkeypatch.setattr(
        hotspot_radio, "_install_create_ap_speed_patch", patch_fails
    )
    monkeypatch.setattr(hotspot_radio, "_ensure_startup_bind", fake_ensure_bind)
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    await hotspot_radio.setup_hotspot_radio()

    assert ensure_calls == [], (
        "_ensure_startup_bind MUST NOT run when _install_create_ap_"
        "speed_patch reported failure - otherwise we re-add a bind "
        "pointing at a missing/un-patched source and re-arm the May "
        "2026 factory-revert trap"
    )


async def test_setup_hotspot_radio_calls_ensure_bind_when_patch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_write(path: str, content: str) -> bool:
        return True

    async def noop_reload() -> None:
        return None

    async def patch_succeeds() -> bool:
        return True

    ensure_calls: list[bool] = []

    async def fake_ensure_bind() -> None:
        ensure_calls.append(True)

    rec = _RunHostCommandRecorder()
    # ls returns nothing matching, so the rename-not-yet-applied
    # logger.info path runs; immaterial to this test.
    rec.respond(True, "")

    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_reload_udev", noop_reload)
    monkeypatch.setattr(hotspot_radio, "_reload_network_manager", noop_reload)
    monkeypatch.setattr(
        hotspot_radio, "_install_create_ap_speed_patch", patch_succeeds
    )
    monkeypatch.setattr(hotspot_radio, "_ensure_startup_bind", fake_ensure_bind)
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    await hotspot_radio.setup_hotspot_radio()

    assert ensure_calls == [True], (
        "_ensure_startup_bind must run exactly once when "
        "_install_create_ap_speed_patch reported success"
    )


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


# ---------------------------------------------------------------------
# _remove_startup_bind: counterpart of _ensure_startup_bind, used by
# the self-heal path.  Must rewrite startup.json with our bind entry
# stripped while leaving every other bind untouched.
# ---------------------------------------------------------------------


def _startup_json_with_our_bind() -> str:
    """A miniature startup.json that contains our wifi-override bind
    plus an unrelated bind we must not touch."""
    import json as _json

    return _json.dumps({
        "core": {
            "tag": "1.5.0",
            "binds": {
                "/dev/": {"bind": "/dev/", "mode": "rw"},
                hotspot_radio.WIFI_OVERRIDE_HOST_PATH: {
                    "bind": hotspot_radio.WIFI_OVERRIDE_CONTAINER_PATH,
                    "mode": "ro",
                },
            },
        }
    }, indent=2) + "\n"


def _startup_json_without_our_bind() -> str:
    import json as _json

    return _json.dumps({
        "core": {
            "tag": "1.5.0",
            "binds": {
                "/dev/": {"bind": "/dev/", "mode": "rw"},
            },
        }
    }, indent=2) + "\n"


async def test_remove_startup_bind_removes_our_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(path: str) -> str:
        assert path == hotspot_radio.BOOTSTRAP_STARTUP_JSON
        return _startup_json_with_our_bind()

    written: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        written.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    await hotspot_radio._remove_startup_bind()

    assert len(written) == 1
    path, content = written[0]
    assert path == hotspot_radio.BOOTSTRAP_STARTUP_JSON
    import json as _json

    parsed = _json.loads(content)
    binds = parsed["core"]["binds"]
    assert hotspot_radio.WIFI_OVERRIDE_HOST_PATH not in binds, (
        "our bind entry must be gone after _remove_startup_bind"
    )
    # And unrelated binds must remain untouched.
    assert "/dev/" in binds


async def test_remove_startup_bind_noop_when_entry_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(path: str) -> str:
        return _startup_json_without_our_bind()

    written: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        written.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    await hotspot_radio._remove_startup_bind()
    assert written == [], (
        f"must not rewrite startup.json when our bind isn't there; "
        f"got writes: {written}"
    )


async def test_remove_startup_bind_handles_unreadable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(path: str) -> None:
        return None  # simulate Commander not returning content

    written: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        written.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    # Must not raise even if startup.json can't be read.
    await hotspot_radio._remove_startup_bind()
    assert written == []


# ---------------------------------------------------------------------
# Bug 3: schema-migration defensive guards.  Both _ensure_startup_bind
# and _remove_startup_bind must refuse to write when the on-disk
# startup.json doesn't have the ``core.binds`` shape we know how to
# walk.  The previous implementation silently created missing keys via
# ``setdefault``, which would survive a bootstrap migration that
# renamed/restructured the schema but write our entry into a section
# bootstrap never reads, leaving us with no signal that the bind
# isn't active.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        # Top-level isn't an object
        '["not", "a", "dict"]',
        # No "core" key at all (schema migration could rename it)
        '{"system": {"binds": {}}}',
        # "core" present but not an object
        '{"core": "factory"}',
        # "core" object but "binds" missing
        '{"core": {"tag": "1.5.0"}}',
        # "core" present, "binds" is a list (schema change)
        '{"core": {"binds": []}}',
    ],
)
async def test_ensure_startup_bind_refuses_unrecognized_schema(
    raw: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(path: str) -> str:
        return raw

    writes: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        writes.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    await hotspot_radio._ensure_startup_bind()

    assert writes == [], (
        "Unrecognized schema must NOT trigger a write - silently "
        "creating a parallel 'core'/'binds' would corrupt the file "
        "with a key bootstrap doesn't read."
    )


@pytest.mark.parametrize(
    "raw",
    [
        '["not", "a", "dict"]',
        '{"system": {"binds": {}}}',
        '{"core": "factory"}',
        '{"core": {"tag": "1.5.0"}}',
        '{"core": {"binds": []}}',
    ],
)
async def test_remove_startup_bind_refuses_unrecognized_schema(
    raw: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read(path: str) -> str:
        return raw

    writes: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        writes.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    await hotspot_radio._remove_startup_bind()

    assert writes == [], (
        "Unrecognized schema during remove must also skip writing."
    )


async def test_ensure_startup_bind_does_not_overwrite_unrelated_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the schema IS recognised, the install must preserve
    every other bind entry unchanged."""
    import json as _json

    raw = _json.dumps({
        "core": {
            "tag": "1.5.0",
            "binds": {
                "/dev/": {"bind": "/dev/", "mode": "rw"},
                "/etc/blueos/": {"bind": "/etc/blueos/", "mode": "rw"},
            },
        },
        "extensions": {"foo": "bar"},
    }) + "\n"

    async def fake_read(path: str) -> str:
        return raw

    writes: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        writes.append((path, content))
        return True

    monkeypatch.setattr(hotspot_radio, "_read_host_file", fake_read)
    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)

    await hotspot_radio._ensure_startup_bind()

    assert len(writes) == 1
    _, content = writes[0]
    parsed = _json.loads(content)
    binds = parsed["core"]["binds"]
    assert "/dev/" in binds
    assert "/etc/blueos/" in binds
    assert hotspot_radio.WIFI_OVERRIDE_HOST_PATH in binds
    # Top-level sibling keys must remain.
    assert parsed.get("extensions") == {"foo": "bar"}


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


# =====================================================================
# Multi-chipset support: per-chip create_ap flags, USB detection,
# country-code parsing, multi-vendor udev rule, MT76 modprobe write.
# These exercise the May 2026 expansion from Realtek-only to four
# supported chipsets (Realtek RTL88x2BU, MediaTek MT7612U / MT7921U,
# Atheros AR9271).
# =====================================================================


# ---------------------------------------------------------------------
# _build_create_ap_flags: per-chip ht_capab and extra argv tokens.
# Asserted as a *unit* test rather than going through
# _install_create_ap_speed_patch so a regression points at the actual
# offending function instead of the entire pipeline.
# ---------------------------------------------------------------------


def test_build_create_ap_flags_realtek() -> None:
    flags = hotspot_radio._build_create_ap_flags(
        indent="            ",
        channel=6,
        country="US",
        radio=_realtek_radio(),
    )
    # Realtek = HT20-only on purpose; see module docstring's
    # "Why HT20 on Realtek but HT40+ on every other supported chipset".
    assert '"-c", "6",' in flags
    assert '"--no-virt",' in flags
    assert '"--ieee80211n",' in flags
    assert "[LDPC]" in flags
    assert "[MAX-AMSDU-7935]" in flags
    assert "[SHORT-GI-20]" in flags
    # No HT40, no HE, no AC on Realtek.
    assert "[HT40+]" not in flags
    assert "--ieee80211ax" not in flags
    assert "--ieee80211ac" not in flags
    assert "DSSS_CCK-40" not in flags
    assert '"--country", "US",' in flags


def test_build_create_ap_flags_mt7612u() -> None:
    flags = hotspot_radio._build_create_ap_flags(
        indent="            ",
        channel=6,
        country="US",
        radio=_mt7612u_radio(),
    )
    # MT7612U handles HT40 cleanly in AP mode.
    assert "[HT40+]" in flags
    assert "[SHORT-GI-40]" in flags
    assert "[LDPC]" in flags
    assert "[DSSS_CCK-40]" in flags
    # 802.11ac chip, but at 2.4 GHz we drive it as n only.
    assert "--ieee80211ac" not in flags
    assert "--ieee80211ax" not in flags
    assert '"--no-virt",' in flags
    assert '"--ieee80211n",' in flags


def test_build_create_ap_flags_mt7921u_emits_ieee80211ax() -> None:
    """MT7921U is the only currently-supported HE-capable chip; if
    we ever stop emitting --ieee80211ax for it, ``ieee80211ax=1`` is
    silently dropped from hostapd.conf and HE rates aren't unlocked."""
    flags = hotspot_radio._build_create_ap_flags(
        indent="            ",
        channel=6,
        country="US",
        radio=_mt7921u_radio(),
    )
    assert '"--ieee80211ax",' in flags
    assert "[HT40+]" in flags
    assert "[LDPC]" in flags
    assert "[TX-STBC]" in flags
    assert "[MAX-AMSDU-7935]" in flags
    # DSSS_CCK-40 must NOT appear: MT7921U's iw phy info reports
    # "No DSSS/CCK HT40" and hostapd refuses to start with it.
    assert "DSSS_CCK-40" not in flags


def test_build_create_ap_flags_ar9271_omits_unsupported_caps() -> None:
    """AR9271 + ath9k_htc doesn't advertise LDPC; hostapd refuses to
    start with ``Driver does not support configured HT capability
    [LDPC]``. Same goes for MAX-AMSDU-7935 (chip max is 3839)."""
    flags = hotspot_radio._build_create_ap_flags(
        indent="            ",
        channel=6,
        country="US",
        radio=_ar9271_radio(),
    )
    # HT40 works on AR9271; SGI-20/40, RX-STBC1, DSSS_CCK-40 OK.
    assert "[HT40+]" in flags
    assert "[SHORT-GI-40]" in flags
    assert "[RX-STBC1]" in flags
    assert "[DSSS_CCK-40]" in flags
    # The two capabilities that broke this chip in May 2026:
    assert "[LDPC]" not in flags, (
        "AR9271's ath9k_htc driver does NOT claim LDPC; emitting "
        "[LDPC] makes hostapd hard-fail with 'Driver does not "
        "support configured HT capability [LDPC]'"
    )
    assert "MAX-AMSDU-7935" not in flags, (
        "AR9271 max A-MSDU is 3839, not 7935; emitting "
        "[MAX-AMSDU-7935] also triggers the unsupported-cap fail."
    )
    # AR9271 is n-only.
    assert "--ieee80211ax" not in flags
    assert "--ieee80211ac" not in flags


def test_build_create_ap_flags_fallback_when_radio_unknown() -> None:
    """When the bus contains an unrecognised chip (or ``lsusb``
    failed), we still emit a HT20 block with two caps that every
    802.11n driver we've checked advertises. The AP comes up at
    mid-throughput rather than not at all."""
    flags = hotspot_radio._build_create_ap_flags(
        indent="            ",
        channel=6,
        country="US",
        radio=None,
    )
    assert '"-c", "6",' in flags
    assert '"--no-virt",' in flags
    assert '"--ieee80211n",' in flags
    assert hotspot_radio._FALLBACK_HT_CAPAB in flags
    # No HT40 / HE / AC / chip-specific high-end caps.
    assert "[HT40+]" not in flags
    assert "[LDPC]" not in flags
    assert "MAX-AMSDU-7935" not in flags
    assert "--ieee80211ax" not in flags


def test_build_create_ap_flags_uses_indent_uniformly() -> None:
    """Every emitted line must use the requested indent. Mixing
    indents in the inserted block produces broken Python in the
    patched ``networkmanager.py`` and wifi-manager crashes on
    import."""
    indent = "    "  # deliberately unusual: 4-space
    flags = hotspot_radio._build_create_ap_flags(
        indent=indent,
        channel=1,
        country="DE",
        radio=_mt7921u_radio(),
    )
    for line in flags.splitlines():
        assert line.startswith(indent), (
            f"line does not use the requested indent: {line!r}"
        )


# ---------------------------------------------------------------------
# _detect_hotspot_radio: parses ``lsusb`` output against the supported
# table. Order matters: first match wins (matters in tests, never on a
# real DORIS since there's only one USB Wi-Fi port).
# ---------------------------------------------------------------------


_LSUSB_REALTEK = (
    "Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    "Bus 003 Device 003: ID 0bda:b812 Realtek Semiconductor Corp. "
    "RTL88x2bu [AC1200 Techkey]\n"
    "Bus 003 Device 004: ID 04b4:0033 Cypress Semiconductor Corp.\n"
)

_LSUSB_MT7612U = (
    "Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    "Bus 003 Device 003: ID 0e8d:7612 MediaTek Inc. MT7612U "
    "802.11ac WLAN Adapter\n"
)

_LSUSB_MT7921U = (
    "Bus 003 Device 003: ID 0e8d:7961 MediaTek Inc. Wireless_Device\n"
)

_LSUSB_AR9271 = (
    "Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    "Bus 003 Device 003: ID 0cf3:9271 Qualcomm Atheros Communications "
    "AR9271 802.11n\n"
)

_LSUSB_NO_RADIO = (
    "Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    "Bus 003 Device 002: ID 04b4:0033 Cypress Semiconductor Corp.\n"
)

# Same vendor as MediaTek MT7921U (0e8d) but a different product ID -
# must NOT match the MT7921U row. Guards against an over-eager
# vendor-only match that would map non-Wi-Fi MediaTek devices into the
# Wi-Fi table.
_LSUSB_MT_FOREIGN_PRODUCT = (
    "Bus 003 Device 003: ID 0e8d:1234 MediaTek Inc. Generic Device\n"
)


@pytest.mark.parametrize(
    "lsusb_out,expected_label",
    [
        (_LSUSB_REALTEK, "Realtek RTL88x2BU"),
        (_LSUSB_MT7612U, "MediaTek MT7612U"),
        (_LSUSB_MT7921U, "MediaTek MT7921AU"),
        (_LSUSB_AR9271, "Atheros AR9271"),
    ],
    ids=["realtek", "mt7612u", "mt7921u", "ar9271"],
)
async def test_detect_hotspot_radio_returns_table_entry(
    monkeypatch: pytest.MonkeyPatch,
    lsusb_out: str,
    expected_label: str,
) -> None:
    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        assert cmd == "lsusb"
        return True, lsusb_out

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    radio = await hotspot_radio._detect_hotspot_radio()
    assert radio is not None
    assert radio.label == expected_label


async def test_detect_hotspot_radio_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        return True, _LSUSB_NO_RADIO

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert await hotspot_radio._detect_hotspot_radio() is None


async def test_detect_hotspot_radio_rejects_foreign_product_with_known_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendor-only matching would map a non-Wi-Fi MediaTek device into
    the table. We match on (vendor, product) precisely."""
    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        return True, _LSUSB_MT_FOREIGN_PRODUCT

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert await hotspot_radio._detect_hotspot_radio() is None


async def test_detect_hotspot_radio_returns_none_when_lsusb_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        return False, ""

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert await hotspot_radio._detect_hotspot_radio() is None


# ---------------------------------------------------------------------
# _get_country_code: parses ``iw reg get`` for the active regdomain.
# Without a country code, hostapd uses world-regulatory which silently
# undoes other parts of the create_ap speed patch.
# ---------------------------------------------------------------------


async def test_get_country_code_returns_active_regdomain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iw_out = (
        "global\n"
        "country US: DFS-FCC\n"
        "        (2402 - 2472 @ 40), (N/A, 36)\n"
    )

    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        assert cmd == "iw reg get"
        return True, iw_out

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert await hotspot_radio._get_country_code() == "US"


async def test_get_country_code_falls_back_on_world_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``country 00`` is the kernel's sentinel for "no country set"
    (world regulatory). Treat it the same as a parse failure."""
    iw_out = (
        "global\n"
        "country 00: DFS-UNSET\n"
    )

    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        return True, iw_out

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert (
        await hotspot_radio._get_country_code()
        == hotspot_radio._COUNTRY_CODE_FALLBACK
    )


async def test_get_country_code_falls_back_when_iw_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        return False, ""

    monkeypatch.setattr(hotspot_radio, "_run_host_command", fake_run)
    assert (
        await hotspot_radio._get_country_code()
        == hotspot_radio._COUNTRY_CODE_FALLBACK
    )


# ---------------------------------------------------------------------
# UDEV_RULE_CONTENT: generated from SUPPORTED_HOTSPOT_RADIOS. Every
# supported chipset's USB IDs must appear so the kernel renames its
# netdev to ``uap0`` regardless of which adapter the user plugged in.
# ---------------------------------------------------------------------


def test_udev_rule_content_includes_every_supported_chipset() -> None:
    for radio in hotspot_radio.SUPPORTED_HOTSPOT_RADIOS:
        assert f'ATTRS{{idVendor}}=="{radio.vendor_id}"' in hotspot_radio.UDEV_RULE_CONTENT, (
            f"udev rule missing idVendor for {radio.label}"
        )
        assert f'ATTRS{{idProduct}}=="{radio.product_id}"' in hotspot_radio.UDEV_RULE_CONTENT, (
            f"udev rule missing idProduct for {radio.label}"
        )
    assert hotspot_radio.UDEV_RULE_CONTENT.count('NAME="uap0"') == len(
        hotspot_radio.SUPPORTED_HOTSPOT_RADIOS
    ), "expected one NAME=\"uap0\" line per supported chipset"


# ---------------------------------------------------------------------
# setup_hotspot_radio: writes the MT76 modprobe.d file alongside the
# udev rule and NetworkManager conf. Installed unconditionally because
# it's harmless when no mt76_usb chip is on the bus.
# ---------------------------------------------------------------------


async def test_setup_hotspot_radio_writes_mt76_modprobe_conf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str]] = []

    async def fake_write(path: str, content: str) -> bool:
        writes.append((path, content))
        return True

    async def noop_reload() -> None:
        return None

    async def patch_succeeds() -> bool:
        return True

    async def fake_ensure_bind() -> None:
        return None

    rec = _RunHostCommandRecorder()

    monkeypatch.setattr(hotspot_radio, "_write_host_file", fake_write)
    monkeypatch.setattr(hotspot_radio, "_reload_udev", noop_reload)
    monkeypatch.setattr(hotspot_radio, "_reload_network_manager", noop_reload)
    monkeypatch.setattr(
        hotspot_radio, "_install_create_ap_speed_patch", patch_succeeds
    )
    monkeypatch.setattr(hotspot_radio, "_ensure_startup_bind", fake_ensure_bind)
    monkeypatch.setattr(hotspot_radio, "_run_host_command", rec)

    await hotspot_radio.setup_hotspot_radio()

    written_paths = {p for p, _ in writes}
    assert hotspot_radio.UDEV_RULE_PATH in written_paths
    assert hotspot_radio.NM_CONF_PATH in written_paths
    assert hotspot_radio.MT76_MODPROBE_PATH in written_paths, (
        "MT76 modprobe conf must be installed alongside udev rule and "
        "NM conf so swapping in an MT chip doesn't need an extra reboot "
        "to pick up disable_usb_sg=1"
    )
    mt76_content = dict(writes)[hotspot_radio.MT76_MODPROBE_PATH]
    assert "disable_usb_sg=1" in mt76_content
    assert "mt76_usb" in mt76_content
