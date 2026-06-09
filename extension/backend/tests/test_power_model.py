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
def test_phase_power_base_only():
    cfg = pm.PhaseConfig()  # everything off
    assert pm.phase_power(cfg) == pytest.approx(pm.BASE_W)


def test_phase_power_full_light_and_camera():
    cfg = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        camera_on=True,
        camera_type="continuous-video",
    )
    expected = pm.BASE_W + pm.led_power_w(100) + pm.CAMERA_RECORDING_W
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


def test_estimate_dive_base_only_energy():
    est = pm.estimate_dive(
        depth_m=0,
        bottom_time_hours=10.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    # depth 0 -> descent 0 h, ascent = 0.75 h burn, bottom 10 h
    expected_wh = pm.BASE_W * (10.0 + 0.75)
    assert est["energy_wh"] == pytest.approx(expected_wh)
    assert est["average_power_w"] == pytest.approx(pm.BASE_W)


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


def test_phase_breakdown_components_sum():
    cfg = pm.PhaseConfig(
        light_on=True, brightness_pct=100, camera_on=True, camera_type="continuous-video"
    )
    b = pm.phase_breakdown("Bottom", cfg, hours=2.0)
    assert b["base_wh"] == pytest.approx(pm.BASE_W * 2.0)
    assert b["light_wh"] == pytest.approx(pm.led_power_w(100) * 2.0)
    assert b["camera_wh"] == pytest.approx(pm.CAMERA_RECORDING_W * 2.0)
    assert b["total_wh"] == pytest.approx(
        b["base_wh"] + b["light_wh"] + b["camera_wh"]
    )


def test_estimate_dive_breakdown_totals_match():
    est = pm.estimate_dive(
        depth_m=500,
        bottom_time_hours=4.0,
        descent=pm.PhaseConfig(light_on=True, brightness_pct=50),
        bottom=pm.PhaseConfig(light_on=True, brightness_pct=75, camera_on=True),
        ascent=pm.PhaseConfig(),
    )
    assert len(est["phases"]) == 3
    assert [p["name"] for p in est["phases"]] == ["Descent", "On Bottom", "Ascent"]
    # Component totals reconcile with overall energy.
    assert est["base_wh"] + est["light_wh"] + est["camera_wh"] == pytest.approx(
        est["energy_wh"]
    )
    assert sum(p["total_wh"] for p in est["phases"]) == pytest.approx(est["energy_wh"])


def test_interval_camera_gates_light():
    # Light follows the camera record/pause duty in interval mode, so a
    # longer pause cuts the (dominant) LED energy, not just the camera term.
    short_pause = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        camera_on=True,
        camera_type="video-interval",
        record_s=10,
        pause_s=10,
    )
    long_pause = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        camera_on=True,
        camera_type="video-interval",
        record_s=10,
        pause_s=50,
    )
    assert pm.effective_light_duty(short_pause) == pytest.approx(0.5)
    assert pm.effective_light_duty(long_pause) == pytest.approx(10 / 60)
    # The light energy must drop substantially, not marginally.
    b_short = pm.phase_breakdown("b", short_pause, hours=4.0)
    b_long = pm.phase_breakdown("b", long_pause, hours=4.0)
    assert b_long["light_wh"] < b_short["light_wh"] * 0.5


def test_timelapse_light_uses_strobe_window():
    cfg = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        camera_on=True,
        camera_type="timelapse",
        capture_period_s=60,
        timelapse_pre_s=2,
        timelapse_post_s=1,
    )
    assert pm.effective_light_duty(cfg) == pytest.approx(3 / 60)


def test_continuous_camera_keeps_light_own_mode():
    cfg = pm.PhaseConfig(
        light_on=True,
        brightness_pct=100,
        light_mode="interval",
        light_on_s=10,
        light_off_s=10,
        camera_on=True,
        camera_type="continuous-video",
    )
    assert pm.effective_light_duty(cfg) == pytest.approx(0.5)


def test_reserve_wh_overrides_percent():
    base = dict(
        depth_m=100,
        bottom_time_hours=2.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    est = pm.estimate_dive(reserve_wh=50.0, **base)
    assert est["reserve_wh"] == pytest.approx(50.0)
    assert est["usable_wh"] == pytest.approx(pm.BATTERY_CAPACITY_WH - 50.0)
    assert est["remaining_after_dive_wh"] == pytest.approx(
        pm.BATTERY_CAPACITY_WH - est["energy_wh"]
    )


def test_reserve_wh_fit_flag():
    # Huge reserve makes even a tiny dive fail the reserve check.
    est = pm.estimate_dive(
        depth_m=50,
        bottom_time_hours=1.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
        reserve_wh=290.0,
    )
    assert est["fits_within_reserve"] is False


def test_recovery_hours_from_remaining_energy():
    est = pm.estimate_dive(
        depth_m=100,
        bottom_time_hours=2.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(),
        ascent=pm.PhaseConfig(),
    )
    assert est["recovery_base_w"] == pytest.approx(pm.RECOVERY_BASE_W)
    expected = est["remaining_after_dive_wh"] / pm.RECOVERY_BASE_W
    assert est["recovery_hours"] == pytest.approx(expected)
    # More energy consumed => less surface recovery time.
    heavy = pm.estimate_dive(
        depth_m=300,
        bottom_time_hours=10.0,
        descent=pm.PhaseConfig(),
        bottom=pm.PhaseConfig(light_on=True, brightness_pct=100),
        ascent=pm.PhaseConfig(),
    )
    assert heavy["recovery_hours"] < est["recovery_hours"]


def test_pack_constants():
    assert pm.BATTERY_TOTAL_AH == pytest.approx(20.0)  # noqa: SIM300
    assert pm.BATTERY_CAPACITY_WH == pytest.approx(296.0)  # noqa: SIM300
