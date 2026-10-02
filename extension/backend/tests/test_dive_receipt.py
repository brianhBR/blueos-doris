"""Dive receipt text generated when a mission is loaded."""

from __future__ import annotations

from datetime import UTC, datetime

from doris.models.configuration import (
    AscentPhase,
    BottomPhase,
    CameraSettings,
    CameraType,
    DeploymentConfiguration,
    DescentPhase,
    LightSettings,
    RecoverySettings,
    ReleaseWeight,
    TimeValue,
)
from doris.services.dive_receipt import (
    bottom_time_hours,
    format_dive_receipt,
    format_elapsed,
)

LOADED = datetime(2026, 10, 2, 4, 10, 22, tzinfo=UTC)


def _config(**overrides: object) -> DeploymentConfiguration:
    release = ReleaseWeight(
        method="elapsed",
        elapsed=TimeValue(number="6", unit="hours"),
    )
    config = DeploymentConfiguration(
        name="deep-timelapse",
        dive_name="Saved name",
        estimated_depth="100",
        descent=DescentPhase(
            camera=CameraSettings(enabled=True, camera_type=CameraType.CONTINUOUS_VIDEO),
            light=LightSettings(enabled=True, brightness=60),
            auto_white_balance=False,
        ),
        bottom=BottomPhase(
            camera=CameraSettings(
                enabled=True,
                camera_type=CameraType.TIMELAPSE,
                capture_frequency=10,
                capture_frequency_unit="seconds",
            ),
            camera_delay=TimeValue(number="30", unit="seconds"),
            light=LightSettings(enabled=True, brightness=40),
            light_delay=TimeValue(number="30", unit="seconds"),
            auto_white_balance=True,
        ),
        ascent=AscentPhase(
            same_as_descent=True,
            release_weight=release,
            auto_white_balance=False,
        ),
        recovery=RecoverySettings(
            activate_mast_light=True,
            update_frequency="5min",
            use_iridium=False,
            use_lora=True,
        ),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _receipt(config: DeploymentConfiguration | None = None, **kwargs: object) -> str:
    base = {
        "dive_name": "Night survey",
        "username": "brian",
        "configuration_name": "deep-timelapse",
        "estimated_depth": "3600",
        "release_date": "2026-10-02",
        "release_time": "12:00",
        "loaded_at": LOADED,
        "profile_id": 12,
        "latitude": 47.6062,
        "longitude": -122.3321,
    }
    base.update(kwargs)
    return format_dive_receipt(config or _config(), **base)  # type: ignore[arg-type]


def test_load_snapshot_records_fix_and_battery() -> None:
    text = _receipt(
        fix_type="3d",
        satellites=12,
        battery_voltage=16.24,
        battery_level=82,
    )
    assert "Location                        47.60620 N, 122.33210 W (3D, 12 sats)" in text
    assert "Battery                         16.2 V, 82%" in text


def test_no_fix_does_not_print_coordinates() -> None:
    text = _receipt(fix_type="none", latitude=0.0, longitude=0.0)
    assert "Location                        no GPS fix" in text
    assert "0.00000" not in text


def test_format_elapsed_rounds_to_minutes() -> None:
    assert format_elapsed(0) == "0h 00m"
    assert format_elapsed(1) == "1h 00m"
    assert format_elapsed(1.75) == "1h 45m"
    assert format_elapsed(8.75) == "8h 45m"


def test_elapsed_release_timeline_from_deployment() -> None:
    # 3600 m at 1 m/s is 1 h of descent. 6 h on bottom. Ascent is a
    # 45 min burn plus a 1 h rise.
    text = _receipt()
    assert "Dive name                       Night survey" in text
    assert "Username                        brian" in text
    assert "Estimated depth                 3600 m" in text
    assert "Release weight                  6 hours on bottom" in text
    assert "Location                        47.60620 N, 122.33210 W" in text
    assert "Battery                         unavailable" in text
    assert "Estimated surface: 8h 45m after deployment" in text
    assert "Profile                         12" in text
    assert "Loaded                          2026-10-02 04:10:22 UTC" in text

    on_bottom = text.index("On bottom                       T+ 1h 00m")
    release = text.index("Weight release starts           T+ 7h 00m")
    leaves = text.index("Leaves seafloor (est.)          T+ 7h 45m")
    surface = text.index("Estimated surface               T+ 8h 45m")
    assert on_bottom < release < leaves < surface

    assert "Camera                          timelapse every 10 seconds" in text
    assert "Light                           on, 40%, continuous" in text
    assert "Camera and light                same as descent" in text
    assert "Mast light                      on" in text
    assert "LoRa                            on" in text
    assert "Iridium                         off" in text
    assert text.endswith("\n")


def test_dashboard_depth_overrides_saved_configuration() -> None:
    text = _receipt(estimated_depth="1800")
    assert "Estimated depth                 1800 m" in text
    assert "On bottom                       T+ 0h 30m" in text


def test_elapsed_release_ignores_dashboard_clock() -> None:
    # The home screen fills a clock from "now + elapsed" when the
    # configuration is selected. Waiting before Load Mission must not
    # shorten an elapsed-on-bottom release.
    hours = bottom_time_hours(
        _config(),
        release_date="2026-10-02",
        release_time="04:20",
        loaded_at=LOADED,
    )
    assert hours == 6.0


def test_datetime_release_is_time_until_that_instant() -> None:
    config = _config()
    config.ascent.release_weight.method = "datetime"
    config.ascent.release_weight.release_date = "2026-10-02"
    config.ascent.release_weight.release_time = "08:10"
    text = _receipt(
        config,
        release_date="2026-10-02",
        release_time="08:10",
        estimated_depth="0",
    )
    assert "Release weight                  2026-10-02 08:10 UTC" in text
    # 4 h from 04:10 to 08:10, plus the 45 min burn. Depth 0 has no rise.
    assert "Weight release starts           T+ 4h 00m" in text
    assert "Estimated surface: 4h 45m after deployment" in text
    assert "deployed immediately" in text


def test_datetime_release_in_the_past_has_no_bottom_time() -> None:
    config = _config()
    config.ascent.release_weight.method = "datetime"
    text = _receipt(
        config,
        release_date="2026-10-02",
        release_time="01:00",
        estimated_depth="0",
    )
    assert "planned bottom time is 0" in text
    assert "Estimated surface: 0h 45m after deployment" in text


def test_video_interval_and_interval_light_are_spelled_out() -> None:
    config = _config()
    config.bottom.camera = CameraSettings(
        enabled=True,
        camera_type=CameraType.VIDEO_INTERVAL,
        video_record=TimeValue(number="10", unit="seconds"),
        video_pause=TimeValue(number="5", unit="minutes"),
    )
    config.bottom.light = LightSettings(
        enabled=True,
        mode="interval",
        brightness=80,
        on_time=TimeValue(number="1", unit="minutes"),
        off_time=TimeValue(number="2", unit="minutes"),
        match_camera_interval=True,
    )
    config.ascent.same_as_descent = False
    config.ascent.camera = CameraSettings(enabled=False)
    config.ascent.light = LightSettings(enabled=False)
    text = _receipt(config, latitude=None, longitude=None)
    assert "video interval, record 10 seconds / pause 5 minutes" in text
    assert "on, 80%, interval 1 minute on / 2 minutes off, follows camera" in text
    assert "Camera                          off" in text
    assert "Location                        no GPS fix" in text
