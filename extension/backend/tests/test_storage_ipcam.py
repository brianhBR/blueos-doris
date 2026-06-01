"""Tests for internal IP-camera recording discovery (issue #32).

When no USB stick is mounted, the recorder writes to
``DATA_ROOT/userdata/ipcam_recordings/dive_<stamp>/``.  Those files used
to be invisible on the data page because the media scan only covered the
recorder tree and mounted USB volumes.  ``StorageService`` now also scans
the internal ipcam tree and labels files by their per-dive folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doris.models.media import MediaType
from doris.services.storage import (
    StorageService,
    media_abs_path_from_download_id,
)


@pytest.fixture
def svc(tmp_path: Path) -> StorageService:
    root = tmp_path
    media_root = root / "userdata" / "recorder"
    ipcam_root = root / "userdata" / "ipcam_recordings"
    media_root.mkdir(parents=True, exist_ok=True)
    ipcam_root.mkdir(parents=True, exist_ok=True)
    return StorageService(root=root, media_root=media_root, ipcam_root=ipcam_root)


def _make_ipcam_recording(svc: StorageService, dive: str) -> Path:
    d = svc.ipcam_root / dive
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{dive}_part00_cyc00_00000.ts"
    f.write_bytes(b"\x00" * 2048)
    return f


def _make_recorder_file(svc: StorageService, mission: str) -> Path:
    d = svc.media_root / mission
    d.mkdir(parents=True, exist_ok=True)
    f = d / "clip.mp4"
    f.write_bytes(b"\x00" * 1024)
    return f


async def test_flat_scan_includes_internal_ipcam(svc):
    _make_ipcam_recording(svc, "dive_20260601_120000")

    files = await svc.get_media_files()

    assert len(files) == 1
    mf = files[0]
    assert mf.media_type == MediaType.VIDEO
    # Grouped by the per-dive folder, not the generic "userdata" dir.
    assert mf.mission_id == "dive_20260601_120000"
    assert mf.dive_name == "dive_20260601_120000"


async def test_flat_scan_no_double_count(svc):
    _make_ipcam_recording(svc, "dive_a")
    _make_recorder_file(svc, "mission_x")

    files = await svc.get_media_files()
    ids = sorted(f.id for f in files)

    assert len(ids) == 2
    assert len(set(ids)) == 2


async def test_missions_include_internal_ipcam(svc):
    _make_ipcam_recording(svc, "dive_20260601_120000")

    missions = await svc.get_missions_with_media()
    by_id = {m.mission_id: m for m in missions}

    assert "dive_20260601_120000" in by_id
    assert by_id["dive_20260601_120000"].video_count == 1


async def test_mission_filter_resolves_ipcam_folder(svc):
    _make_ipcam_recording(svc, "dive_20260601_120000")

    files = await svc.get_media_files(mission_id="dive_20260601_120000")

    assert len(files) == 1
    assert files[0].filename.endswith(".ts")


async def test_download_id_resolves_for_internal_ipcam(svc):
    f = _make_ipcam_recording(svc, "dive_20260601_120000")

    files = await svc.get_media_files()
    resolved = media_abs_path_from_download_id(files[0].id, svc.root)

    assert resolved == f.resolve()


async def test_empty_ipcam_tree_is_noop(svc):
    _make_recorder_file(svc, "mission_x")

    files = await svc.get_media_files()
    missions = await svc.get_missions_with_media()

    assert len(files) == 1
    assert all(m.mission_id == "mission_x" for m in missions)
