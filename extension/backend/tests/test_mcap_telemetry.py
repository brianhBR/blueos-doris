"""Tests for the dive CSV export (services/mcap_telemetry.py).

Builds a tiny JSON-encoded .mcap mirroring the BlueOS recorder layout (one
topic per MAVLink message type, ``{"header": ..., "message": ...}`` payloads)
and checks that telemetry is decoded, bucketed into per-second rows, and
rendered into the dive CSV.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from mcap.writer import Writer

from doris.services.mcap_telemetry import build_dive_csv, summarize_mcap

BASE = datetime(2026, 5, 20, 17, 49, 7, tzinfo=timezone.utc)
BASE_NS = int(BASE.timestamp() * 1_000_000_000)


def _named_float(name: str, value: float) -> tuple[str, dict]:
    return "NAMED_VALUE_FLOAT", {
        "type": "NAMED_VALUE_FLOAT",
        "time_boot_ms": 1000,
        "value": value,
        "name": name,
    }


def _cycle(state: int, depth: float, temp: float, volt: float, climb: float) -> list[tuple[str, dict]]:
    """A DORIS telemetry burst plus a few standard autopilot messages."""
    return [
        _named_float("STATE", float(state)),
        _named_float("DEPTH", depth),
        # Internal baro MIN_TEMP is denylisted (troubleshooting only); the
        # external probe (SCALED_PRESSURE3 below) drives environmental temp.
        _named_float("MIN_TEMP", 99.0),
        _named_float("BATT_V", volt),
        _named_float("RELAY", 0.0),
        # Science sensors: recognized aliases + a future/unknown name + a
        # denylisted ArduSub pilot input that must be ignored.
        _named_float("COND", 4.21),
        _named_float("CO2", 410.0),
        _named_float("TURB", 7.5),
        _named_float("CamTilt", 1.0),
        ("SCALED_PRESSURE", {"type": "SCALED_PRESSURE", "press_abs": 1011.0, "temperature": 4822}),
        ("SCALED_PRESSURE2", {"type": "SCALED_PRESSURE2", "press_abs": 1164.0, "temperature": 2475}),
        ("SCALED_PRESSURE3", {"type": "SCALED_PRESSURE3", "press_abs": 0, "temperature": int(temp * 100)}),
        ("VFR_HUD", {"type": "VFR_HUD", "climb": climb, "groundspeed": 0.1}),
        (
            "GLOBAL_POSITION_INT",
            {"type": "GLOBAL_POSITION_INT", "lat": 337261140, "lon": -1182754125, "alt": 3350, "hdg": 33034},
        ),
        (
            "GPS_RAW_INT",
            {"type": "GPS_RAW_INT", "satellites_visible": 13, "cog": 12000, "fix_type": {"type": "GPS_FIX_TYPE_3D_FIX"}},
        ),
        ("ATTITUDE", {"type": "ATTITUDE", "roll": 0.0, "pitch": 0.0, "yaw": 1.5707963}),
        # Sentinel pack voltage must be rejected; DORIS BATT_V wins instead.
        ("BATTERY_STATUS", {"type": "BATTERY_STATUS", "voltages": [65535], "current_battery": 72, "battery_remaining": 91}),
    ]


def _write_mcap(path: Path, cycles: list[list[tuple[str, dict]]]) -> None:
    channels: dict[str, int] = {}
    with path.open("wb") as f:
        writer = Writer(f)
        writer.start()

        def channel_for(msg_type: str) -> int:
            topic = f"mavlink/1/1/{msg_type}"
            if topic not in channels:
                sid = writer.register_schema(
                    name=f"mavlink.1.1.{msg_type}", encoding="jsonschema", data=b"{}"
                )
                channels[topic] = writer.register_channel(
                    topic=topic, message_encoding="json", schema_id=sid
                )
            return channels[topic]

        for i, cycle in enumerate(cycles):
            base = BASE_NS + i * 1_000_000_000  # one bucket (1 s) apart
            for j, (msg_type, message) in enumerate(cycle):
                ts = base + j  # within-burst offset (same 1 s bucket)
                payload = {"header": {"system_id": 1, "component_id": 1}, "message": message}
                writer.add_message(
                    channel_id=channel_for(msg_type),
                    log_time=ts,
                    data=json.dumps(payload).encode("utf-8"),
                    publish_time=ts,
                )
        writer.finish()


def test_summarize_buckets_and_aggregates(tmp_path: Path) -> None:
    mcap = tmp_path / "dive.mcap"
    _write_mcap(
        mcap,
        [
            _cycle(state=1, depth=10.0, temp=18.0, volt=15.9, climb=-0.4),
            _cycle(state=2, depth=25.0, temp=16.5, volt=15.4, climb=0.0),
        ],
    )
    s = summarize_mcap(mcap)

    assert len(s.frames) == 2
    assert s.max_depth_m == 25.0
    assert s.min_external_temperature_c == 16.5  # from external probe (P3), not internal MIN_TEMP
    assert s.min_battery_voltage_v == 15.4  # from DORIS BATT_V, not 65535 sentinel
    assert s.max_satellites == 13
    assert s.last_lat is not None and abs(s.last_lat - 33.726114) < 1e-4
    assert s.last_lon is not None and abs(s.last_lon - (-118.2754125)) < 1e-4

    f0 = s.frames[0]
    assert f0.values["mission_state"] == 1.0
    assert f0.values["depth_m"] == 10.0
    assert f0.values["vertical_velocity_mps"] == -0.4
    assert f0.values["gps_fix_type"] == "3D_FIX"
    assert f0.values["internal_pressure_hpa"] == 1011.0
    assert f0.values["internal_temperature_c"] == 48.22
    assert f0.values["external_pressure_hpa"] == 1164.0
    # External temperature comes from the dedicated probe (P3), not the P2
    # pressure sensor's duplicate reading.
    assert f0.values["external_temperature_c"] == 18.0
    # GLOBAL_POSITION_INT.hdg is the EKF yaw, declination-corrected to true
    # north by ArduPilot -> reported as heading_trueN_degrees.
    assert f0.values["heading_trueN_degrees"] == 330.34
    # GPS course-over-ground is not exported (meaningless for a vertical
    # profiler); compass heading covers it.
    assert "gps_heading_deg" not in f0.values
    assert abs(f0.values["yaw_deg"] - 90.0) < 0.01
    assert f0.values["battery_voltage_v"] == 15.9
    assert f0.values["battery_current_a"] == 0.72
    # MAX_DPTH is no longer parsed (max depth lives only in the CSV header).
    assert "max_depth_m" not in f0.values

    # Dynamic science sensors: friendly aliases + raw unknown name.
    assert f0.values["conductivity"] == 4.21
    assert f0.values["co2"] == 410.0
    assert f0.values["turb"] == 7.5
    assert s.extra_columns == ["co2", "conductivity", "turb"]
    # Denylisted ArduSub pilot float must not leak into the export.
    assert "camtilt" not in f0.values


def test_build_dive_csv_structure(tmp_path: Path) -> None:
    mcap = tmp_path / "dive.mcap"
    _write_mcap(
        mcap,
        [
            _cycle(state=1, depth=10.0, temp=18.0, volt=15.9, climb=-0.4),
            _cycle(state=2, depth=25.0, temp=16.5, volt=15.4, climb=0.0),
        ],
    )
    s = summarize_mcap(mcap)
    record = {
        "dive_name": "Unit Test Dive",
        "username": "tester",
        "configuration": "cfg",
        "status": "completed",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE.replace(minute=59)).isoformat(),
        "latitude": 33.7,
        "longitude": -118.2,
    }
    text = build_dive_csv(record, s, "recorder/dive.mcap")

    assert "# DIVE DATA" in text
    assert "doris_dive_export,v2" in text
    assert "dive_name,Unit Test Dive" in text
    assert "user_name,tester" in text
    assert "operator," not in text
    assert "estimated_depth" not in text
    assert "max_depth_from_log_meters,25" in text
    assert "# TIME SERIES" in text

    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp_utc")
    header = rows[header_idx]
    # Numeric mission_state column removed; readable label retained.
    assert header[:2] == ["timestamp_utc", "mission_state_label"]
    assert "mission_state" not in header
    # Units fully spelled out in the column headers.
    assert "depth_meters" in header and "battery_voltage_volts" in header and "gps_fix_type" in header
    assert "vertical_velocity_meters_per_second" in header
    for col in (
        "internal_temperature_celsius",
        "external_temperature_celsius",
        "internal_pressure_hectopascals",
        "external_pressure_hectopascals",
        "latitude_degrees",
        "longitude_degrees",
    ):
        assert col in header
    # Old abbreviated names must not linger.
    for gone in ("depth_m", "battery_voltage_v", "internal_temperature_c", "latitude", "longitude"):
        assert gone not in header
    # Consolidated: no duplicate external temperature / brand-named columns.
    for gone in ("water_temperature_c", "bar100_temperature_c", "celsius_temperature_c", "baro_temperature_c"):
        assert gone not in header
    # Dynamic sensor columns appended after the fixed columns.
    for sensor_col in ("conductivity", "co2", "turb"):
        assert sensor_col in header
    assert "camtilt" not in header and "CamTilt" not in header
    # Dropped columns must not appear in the time-series header.
    for dropped in (
        "max_depth_m",
        "min_temperature_c",
        "descent_rate_mps",
        "ascent_rate_mps",
        "ascent_velocity_mps",
        "climb_rate_mps",
        "gps_heading_deg",
    ):
        assert dropped not in header
    # True-north heading remains the single heading column.
    assert "heading_trueN_degrees" in header
    assert "mag_heading_deg" not in header

    data_rows = [r for r in rows[header_idx + 1 :] if r]
    assert len(data_rows) == 2
    first = dict(zip(header, data_rows[0], strict=False))
    assert first["mission_state_label"] == "DESCENT"
    assert first["depth_meters"] == "10"
    assert first["vertical_velocity_meters_per_second"] == "-0.4"
    assert first["gps_fix_type"] == "3D_FIX"
    # Timestamps carry no UTC offset (column is already named *_utc).
    assert data_rows[0][0] == "2026-05-20T17:49:07"
    assert "+00:00" not in text
    assert "started_at_utc,2026-05-20T17:49:07" in text


def test_no_gps_fix_writes_na_for_lat_lon(tmp_path: Path) -> None:
    """A bucket with NO_FIX must report latitude/longitude as 'na', while a
    3D-fix bucket keeps the real coordinates."""
    mcap = tmp_path / "dive.mcap"
    fixed = [
        _named_float("DEPTH", 1.0),
        (
            "GLOBAL_POSITION_INT",
            {"type": "GLOBAL_POSITION_INT", "lat": 337261140, "lon": -1182754125, "hdg": 1000},
        ),
        (
            "GPS_RAW_INT",
            {"type": "GPS_RAW_INT", "satellites_visible": 12, "fix_type": {"type": "GPS_FIX_TYPE_3D_FIX"}},
        ),
    ]
    no_fix = [
        _named_float("DEPTH", 80.0),
        # Stale position still streamed underwater, but the fix is gone.
        (
            "GLOBAL_POSITION_INT",
            {"type": "GLOBAL_POSITION_INT", "lat": 337261140, "lon": -1182754125, "hdg": 1000},
        ),
        (
            "GPS_RAW_INT",
            {"type": "GPS_RAW_INT", "satellites_visible": 0, "fix_type": {"type": "GPS_FIX_TYPE_NO_FIX"}},
        ),
    ]
    _write_mcap(mcap, [fixed, no_fix])
    s = summarize_mcap(mcap)
    text = build_dive_csv({"dive_name": "Fix Test"}, s, None)

    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp_utc")
    header = rows[header_idx]
    data = [dict(zip(header, r, strict=False)) for r in rows[header_idx + 1 :] if r]
    assert len(data) == 2
    # First bucket: 3D fix -> real coordinates.
    assert data[0]["gps_fix_type"] == "3D_FIX"
    assert abs(float(data[0]["latitude_degrees"]) - 33.726114) < 1e-4
    # Second bucket: no fix -> na for both lat and lon.
    assert data[1]["gps_fix_type"] == "NO_FIX"
    assert data[1]["latitude_degrees"] == "na"
    assert data[1]["longitude_degrees"] == "na"


def test_compass_declination_parsed_into_header(tmp_path: Path) -> None:
    """COMPASS_DEC / COMPASS_AUTODEC from the PARAM_VALUE stream surface in
    the dive-data header (declination converted from radians to degrees)."""
    mcap = tmp_path / "dive.mcap"
    cycle = [
        _named_float("DEPTH", 5.0),
        ("PARAM_VALUE", {"type": "PARAM_VALUE", "param_id": "COMPASS_DEC", "param_value": 0.19978905}),
        ("PARAM_VALUE", {"type": "PARAM_VALUE", "param_id": "COMPASS_AUTODEC", "param_value": 1.0}),
        ("PARAM_VALUE", {"type": "PARAM_VALUE", "param_id": "SOME_OTHER", "param_value": 42.0}),
    ]
    _write_mcap(mcap, [cycle])
    s = summarize_mcap(mcap)

    assert s.compass_declination_deg is not None
    assert abs(s.compass_declination_deg - 11.447) < 0.01
    assert s.compass_autodec == 1

    text = build_dive_csv({"dive_name": "Declination"}, s, None)
    assert "compass_declination_degrees,11.447" in text
    assert "compass_autodec,1" in text


def test_light_level_percent_mapping(tmp_path: Path) -> None:
    """RC channel 9 PWM (us) maps to a 0-100% light level over 1100-1900 us
    (clamped); an unavailable channel (0) yields no value."""
    mcap = tmp_path / "dive.mcap"
    cycles = [
        [_named_float("DEPTH", 1.0), ("RC_CHANNELS", {"type": "RC_CHANNELS", "chan9_raw": 1100})],
        [_named_float("DEPTH", 2.0), ("RC_CHANNELS", {"type": "RC_CHANNELS", "chan9_raw": 1500})],
        [_named_float("DEPTH", 3.0), ("RC_CHANNELS", {"type": "RC_CHANNELS", "chan9_raw": 1900})],
        [_named_float("DEPTH", 4.0), ("RC_CHANNELS", {"type": "RC_CHANNELS", "chan9_raw": 2100})],
        [_named_float("DEPTH", 5.0), ("RC_CHANNELS", {"type": "RC_CHANNELS", "chan9_raw": 0})],
    ]
    _write_mcap(mcap, cycles)
    s = summarize_mcap(mcap)

    assert s.frames[0].values["light_level_percent"] == 0.0
    assert s.frames[1].values["light_level_percent"] == 50.0
    assert s.frames[2].values["light_level_percent"] == 100.0
    # Above the operational max clamps to 100.
    assert s.frames[3].values["light_level_percent"] == 100.0
    assert "light_level_percent" not in s.frames[4].values

    text = build_dive_csv({"dive_name": "Lights"}, s, None)
    rows = list(csv.reader(io.StringIO(text)))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp_utc")
    header = rows[hi]
    assert "light_level_percent" in header
    li = header.index("light_level_percent")
    data = [r for r in rows[hi + 1 :] if r]
    assert [data[0][li], data[1][li], data[2][li], data[3][li], data[4][li]] == ["0", "50", "100", "100", ""]


def test_value_precision_rounding() -> None:
    """Each column is rounded to its useful resolution; position keeps 6 dp."""
    from doris.services.mcap_telemetry import McapSummary, TelemetryFrame

    s = McapSummary()
    s.frames.append(
        TelemetryFrame(
            log_time_ns=BASE_NS,
            values={
                "depth_m": 12.34567,
                "vertical_velocity_mps": -0.540541,
                "internal_pressure_hpa": 989.4719,
                "internal_temperature_c": 26.7434,
                "roll_deg": 86.6259,
                "heading_trueN_degrees": 300.541,
                "latitude": 33.7261148,
                "longitude": -118.2754123,
                "battery_voltage_v": 16.4843,
                "gps_satellites": 14.0,
            },
        )
    )
    text = build_dive_csv({"dive_name": "Precision"}, s, None)
    rows = list(csv.reader(io.StringIO(text)))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp_utc")
    row = dict(zip(rows[hi], rows[hi + 1], strict=False))

    assert row["depth_meters"] == "12.35"
    assert row["vertical_velocity_meters_per_second"] == "-0.54"
    assert row["internal_pressure_hectopascals"] == "989.5"
    assert row["internal_temperature_celsius"] == "26.74"
    assert row["roll_degrees"] == "86.6"
    assert row["heading_trueN_degrees"] == "300.5"
    assert row["battery_voltage_volts"] == "16.48"
    assert row["gps_satellites"] == "14"
    # Position retains sub-meter precision (6 decimal places).
    assert row["latitude_degrees"] == "33.726115"
    assert row["longitude_degrees"] == "-118.275412"


def test_dive_csv_filename() -> None:
    from doris.services.mcap_telemetry import dive_csv_filename

    # Start time (UTC, YYYYMMDDtHHMMSS to match finalized MP4 names) then name.
    assert (
        dive_csv_filename(
            {"started_at": "2026-05-28T16:50:58+00:00", "dive_name": "Reef Survey #2"},
            "dive_0007",
        )
        == "20260528t165058_reef_survey_2_dive_data.csv"
    )
    # Non-UTC offset is converted to UTC.
    assert (
        dive_csv_filename({"started_at": "2026-05-28T09:50:58-07:00", "dive_name": ""}, "dive_0007")
        == "20260528t165058_dive_data.csv"
    )
    # Missing/!parseable start time falls back to the dive id.
    assert dive_csv_filename({"dive_name": "Test"}, "dive_0007") == "dive_0007_test_dive_data.csv"
    assert dive_csv_filename({"started_at": "nope"}, "dive_0007") == "dive_0007_dive_data.csv"


def test_empty_summary_still_emits_header() -> None:
    from doris.services.mcap_telemetry import McapSummary

    text = build_dive_csv({"dive_name": "No Log"}, McapSummary(), None)
    assert "# DIVE DATA" in text
    assert "telemetry_rows,0" in text
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp_utc")
    assert not [r for r in rows[header_idx + 1 :] if r]


def test_summarize_tracks_mission_state_and_time(tmp_path: Path) -> None:
    """max_mission_state / max_mission_time_s aggregate the STATE and MSN_TIME
    named floats so the dive record can record completion + on-mission time."""
    mcap = tmp_path / "dive.mcap"
    _write_mcap(
        mcap,
        [
            [_named_float("STATE", 1.0), _named_float("MSN_TIME", 10.0), _named_float("DEPTH", 5.0)],
            [_named_float("STATE", 2.0), _named_float("MSN_TIME", 120.0), _named_float("DEPTH", 30.0)],
            [_named_float("STATE", 4.0), _named_float("MSN_TIME", 240.0), _named_float("DEPTH", 1.0)],
        ],
    )
    s = summarize_mcap(mcap)
    assert s.max_mission_state == 4
    assert s.max_mission_time_s == 240.0
    assert s.max_depth_m == 30.0


def test_apply_dive_summary_metadata_upgrades_and_persists(tmp_path: Path) -> None:
    """A cancelled dive whose log reached RECOVERY is upgraded to completed,
    and measured depth / end position / on-mission duration are written back."""
    from doris.services.mcap_telemetry import apply_dive_summary_metadata

    mcap = tmp_path / "dive.mcap"
    _write_mcap(
        mcap,
        [
            _cycle(state=2, depth=10.0, temp=18.0, volt=15.9, climb=-0.4)
            + [_named_float("MSN_TIME", 60.0)],
            _cycle(state=4, depth=25.0, temp=16.5, volt=15.4, climb=0.0)
            + [_named_float("MSN_TIME", 300.0)],
        ],
    )
    s = summarize_mcap(mcap)

    record = {"status": "cancelled"}
    changed = apply_dive_summary_metadata(record, s)

    assert changed is True
    assert record["status"] == "completed"
    assert record["log_max_depth_m"] == 25.0
    assert record["mission_duration_s"] == 300.0
    assert abs(record["end_latitude"] - 33.726114) < 1e-4
    assert abs(record["end_longitude"] - (-118.2754125)) < 1e-4


def test_apply_dive_summary_metadata_never_downgrades(tmp_path: Path) -> None:
    """A dive that never reached RECOVERY keeps its cancelled status, and a
    completed dive is left untouched."""
    from doris.services.mcap_telemetry import apply_dive_summary_metadata

    mcap = tmp_path / "dive.mcap"
    _write_mcap(
        mcap,
        [_cycle(state=2, depth=12.0, temp=18.0, volt=15.9, climb=-0.4)],
    )
    s = summarize_mcap(mcap)

    cancelled = {"status": "cancelled"}
    assert apply_dive_summary_metadata(cancelled, s) is False
    assert cancelled["status"] == "cancelled"
    assert cancelled["log_max_depth_m"] == 12.0

    completed = {"status": "completed"}
    assert apply_dive_summary_metadata(completed, s) is False
    assert completed["status"] == "completed"
