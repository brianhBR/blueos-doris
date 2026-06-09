"""Shared DORIS power model.

Single source of truth for the vehicle's power budget, used by the live
battery estimator (``system.py``) and mirrored by the frontend planner
(``frontend/src/lib/powerModel.ts``) so the home screen, mission planner,
and backend never disagree.

All constants below were validated against a real 140-minute / 791 m dive
log (recorder_20260528_165058.mcap) by joining MAVLink ``BATTERY_STATUS``
(pack V/I) with the dive ``STATE``, the light PWM (RC9), and the release
``RELAY`` named-value floats:

* LED: measured pack-current rise at 1700 us was 1.28 A vs the bench table's
  1.24 A (3% agreement), so the bench curve is trustworthy and the LED draws
  on the monitored pack rail.
* Hotel (everything except the LED, lights off, idle): ~0.49 A ≈ 7-8 W.
* Camera recording adds only ~1.5 W over idle (descent/bottom/ascent
  lights-off baseline rose from ~0.49 A to ~0.59 A).
* Release/burn-wire relay showed no measurable current step at activation,
  so release power is treated as negligible for planning.

NOTE: these figures reflect whatever is on the autopilot's monitored power
rail. The LED is definitely on it (delta matches the bench curve). If the
Pi5/RadCAM are ever moved to a separate unmonitored converter, revisit
``HOTEL_W`` / ``CAMERA_RECORDING_W``.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Dive profile ─────────────────────────────────────────────────────
# Static buoyancy descent / drop-weight ascent both ~1 m/s.
DESCENT_RATE_M_S = 1.0
# Drop-weight burn-wire release time before the vehicle starts rising.
ASCENT_BURN_MINUTES = 45.0

# ── Battery pack ─────────────────────────────────────────────────────
# 2× Blue Robotics 10 Ah 4S Li-ion packs in parallel.
BATTERY_PACK_COUNT = 2
BATTERY_PACK_AH = 10.0
BATTERY_NOMINAL_V = 14.8
BATTERY_TOTAL_AH = BATTERY_PACK_COUNT * BATTERY_PACK_AH       # 20 Ah
BATTERY_CAPACITY_WH = BATTERY_TOTAL_AH * BATTERY_NOMINAL_V    # 296 Wh

# Reserve held back from usable-energy estimates (surfacing margin).
BATTERY_RESERVE_PCT = 15.0

# ── Component loads (empirical, see module docstring) ────────────────
# Hotel = Raspberry Pi 5 + autopilot + sensors, everything but the LED.
HOTEL_W = 8.0
# Extra draw while the camera pipeline is actively recording video.
CAMERA_RECORDING_W = 1.5
# Burn-wire / drop-weight release: no measurable draw in the logs.
RELEASE_W = 0.0
# Whole-dive average draw measured in-log (~26 Wh over 140 min). Used as
# the live time-remaining fallback when no instantaneous current is read.
TYPICAL_DIVE_LOAD_W = 11.0

# ── LED light ────────────────────────────────────────────────────────
# Single LED. Brightness 0-100 % maps linearly to the ArduPilot light
# servo PWM, matching doris.lua ``brightness_to_pwm``.
LIGHT_PWM_MIN = 1100
LIGHT_PWM_MAX = 1900

# Bench-measured LED current (A) vs PWM (us) at 15 V. Single LED.
_LED_CURVE: list[tuple[float, float]] = [
    (1100.0, 0.01),
    (1200.0, 0.25),
    (1500.0, 0.85),
    (1700.0, 1.24),
    (1900.0, 1.64),
]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def brightness_to_pwm(brightness_pct: float) -> float:
    """Map a 0-100 % brightness to light servo PWM (matches doris.lua)."""
    pct = _clamp(brightness_pct, 0.0, 100.0)
    return LIGHT_PWM_MIN + (pct / 100.0) * (LIGHT_PWM_MAX - LIGHT_PWM_MIN)


def led_current_at_pwm(pwm: float) -> float:
    """Interpolate LED current (A) for a given PWM from the bench curve."""
    if pwm <= _LED_CURVE[0][0]:
        return _LED_CURVE[0][1]
    if pwm >= _LED_CURVE[-1][0]:
        return _LED_CURVE[-1][1]
    for (p_lo, i_lo), (p_hi, i_hi) in zip(_LED_CURVE, _LED_CURVE[1:], strict=False):
        if p_lo <= pwm <= p_hi:
            frac = (pwm - p_lo) / (p_hi - p_lo)
            return i_lo + frac * (i_hi - i_lo)
    return _LED_CURVE[-1][1]


def led_power_w(brightness_pct: float, voltage: float = BATTERY_NOMINAL_V) -> float:
    """Power (W) drawn by the single LED at a given brightness.

    LED current is roughly voltage-independent over the pack's operating
    range (validated in-log), so power scales with the supply voltage.
    A brightness of 50 % therefore draws meaningfully less than 75 %.
    """
    return led_current_at_pwm(brightness_to_pwm(brightness_pct)) * voltage


# ── Duty cycles ──────────────────────────────────────────────────────
def interval_duty(on_seconds: float, off_seconds: float) -> float:
    """Fraction of time ON for an on/off interval cycle."""
    total = on_seconds + off_seconds
    if total <= 0:
        return 1.0
    return _clamp(on_seconds / total, 0.0, 1.0)


def light_duty(
    *,
    enabled: bool,
    mode: str = "continuous",
    on_seconds: float = 0.0,
    off_seconds: float = 0.0,
) -> float:
    """Average-on fraction for the LED in a phase."""
    if not enabled:
        return 0.0
    if mode == "interval":
        return interval_duty(on_seconds, off_seconds)
    return 1.0


def camera_duty(
    *,
    enabled: bool,
    mode: str = "continuous-video",
    record_seconds: float = 0.0,
    pause_seconds: float = 0.0,
    capture_period_seconds: float = 0.0,
    snapshot_seconds: float = 3.0,
) -> float:
    """Average video-pipeline-active fraction for the camera in a phase.

    * ``continuous-video`` -> always recording (duty 1).
    * ``video-interval``   -> record/pause duty cycle.
    * ``timelapse``        -> brief snapshots; the heavy video pipeline is
      stopped, so the duty is roughly snapshot window / capture period.
    """
    if not enabled:
        return 0.0
    if mode == "video-interval":
        return interval_duty(record_seconds, pause_seconds)
    if mode == "timelapse":
        if capture_period_seconds <= 0:
            return 0.0
        return _clamp(snapshot_seconds / capture_period_seconds, 0.0, 1.0)
    return 1.0


def phase_average_power_w(
    *,
    light_brightness_pct: float = 0.0,
    light_duty_fraction: float = 0.0,
    camera_duty_fraction: float = 0.0,
    voltage: float = BATTERY_NOMINAL_V,
) -> float:
    """Average power (W) for a dive phase = hotel + LED + camera."""
    led = led_power_w(light_brightness_pct, voltage) * _clamp(light_duty_fraction, 0.0, 1.0)
    cam = CAMERA_RECORDING_W * _clamp(camera_duty_fraction, 0.0, 1.0)
    return HOTEL_W + led + cam


def usable_capacity_wh(reserve_pct: float = BATTERY_RESERVE_PCT) -> float:
    """Energy (Wh) available above the safety reserve."""
    return BATTERY_CAPACITY_WH * (1.0 - _clamp(reserve_pct, 0.0, 100.0) / 100.0)


def hours_remaining(soc_pct: float, average_power_w: float) -> float:
    """Hours until the reserve is hit at a given SOC and average load."""
    if average_power_w <= 0:
        return 0.0
    usable_pct = max(0.0, soc_pct - BATTERY_RESERVE_PCT)
    usable_wh = BATTERY_CAPACITY_WH * (usable_pct / 100.0)
    return usable_wh / average_power_w


# ── Per-phase planning ───────────────────────────────────────────────
@dataclass
class PhaseConfig:
    """Light + camera settings for one dive phase (descent/bottom/ascent)."""

    light_on: bool = False
    brightness_pct: float = 0.0
    light_mode: str = "continuous"  # "continuous" | "interval"
    light_on_s: float = 0.0
    light_off_s: float = 0.0
    camera_on: bool = False
    camera_type: str = "continuous-video"  # continuous-video|video-interval|timelapse
    record_s: float = 0.0
    pause_s: float = 0.0
    capture_period_s: float = 0.0


def phase_power(cfg: PhaseConfig, voltage: float = BATTERY_NOMINAL_V) -> float:
    """Average power (W) for a phase given its light/camera configuration."""
    ld = light_duty(
        enabled=cfg.light_on,
        mode=cfg.light_mode,
        on_seconds=cfg.light_on_s,
        off_seconds=cfg.light_off_s,
    )
    cd = camera_duty(
        enabled=cfg.camera_on,
        mode=cfg.camera_type,
        record_seconds=cfg.record_s,
        pause_seconds=cfg.pause_s,
        capture_period_seconds=cfg.capture_period_s,
    )
    return phase_average_power_w(
        light_brightness_pct=cfg.brightness_pct,
        light_duty_fraction=ld,
        camera_duty_fraction=cd,
        voltage=voltage,
    )


def estimate_dive(
    *,
    depth_m: float,
    bottom_time_hours: float,
    descent: PhaseConfig,
    bottom: PhaseConfig,
    ascent: PhaseConfig,
    voltage: float = BATTERY_NOMINAL_V,
    reserve_pct: float = BATTERY_RESERVE_PCT,
) -> dict:
    """Estimate energy/time/battery usage for a planned dive.

    Durations:
      * descent = depth / DESCENT_RATE_M_S
      * bottom  = operator's release-weight elapsed/datetime time
      * ascent  = ASCENT_BURN_MINUTES burn + depth / DESCENT_RATE_M_S rise
                  (ascent phase light/camera settings apply throughout)
    """
    rise_h = max(0.0, depth_m) / DESCENT_RATE_M_S / 3600.0
    descent_h = rise_h
    ascent_h = ASCENT_BURN_MINUTES / 60.0 + rise_h
    bottom_h = max(0.0, bottom_time_hours)

    p_desc = phase_power(descent, voltage)
    p_btm = phase_power(bottom, voltage)
    p_asc = phase_power(ascent, voltage)

    energy_wh = p_desc * descent_h + p_btm * bottom_h + p_asc * ascent_h
    total_h = descent_h + bottom_h + ascent_h
    avg_w = energy_wh / total_h if total_h > 0 else 0.0

    usage_pct = (energy_wh / BATTERY_CAPACITY_WH) * 100.0 if BATTERY_CAPACITY_WH else 0.0
    usable_wh = usable_capacity_wh(reserve_pct)
    battery_life_h = BATTERY_CAPACITY_WH / avg_w if avg_w > 0 else 0.0

    return {
        "descent_hours": descent_h,
        "bottom_hours": bottom_h,
        "ascent_hours": ascent_h,
        "total_hours": total_h,
        "descent_power_w": p_desc,
        "bottom_power_w": p_btm,
        "ascent_power_w": p_asc,
        "average_power_w": avg_w,
        "energy_wh": energy_wh,
        "usage_percent": min(usage_pct, 100.0),
        "remaining_percent": max(0.0, 100.0 - usage_pct),
        "battery_life_hours": battery_life_h,
        "fits_within_reserve": energy_wh <= usable_wh,
    }
