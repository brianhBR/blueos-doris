"""Camera service."""

import logging

from ..config import blueos_services
from .base import BlueOSClient

logger = logging.getLogger(__name__)


class CameraSettings:
    """Camera configuration."""

    def __init__(
        self,
        resolution: str = "4K",
        frame_rate: int = 30,
        focus: str = "auto",
        brightness: int = 75,
    ):
        self.resolution = resolution
        self.frame_rate = frame_rate
        self.focus = focus
        self.brightness = brightness


class CameraService:
    """Service for managing camera and lighting."""

    def __init__(self):
        self.camera_manager = BlueOSClient(blueos_services.camera_manager)
        self.mavlink2rest = BlueOSClient(blueos_services.mavlink2rest)
        self._lgt_param_ok: bool | None = None

    async def get_cameras(self) -> list[dict]:
        """Get list of connected cameras."""
        try:
            cameras = await self.camera_manager.get("/cameras")
            return cameras
        except Exception:
            return []

    async def get_camera_settings(self, camera_id: str = "default") -> CameraSettings:
        """Get camera settings."""
        try:
            settings = await self.camera_manager.get(f"/cameras/{camera_id}/settings")
            return CameraSettings(
                resolution=settings.get("resolution", "4K"),
                frame_rate=settings.get("frame_rate", 30),
                focus=settings.get("focus", "auto"),
                brightness=settings.get("brightness", 75),
            )
        except Exception:
            return CameraSettings()

    async def set_camera_settings(
        self, camera_id: str = "default", settings: CameraSettings = None
    ) -> bool:
        """Update camera settings."""
        if settings is None:
            return False
        try:
            await self.camera_manager.put(
                f"/cameras/{camera_id}/settings",
                json={
                    "resolution": settings.resolution,
                    "frame_rate": settings.frame_rate,
                    "focus": settings.focus,
                    "brightness": settings.brightness,
                },
            )
            return True
        except Exception:
            return False

    async def start_recording(self, camera_id: str = "default") -> bool:
        """Start video recording."""
        try:
            await self.camera_manager.post(f"/cameras/{camera_id}/record/start")
            return True
        except Exception:
            return False

    async def stop_recording(self, camera_id: str = "default") -> bool:
        """Stop video recording."""
        try:
            await self.camera_manager.post(f"/cameras/{camera_id}/record/stop")
            return True
        except Exception:
            return False

    async def take_photo(self, camera_id: str = "default") -> str | None:
        """Take a photo and return the file path."""
        try:
            result = await self.camera_manager.post(f"/cameras/{camera_id}/photo")
            return result.get("path")
        except Exception:
            return None

    async def set_light_brightness(self, brightness: int) -> dict:
        """Set light brightness (0-100) via DORIS_LGT_TST parameter.

        The Lua script checks this parameter each cycle and drives
        RC9 (Lights 1) directly via set_override, which works even
        when disarmed.  Setting to 0 turns off the test.

        Returns ``{"ok": bool, "error": str | None}``.
        """
        import asyncio
        import httpx

        try:
            url = f"{self.mavlink2rest.base_url}/mavlink"
            payload = {
                "header": {"system_id": 255, "component_id": 0, "sequence": 0},
                "message": {
                    "type": "PARAM_SET",
                    "target_system": 1,
                    "target_component": 1,
                    "param_id": list("DORIS_LGT_TST".ljust(16, "\x00")),
                    "param_value": float(brightness),
                    "param_type": {"type": "MAV_PARAM_TYPE_REAL32"},
                },
            }
            logger.info("Light PARAM_SET: url=%s brightness=%s", url, brightness)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
            logger.info("Light PARAM_SET response: status=%s body=%r", resp.status_code, resp.text[:200])
            resp.raise_for_status()
        except Exception as e:
            logger.error("Light PARAM_SET failed: %s", e)
            return {"ok": False, "error": f"Failed to send PARAM_SET: {e}"}

        if brightness > 0 and not self._lgt_param_ok:
            verified = await self._verify_light_param()
            if not verified:
                return {
                    "ok": False,
                    "error": (
                        "DORIS_LGT_TST parameter not found on the flight controller. "
                        "Check that SCR_ENABLE=1 and doris.lua is loaded."
                    ),
                }

        return {"ok": True, "error": None}

    async def _verify_light_param(self) -> bool:
        """Request DORIS_LGT_TST back from the autopilot to confirm it exists."""
        import asyncio
        import httpx

        try:
            base = self.mavlink2rest.base_url
            req_payload = {
                "header": {"system_id": 255, "component_id": 0, "sequence": 0},
                "message": {
                    "type": "PARAM_REQUEST_READ",
                    "target_system": 1,
                    "target_component": 1,
                    "param_id": list("DORIS_LGT_TST".ljust(16, "\x00")),
                    "param_index": -1,
                },
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base}/mavlink", json=req_payload)
                resp.raise_for_status()

                await asyncio.sleep(0.4)

                resp = await client.get(
                    f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
                )
                resp.raise_for_status()
                data = resp.json()

            param_id = "".join(
                c for c in data.get("message", {}).get("param_id", []) if c != "\x00"
            )
            if param_id == "DORIS_LGT_TST":
                logger.info("DORIS_LGT_TST verified on flight controller")
                self._lgt_param_ok = True
                return True

            logger.warning(
                "DORIS_LGT_TST not found (got param_id=%r instead)", param_id
            )
            self._lgt_param_ok = False
            return False
        except Exception as e:
            logger.warning("DORIS_LGT_TST verification failed: %s", e)
            self._lgt_param_ok = False
            return False

    async def get_stream_url(self, camera_id: str = "default") -> str | None:
        """Get the video stream URL for a camera."""
        try:
            cameras = await self.camera_manager.get("/cameras")
            for cam in cameras:
                if cam.get("id") == camera_id or camera_id == "default":
                    return cam.get("stream_url")
        except Exception:
            pass
        return None

    async def get_snapshot(self, source: str | None = None) -> bytes | None:
        """Grab a single JPEG frame from the camera.

        Returns ``None`` immediately when the IP camera recorder is active
        to avoid opening a second RTSP session (most cameras allow only one).

        Tries (in order):
        1. MCM /thumbnail (works for v4l2/USB cameras)
        2. Direct HTTP snapshot from the IP camera (for RTSP/ONVIF cameras)
        3. ffmpeg RTSP single-frame grab (skipped when recorder is active)
        """
        import httpx as _httpx
        import logging as _logging
        import re as _re
        _logger = _logging.getLogger(__name__)

        from . import ip_camera_recorder as _iprec
        if _iprec._process is not None and _iprec._process.returncode is None:
            _logger.debug("Snapshot skipped — IP camera recorder is active")
            return None

        params: dict[str, str] = {"quality": "70"}
        if source:
            params["source"] = source

        # 1. Try MCM thumbnail endpoint (v4l2 cameras)
        for path in ("/thumbnail", "/snapshot"):
            try:
                resp = await self.camera_manager.client.get(path, params=params)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                    return resp.content
            except Exception:
                pass

        # 2. For RTSP/ONVIF cameras: discover the camera IP from MCM streams
        #    and try common HTTP snapshot URLs on the camera itself.
        camera_ips: list[str] = []
        try:
            streams = await self.camera_manager.client.get("/streams")
            if streams.status_code == 200:
                data = streams.json()
                items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for item in items:
                    vs = item.get("video_and_stream", {}).get("video_source", {})
                    onvif = vs.get("Onvif", {})
                    src = onvif.get("source", {})
                    rtsp_url = src.get("Onvif") or src.get("rtsp") or ""
                    m = _re.search(r"//(\d+\.\d+\.\d+\.\d+)", rtsp_url)
                    if m:
                        camera_ips.append(m.group(1))
        except Exception as e:
            _logger.debug("Could not discover camera IPs from MCM: %s", e)

        snapshot_paths = [
            "/cgi-bin/snapshot.cgi",
            "/webcapture.jpg?command=snap&channel=0",
            "/ISAPI/Streaming/channels/101/picture",
            "/onvif-http/snapshot",
            "/snapshot.jpg",
            "/shot.jpg",
            "/snap.jpg",
        ]
        for ip in camera_ips:
            for spath in snapshot_paths:
                try:
                    async with _httpx.AsyncClient(timeout=3.0) as client:
                        resp = await client.get(f"http://{ip}{spath}")
                    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                        _logger.info("Camera snapshot from http://%s%s", ip, spath)
                        return resp.content
                except Exception:
                    continue

        # 3. Last resort: grab a single frame via ffmpeg from MCM's RTSP relay.
        #    Uses MCM relay (port 8554) so we don't open a second session on the camera.
        rtsp_urls = await self._discover_rtsp_urls()
        for url in rtsp_urls:
            frame = await self._ffmpeg_snapshot(url)
            if frame:
                return frame

        return None

    async def _discover_rtsp_urls(self) -> list[str]:
        """Get RTSP URLs from MCM, preferring the local 8554 relay to avoid extra camera sessions."""
        import os as _os
        import re as _re
        host_ip = (
            _os.environ.get("DORIS_BLUEOS_ADDRESS", "http://host.docker.internal")
            .replace("http://", "").replace("https://", "").split(":")[0]
        )
        urls: list[str] = []
        try:
            streams = await self.camera_manager.client.get("/streams")
            if streams.status_code != 200:
                return urls
            data = streams.json()
            items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in items:
                endpoints = (
                    item.get("video_and_stream", {})
                    .get("stream_information", {})
                    .get("endpoints", [])
                )
                for ep in endpoints:
                    if "8554" in ep:
                        rewritten = _re.sub(
                            r"rtsp://[^:/]+:8554",
                            f"rtsp://{host_ip}:8554",
                            ep,
                        )
                        urls.append(rewritten)
        except Exception:
            pass
        return urls

    @staticmethod
    async def _ffmpeg_snapshot(rtsp_url: str) -> bytes | None:
        """Grab one JPEG frame from an RTSP URL using ffmpeg."""
        import asyncio as _asyncio
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        try:
            proc = await _asyncio.create_subprocess_exec(
                "ffmpeg", "-rtsp_transport", "tcp",
                "-i", rtsp_url,
                "-frames:v", "1",
                "-vf", "scale=1280:-1",
                "-q:v", "2",
                "-f", "image2", "pipe:1",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=8.0)
            if proc.returncode == 0 and stdout:
                _logger.info("Camera snapshot via ffmpeg from %s (%d bytes)", rtsp_url, len(stdout))
                return stdout
            _logger.debug("ffmpeg failed (rc=%s): %s", proc.returncode, stderr[:200] if stderr else "")
        except FileNotFoundError:
            _logger.debug("ffmpeg not installed, skipping RTSP snapshot")
        except Exception as e:
            _logger.debug("ffmpeg snapshot error: %s", e)
        return None

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.camera_manager.close()
        await self.mavlink2rest.close()

