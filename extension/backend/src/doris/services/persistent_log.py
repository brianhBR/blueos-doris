"""Persistent logging that survives container reboots.

Writes rotating log files to the bind-mounted userdata directory so
diagnostic history is available across container restarts.  Also
captures periodic dmesg snapshots (USB/WiFi/power) and Pi 5 rail samples
(PMIC ADCs, ``get_throttled``, hwmon) so a sudden 5 V cut leaves a
last-gasp line on disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("DORIS_LOG_DIR", "/tmp/storage/userdata/doris_logs"))

MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
BACKUP_COUNT = 5  # keep 5 rotated files (10 MB total max)

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Third-party / framework loggers that emit high-volume DEBUG/INFO
# records.  Most damagingly, robyn logs full HTTP response *bodies* at
# DEBUG -- so downloading a log file dumps that file's own bytes straight
# back into the log as a multi-MB single-line entry, which blows through
# rotation and evicts the real dive/recorder history we actually need.
# These are pinned to WARNING (and enforced by a handler filter below) so
# DORIS's own ``doris.*`` loggers stay fully verbose while the framework
# chatter is kept out of the persistent log.
NOISY_LOGGER_PREFIXES = (
    "robyn",
    "httpcore",
    "httpx",
    "websockets",
    "actix_files",
)


class _NoisyLoggerFilter(logging.Filter):
    """Drop sub-WARNING records from known high-volume framework loggers.

    Attached to the persistent file handler so verbose request/response
    DEBUG (including robyn's full response-body dumps) never reaches the
    rotating log regardless of how those loggers' levels are configured
    elsewhere -- robyn reconfigures its own logging after startup, so a
    plain ``setLevel`` is not enough on its own.  ``doris.*`` records and
    anything at WARNING or above always pass through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not record.name.startswith(NOISY_LOGGER_PREFIXES)


def _quiet_noisy_loggers() -> None:
    """Pin the noisy framework loggers to WARNING (idempotent)."""
    for name in NOISY_LOGGER_PREFIXES:
        logging.getLogger(name).setLevel(logging.WARNING)

DMESG_INTERVAL_S = 300  # idle: capture dmesg every 5 minutes
DMESG_INTERVAL_DIVE_S = 30  # in-mission: tighter last-gasp window
DMESG_POLL_S = 5  # how often to re-check dive-active vs idle interval
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
    "undervolt",
    "under-voltage",
    "throttl",
    "pmic",
    "uvlo",
    "panic",
    "oom",
    "thermal",
    "hwmon",
)

# Pi 5 5 V rail + throttle.  Idle 30 s is enough on deck; 10 s in-mission
# so a splash-cut leaves a recent EXT5V sample.  Written to power.log, not
# doris.log — 15 s INFO lines would rotate the dive/recorder history out.
POWER_INTERVAL_S = 30
POWER_INTERVAL_DIVE_S = 10
POWER_POLL_S = 5
EXT5V_WARN_V = 4.75
# Raspberry Pi firmware mailbox error; not an undervolt sticky bit.
THROTTLED_MAILBOX_ERROR = 0x80000001

# One Commander round-trip.  Channel names match `vcgencmd pmic_read_adc`.
POWER_HOST_CMD = (
    "printf 'EXT5V='; vcgencmd pmic_read_adc EXT5V_V 2>/dev/null; "
    "printf 'HDMI='; vcgencmd pmic_read_adc HDMI_V 2>/dev/null; "
    "printf 'VDD_CORE='; vcgencmd pmic_read_adc VDD_CORE_V 2>/dev/null; "
    "printf '3V3_SYS='; vcgencmd pmic_read_adc 3V3_SYS_V 2>/dev/null; "
    "printf 'THROTTLED='; vcgencmd get_throttled 2>/dev/null; "
    "echo HWMON; "
    "for d in /sys/class/hwmon/hwmon*; do "
    "n=$(cat \"$d/name\" 2>/dev/null); "
    "for i in \"$d\"/in*_input; do "
    "[ -f \"$i\" ] || continue; "
    "b=${i%_input}; "
    "lab=$(cat \"${b}_label\" 2>/dev/null || basename \"$b\"); "
    "echo \"$n $lab $(cat \"$i\")\"; "
    "done; "
    "done"
)

_THROTTLED_RE = re.compile(r"throttled=(0x[0-9a-f]+)", re.IGNORECASE)

_dmesg_task: asyncio.Task | None = None
_power_task: asyncio.Task | None = None
_power_host_fail_logged = False

logger = logging.getLogger(__name__)


class _FsyncRotatingFileHandler(RotatingFileHandler):
    """Flush + fsync after every record so a hard 5 V cut keeps the last line."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()
        stream = self.stream
        if stream is None:
            return
        try:
            os.fsync(stream.fileno())
        except OSError:
            pass


def dmesg_interval_s(in_dive: bool) -> int:
    return DMESG_INTERVAL_DIVE_S if in_dive else DMESG_INTERVAL_S


def power_interval_s(in_dive: bool) -> int:
    return POWER_INTERVAL_DIVE_S if in_dive else POWER_INTERVAL_S


def _dive_in_progress() -> bool:
    """True while a dive JSON record is still marked active.

    Companion-side, not Lua STATE: Batt FS Test never left MISSION_START
    but still had an active dive file.  Stale-active after a blackout is
    useful — we keep the faster interval until the record is closed.
    """
    try:
        from .dive_records import find_latest_active_dive_record
        from .storage import DATA_ROOT

        return find_latest_active_dive_record(DATA_ROOT / "dives") is not None
    except Exception:
        return False


def _dmesg_line_matches(line: str) -> bool:
    lower = line.lower()
    return any(kw in lower for kw in DMESG_KEYWORDS)


def parse_power_host_output(text: str) -> dict[str, object]:
    """Parse the POWER_HOST_CMD blob into a compact dict for logging."""
    ext5v = _first_volt(text, "EXT5V")
    hdmi = _first_volt(text, "HDMI")
    vdd_core = _first_volt(text, "VDD_CORE")
    v3v3 = _first_volt(text, "3V3_SYS")
    throttled = None
    match = _THROTTLED_RE.search(text)
    if match:
        throttled = match.group(1).lower()
    hwmon: list[str] = []
    in_hwmon = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "HWMON":
            in_hwmon = True
            continue
        if in_hwmon and line:
            hwmon.append(line)
    return {
        "ext5v_v": ext5v,
        "hdmi_v": hdmi,
        "vdd_core_v": vdd_core,
        "v3v3_sys_v": v3v3,
        "throttled": throttled,
        "hwmon": hwmon,
    }


def _first_volt(text: str, prefix: str) -> float | None:
    # Lines look like: EXT5V=EXT5V_V volt(24)=5.01400000V
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith(prefix.upper() + "="):
            continue
        match = re.search(
            r"volt\(\d+\)=([0-9]+(?:\.[0-9]+)?)V", stripped, re.IGNORECASE
        )
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def format_power_sample(sample: dict[str, object]) -> str:
    def _v(key: str) -> str:
        val = sample.get(key)
        return f"{val:.3f}" if isinstance(val, float) else "?"

    hwmon = sample.get("hwmon") or []
    hwmon_s = ",".join(str(x).replace(" ", ":") for x in hwmon) if hwmon else "-"
    throttled = sample.get("throttled") or "?"
    return (
        f"EXT5V={_v('ext5v_v')} HDMI={_v('hdmi_v')} "
        f"VDD_CORE={_v('vdd_core_v')} 3V3_SYS={_v('v3v3_sys_v')} "
        f"throttled={throttled} hwmon={hwmon_s}"
    )


def power_sample_is_alarm(sample: dict[str, object]) -> bool:
    ext5v = sample.get("ext5v_v")
    if isinstance(ext5v, float) and ext5v < EXT5V_WARN_V:
        return True
    throttled = sample.get("throttled")
    if not isinstance(throttled, str):
        return False
    try:
        bits = int(throttled, 16)
    except ValueError:
        return False
    if bits in (0, THROTTLED_MAILBOX_ERROR):
        return False
    return True


def setup_persistent_logging(level: int = logging.DEBUG) -> Path:
    """Configure the root logger with a RotatingFileHandler on persistent storage.

    Returns the log directory path.  Safe to call multiple times — skips
    if a file handler on the same directory is already attached.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Re-assert on every call so it survives the framework reconfiguring
    # its loggers after startup.
    _quiet_noisy_loggers()

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
    file_handler.addFilter(_NoisyLoggerFilter())
    root.addHandler(file_handler)

    if root.level > level:
        root.setLevel(level)

    # Also ensure console output keeps working
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
        console.addFilter(_NoisyLoggerFilter())
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
        lines = [line for line in raw.splitlines() if _dmesg_line_matches(line)]
        return "\n".join(lines[-100:]) if lines else None
    except Exception:
        return None


async def _dmesg_loop() -> None:
    """Periodically capture relevant dmesg lines and write them to a separate log."""
    dmesg_log = LOG_DIR / "dmesg.log"
    dmesg_handler = _FsyncRotatingFileHandler(
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
    last_capture = 0.0

    while True:
        try:
            interval = dmesg_interval_s(_dive_in_progress())
            now = time.monotonic()
            if now - last_capture >= interval:
                last_capture = now
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
                        dmesg_logger.info(
                            "--- dmesg snapshot %s UTC (%d new, interval %ds) ---",
                            stamp, len(new_lines), interval,
                        )
                        for line in new_lines:
                            dmesg_logger.info(line)
                    if len(seen_lines) > 5000:
                        seen_lines.clear()
        except Exception as e:
            logger.debug("dmesg capture error: %s", e)
        await asyncio.sleep(DMESG_POLL_S)


async def _sample_power_once() -> None:
    """Ask the host for PMIC/hwmon rails; write power.log; warn on sag."""
    global _power_host_fail_logged
    from .hotspot_radio import _run_host_command

    ok, out = await _run_host_command(POWER_HOST_CMD, timeout=15.0)
    if not ok:
        if not _power_host_fail_logged:
            logger.warning("power sample via Commander failed: %s", out[:200])
            _power_host_fail_logged = True
        return
    _power_host_fail_logged = False
    sample = parse_power_host_output(out)
    line = format_power_sample(sample)
    power_logger = logging.getLogger("doris.power")
    power_logger.info(line)
    if power_sample_is_alarm(sample):
        logger.warning("Pi rail alarm: %s", line)


async def _power_loop() -> None:
    """Periodic Pi 5 5 V / throttle / hwmon samples with fsync'd power.log."""
    power_log = LOG_DIR / "power.log"
    power_handler = _FsyncRotatingFileHandler(
        str(power_log),
        maxBytes=MAX_BYTES,
        backupCount=2,
        encoding="utf-8",
    )
    power_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(message)s", datefmt=LOG_DATEFMT)
    )
    power_logger = logging.getLogger("doris.power")
    power_logger.addHandler(power_handler)
    power_logger.setLevel(logging.INFO)
    power_logger.propagate = False

    last_capture = 0.0
    while True:
        try:
            interval = power_interval_s(_dive_in_progress())
            now = time.monotonic()
            if now - last_capture >= interval:
                last_capture = now
                await _sample_power_once()
        except Exception as e:
            logger.debug("power sample error: %s", e)
        await asyncio.sleep(POWER_POLL_S)


def start_dmesg_capture() -> None:
    """Start background dmesg capture (idempotent)."""
    global _dmesg_task
    if _dmesg_task is not None and not _dmesg_task.done():
        return
    _dmesg_task = asyncio.get_event_loop().create_task(_dmesg_loop())
    logger.info(
        "dmesg capture started (idle %ds, dive %ds)",
        DMESG_INTERVAL_S, DMESG_INTERVAL_DIVE_S,
    )


def start_power_capture() -> None:
    """Start background Pi rail sampling (idempotent)."""
    global _power_task
    if _power_task is not None and not _power_task.done():
        return
    _power_task = asyncio.get_event_loop().create_task(_power_loop())
    logger.info(
        "power capture started (idle %ds, dive %ds) -> %s",
        POWER_INTERVAL_S, POWER_INTERVAL_DIVE_S, LOG_DIR / "power.log",
    )


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
