"""Deterministic BIN-log -> dive linking in the media browser.

ArduPilot ``.BIN`` logs routinely carry a bogus mtime (the flight-controller
RTC is often unset), so the file manager's timestamp-based dive matching
fails and archived logs show up as unlinked "System" data (Dive Name "—").

The archive step (services.binlog) names copies ``<dive-slug>_<num>.BIN`` and
records them in each dive's ``bin_log_files``.  ``storage`` recovers the link
from the filename alone -- no clock required.  These tests exercise that path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from doris.models.media import MediaType
from doris.services.storage import (
    _bin_log_dive_name,
    _load_bin_log_index,
    _usb_file_to_media,
)

# A clearly-bogus mtime (~year 2074) like the ones the flight controller
# writes with an unset RTC -- outside storage._sane_bounds' upper limit.
BOGUS_MTIME = 3_300_000_000.0


def _write_dive(root: Path, stem: str, dive_name: str, bin_files: list[str]) -> None:
    ddir = root / "dives"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / f"{stem}.json").write_text(
        json.dumps(
            {
                "dive_name": dive_name,
                "started_at": "2026-08-17T23:17:59+00:00",
                "ended_at": "2026-08-17T23:44:44+00:00",
                "status": "completed",
                "bin_log_files": bin_files,
            }
        )
    )


def test_index_links_by_exact_archived_filename(tmp_path: Path):
    _write_dive(
        tmp_path,
        "dive_0008",
        "8-17-pool-2",
        ["/mnt/usb/DORIS/binlogs/8_17_pool_2_00000183.BIN"],
    )
    idx = _load_bin_log_index(tmp_path)

    assert idx.lookup("8_17_pool_2_00000183.BIN") == "8-17-pool-2"


def test_index_links_by_slug_when_bin_files_missing(tmp_path: Path):
    # No bin_log_files recorded -> fall back to the reconstructed slug.
    _write_dive(tmp_path, "dive_0008", "8-17-pool-2", [])
    idx = _load_bin_log_index(tmp_path)

    # slug_for_dive("8-17-pool-2") == "8_17_pool_2"; any archived number links.
    assert idx.lookup("8_17_pool_2_00000999.BIN") == "8-17-pool-2"


def test_index_returns_none_for_unknown(tmp_path: Path):
    _write_dive(tmp_path, "dive_0008", "8-17-pool-2", [])
    idx = _load_bin_log_index(tmp_path)

    assert idx.lookup("some_other_dive_00000001.BIN") is None


def test_bin_log_dive_name_ignores_non_bin_and_non_data(tmp_path: Path):
    _write_dive(tmp_path, "dive_0008", "8-17-pool-2", [])
    idx = _load_bin_log_index(tmp_path)

    # A non-.bin data file must not be linked by this path.
    assert _bin_log_dive_name("8_17_pool_2_telemetry.csv", MediaType.DATA, idx) is None
    # A .bin classified as something other than DATA is skipped too.
    assert _bin_log_dive_name("8_17_pool_2_00000183.BIN", MediaType.IMAGE, idx) is None


def test_usb_bin_with_bogus_mtime_links_to_dive(tmp_path: Path):
    """The regression: a BIN with an out-of-range mtime still gets its dive."""
    _write_dive(
        tmp_path,
        "dive_0008",
        "8-17-pool-2",
        ["/mnt/usb/DORIS/binlogs/8_17_pool_2_00000183.BIN"],
    )
    idx = _load_bin_log_index(tmp_path)

    binfile = tmp_path / "8_17_pool_2_00000183.BIN"
    binfile.write_bytes(b"\x00" * 4096)
    os.utime(binfile, (BOGUS_MTIME, BOGUS_MTIME))

    mf = _usb_file_to_media(
        binfile,
        tmp_path,
        dive_windows=[],  # timestamp matching intentionally has nothing to match
        mount_key="usb0",
        rel_under_mount=Path("DORIS/binlogs/8_17_pool_2_00000183.BIN"),
        bin_index=idx,
    )

    assert mf.dive_name == "8-17-pool-2"
    # And it must stay classified as data, not downgraded to SYSTEM.
    assert mf.media_type == MediaType.DATA


def test_usb_bin_without_match_falls_back_to_system(tmp_path: Path):
    """A BIN with no dive link (and no timestamp match) is still SYSTEM."""
    _write_dive(tmp_path, "dive_0008", "8-17-pool-2", [])
    idx = _load_bin_log_index(tmp_path)

    orphan = tmp_path / "unknown_00000005.BIN"
    orphan.write_bytes(b"\x00" * 2048)
    os.utime(orphan, (BOGUS_MTIME, BOGUS_MTIME))

    mf = _usb_file_to_media(
        orphan,
        tmp_path,
        dive_windows=[],
        mount_key="usb0",
        rel_under_mount=Path("DORIS/binlogs/unknown_00000005.BIN"),
        bin_index=idx,
    )

    assert mf.dive_name is None
    assert mf.media_type == MediaType.SYSTEM
