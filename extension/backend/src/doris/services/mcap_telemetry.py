"""Best-effort telemetry extraction from recorder .mcap files.

DORIS Lua publishes mavlink NAMED_VALUE_FLOAT-style names such as MAX_DPTH, DEPTH,
and MIN_TEMP. Messages may be wrapped in ROS/JSON/other encodings; we scan each
record payload for those name patterns and little-endian floats that precede the
10-byte MAVLink name field (uint32 time + float value + char[10] name).
"""

from __future__ import annotations

import csv
import io
import json
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcap.reader import make_reader

# ── MCAP → per-dive file mapping (lazy import storage helpers to avoid cycles) ──


def map_dive_stem_to_largest_mcap(root: Path, windows: list[Any]) -> dict[str, Path]:
    """Pick the largest .mcap under recorder/ whose timestamp falls in each dive window."""
    from .storage import RECORDER_DIR, _effective_created_at, _match_dive_window

    rec = root / RECORDER_DIR
    if not rec.is_dir():
        return {}
    best: dict[str, tuple[int, Path]] = {}
    for path in rec.rglob("*.mcap"):
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


def _pad_name_10(name: str) -> bytes:
    b = name.encode("ascii", errors="ignore")[:10]
    return b + b"\x00" * (10 - len(b))


def _values_before_name(blob: bytes, name: str) -> list[float]:
    """Extract float values immediately before a 10-byte MAVLink-style name field."""
    needle = _pad_name_10(name)
    if len(needle) != 10:
        return []
    vals: list[float] = []
    start = 0
    while True:
        i = blob.find(needle, start)
        if i < 0:
            break
        if i >= 4:
            (v,) = struct.unpack_from("<f", blob, i - 4)
            if math.isfinite(v):
                vals.append(v)
        start = i + 1
    return vals


def _scan_named_floats(blob: bytes) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name in ("MAX_DPTH", "DEPTH", "MIN_TEMP", "TEMP"):
        vs = _values_before_name(blob, name)
        if vs:
            out[name] = vs
    return out


def _try_json_coords(blob: bytes) -> tuple[float | None, float | None]:
    """If payload is JSON, try to read latitude/longitude-like keys."""
    try:
        t = blob.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, None
    t = t.strip()
    if not t or t[0] not in "{[":
        return None, None
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None, None

    def walk(o: Any) -> tuple[float | None, float | None]:
        if isinstance(o, dict):
            lat = o.get("latitude", o.get("lat"))
            lon = o.get("longitude", o.get("lon", o.get("lng")))
            try:
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
            except (TypeError, ValueError):
                pass
            for v in o.values():
                la, lo = walk(v)
                if la is not None and lo is not None:
                    return la, lo
        elif isinstance(o, list):
            for it in o:
                la, lo = walk(it)
                if la is not None and lo is not None:
                    return la, lo
        return None, None

    return walk(obj)


@dataclass
class TelemetrySample:
    log_time_ns: int
    depth_m: float | None = None
    temperature_c: float | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class McapSummary:
    """Aggregates used for dive history and CSV export."""

    max_depth_m: float | None = None
    log_max_depth_m: float | None = None
    min_temperature_c: float | None = None
    samples: list[TelemetrySample] = field(default_factory=list)
    last_lat: float | None = None
    last_lon: float | None = None
    messages_seen: int = 0


_MAX_MESSAGES = 400_000


def summarize_mcap(path: Path) -> McapSummary:
    """Parse one .mcap file; tolerates missing or non-mavlink data."""
    summary = McapSummary()
    max_depth = 0.0
    max_d_max = 0.0
    has_depth = False
    has_max_d = False
    min_temp: float | None = None
    lat: float | None = None
    lon: float | None = None

    try:
        with path.open("rb") as f:
            reader = make_reader(f)
            for _schema, _channel, message in reader.iter_messages():
                summary.messages_seen += 1
                if summary.messages_seen > _MAX_MESSAGES:
                    break
                blob = message.data
                if not blob:
                    continue
                lt = int(message.log_time)
                nf = _scan_named_floats(blob)
                row = TelemetrySample(log_time_ns=lt)
                if "DEPTH" in nf:
                    v = nf["DEPTH"][-1]
                    if math.isfinite(v) and 0 <= v < 15_000:
                        row.depth_m = v
                        max_depth = max(max_depth, v)
                        has_depth = True
                if "MAX_DPTH" in nf:
                    v = nf["MAX_DPTH"][-1]
                    if math.isfinite(v) and 0 <= v < 15_000:
                        row.depth_m = row.depth_m or v
                        max_d_max = max(max_d_max, v)
                        has_max_d = True
                if "MIN_TEMP" in nf:
                    v = nf["MIN_TEMP"][-1]
                    if math.isfinite(v) and -100 < v < 60:
                        row.temperature_c = v
                        min_temp = v if min_temp is None else min(min_temp, v)
                elif "TEMP" in nf:
                    v = nf["TEMP"][-1]
                    if math.isfinite(v) and -100 < v < 60:
                        row.temperature_c = v
                        min_temp = v if min_temp is None else min(min_temp, v)

                jlat, jlon = _try_json_coords(blob)
                if jlat is not None and jlon is not None:
                    row.lat, row.lon = jlat, jlon
                    lat, lon = jlat, jlon

                if (
                    row.depth_m is not None
                    or row.temperature_c is not None
                    or (row.lat is not None and row.lon is not None)
                ):
                    summary.samples.append(row)

    except Exception:
        return McapSummary()

    if has_max_d:
        summary.log_max_depth_m = max_d_max
    if has_depth:
        summary.max_depth_m = max_depth
    if has_max_d and has_depth:
        summary.max_depth_m = max(max_depth, max_d_max)
    elif has_max_d and not has_depth:
        summary.max_depth_m = max_d_max
    summary.min_temperature_c = min_temp
    summary.last_lat = lat
    summary.last_lon = lon
    return summary


def _ns_to_utc_iso(ns: int) -> str:
    sec = ns / 1e9
    dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    return dt.isoformat()


def build_scientific_csv(
    dive_record: dict[str, Any],
    summary: McapSummary,
    mcap_rel: str | None,
) -> str:
    """Build CSV text: metadata rows then time series."""
    start_lat = dive_record.get("latitude")
    start_lon = dive_record.get("longitude")
    try:
        s_lat = float(start_lat) if start_lat is not None else None
    except (TypeError, ValueError):
        s_lat = None
    try:
        s_lon = float(start_lon) if start_lon is not None else None
    except (TypeError, ValueError):
        s_lon = None

    # Ending GPS: last position seen in the log (not duplicated from start if unknown).
    end_lat = summary.last_lat
    end_lon = summary.last_lon

    started = dive_record.get("started_at")
    ended = dive_record.get("ended_at")
    dive_name = str(dive_record.get("dive_name") or "").strip()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["doris_scientific_export", "v1"])
    w.writerow(["dive_name", dive_name])
    w.writerow(["mcap_file", mcap_rel or ""])
    w.writerow(["started_at_utc", str(started or "")])
    w.writerow(["ended_at_utc", str(ended or "")])
    w.writerow(["max_depth_from_log_m", summary.max_depth_m if summary.max_depth_m is not None else ""])
    w.writerow(["min_temperature_from_log_c", summary.min_temperature_c if summary.min_temperature_c is not None else ""])
    w.writerow(["start_latitude", s_lat if s_lat is not None else ""])
    w.writerow(["start_longitude", s_lon if s_lon is not None else ""])
    w.writerow(["end_latitude", end_lat if end_lat is not None else ""])
    w.writerow(["end_longitude", end_lon if end_lon is not None else ""])
    w.writerow([])
    w.writerow(
        [
            "timestamp_utc",
            "depth_m",
            "temperature_c",
            "latitude",
            "longitude",
            "dive_start_latitude",
            "dive_start_longitude",
            "dive_end_latitude",
            "dive_end_longitude",
        ]
    )
    for s in sorted(summary.samples, key=lambda x: x.log_time_ns):
        w.writerow(
            [
                _ns_to_utc_iso(s.log_time_ns),
                s.depth_m if s.depth_m is not None else "",
                s.temperature_c if s.temperature_c is not None else "",
                s.lat if s.lat is not None else "",
                s.lon if s.lon is not None else "",
                s_lat if s_lat is not None else "",
                s_lon if s_lon is not None else "",
                (end_lat if end_lat is not None else ""),
                (end_lon if end_lon is not None else ""),
            ]
        )
    return buf.getvalue()
