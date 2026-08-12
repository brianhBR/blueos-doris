"""Tests for Lua script deployment (utils.deploy_lua_scripts).

The autopilot reads doris.lua on its own schedule, so a reader must never see a
half-written file. A partial read compiles whatever prefix is on disk and
reports a syntax error at an arbitrary line, which is indistinguishable from a
real bug in the script.
"""

from __future__ import annotations

import logging

import pytest

from doris.utils import _atomic_copy

logger = logging.getLogger(__name__)


def test_copy_reproduces_the_source_exactly(tmp_path):
    src = tmp_path / "doris.lua"
    dest = tmp_path / "scripts" / "doris.lua"
    dest.parent.mkdir()
    body = ("-- line\n" * 10_000).encode()
    src.write_bytes(body)

    _atomic_copy(src, dest)

    assert dest.read_bytes() == body


def test_replacing_an_existing_script_leaves_no_partial_state(tmp_path):
    src = tmp_path / "doris.lua"
    dest = tmp_path / "scripts" / "doris.lua"
    dest.parent.mkdir()
    dest.write_bytes(b"-- previous version\n")
    body = ("-- new\n" * 20_000).encode()
    src.write_bytes(body)

    _atomic_copy(src, dest)

    assert dest.read_bytes() == body
    # The temporary file must not survive next to the script; ArduPilot scans
    # the whole scripts directory and would try to load a stray .lua.
    assert [p.name for p in dest.parent.iterdir()] == ["doris.lua"]


def test_destination_is_never_observed_truncated(tmp_path, monkeypatch):
    """The destination inode is only ever swapped, never written through.

    Fails against a plain shutil.copy2, which opens the destination for writing
    and so exposes an empty file to any reader that arrives mid-copy.
    """
    src = tmp_path / "doris.lua"
    dest = tmp_path / "scripts" / "doris.lua"
    dest.parent.mkdir()
    old = b"-- previous version\n"
    dest.write_bytes(old)
    src.write_bytes(("-- new\n" * 20_000).encode())

    seen: list[bytes] = []
    real_replace = __import__("os").replace

    def spy_replace(a, b):
        # Whatever a reader would get immediately before the swap.
        seen.append(dest.read_bytes())
        return real_replace(a, b)

    monkeypatch.setattr("doris.utils.os.replace", spy_replace)
    _atomic_copy(src, dest)

    assert seen == [old], "destination changed before the atomic swap"


def test_failure_does_not_clobber_the_installed_script(tmp_path, monkeypatch):
    src = tmp_path / "doris.lua"
    dest = tmp_path / "scripts" / "doris.lua"
    dest.parent.mkdir()
    old = b"-- known good\n"
    dest.write_bytes(old)
    src.write_bytes(b"-- new\n")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("doris.utils.os.replace", boom)
    with pytest.raises(OSError):
        _atomic_copy(src, dest)

    assert dest.read_bytes() == old
    assert [p.name for p in dest.parent.iterdir()] == ["doris.lua"]
