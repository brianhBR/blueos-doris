"""Removable USB volume detection and mount (DropCam-style).

Scans sysfs for removable ``sd*`` block devices, mounts the first usable
partition under a dedicated mount point, and exposes paths for
recordings. A background thread re-tries periodically so a drive
plugged after boot is picked up.

Used by :mod:`ip_camera_recorder` so segmented RTSP files land on
external USB when available, with the same behaviour class as
``BlueOS_videorecorder`` ``app/usb_storage.py``.
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

from ..config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_mounted = False
_device: str | None = None
_probe_thread: threading.Thread | None = None
_stop_probe = threading.Event()


def _mount_point() -> str:
    return settings.usb_mount_point.rstrip("/") or "/mnt/usb"


def _probe_interval_s() -> int:
    return max(5, int(settings.usb_probe_interval_s))


def _scan_usb_devices() -> list[str]:
    """Partition device paths on removable block devices (``/dev/sd*``)."""
    partitions: list[str] = []
    for block in glob.glob("/sys/block/sd*"):
        try:
            with open(os.path.join(block, "removable"), "r") as f:
                if f.read().strip() != "1":
                    continue
        except OSError:
            continue
        dev_name = os.path.basename(block)
        this_block: list[str] = []
        for part in sorted(glob.glob(os.path.join(block, dev_name + "*"))):
            part_name = os.path.basename(part)
            dev_path = f"/dev/{part_name}"
            if os.path.exists(dev_path):
                this_block.append(dev_path)
        if this_block:
            partitions.extend(this_block)
        else:
            dev_path = f"/dev/{dev_name}"
            if os.path.exists(dev_path):
                partitions.append(dev_path)
    return partitions


def is_mounted() -> bool:
    """Return True if our mount point is an active mount."""
    mp = _mount_point()
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mp:
                    return True
    except OSError:
        pass
    return False


def try_mount() -> bool:
    """Detect and mount the first usable USB partition. Returns True on success."""
    global _mounted, _device

    with _lock:
        if _mounted and is_mounted():
            return True

        partitions = _scan_usb_devices()
        if not partitions:
            _mounted = False
            _device = None
            return False

        mp = _mount_point()
        os.makedirs(mp, exist_ok=True)

        if is_mounted():
            _mounted = True
            _device = _device or partitions[0]
            return True

        for dev in partitions:
            result = subprocess.run(
                ["mount", "-o", "rw", dev, mp],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                _mounted = True
                _device = dev
                logger.info("USB mounted: %s -> %s", dev, mp)
                return True
            err = (result.stderr or b"").decode(errors="replace").strip()
            logger.debug("mount %s failed: %s", dev, err)

        _mounted = False
        _device = None
        return False


def unmount() -> None:
    """Unmount USB storage if we mounted it at our mount point."""
    global _mounted, _device
    with _lock:
        mp = _mount_point()
        if is_mounted():
            subprocess.run(["umount", mp], capture_output=True, timeout=10, check=False)
            logger.info("USB unmounted from %s", mp)
        _mounted = False
        _device = None


def get_free_mb() -> float | None:
    """Free space in MB on the USB mount, or None if unavailable."""
    if not (_mounted and is_mounted()):
        return None
    try:
        st = os.statvfs(_mount_point())
        return round((st.f_bavail * st.f_frsize) / (1024 * 1024), 1)
    except OSError:
        return None


def _free_mb_for_path(path: str) -> float | None:
    """Free megabytes on the filesystem that contains ``path``."""
    try:
        st = os.statvfs(path)
        return round((st.f_bavail * st.f_frsize) / (1024 * 1024), 1)
    except OSError:
        return None


def usb_has_room_for_recording() -> bool:
    """True if our USB mount has enough free space."""
    free = get_free_mb()
    if free is None:
        return False
    return free >= float(settings.ipcam_usb_min_free_mb)


def _candidate_recording_bases(relative_subpath: str) -> list[str]:
    """Ordered directory roots to try (DropCam mount, then host ``/mnt`` USB)."""
    root = settings.usb_doris_folder.strip("/").strip() or "DORIS"
    rel = relative_subpath.strip("/").strip()
    mp = _mount_point()
    bases = [os.path.join(mp, root, rel)]
    # When ``external_storage`` already mounted the same disk at ``/mnt``,
    # a second mount at ``/mnt/usb`` fails — use the live mount instead.
    if mp.rstrip("/") != "/mnt" and os.path.ismount("/mnt"):
        bases.append(os.path.join("/mnt", root, rel))
    return bases


def get_recording_dir_if_available(relative_subpath: str) -> str | None:
    """Return a directory on external USB if available and roomy.

    Tries :attr:`Settings.usb_mount_point` first (DropCam-style), then
    ``/mnt`` when that is already the active USB mount from
    ``external_storage``. Creates directories. Returns ``None`` so callers
    can fall back to ``DORIS_DATA_ROOT``.
    """
    min_free = float(settings.ipcam_usb_min_free_mb)
    for base in _candidate_recording_bases(relative_subpath):
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            logger.debug("Skip USB candidate %s: %s", base, e)
            continue
        free = _free_mb_for_path(base)
        if free is None or free < min_free:
            logger.debug(
                "Skip USB candidate %s: free_mb=%s (need >= %s)",
                base,
                free,
                min_free,
            )
            continue
        return base
    return None


def get_status() -> dict:
    """JSON-friendly status for APIs / logging."""
    mounted = _mounted and is_mounted()
    free = get_free_mb() if mounted else None
    mnt_free = _free_mb_for_path("/mnt") if os.path.ismount("/mnt") else None
    min_free = float(settings.ipcam_usb_min_free_mb)
    usable = False
    if free is not None and free >= min_free:
        usable = True
    elif mnt_free is not None and mnt_free >= min_free and _mount_point().rstrip("/") != "/mnt":
        usable = True
    return {
        "mounted": mounted,
        "device": _device,
        "free_mb": free,
        "mnt_free_mb": mnt_free,
        "usable_for_ipcam": usable,
        "mount_point": _mount_point(),
    }


def iter_media_scan_roots() -> list[tuple[str, Path]]:
    """Named filesystem roots used to index downloadable media on USB volumes.

    * ``portable`` — :attr:`Settings.usb_mount_point` when mounted (typically
      ``/mnt/usb``), entire volume (DropCam-style dedicated stick).
    * ``host_mnt`` — host ``/mnt`` when it is a separate mount (e.g. external
      storage already mounted there). Files under ``/mnt/recorder`` are skipped
      to avoid duplicating the BlueOS recorder bind mount.
    """
    roots: list[tuple[str, Path]] = []
    try:
        mp = Path(settings.usb_mount_point.rstrip("/") or "/mnt/usb")
        if mp.exists() and os.path.ismount(str(mp)):
            roots.append(("portable", mp.resolve()))
    except OSError:
        pass
    try:
        mnt = Path("/mnt")
        portable_resolved: Path | None = None
        try:
            portable_resolved = Path(
                settings.usb_mount_point.rstrip("/") or "/mnt/usb"
            ).resolve()
        except OSError:
            portable_resolved = None
        if mnt.exists() and os.path.ismount(str(mnt)):
            if portable_resolved is None or mnt.resolve() != portable_resolved:
                roots.append(("host_mnt", mnt.resolve()))
    except OSError:
        pass
    dedup: dict[str, Path] = {}
    for k, p in roots:
        dedup[k] = p
    return list(dedup.items())


def iter_media_files_on_usb(mount_key: str, base: Path):
    """Yield regular files under ``base`` for media indexing."""
    try:
        base_r = base.resolve()
    except OSError:
        return
    skip_dirs: list[Path] = []
    if mount_key == "host_mnt":
        skip_dirs.append(base_r / "recorder")
    for path in base_r.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        try:
            rp = path.resolve()
        except OSError:
            continue
        skip = False
        for sd in skip_dirs:
            try:
                if sd.exists() and rp.is_relative_to(sd.resolve()):
                    skip = True
                    break
            except (OSError, ValueError, RuntimeError):
                continue
        if skip:
            continue
        yield path


def _probe_loop() -> None:
    while not _stop_probe.is_set():
        if not (_mounted and is_mounted()):
            try:
                try_mount()
            except Exception as e:
                logger.debug("USB probe error: %s", e)
        _stop_probe.wait(_probe_interval_s())


def start_probe() -> None:
    """Start the background USB probe thread (idempotent)."""
    global _probe_thread
    if _probe_thread and _probe_thread.is_alive():
        return
    _stop_probe.clear()
    _probe_thread = threading.Thread(target=_probe_loop, daemon=True, name="doris-usb-probe")
    _probe_thread.start()
    logger.info("USB probe thread started (interval %ss)", _probe_interval_s())


def stop_probe() -> None:
    """Stop the background probe thread."""
    _stop_probe.set()
    if _probe_thread and _probe_thread.is_alive():
        _probe_thread.join(timeout=5)


def start_usb_storage_probe() -> None:
    """Try an immediate mount, then start periodic probing (extension startup)."""
    try_mount()
    start_probe()
