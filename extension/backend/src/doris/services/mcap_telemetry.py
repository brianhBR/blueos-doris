"""Telemetry extraction from recorder .mcap files for the dive CSV export.

The BlueOS recorder stores each MAVLink message as a JSON record on its own
topic, e.g. ``mavlink/1/1/NAMED_VALUE_FLOAT`` or ``mavlink/1/1/SCALED_PRESSURE``
with a ``{"header": {...}, "message": {...}}`` payload.  We decode the subset
of autopilot (system 1, component 1) messages that carry relevant system and
science data and merge those asynchronous streams into fixed UTC time buckets
so the export has one row per timestamp and one column per signal.

DORIS-specific telemetry arrives as NAMED_VALUE_FLOAT bursts published by
``backend/scripts/doris.lua`` (STATE, DEPTH, MAX_DPTH, MIN_TEMP, ...); standard
ArduSub messages provide position, attitude, pressure and power.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcap.reader import make_reader

# ── MCAP → per-dive file mapping (lazy import storage helpers to avoid cycles) ──


def map_dive_stem_to_largest_mcap(root: Path, windows: list[Any]) -> dict[str, Path]:
    """Pick the largest .mcap per dive window from recorder/ and USB volumes."""
    from .storage import RECORDER_DIR, _effective_created_at, _match_dive_window
    from .usb_storage import iter_media_files_on_usb, iter_media_scan_roots

    trees: list[tuple[str | None, Path]] = []
    rec = root / RECORDER_DIR
    if rec.is_dir():
        trees.append((None, rec))
    for mount_key, base in iter_media_scan_roots():
        if base.is_dir():
            trees.append((mount_key, base))

    best: dict[str, tuple[int, Path]] = {}
    for mount_key, tree in trees:
        if mount_key is None:
            mcap_iter = tree.rglob("*.mcap")
        else:
            mcap_iter = (
                p for p in iter_media_files_on_usb(mount_key, tree) if p.suffix.lower() == ".mcap"
            )
        for path in mcap_iter:
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            eff = _effective_created_at(path, st.st_mtime)
            eff_u = eff if eff.tzinfo else eff.replace(tzinfo=timezone.utc)
            wn = _match_dive_window(windows, eff_u)
            if wn is None:
                continue
            sz = st.st_size
            prev = best.get(wn.stem)
            if prev is None or sz > prev[0]:
                best[wn.stem] = (sz, path)
    return {stem: p for stem, (_, p) in best.items()}


# ── column model ─────────────────────────────────────────────────────────────

# DORIS NAMED_VALUE_FLOAT name -> CSV column (see update_telemetry in doris.lua).
# The descent/ascent rate floats are intentionally omitted: vertical motion is
# represented by the single signed vertical_velocity_mps column (VFR_HUD.climb).
NAMED_FLOAT_COLUMNS: dict[str, str] = {
    "STATE": "mission_state",
    "MSN_TIME": "mission_time_s",
    "BTM_TIME": "bottom_time_s",
    "DEPTH": "depth_m",
    "RELAY": "relay_active",
    "BATT_V": "battery_voltage_v",
}

# NAMED_VALUE_FLOATs that are deliberately excluded from the export:
#   * ArduSub manual-control pilot inputs (not relevant to a passive lander)
#   * DORIS floats superseded by other columns -- MAX_DPTH (max depth is in the
#     header only), DSC_RATE/ASC_RATE/ASC_VEL (replaced by vertical_velocity_mps)
#     and BATT_PCT (dropped).
# Everything else is captured dynamically (see _sensor_column).
NAMED_FLOAT_DENYLIST: frozenset[str] = frozenset(
    {
        # ArduSub pilot inputs
        "CamTilt",
        "CamPan",
        "TetherTrn",
        "Lights1",
        "Lights2",
        "PilotGain",
        "InputHold",
        "RollPitch",
        "RFTarget",
        # DORIS floats intentionally not exported as time-series columns
        "MAX_DPTH",
        "DSC_RATE",
        "ASC_RATE",
        "ASC_VEL",
        "BATT_PCT",
        # MIN_TEMP is the internal baro min; environmental temperature comes
        # from the external probe instead (see min_external_temperature_c).
        "MIN_TEMP",
    }
)

# Friendly column names for recognized sensor NAMED_VALUE_FLOATs (keyed by the
# uppercased mavlink name).  Any other non-denylisted named float is still
# captured dynamically under its own sanitized name, so new sensors added to
# the MAVLink stream (e.g. a CO2/oxygen probe) appear in the CSV automatically
# without a code change -- see _sensor_column().
SENSOR_FLOAT_ALIASES: dict[str, str] = {
    "COND": "conductivity",
    "CONDUCT": "conductivity",
    "SALINITY": "salinity",
    "CO2": "co2",
    "O2": "oxygen",
    "OXY": "oxygen",
    "OXYGEN": "oxygen",
    "DOXY": "oxygen",
}


def _sensor_column(name: str) -> str | None:
    """Map an unknown NAMED_VALUE_FLOAT name to a CSV column.

    Recognized sensors get a friendly alias; anything else is sanitized to a
    lowercase snake-ish identifier so future named floats are captured as-is.
    """
    alias = SENSOR_FLOAT_ALIASES.get(name.upper())
    if alias:
        return alias
    safe = re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")
    return safe or None

# Inclusive sanity bounds; out-of-range samples are dropped so a corrupt or
# sentinel value (e.g. 65535) never reaches the CSV.  Columns without an entry
# (string columns) are passed through untouched.
COLUMN_BOUNDS: dict[str, tuple[float, float]] = {
    "mission_state": (-1.0, 4.0),
    "mission_time_s": (0.0, 1.0e7),
    "bottom_time_s": (0.0, 1.0e7),
    "depth_m": (-10.0, 15_000.0),
    "vertical_velocity_mps": (-50.0, 50.0),
    "internal_pressure_hpa": (0.0, 20_000.0),
    "internal_temperature_c": (-100.0, 150.0),
    "external_pressure_hpa": (0.0, 20_000.0),
    "external_temperature_c": (-100.0, 150.0),
    "latitude": (-90.0, 90.0),
    "longitude": (-180.0, 180.0),
    "heading_trueN_degrees": (0.0, 360.0),
    "ground_speed_mps": (-50.0, 50.0),
    "gps_satellites": (0.0, 255.0),
    "roll_deg": (-360.0, 360.0),
    "pitch_deg": (-360.0, 360.0),
    "yaw_deg": (-360.0, 360.0),
    "relay_active": (0.0, 1.0),
    "light_level_percent": (0.0, 100.0),
    "battery_voltage_v": (0.0, 100.0),
    "battery_current_a": (-1_000.0, 1_000.0),
}

# Ordered numeric/string signal columns stored per time bucket.
SIGNAL_COLUMNS: list[str] = [
    "mission_state",
    "mission_time_s",
    "bottom_time_s",
    "depth_m",
    "vertical_velocity_mps",
    "internal_pressure_hpa",
    "internal_temperature_c",
    "external_pressure_hpa",
    "external_temperature_c",
    "latitude",
    "longitude",
    "heading_trueN_degrees",
    "ground_speed_mps",
    "gps_fix_type",
    "gps_satellites",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "relay_active",
    "light_level_percent",
    "battery_voltage_v",
    "battery_current_a",
]

# Columns produced by the fixed extractors; anything else a message yields is a
# dynamically-discovered sensor column (appended after these in the CSV).
_KNOWN_COLUMNS: frozenset[str] = frozenset(SIGNAL_COLUMNS)

# CSV header labels with units fully spelled out.  Internal logic keeps the
# short keys above; this map is applied only when writing the time-series
# header row.  Columns not listed (e.g. dynamically-discovered sensors) fall
# back to their raw name.
COLUMN_DISPLAY_NAMES: dict[str, str] = {
    "mission_time_s": "mission_time_seconds",
    "bottom_time_s": "bottom_time_seconds",
    "depth_m": "depth_meters",
    "vertical_velocity_mps": "vertical_velocity_meters_per_second",
    "internal_pressure_hpa": "internal_pressure_hectopascals",
    "internal_temperature_c": "internal_temperature_celsius",
    "external_pressure_hpa": "external_pressure_hectopascals",
    "external_temperature_c": "external_temperature_celsius",
    "latitude": "latitude_degrees",
    "longitude": "longitude_degrees",
    "ground_speed_mps": "ground_speed_meters_per_second",
    "roll_deg": "roll_degrees",
    "pitch_deg": "pitch_degrees",
    "yaw_deg": "yaw_degrees",
    "battery_voltage_v": "battery_voltage_volts",
    "battery_current_a": "battery_current_amperes",
}

# Decimal places per column for CSV output (trailing zeros are stripped).
# Chosen to match each sensor's useful resolution rather than dumping the raw
# float's 6 significant figures.  Position keeps 6 dp (~0.1 m); columns not
# listed (e.g. dynamically-discovered sensors) use _DEFAULT_DECIMALS.
_DEFAULT_DECIMALS = 3
COLUMN_DECIMALS: dict[str, int] = {
    "mission_time_s": 0,
    "bottom_time_s": 0,
    "depth_m": 2,
    "vertical_velocity_mps": 2,
    "internal_pressure_hpa": 1,
    "internal_temperature_c": 2,
    "external_pressure_hpa": 1,
    "external_temperature_c": 2,
    "latitude": 6,
    "longitude": 6,
    "heading_trueN_degrees": 1,
    "ground_speed_mps": 2,
    "gps_satellites": 0,
    "roll_deg": 1,
    "pitch_deg": 1,
    "yaw_deg": 1,
    "relay_active": 0,
    "light_level_percent": 1,
    "battery_voltage_v": 2,
    "battery_current_a": 2,
}

# DORIS_STATE numeric -> human label (mirrors STATE_* constants in doris.lua).
STATE_LABELS: dict[int, str] = {
    -1: "CONFIG",
    0: "MISSION_START",
    1: "DESCENT",
    2: "ON_BOTTOM",
    3: "ASCENT",
    4: "RECOVERY",
}

_RAD_TO_DEG = 180.0 / math.pi
# Bucket asynchronous streams into 1-second rows keyed by recorder UTC time.
BUCKET_NS = 1_000_000_000


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# Lights are commanded over the ArduSub default lights PWM range (1100-1900 us,
# off..full); express the level as 0-100% of that span (values are clamped).
_LIGHT_PWM_MIN = 1100.0
_LIGHT_PWM_MAX = 1900.0


def _pwm_to_percent(us: float) -> float:
    pct = (us - _LIGHT_PWM_MIN) / (_LIGHT_PWM_MAX - _LIGHT_PWM_MIN) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def _extract_message(msg_type: str, m: dict[str, Any]) -> dict[str, float | str]:
    """Map one decoded MAVLink message to {column: value} signal pairs."""
    out: dict[str, float | str] = {}
    if msg_type == "NAMED_VALUE_FLOAT":
        name = str(m.get("name", "")).replace("\x00", "").strip()
        v = _f(m.get("value"))
        if name and v is not None:
            if name in NAMED_FLOAT_COLUMNS:
                out[NAMED_FLOAT_COLUMNS[name]] = v
            elif name not in NAMED_FLOAT_DENYLIST:
                col = _sensor_column(name)
                if col is not None:
                    out[col] = v
    elif msg_type == "SCALED_PRESSURE":
        # Pressure 1: internal barometer inside the sealed housing.
        pa = _f(m.get("press_abs"))
        if pa is not None:
            out["internal_pressure_hpa"] = pa
        t = _f(m.get("temperature"))
        if t is not None:
            out["internal_temperature_c"] = t / 100.0
    elif msg_type == "SCALED_PRESSURE2":
        # Pressure 2: external pressure sensor.  Its on-board temperature
        # duplicates the dedicated external probe below, so only pressure is
        # kept here.
        pa = _f(m.get("press_abs"))
        if pa is not None:
            out["external_pressure_hpa"] = pa
    elif msg_type == "SCALED_PRESSURE3":
        # Pressure 3: dedicated external temperature probe (press_abs unused).
        t = _f(m.get("temperature"))
        if t is not None:
            out["external_temperature_c"] = t / 100.0
    elif msg_type == "GLOBAL_POSITION_INT":
        lat = _f(m.get("lat"))
        lon = _f(m.get("lon"))
        if lat is not None:
            out["latitude"] = lat / 1.0e7
        if lon is not None:
            out["longitude"] = lon / 1.0e7
        hdg = _f(m.get("hdg"))
        if hdg is not None and hdg != 65535:
            # EKF yaw (GLOBAL_POSITION_INT.hdg).  ArduPilot applies the
            # magnetic declination (COMPASS_DEC, auto-set via COMPASS_AUTODEC)
            # so this is referenced to TRUE north, not magnetic.
            out["heading_trueN_degrees"] = hdg / 100.0
    elif msg_type == "GPS_RAW_INT":
        sats = _f(m.get("satellites_visible"))
        if sats is not None and sats != 255:
            out["gps_satellites"] = sats
        # GPS course-over-ground (cog) is intentionally not exported: it is
        # only meaningful for horizontal surface travel and reads 0 for a
        # vertical profiler.  Heading comes from the compass-aided
        # GLOBAL_POSITION_INT.hdg -> mag_heading_deg instead.
        fix = m.get("fix_type")
        if isinstance(fix, dict):
            fix = fix.get("type")
        if isinstance(fix, str):
            out["gps_fix_type"] = fix.replace("GPS_FIX_TYPE_", "")
    elif msg_type == "VFR_HUD":
        climb = _f(m.get("climb"))
        if climb is not None:
            # Single signed vertical-velocity signal: positive = up (ascending),
            # negative = down (descending).
            out["vertical_velocity_mps"] = climb
        gs = _f(m.get("groundspeed"))
        if gs is not None:
            out["ground_speed_mps"] = gs
    elif msg_type == "ATTITUDE":
        for key, col in (("roll", "roll_deg"), ("pitch", "pitch_deg"), ("yaw", "yaw_deg")):
            rad = _f(m.get(key))
            if rad is not None:
                out[col] = rad * _RAD_TO_DEG
    elif msg_type == "BATTERY_STATUS":
        volts = m.get("voltages")
        if isinstance(volts, list) and volts:
            mv = _f(volts[0])
            if mv is not None and mv != 65535:
                out["battery_voltage_v"] = mv / 1000.0
        cur = _f(m.get("current_battery"))
        if cur is not None and cur >= 0:
            out["battery_current_a"] = cur / 100.0
    elif msg_type == "RC_CHANNELS":
        # ArduSub Lights 1 is on RC input channel 9.  Map the PWM (us) to a
        # 0-100% light level.  0 / 65535 mean "channel not available".
        raw = _f(m.get("chan9_raw"))
        if raw is not None and raw > 0 and raw != 65535:
            out["light_level_percent"] = _pwm_to_percent(raw)
    return out


# Message types we decode (component 1, the autopilot).  NAMED_VALUE_FLOAT is
# matched specially since DORIS publishes its science/system signals there.
_DECODED_TYPES = frozenset(
    {
        "NAMED_VALUE_FLOAT",
        "SCALED_PRESSURE",
        "SCALED_PRESSURE2",
        "SCALED_PRESSURE3",
        "GLOBAL_POSITION_INT",
        "GPS_RAW_INT",
        "VFR_HUD",
        "ATTITUDE",
        "BATTERY_STATUS",
        "RC_CHANNELS",
    }
)


@dataclass
class TelemetryFrame:
    """One reassembled time bucket: a UTC timestamp + the latest signals in it."""

    log_time_ns: int
    values: dict[str, float | str] = field(default_factory=dict)


@dataclass
class McapSummary:
    """Aggregates + per-bucket frames used for dive history and CSV export."""

    max_depth_m: float | None = None
    min_external_temperature_c: float | None = None
    min_battery_voltage_v: float | None = None
    max_satellites: int | None = None
    frames: list[TelemetryFrame] = field(default_factory=list)
    last_lat: float | None = None
    last_lon: float | None = None
    messages_seen: int = 0
    # Magnetic declination the autopilot applied to derive true heading
    # (COMPASS_DEC, converted to degrees) and whether it was auto-set
    # (COMPASS_AUTODEC).  Read from the PARAM_VALUE stream.
    compass_declination_deg: float | None = None
    compass_autodec: int | None = None
    # Dynamically-discovered sensor columns (e.g. conductivity, co2, oxygen)
    # that aren't part of the fixed column set, sorted for stable output.
    extra_columns: list[str] = field(default_factory=list)


# Cap parsed (decoded) messages so a pathologically long recording can't stall
# the request path; 2M decoded messages already covers a multi-hour dive.
_MAX_DECODED = 2_000_000


def summarize_mcap(path: Path) -> McapSummary:
    """Parse one .mcap file into time-bucketed telemetry frames.

    Tolerant of missing or unexpected data: anything that fails to decode is
    skipped and an empty summary is returned on a hard read error.
    """
    summary = McapSummary()
    # bucket_ns -> {column: (value, log_time_ns)} keeping the latest sample.
    buckets: dict[int, dict[str, tuple[float | str, int]]] = {}
    dynamic_cols: set[str] = set()
    decoded = 0

    try:
        with path.open("rb") as f:
            reader = make_reader(f)
            for _schema, channel, message in reader.iter_messages():
                summary.messages_seen += 1
                topic = channel.topic
                # Autopilot messages only (system 1, component 1).
                if not topic.startswith("mavlink/1/1/"):
                    continue
                msg_type = topic.rsplit("/", 1)[-1]
                if msg_type == "PARAM_VALUE":
                    try:
                        pm = json.loads(message.data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    pmsg = pm.get("message") if isinstance(pm, dict) else None
                    if isinstance(pmsg, dict):
                        _extract_param(summary, pmsg)
                    continue
                if msg_type not in _DECODED_TYPES:
                    continue
                decoded += 1
                if decoded > _MAX_DECODED:
                    break
                try:
                    payload = json.loads(message.data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                m = payload.get("message") if isinstance(payload, dict) else None
                if not isinstance(m, dict):
                    continue
                signals = _extract_message(msg_type, m)
                if not signals:
                    continue

                lt = int(message.log_time)
                bkey = lt // BUCKET_NS
                slot = buckets.setdefault(bkey, {})
                for col, val in signals.items():
                    bounds = COLUMN_BOUNDS.get(col)
                    if bounds is not None and (
                        not isinstance(val, (int, float)) or not (bounds[0] <= val <= bounds[1])
                    ):
                        continue
                    prev = slot.get(col)
                    if prev is None or lt >= prev[1]:
                        slot[col] = (val, lt)
                    if col not in _KNOWN_COLUMNS:
                        dynamic_cols.add(col)
                    _update_aggregates(summary, col, val)
    except Exception:
        return McapSummary()

    summary.extra_columns = sorted(dynamic_cols)
    for bkey in sorted(buckets):
        slot = buckets[bkey]
        frame = TelemetryFrame(log_time_ns=bkey * BUCKET_NS)
        for col, (val, _ts) in slot.items():
            frame.values[col] = val
        summary.frames.append(frame)
    return summary


def _update_aggregates(summary: McapSummary, col: str, val: float | str) -> None:
    if not isinstance(val, (int, float)):
        return
    if col == "depth_m":
        summary.max_depth_m = val if summary.max_depth_m is None else max(summary.max_depth_m, val)
    elif col == "external_temperature_c":
        summary.min_external_temperature_c = (
            val
            if summary.min_external_temperature_c is None
            else min(summary.min_external_temperature_c, val)
        )
    elif col == "battery_voltage_v" and val > 1.0:
        summary.min_battery_voltage_v = (
            val if summary.min_battery_voltage_v is None else min(summary.min_battery_voltage_v, val)
        )
    elif col == "gps_satellites":
        iv = int(val)
        summary.max_satellites = iv if summary.max_satellites is None else max(summary.max_satellites, iv)
    elif col == "latitude":
        summary.last_lat = val
    elif col == "longitude":
        summary.last_lon = val


def _extract_param(summary: McapSummary, m: dict[str, Any]) -> None:
    """Capture compass declination params from a PARAM_VALUE message.

    ArduPilot derives ``heading_trueN_degrees`` (GLOBAL_POSITION_INT.hdg, the
    EKF yaw) referenced to true north by applying ``COMPASS_DEC``.  We surface
    the declination it used (and whether it was auto-set) in the CSV header.
    """
    pid = str(m.get("param_id", "")).replace("\x00", "").strip()
    if pid == "COMPASS_DEC":
        rad = _f(m.get("param_value"))
        if rad is not None:
            summary.compass_declination_deg = round(math.degrees(rad), 3)
    elif pid == "COMPASS_AUTODEC":
        v = _f(m.get("param_value"))
        if v is not None:
            summary.compass_autodec = int(v)


# ── CSV export ─────────────────────────────────────────────────────────────


def _ns_to_utc_iso(ns: int) -> str:
    # No timezone offset: every timestamp in this file is UTC (the column is
    # named timestamp_utc), so a trailing "+00:00" is just noise.
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _strip_utc_offset(value: Any) -> str:
    """Drop a trailing UTC offset / 'Z' from an ISO timestamp for display.

    Header timestamps are already labeled ``*_utc``; the offset is redundant.
    """
    s = str(value or "")
    if s.endswith("+00:00"):
        return s[:-6]
    if s.endswith("Z"):
        return s[:-1]
    return s


def _to_float(value: Any) -> float | None:
    return _f(value)


def _cell(value: float | str | None, decimals: int | None = None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if decimals is None:
        return f"{value:g}"
    r = round(value, decimals)
    if r == 0:  # collapse -0.0 -> 0
        r = 0.0
    # Fixed-point (so we don't hit %g's 6-significant-figure cap, which would
    # truncate latitude/longitude), then drop trailing zeros for a clean cell.
    s = f"{r:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# gps_fix_type values (GPS_FIX_TYPE_* prefix already stripped) that mean the
# receiver has no horizontal position lock, so any latitude/longitude in that
# bucket is stale and must not be reported as a real coordinate.
_NO_FIX_LABELS: frozenset[str] = frozenset({"NO_GPS", "NO_FIX"})


def _has_no_gps_fix(frame_values: dict[str, float | str]) -> bool:
    fix = frame_values.get("gps_fix_type")
    return isinstance(fix, str) and fix in _NO_FIX_LABELS


def _duration_label(started: str | None, ended: str | None) -> str:
    """H:MM:SS between two ISO-8601 timestamps, or '' if unavailable."""
    if not started or not ended:
        return ""
    try:
        a = datetime.fromisoformat(str(started))
        b = datetime.fromisoformat(str(ended))
    except ValueError:
        return ""
    secs = int((b - a).total_seconds())
    if secs < 0:
        return ""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


_FNAME_BAD = re.compile(r"[^\w\s-]")
_FNAME_SPACE = re.compile(r"[\s-]+")


def _filename_slug(value: str) -> str:
    """Filename-safe lowercase slug of a dive name (empty if nothing usable)."""
    s = _FNAME_BAD.sub("", value.strip().lower())
    s = _FNAME_SPACE.sub("_", s)
    return s.strip("_")


def dive_csv_filename(dive_record: dict[str, Any], dive_id: str) -> str:
    """Download/USB name: ``<YYYYMMDD_HHMMSS>_<dive_name>_dive_data.csv``.

    Leads with the dive's start timestamp (UTC) so exports sort
    chronologically, followed by the slugified dive name.  The timestamp
    falls back to the dive id stem when the start time is missing/unparseable,
    and the dive-name segment is omitted when there is no name.
    """
    started = dive_record.get("started_at")
    stamp = ""
    if started:
        try:
            dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stamp = dt.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parts = [stamp or dive_id]
    name = _filename_slug(str(dive_record.get("dive_name") or ""))
    if name:
        parts.append(name)
    return "_".join(parts) + "_dive_data.csv"


def build_dive_csv(
    dive_record: dict[str, Any],
    summary: McapSummary,
    mcap_rel: str | None,
) -> str:
    """Build the dive CSV: a dive-data header section then per-second telemetry.

    Section 1 (DIVE DATA): key/value metadata from the dive record plus
    aggregates computed from the linked .mcap.  Section 2 (TIME SERIES): one row
    per 1-second UTC bucket covering system data (state, relay, battery,
    attitude, rates) and science data (depth, temperature, pressure, GPS).
    """
    s_lat = _to_float(dive_record.get("latitude"))
    s_lon = _to_float(dive_record.get("longitude"))
    end_lat = summary.last_lat
    end_lon = summary.last_lon
    started = dive_record.get("started_at")
    ended = dive_record.get("ended_at")
    dive_name = str(dive_record.get("dive_name") or "").strip()

    buf = io.StringIO()
    # Pin the row terminator to a single "\n"; csv's default "\r\n" turns into
    # "\r\r\n" once written/served in text mode, which shows up as a blank row
    # between every line in Excel.
    w = csv.writer(buf, lineterminator="\n")

    # ── Section 1: dive data ──
    w.writerow(["# DIVE DATA"])
    w.writerow(["doris_dive_export", "v2"])
    w.writerow(["dive_name", dive_name])
    w.writerow(["user_name", str(dive_record.get("username") or "")])
    w.writerow(["configuration", str(dive_record.get("configuration") or "")])
    w.writerow(["status", str(dive_record.get("status") or "")])
    w.writerow(["profile_id", str(dive_record.get("profile_id") or "")])
    w.writerow(["started_at_utc", _strip_utc_offset(started)])
    w.writerow(["ended_at_utc", _strip_utc_offset(ended)])
    w.writerow(["duration", _duration_label(started, ended)])
    w.writerow(["start_latitude", _cell(s_lat, 6)])
    w.writerow(["start_longitude", _cell(s_lon, 6)])
    w.writerow(["end_latitude", _cell(end_lat, 6)])
    w.writerow(["end_longitude", _cell(end_lon, 6)])
    w.writerow(["max_depth_from_log_meters", _cell(summary.max_depth_m, 2)])
    w.writerow(["min_external_temperature_celsius", _cell(summary.min_external_temperature_c, 2)])
    w.writerow(["min_battery_voltage_volts", _cell(summary.min_battery_voltage_v, 2)])
    w.writerow(["max_gps_satellites", _cell(summary.max_satellites, 0)])
    w.writerow(["compass_declination_degrees", _cell(summary.compass_declination_deg, 3)])
    w.writerow(["compass_autodec", _cell(summary.compass_autodec)])
    w.writerow(["mcap_file", mcap_rel or ""])
    w.writerow(["telemetry_rows", str(len(summary.frames))])
    w.writerow([])

    # ── Section 2: per-second telemetry ──
    # Fixed columns first, then any dynamically-discovered sensor columns
    # (conductivity, co2, oxygen, ...) appended in stable sorted order.
    data_columns = [c for c in SIGNAL_COLUMNS if c != "mission_state"]
    data_columns += list(summary.extra_columns)
    display_columns = [COLUMN_DISPLAY_NAMES.get(c, c) for c in data_columns]
    w.writerow(["# TIME SERIES"])
    w.writerow(["timestamp_utc", "mission_state_label", *display_columns])

    for fr in summary.frames:
        state_val = fr.values.get("mission_state")
        label = ""
        if isinstance(state_val, (int, float)):
            label = STATE_LABELS.get(int(round(state_val)), "")
        row: list[str] = [_ns_to_utc_iso(fr.log_time_ns), label]
        # When the GPS has no fix, position is stale -> report "na" rather
        # than a misleading last-known coordinate.
        no_fix = _has_no_gps_fix(fr.values)
        for col in data_columns:
            if no_fix and col in ("latitude", "longitude"):
                row.append("na")
            else:
                row.append(_cell(fr.values.get(col), COLUMN_DECIMALS.get(col, _DEFAULT_DECIMALS)))
        w.writerow(row)
    return buf.getvalue()
