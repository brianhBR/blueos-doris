"""Plain-text dive receipt written when a mission is loaded.

Elapsed time matches the home-screen planner: descent at
``DESCENT_RATE_M_S``, bottom time from the release-weight setting, then
a burn-wire interval plus a rise at the same rate. The clock starts at
deployment (the depth gate), not at the Load Mission click.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models.configuration import (
    CameraSettings,
    DeploymentConfiguration,
    LightSettings,
    TimeValue,
)
from .power_model import ASCENT_BURN_MINUTES, DESCENT_RATE_M_S

_LABEL_WIDTH = 32


def format_elapsed(hours: float) -> str:
    """Format a duration as ``Hh MM`` rounded to the nearest minute."""
    whole_hours, minutes = _hours_minutes(hours)
    return f"{whole_hours}h {minutes:02d}m"


def _short_duration(hours: float) -> str:
    """``45 min`` under an hour, otherwise the same ``Hh MM`` form."""
    whole_hours, minutes = _hours_minutes(hours)
    if whole_hours == 0:
        return f"{minutes} min"
    return f"{whole_hours}h {minutes:02d}m"


def _hours_minutes(hours: float) -> tuple[int, int]:
    total_minutes = int(round(max(0.0, hours) * 60.0))
    return divmod(total_minutes, 60)


def bottom_time_hours(
    config: DeploymentConfiguration,
    *,
    release_date: str,
    release_time: str,
    loaded_at: datetime,
) -> float:
    """Hours on bottom used by the planner.

    Elapsed release is the configured on-bottom duration. Date/time
    release is the interval from mission load until that instant, which
    assumes the vehicle is deployed immediately.
    """
    release = config.ascent.release_weight
    if release.method == "elapsed":
        return max(0.0, _time_value_hours(release.elapsed))

    date = (release_date or release.release_date or "").strip()
    clock = (release_time or release.release_time or "").strip()
    if not date or not clock:
        return 0.0
    try:
        release_at = datetime.fromisoformat(f"{date}T{clock}:00+00:00")
    except ValueError:
        return 0.0
    loaded = loaded_at if loaded_at.tzinfo else loaded_at.replace(tzinfo=UTC)
    return max(0.0, (release_at - loaded).total_seconds() / 3600.0)


def format_dive_receipt(
    config: DeploymentConfiguration,
    *,
    dive_name: str,
    username: str,
    configuration_name: str,
    estimated_depth: str,
    release_date: str,
    release_time: str,
    loaded_at: datetime,
    profile_id: int,
    latitude: float | None = None,
    longitude: float | None = None,
    fix_type: str | None = None,
    satellites: int | None = None,
    battery_voltage: float | None = None,
    battery_level: float | None = None,
) -> str:
    """Render the receipt for the mission that was just loaded."""
    entered_depth = _optional_depth_m(estimated_depth)
    depth_m = (
        entered_depth
        if entered_depth is not None
        else _parse_depth_m(config.estimated_depth)
    )

    bottom_h = bottom_time_hours(
        config,
        release_date=release_date,
        release_time=release_time,
        loaded_at=loaded_at,
    )
    descent_h = max(0.0, depth_m) / DESCENT_RATE_M_S / 3600.0
    burn_h = ASCENT_BURN_MINUTES / 60.0
    rise_h = descent_h
    ascent_h = burn_h + rise_h
    on_bottom = descent_h
    release_starts = descent_h + bottom_h
    leaves_bottom = release_starts + burn_h
    surface = descent_h + bottom_h + ascent_h

    loaded = loaded_at if loaded_at.tzinfo else loaded_at.replace(tzinfo=UTC)
    lines = [
        "DORIS DIVE RECEIPT",
        "==================",
        "",
        _row("Dive name", dive_name.strip() or "—"),
        _row("Username", username.strip() or "—"),
        _row("Configuration", configuration_name.strip() or config.name),
        _row("Profile", str(profile_id)),
        _row("Loaded", loaded.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")),
        _row("Estimated depth", _format_depth(depth_m)),
        _row("Release weight", _release_summary(config, release_date, release_time)),
        _row("Location", _location_summary(latitude, longitude, fix_type, satellites)),
        _row("Battery", _battery_summary(battery_voltage, battery_level)),
    ]

    lines.extend([
        "",
        "ELAPSED TIME FROM DEPLOYMENT",
        "----------------------------",
        f"Estimated surface: {format_elapsed(surface)} after deployment",
        "",
        "The clock starts when DORIS passes the depth gate, not when",
        "Load Mission is pressed.",
        "",
        _event("Deployment", 0.0),
        _event("On bottom", on_bottom),
        _event("Weight release starts", release_starts),
        _event("Leaves seafloor (est.)", leaves_bottom),
        _event("Estimated surface", surface),
        "",
        _row("Descent", f"{format_elapsed(descent_h)} at {DESCENT_RATE_M_S:g} m/s"),
        _row("On bottom", format_elapsed(bottom_h)),
        _row(
            "Ascent",
            f"{format_elapsed(ascent_h)} ({_short_duration(burn_h)} burn"
            f" + {format_elapsed(rise_h)} rise)",
        ),
        _row("Total", format_elapsed(surface)),
    ])

    release = config.ascent.release_weight
    if release.method == "datetime":
        lines.extend([
            "",
            "Date/time release is planned as time on bottom measured from",
            "mission load, assuming the vehicle is deployed immediately.",
        ])
        if bottom_h <= 0:
            lines.append("The release time is already past, so planned bottom time is 0.")

    lines.extend([
        "",
        "MISSION PARAMETERS",
        "------------------",
        "",
        "Descent",
        _row("Camera", _camera_line(config.descent.camera)),
        _row("Light", _light_line(config.descent.light)),
        _row("Auto white balance", _on_off(config.descent.auto_white_balance)),
        "",
        "On bottom",
        _row("Camera", _camera_line(config.bottom.camera)),
    ])
    if config.bottom.camera.enabled:
        lines.append(_row("Camera delay", _phrase(config.bottom.camera_delay)))
        if config.bottom.camera.camera_type.value == "timelapse":
            lines.append(
                _row(
                    "Light strobe",
                    f"{_phrase(config.bottom.camera.timelapse_light_pre)} before, "
                    f"{_phrase(config.bottom.camera.timelapse_light_post)} after",
                )
            )
    if config.bottom.camera.sleep_timer_enabled:
        lines.append(_row("Camera sleep", _phrase(config.bottom.camera.sleep_timer)))
    lines.append(_row("Light", _light_line(config.bottom.light)))
    if config.bottom.light.enabled:
        lines.append(_row("Light delay", _phrase(config.bottom.light_delay)))
    lines.append(_row("Auto white balance", _on_off(config.bottom.auto_white_balance)))

    lines.extend(["", "Ascent"])
    if config.ascent.same_as_descent:
        lines.append(_row("Camera and light", "same as descent"))
    else:
        lines.append(_row("Camera", _camera_line(config.ascent.camera)))
        lines.append(_row("Light", _light_line(config.ascent.light)))
    lines.append(_row("Auto white balance", _on_off(config.ascent.auto_white_balance)))

    recovery = config.recovery
    lines.extend([
        "",
        "Recovery",
        _row("Mast light", _on_off(recovery.activate_mast_light)),
        _row("Position updates", recovery.update_frequency or "—"),
        _row("Iridium", _on_off(recovery.use_iridium)),
        _row("LoRa", _on_off(recovery.use_lora)),
        "",
        f"Descent and rise use {DESCENT_RATE_M_S:g} m/s. The burn wire is",
        f"estimated at {ASCENT_BURN_MINUTES:g} minutes before the vehicle",
        "leaves the seafloor. Actual times vary with buoyancy and current.",
        "",
    ])
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return f"  {label:<{_LABEL_WIDTH}}{value}"


def _event(label: str, hours: float) -> str:
    return _row(label, f"T+ {format_elapsed(hours)}")


def _on_off(enabled: bool) -> str:
    return "on" if enabled else "off"


def _optional_depth_m(raw: str) -> float | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        return None


def _parse_depth_m(raw: str) -> float:
    parsed = _optional_depth_m(raw)
    return 0.0 if parsed is None else parsed


def _format_depth(meters: float) -> str:
    if meters == int(meters):
        return f"{int(meters)} m"
    return f"{meters:.1f} m"


_FIX_LABELS = {
    "2d": "2D",
    "3d": "3D",
    "dgps": "DGPS",
    "rtk_float": "RTK float",
    "rtk_fixed": "RTK fixed",
}


def _location_summary(
    latitude: float | None,
    longitude: float | None,
    fix_type: str | None,
    satellites: int | None,
) -> str:
    fix = (fix_type or "").strip().lower()
    if latitude is None or longitude is None or fix == "none":
        return "no GPS fix"
    text = f"{_format_coord(latitude, 'N', 'S')}, {_format_coord(longitude, 'E', 'W')}"
    details: list[str] = []
    if fix:
        details.append(_FIX_LABELS.get(fix, fix))
    if satellites is not None and satellites >= 0:
        details.append(f"{satellites} sats")
    if details:
        text += f" ({', '.join(details)})"
    return text


def _battery_summary(voltage: float | None, level: float | None) -> str:
    parts: list[str] = []
    if voltage is not None:
        parts.append(f"{voltage:.1f} V")
    if level is not None:
        parts.append(f"{level:.0f}%")
    return ", ".join(parts) if parts else "unavailable"


def _format_coord(value: float, positive: str, negative: str) -> str:
    direction = positive if value >= 0 else negative
    return f"{abs(value):.5f} {direction}"


def _time_value_seconds(tv: TimeValue | None) -> float:
    if tv is None:
        return 0.0
    try:
        number = float(tv.number) if tv.number else 0.0
    except (TypeError, ValueError):
        return 0.0
    if tv.unit == "hours":
        return number * 3600.0
    if tv.unit == "minutes":
        return number * 60.0
    return number


def _time_value_hours(tv: TimeValue) -> float:
    return _time_value_seconds(tv) / 3600.0


def _trim_number(raw: str) -> str:
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw.strip() or "0"
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def _phrase(tv: TimeValue) -> str:
    number = _trim_number(tv.number)
    unit = tv.unit
    try:
        if float(number) == 1:
            unit = unit.rstrip("s")
    except ValueError:
        pass
    return f"{number} {unit}"


def _camera_line(camera: CameraSettings) -> str:
    if not camera.enabled:
        return "off"
    kind = camera.camera_type.value
    if kind == "continuous-video":
        return "continuous video"
    if kind == "video-interval":
        return (
            f"video interval, record {_phrase(camera.video_record)}"
            f" / pause {_phrase(camera.video_pause)}"
        )
    if kind == "timelapse":
        period = TimeValue(
            number=str(camera.capture_frequency),
            unit=camera.capture_frequency_unit,
        )
        return f"timelapse every {_phrase(period)}"
    return kind


def _light_line(light: LightSettings) -> str:
    if not light.enabled:
        return "off"
    if light.mode.value == "interval":
        text = (
            f"on, {light.brightness}%, interval "
            f"{_phrase(light.on_time)} on / {_phrase(light.off_time)} off"
        )
    else:
        text = f"on, {light.brightness}%, continuous"
    if light.match_camera_interval:
        text += ", follows camera"
    return text


def _release_summary(
    config: DeploymentConfiguration,
    release_date: str,
    release_time: str,
) -> str:
    release = config.ascent.release_weight
    if release.method == "elapsed":
        return f"{_phrase(release.elapsed)} on bottom"
    date = (release_date or release.release_date or "").strip()
    clock = (release_time or release.release_time or "").strip()
    if date and clock:
        return f"{date} {clock} UTC"
    return "date/time, not set"
