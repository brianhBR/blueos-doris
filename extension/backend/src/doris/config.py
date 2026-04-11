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

    # IP camera recorder (RTSP -> segmented MPEG-TS via ffmpeg; URL is hardcoded in service)
    ipcam_recordings_subdir: str = "userdata/ipcam_recordings"
    ipcam_segment_seconds_default: int = 300
    # copy = remux only (same idea as gst rtspsrc/depay/parse/mux — no re-encode, low CPU).
    # libx264 = transcode to H.264 (heavy; only if you need H.264 in file from non-H.264 RTSP).
    ipcam_video_codec: str = "copy"
    ipcam_x264_preset: str = "veryfast"

    # Removable USB for RTSP segments (same idea as BlueOS_videorecorder DropCam usb_storage)
    usb_mount_point: str = "/mnt/usb"
    usb_doris_folder: str = "DORIS"
    ipcam_usb_min_free_mb: float = 256.0
    usb_probe_interval_s: int = 30

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

