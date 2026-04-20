"""Persistent logging that survives container reboots.

Writes rotating log files to the bind-mounted userdata directory so
diagnostic history is available across container restarts.  Also
captures periodic dmesg snapshots for USB/WiFi kernel-level issues.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("DORIS_LOG_DIR", "/tmp/storage/userdata/doris_logs"))

MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
BACKUP_COUNT = 5  # keep 5 rotated files (10 MB total max)

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

DMESG_INTERVAL_S = 300  # capture dmesg every 5 minutes
DMESG_KEYWORDS = (
    "usb",
    "wlan",
    "wifi",
    "rtw",
    "88x2bu",
    "rtl",
    "firmware",
    "error",
    "disconnect",
    "overcurrent",
    "reset",
)

_dmesg_task: asyncio.Task | None = None

logger = logging.getLogger(__name__)


def setup_persistent_logging(level: int = logging.DEBUG) -> Path:
    """Configure the root logger with a RotatingFileHandler on persistent storage.

    Returns the log directory path.  Safe to call multiple times — skips
    if a file handler on the same directory is already attached.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()

    log_file = LOG_DIR / "doris.log"
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == log_file:
            return LOG_DIR

    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    root.addHandler(file_handler)

    if root.level > level:
        root.setLevel(level)

    # Also ensure console output keeps working
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
        root.addHandler(console)

    logger.info("Persistent logging initialized -> %s", log_file)
    return LOG_DIR


def _capture_dmesg_snapshot() -> str | None:
    """Run dmesg and filter for relevant kernel messages."""
    try:
        result = subprocess.run(
            ["dmesg", "--time-format=iso"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["dmesg"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        if result.returncode != 0:
            return None
        raw = result.stdout.decode(errors="replace")
        lines = []
        for line in raw.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in DMESG_KEYWORDS):
                lines.append(line)
        return "\n".join(lines[-100:]) if lines else None
    except Exception:
        return None


async def _dmesg_loop() -> None:
    """Periodically capture relevant dmesg lines and write them to a separate log."""
    dmesg_log = LOG_DIR / "dmesg.log"
    dmesg_handler = RotatingFileHandler(
        str(dmesg_log),
        maxBytes=MAX_BYTES,
        backupCount=2,  # 3 files x 2 MB = 6 MB max for dmesg
        encoding="utf-8",
    )
    dmesg_handler.setFormatter(logging.Formatter("%(message)s"))
    dmesg_logger = logging.getLogger("doris.dmesg")
    dmesg_logger.addHandler(dmesg_handler)
    dmesg_logger.setLevel(logging.INFO)
    dmesg_logger.propagate = False

    seen_lines: set[str] = set()

    while True:
        try:
            snapshot = await asyncio.get_event_loop().run_in_executor(
                None, _capture_dmesg_snapshot
            )
            if snapshot:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                new_lines = []
                for line in snapshot.splitlines():
                    if line not in seen_lines:
                        seen_lines.add(line)
                        new_lines.append(line)
                if new_lines:
                    dmesg_logger.info("--- dmesg snapshot %s UTC (%d new) ---", stamp, len(new_lines))
                    for line in new_lines:
                        dmesg_logger.info(line)
                # Prevent unbounded memory growth
                if len(seen_lines) > 5000:
                    seen_lines.clear()
        except Exception as e:
            logger.debug("dmesg capture error: %s", e)
        await asyncio.sleep(DMESG_INTERVAL_S)


def start_dmesg_capture() -> None:
    """Start the background dmesg capture task (idempotent)."""
    global _dmesg_task
    if _dmesg_task is not None and not _dmesg_task.done():
        return
    _dmesg_task = asyncio.get_event_loop().create_task(_dmesg_loop())
    logger.info("dmesg capture started (interval %ds)", DMESG_INTERVAL_S)


def list_log_files() -> list[dict]:
    """Return metadata for all log files in the log directory."""
    if not LOG_DIR.is_dir():
        return []
    files = []
    for path in sorted(LOG_DIR.iterdir()):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            files.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            continue
    return files


def read_log_file(name: str, tail_lines: int = 200) -> str | None:
    """Read the last N lines of a log file. Returns None if not found."""
    path = LOG_DIR / name
    if not path.is_file() or not path.is_relative_to(LOG_DIR):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if tail_lines and len(lines) > tail_lines:
            lines = lines[-tail_lines:]
        return "\n".join(lines)
    except OSError:
        return None


def read_log_bytes(name: str) -> bytes | None:
    """Read a log file as raw bytes for download. Returns None if not found."""
    path = LOG_DIR / name
    if not path.is_file() or not path.is_relative_to(LOG_DIR):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None
