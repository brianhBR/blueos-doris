"""DORIS dive-load receipt generation.

Produces a human-readable ``DORIS_dive_load_<stamp>.txt`` summary
file when an operator clicks "Load Mission" in the extension UI and
the backend successfully pushes DORIS_* parameters to the autopilot.

The file is written into the same directory videos land in
(``<usb>/DORIS/userdata/ipcam_recordings/`` when a USB stick is
mounted, otherwise ``DATA_ROOT/userdata/ipcam_recordings/``) so the
existing media catalog picks it up and the existing
``/api/v1/media/download`` streaming pipeline can serve it back to
the operator's browser without any new download path.

Filename: ``DORIS_dive_load_MM.DD.YY_HHMM_UTC.txt`` (colon dropped
for FAT/exFAT compatibility on the typical DORIS USB stick).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..models.configuration import (
    BottomPhase,
    CameraSettings,
    CameraType,
    DeploymentConfiguration,
    LightSettings,
    ReleaseWeight,
)
from . import usb_storage
from .storage import DATA_ROOT, media_download_id_from_abs_path

logger = logging.getLogger(__name__)

# Burn release fires, then it takes ~40 minutes for the ballast to
# fully separate before the vehicle starts ascending.  The receipt
# accounts for this fixed delay in the total dive-time estimate.
BURN_RELEASE_TO_ASCENT_DELAY_MIN = 40.0

# Descent and ascent rate assumed to be 1 m/s; acceleration ignored.
VERTICAL_RATE_M_PER_S = 1.0


def _format_filename_stamp(loaded_at: datetime) -> str:
    """Return ``MM.DD.YY_HHMM_UTC`` for the receipt filename."""
    dt = loaded_at.astimezone(timezone.utc)
    return dt.strftime("%m.%d.%y_%H%M_UTC")


def receipt_filename(loaded_at: datetime) -> str:
    """Public helper: full receipt filename for a given load timestamp."""
    return f"DORIS_dive_load_{_format_filename_stamp(loaded_at)}.txt"


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _depth_meters(body: dict, config: DeploymentConfiguration | None) -> float | None:
    """Pull estimated depth (m) from the body first, then the config snapshot."""
    d = _parse_float(body.get("estimated_depth"))
    if d is not None and d > 0:
        return d
    if config is not None:
        d = _parse_float(config.estimated_depth)
        if d is not None and d > 0:
            return d
    return None


def _release_window_minutes(
    body: dict,
    config: DeploymentConfiguration | None,
    loaded_at: datetime,
) -> float | None:
    """Return minutes from ``loaded_at`` to burn-release trigger.

    Uses the configuration's ``release_weight`` block (the source of
    truth for what the autopilot actually got) and falls back to the
    explicit ``release_weight_date``/``release_weight_time`` fields
    sent in the dive-start body when no config is present.

    For ``method == "datetime"`` this is the wall-clock gap between
    the load time and the configured release datetime (UTC).  For
    ``method == "elapsed"`` it is just the configured elapsed value.
    Returns ``None`` when neither produces a usable number.
    """
    rw: ReleaseWeight | None = config.ascent.release_weight if config else None

    if rw is not None and rw.method == "datetime":
        return _datetime_release_minutes(rw.release_date, rw.release_time, loaded_at)

    if rw is not None and rw.method == "elapsed":
        secs = _release_elapsed_to_seconds(rw)
        if secs is not None:
            return max(0.0, secs / 60.0)

    rw_date = str(body.get("release_weight_date") or "").strip()
    rw_time = str(body.get("release_weight_time") or "").strip()
    if rw_date and rw_time:
        return _datetime_release_minutes(rw_date, rw_time, loaded_at)
    return None


def _release_elapsed_to_seconds(rw: ReleaseWeight) -> float | None:
    try:
        n = float(rw.elapsed.number) if rw.elapsed.number else 0.0
    except (TypeError, ValueError):
        return None
    unit = rw.elapsed.unit
    if unit == "hours":
        return n * 3600.0
    if unit == "minutes":
        return n * 60.0
    return n


def _datetime_release_minutes(
    rw_date: str, rw_time: str, loaded_at: datetime,
) -> float | None:
    if not rw_date:
        return None
    t = rw_time.strip() or "00:00"
    iso = f"{rw_date}T{t}:00+00:00" if len(t) == 5 else f"{rw_date}T{t}+00:00"
    try:
        release_dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    delta = (release_dt - loaded_at.astimezone(timezone.utc)).total_seconds() / 60.0
    return max(0.0, delta)


def _format_minutes(m: float) -> str:
    """Render minutes as ``X min`` or ``Xh Ym`` for readability."""
    if m < 60:
        return f"{m:.1f} min"
    hours = int(m // 60)
    rem = m - hours * 60
    return f"{hours}h {rem:.0f}m ({m:.0f} min)"


def _light_summary(light: LightSettings) -> str:
    if not light.enabled:
        return "off"
    parts = [f"on @ {light.brightness}%"]
    if light.mode == "interval":
        parts.append(
            f"interval (on={light.on_time.number}{light.on_time.unit[0]}, "
            f"off={light.off_time.number}{light.off_time.unit[0]})"
        )
    else:
        parts.append("continuous")
    return ", ".join(parts)


def _camera_summary(cam: CameraSettings) -> str:
    if not cam.enabled:
        return "off"
    if cam.camera_type == CameraType.CONTINUOUS_VIDEO:
        return f"continuous video ({cam.resolution} @ {cam.frame_rate}fps)"
    if cam.camera_type == CameraType.VIDEO_INTERVAL:
        return (
            f"interval video: {cam.video_record.number}{cam.video_record.unit[0]} on / "
            f"{cam.video_pause.number}{cam.video_pause.unit[0]} off"
        )
    if cam.camera_type == CameraType.TIMELAPSE:
        return (
            f"timelapse: 1 frame every {cam.capture_frequency}"
            f"{cam.capture_frequency_unit[0]}"
        )
    return f"{cam.camera_type} (unhandled)"


def _bottom_camera_summary(bottom: BottomPhase) -> str:
    cam = _camera_summary(bottom.camera)
    delay = (
        f"{bottom.camera_delay.number}{bottom.camera_delay.unit[0]}"
        if bottom.camera.enabled
        else "n/a"
    )
    return f"{cam}; start delay {delay}"


def _release_summary(rw: ReleaseWeight) -> str:
    if rw.method == "datetime":
        return f"datetime: {rw.release_date} {rw.release_time} UTC"
    return f"elapsed: {rw.elapsed.number} {rw.elapsed.unit}"


def build_receipt_text(
    body: dict,
    config: DeploymentConfiguration | None,
    loaded_at: datetime,
    profile_id: int,
) -> str:
    """Assemble the receipt body. Pure function (no I/O)."""
    lines: list[str] = []
    lines.append("DORIS Dive Load Receipt")
    lines.append("=" * 48)

    loaded_utc = loaded_at.astimezone(timezone.utc)
    lines.append(f"Loaded at: {loaded_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Profile ID: {profile_id}")
    lines.append("")

    # ── Operator-entered fields from the Start New Dive card ──
    lines.append("Start New Dive entries")
    lines.append("-" * 48)
    lines.append(f"  Dive name:        {body.get('dive_name', '') or '(blank)'}")
    lines.append(f"  Username:         {body.get('username', '') or '(blank)'}")
    lines.append(f"  Configuration:    {body.get('configuration', '') or '(none)'}")

    depth_str = str(body.get("estimated_depth") or "").strip() or (
        config.estimated_depth.strip() if config and config.estimated_depth else ""
    )
    lines.append(f"  Estimated depth:  {depth_str or '(blank)'} m")

    rw_date = str(body.get("release_weight_date") or "").strip()
    rw_time = str(body.get("release_weight_time") or "").strip()
    if rw_date or rw_time:
        lines.append(
            f"  Release weight:   {rw_date or '----'} {rw_time or '--:--'} UTC"
        )
    elif config is not None:
        lines.append(f"  Release weight:   {_release_summary(config.ascent.release_weight)}")
    else:
        lines.append("  Release weight:   (not set)")

    lat = body.get("latitude")
    lon = body.get("longitude")
    if lat is not None and lon is not None:
        lines.append(f"  Latitude:         {lat}")
        lines.append(f"  Longitude:        {lon}")
    loc = str(body.get("location") or "").strip()
    if loc:
        lines.append(f"  Location:         {loc}")
    lines.append("")

    # ── Total dive time estimate (depth + 40-min release delay) ──
    lines.append("Estimated dive time")
    lines.append("-" * 48)
    depth_m = _depth_meters(body, config)
    if depth_m is None:
        lines.append("  (depth not provided -- cannot estimate)")
    else:
        descent_min = depth_m / (VERTICAL_RATE_M_PER_S * 60.0)
        ascent_min = depth_m / (VERTICAL_RATE_M_PER_S * 60.0)
        rel_min = _release_window_minutes(body, config, loaded_at)
        if rel_min is None:
            bottom_min = 0.0
            bottom_note = " (release window unknown -- treated as 0)"
        else:
            bottom_min = max(0.0, rel_min - descent_min)
            bottom_note = ""
        delay_min = BURN_RELEASE_TO_ASCENT_DELAY_MIN
        total_min = descent_min + bottom_min + delay_min + ascent_min

        lines.append(
            f"  Descent ({depth_m:.0f} m @ {VERTICAL_RATE_M_PER_S:.0f} m/s):"
            f" {_format_minutes(descent_min)}"
        )
        lines.append(
            f"  Bottom phase (until burn release):"
            f" {_format_minutes(bottom_min)}{bottom_note}"
        )
        lines.append(
            f"  Burn release -> ascent delay: {_format_minutes(delay_min)}"
        )
        lines.append(
            f"  Ascent ({depth_m:.0f} m @ {VERTICAL_RATE_M_PER_S:.0f} m/s):"
            f" {_format_minutes(ascent_min)}"
        )
        lines.append(f"  TOTAL ESTIMATED:   {_format_minutes(total_min)}")
    lines.append("")

    # ── Dive profile summary (mirrors what gets pushed as DORIS_*) ──
    lines.append("Dive profile summary")
    lines.append("-" * 48)
    if config is None:
        lines.append("  (no configuration loaded -- vehicle kept previous DORIS_* params)")
    else:
        lines.append(f"  Profile name:     {config.name}")
        lines.append(f"  Dive label:       {config.dive_name}")
        lines.append("  Descent:")
        lines.append(f"    camera:         {_camera_summary(config.descent.camera)}")
        lines.append(f"    light:          {_light_summary(config.descent.light)}")
        lines.append("  Bottom:")
        lines.append(f"    camera:         {_bottom_camera_summary(config.bottom)}")
        lines.append(
            f"    light:          {_light_summary(config.bottom.light)}"
            f"; start delay {config.bottom.light_delay.number}"
            f"{config.bottom.light_delay.unit[0]}"
        )
        lines.append("  Ascent:")
        ascent_cam = (
            config.descent.camera if config.ascent.same_as_descent else config.ascent.camera
        )
        lines.append(f"    camera:         {_camera_summary(ascent_cam)}")
        lines.append(f"    light:          {_light_summary(config.ascent.light)}")
        lines.append(f"    release weight: {_release_summary(config.ascent.release_weight)}")
        lines.append("  Recovery:")
        lines.append(f"    mast light:     {'on' if config.recovery.activate_mast_light else 'off'}")
        lines.append(f"    iridium:        {'on' if config.recovery.use_iridium else 'off'}")
        lines.append(f"    LoRa:           {'on' if config.recovery.use_lora else 'off'}")
        lines.append(f"    update freq:    {config.recovery.update_frequency}")
    lines.append("")

    lines.append("--")
    lines.append("Generated by the DORIS extension at Load Mission time.")
    lines.append(
        "Vertical rates assumed at "
        f"{VERTICAL_RATE_M_PER_S:.0f} m/s; "
        f"{BURN_RELEASE_TO_ASCENT_DELAY_MIN:.0f}-minute burn-release "
        "delay applied before ascent begins."
    )
    return "\n".join(lines) + "\n"


def _resolve_recordings_dir() -> Path:
    """USB-first recordings dir, with internal-storage fallback.

    Mirrors :func:`services.dive_finalize._recordings_dir` so the
    receipt lands next to the videos for this dive.
    """
    sub = settings.ipcam_recordings_subdir.strip("/").strip()
    usb = usb_storage.get_recording_dir_if_available(sub)
    if usb is not None:
        return Path(usb)
    fallback = Path(os.environ.get("DORIS_DATA_ROOT", str(DATA_ROOT))) / sub
    return fallback


def write_receipt(
    body: dict,
    config: DeploymentConfiguration | None,
    loaded_at: datetime,
    profile_id: int,
) -> tuple[Path, str, int] | None:
    """Build + write the receipt to the recordings dir.

    Returns ``(absolute_path, media_download_id, size_bytes)`` on
    success, or ``None`` on any failure (caller logs and continues --
    the receipt is best-effort and never blocks the dive start).
    """
    try:
        text = build_receipt_text(body, config, loaded_at, profile_id)
    except Exception:
        logger.exception("Failed to build dive-load receipt text")
        return None

    try:
        rec_dir = _resolve_recordings_dir()
        rec_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Failed to prepare recordings dir for receipt: %s", e)
        return None

    filename = receipt_filename(loaded_at)
    path = rec_dir / filename
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to write dive-load receipt %s: %s", path, e)
        return None

    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = len(text.encode("utf-8"))

    try:
        download_id = media_download_id_from_abs_path(path, DATA_ROOT)
    except Exception:
        logger.exception("Failed to compute download id for receipt %s", path)
        return None

    logger.info(
        "Dive-load receipt written: %s (%d bytes, id=%s)",
        path, size_bytes, download_id,
    )
    return path, download_id, size_bytes
