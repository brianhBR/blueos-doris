"""Configuration settings for DORIS backend."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8095
    debug: bool = False

    # BlueOS settings - defaults to host.docker.internal for Docker
    # When running as BlueOS extension, use host.docker.internal to access BlueOS services
    # Set DORIS_BLUEOS_ADDRESS to override (e.g., http://192.168.2.2 for direct access)
    blueos_address: str = os.environ.get("DORIS_BLUEOS_ADDRESS", "http://host.docker.internal")

    # BlueOS service ports
    cable_guy_port: int = 9090
    camera_manager_port: int = 6020
    autopilot_manager_port: int = 8000
    commander_port: int = 9100
    bag_of_holding_port: int = 9101
    linux2rest_port: int = 6030
    mavlink_server_port: int = 8080
    version_chooser_port: int = 8081
    helper_port: int = 81
    ping_service_port: int = 9110
    beacon_service_port: int = 9111
    mavlink2rest_port: int = 6040
    bridget_port: int = 27353
    file_browser_port: int = 7777
    wifi_manager_port: int = 9000
    kraken_port: int = 9134
    nmea_injector_port: int = 2748
    recorder_extractor_port: int = 9150

    # IP camera recorder (RTSP -> segmented MPEG-TS via gst-launch; URL is hardcoded in service)
    ipcam_recordings_subdir: str = "userdata/ipcam_recordings"
    ipcam_segment_seconds_default: int = 1800

    # Removable USB for RTSP segments (same idea as BlueOS_videorecorder DropCam usb_storage)
    usb_mount_point: str = "/mnt/usb"
    usb_doris_folder: str = "DORIS"
    ipcam_usb_min_free_mb: float = 256.0
    usb_probe_interval_s: int = 30

    # ── External conductivity probe (AD5933 on i2c6) ────────────────
    # Opt-in: leave disabled until the probe is physically wired to the
    # Navigator's i2c6 bus (shared with the Bar100 @ 0x40 and Celsius
    # @ 0x77; the AD5933 lives at 0x0D so there is no address clash).
    # The DORIS container already has /dev/i2c access (Privileged +
    # /dev:/dev bind), so the probe is read directly and the result is
    # published as a NAMED_VALUE_FLOAT into the MAVLink stream — no
    # ArduSub firmware change required.
    conductivity_enabled: bool = False
    conductivity_i2c_bus: int = 6
    conductivity_ad5933_addr: int = 0x0D
    # Probe cal EEPROM (24-series) address; used only by the bench-only
    # /calibration/read helper, never by the live measurement loop.
    conductivity_eeprom_addr: int = 0x50
    # Excitation sweep: 70 kHz / range 3 are the modern CProbe defaults;
    # legacy units (serNo 4/7) used 47500 Hz / range 2.
    conductivity_frequency_hz: float = 70000.0
    conductivity_range: int = 3
    conductivity_settling_cycles: int = 128
    # ``gain`` is the per-probe AD5933 gain factor read once on the bench
    # from the probe's EEPROM (GetCoeffs()).  Stored here so the live bus
    # never has to touch the cal EEPROM @ 0x50.  Must be > 0 to publish a
    # physically meaningful conductance.
    conductivity_gain: float = 0.0
    # Optional linear calibration: conductivity_uScm = 1000*(CB*raw + CC).
    conductivity_apply_linear_cal: bool = False
    conductivity_cal_cb: float = 0.0
    conductivity_cal_cc: float = 0.0
    # Polling cadence and the NAMED_VALUE_FLOAT name (<=10 chars).
    conductivity_publish_interval_s: float = 1.0
    conductivity_named_float: str = "COND"
    # Source ids the injected NAMED_VALUE_FLOAT carries.  Defaults mirror
    # the autopilot's Lua named floats (sys 1 / comp 1) so the value lands
    # in the same telemetry namespace as DEPTH, BATT_V, etc.
    conductivity_src_system: int = 1
    conductivity_src_component: int = 1

    class Config:
        env_prefix = "DORIS_"
        env_file = ".env"


settings = Settings()


# BlueOS service URLs
class BlueOSServices:
    """BlueOS service URL builder."""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or settings.blueos_address

    def _url(self, port: int, path: str = "") -> str:
        return f"{self.base_url}:{port}{path}"

    @property
    def cable_guy(self) -> str:
        return self._url(settings.cable_guy_port)

    @property
    def camera_manager(self) -> str:
        return self._url(settings.camera_manager_port)

    @property
    def autopilot_manager(self) -> str:
        return self._url(settings.autopilot_manager_port)

    @property
    def commander(self) -> str:
        return self._url(settings.commander_port)

    @property
    def bag_of_holding(self) -> str:
        return self._url(settings.bag_of_holding_port)

    @property
    def linux2rest(self) -> str:
        return self._url(settings.linux2rest_port)

    @property
    def mavlink_server(self) -> str:
        return self._url(settings.mavlink_server_port)

    @property
    def version_chooser(self) -> str:
        return self._url(settings.version_chooser_port)

    @property
    def helper(self) -> str:
        return self._url(settings.helper_port)

    @property
    def ping_service(self) -> str:
        return self._url(settings.ping_service_port)

    @property
    def beacon_service(self) -> str:
        return self._url(settings.beacon_service_port)

    @property
    def mavlink2rest(self) -> str:
        return self._url(settings.mavlink2rest_port)

    @property
    def bridget(self) -> str:
        return self._url(settings.bridget_port)

    @property
    def file_browser(self) -> str:
        return self._url(settings.file_browser_port)

    @property
    def wifi_manager(self) -> str:
        return self._url(settings.wifi_manager_port)

    @property
    def kraken(self) -> str:
        return self._url(settings.kraken_port)

    @property
    def nmea_injector(self) -> str:
        return self._url(settings.nmea_injector_port)

    @property
    def recorder_extractor(self) -> str:
        return self._url(settings.recorder_extractor_port)


blueos_services = BlueOSServices()

