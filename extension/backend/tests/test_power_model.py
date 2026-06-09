"""Tests for the shared power model.

The LED curve and component-load constants were validated against a real
dive log (recorder_20260528_165058.mcap); these tests lock in the curve
behaviour, duty-cycle math, and dive-energy estimation.
"""

import pytest

from doris.services import power_model as pm


# ── LED current curve ────────────────────────────────────────────────
def test_brightness_to_pwm_endpoints():
    assert pm.brightness_to_pwm(0) == pm.LIGHT_PWM_MIN
    assert pm.brightness_to_pwm(100) == pm.LIGHT_PWM_MAX
    assert pm.brightness_to_pwm(50) == pytest.approx(1500.0)


def test_brightness_to_pwm_clamps():
    assert pm.brightness_to_pwm(-10) == pm.LIGHT_PWM_MIN
    assert pm.brightness_to_pwm(150) == pm.LIGHT_PWM_MAX


@pytest.mark.parametrize(
    "pwm,amps",
    [(1100, 0.01), (1200, 0.25), (1500, 0.85), (1700, 1.24), (1900, 1.64)],
)
def test_led_curve_matches_bench_points(pwm, amps):
    assert pm.led_current_at_pwm(pwm) == pytest.approx(amps)


def test_led_curve_interpolates_midpoint():
    # Halfway between 1200 (0.25 A) and 1500 (0.85 A) -> 1350 us ≈ 0.55 A
    assert pm.led_current_at_pwm(1350) == pytest.approx(0.55, abs=1e-6)


def test_led_curve_clamps_out_of_range():
    assert pm.led_current_at_pwm(900) == 0.01
    assert pm.led_current_at_pwm(2200) == 1.64


def test_led_power_monotonic_in_brightness():
    # The user's headline requirement: 50% draws less than 75%.
    assert pm.led_power_w(50) < pm.led_power_w(75)
    assert pm.led_power_w(0) < pm.led_power_w(50)


def test_led_power_scales_with_voltage():
    assert pm.led_power_w(75, voltage=15.0) == pytest.approx(1.24 * 15.0)


# ── Duty cycles ──────────────────────────────────────────────────────
def test_interval_duty():
    assert pm.interval_duty(10, 0) == 1.0
    assert pm.interval_duty(0, 10) == 0.0
    assert pm.interval_duty(10, 30) == pytest.approx(0.25)
    assert pm.interval_duty(0, 0) == 1.0


def test_light_duty_modes():
    assert pm.light_duty(enabled=False) == 0.0
    assert pm.light_duty(enabled=True, mode="continuous") == 1.0
    assert (
        pm.light_duty(enabled=True, mode="interval", on_seconds=10, off_seconds=10)
        == pytest.approx(0.5)
    )


def test_camera_duty_modes():
    assert pm.camera_duty(enabled=False) == 0.0
    assert pm.camera_duty(enabled=True, mode="continuous-video") == 1.0
    assert (
        pm.camera_duty(enabled=True, mode="video-interval", record_seconds=10, pause_seconds=10)
        == pytest.approx(0.5)
    )
    tl = pm.camera_duty(
        enabled=True, mode="timelapse", capture_period_seconds=60, snapshot_seconds=3
    )
    assert tl == pytest.approx(0.05)


# ── Phase + dive estimation ──────────────────────────────────────────
def test_phase_power_hotel_only():
    cfg = pm.PhaseConfig()  # everything off
    assert pm.phase_power(cfg) == pytest.approx(pm.HOTEL_W)


def test_phase_power_full_light_and_camera():
    cfg = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        camera_on=True,
        camera_type="continuous-video",
    )
    expected = pm.HOTEL_W + pm.led_power_w(100) + pm.CAMERA_RECORDING_W
    assert pm.phase_power(cfg) == pytest.approx(expected)


def test_estimate_dive_durations():
    est = pm.estimate_dive(
        depth_m=3600,  # 3600 m at 1 m/s = 1 h descent
        bottom_time_hours=2.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    assert est["descent_hours"] == pytest.approx(1.0)
    assert est["bottom_hours"] == pytest.approx(2.0)
    # ascent = 45 min burn + 1 h rise = 1.75 h
    assert est["ascent_hours"] == pytest.approx(1.75)
    assert est["total_hours"] == pytest.approx(4.75)


def test_estimate_dive_hotel_only_energy():
    est = pm.estimate_dive(
        depth_m=0,
        bottom_time_hours=10.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    # depth 0 -> descent 0 h, ascent = 0.75 h burn, bottom 10 h
    expected_wh = pm.HOTEL_W * (10.0 + 0.75)
    assert est["energy_wh"] == pytest.approx(expected_wh)
    assert est["average_power_w"] == pytest.approx(pm.HOTEL_W)


def test_estimate_dive_brightness_affects_usage():
    base = dict(
        depth_m=1000,
        bottom_time_hours=4.0,
        descent=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    dim = pm.estimate_dive(
        bottom=pm.PhaseConfig(light_on=True, brightness_pct=50), **base
    )
    bright = pm.estimate_dive(
        bottom=pm.PhaseConfig(light_on=True, brightness_pct=75), **base
    )
    assert dim["energy_wh"] < bright["energy_wh"]
    assert dim["usage_percent"] < bright["usage_percent"]


def test_estimate_dive_interval_vs_continuous_lights():
    base = dict(
        depth_m=500,
        bottom_time_hours=6.0,
        descent=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    continuous = pm.estimate_dive(
        bottom=pm.PhaseConfig(light_on=True, brightness_pct=100, light_mode="continuous"),
        **base,
    )
    interval = pm.estimate_dive(
        bottom=pm.PhaseConfig(
            light_on=True,
            brightness_pct=100,
            light_mode="interval",
            light_on_s=10,
            light_off_s=30,
        ),
        **base,
    )
    assert interval["energy_wh"] < continuous["energy_wh"]


def test_reserve_flag():
    # A short, light-load dive fits within reserve.
    ok = pm.estimate_dive(
        depth_m=100,
        bottom_time_hours=2.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    assert ok["fits_within_reserve"] is True
    # An enormous bottom time blows past usable capacity.
    over = pm.estimate_dive(
        depth_m=100,
        bottom_time_hours=200.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    assert over["fits_within_reserve"] is False


def test_pack_constants():
    assert pm.BATTERY_TOTAL_AH == pytest.approx(20.0)  # noqa: SIM300
    assert pm.BATTERY_CAPACITY_WH == pytest.approx(296.0)  # noqa: SIM300
