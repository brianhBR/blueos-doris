"""Sensor API routes."""

import json

from robyn import Response, Robyn

from ..models.sensors import SensorConfig
from ..services.barometer import BarometerService
from ..services.camera import CameraService
from ..services.release import release_service
from ..services.sensors import SensorService
from ..services.tracker import tracker_service


def register_sensor_routes(app: Robyn) -> None:
    """Register sensor-related API routes."""

    sensor_service = SensorService()
    camera_service = CameraService()
    barometer_service = BarometerService()

    @app.get("/api/v1/sensors/modules")
    async def get_connected_modules(request):
        """Get all connected modules (cameras, sensors, lights)."""
        try:
            modules = await sensor_service.get_connected_modules()
            return json.dumps([m.model_dump(mode="json") for m in modules])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/sensors/streams")
    async def get_video_streams(request):
        """Get all available video streams from the Camera Manager."""
        try:
            streams = await sensor_service.get_video_streams()
            return json.dumps([s.model_dump(mode="json") for s in streams])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/camera/snapshot")
    async def camera_snapshot(request):
        """Single JPEG frame from the IP camera for the sensor-page preview.

        Uses the camera's built-in HTTP snapshot CGI directly (the same
        path :func:`ip_camera_recorder.take_phase_snapshot` uses for the
        TIMELAPSE mode).  No MCM, no ffmpeg, no RTSP -- a plain HTTP GET
        that returns in ~200 ms.  This means the preview works even
        when the BlueOS Camera Manager stream is stopped, as long as
        the camera itself is reachable on the network.

        Returns 409 while the IP camera recorder is active because the
        camera serves only one RTSP/snapshot session at a time and a
        preview poll would interrupt the recording.
        """
        from doris.services import ip_camera_recorder as _iprec
        if _iprec.is_recording():
            return Response(
                status_code=409,
                description=json.dumps({
                    "error": "Snapshot disabled while IP camera recorder is active",
                    "reason": "recorder_active",
                }),
                headers={"Content-Type": "application/json"},
            )
        try:
            data, reason = await _iprec.fetch_camera_jpeg()
        except Exception as e:  # pragma: no cover - defensive
            data, reason = None, f"unexpected_error: {e}"
        if data is None:
            return Response(
                status_code=502,
                description=json.dumps({
                    "error": f"Camera snapshot failed: {reason}",
                    "url": _iprec.IPCAM_SNAPSHOT_URL,
                }),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=data,
            headers={
                "Content-Type": "image/jpeg",
                "Cache-Control": "no-cache",
                "Content-Length": str(len(data)),
            },
        )

    @app.post("/api/v1/lights/brightness")
    async def set_light_brightness(request):
        """Set light brightness (0-100). Used for momentary test button."""
        try:
            data = json.loads(request.body) if request.body else {}
            brightness = max(0, min(100, int(data.get("brightness", 0))))
            result = await camera_service.set_light_brightness(brightness)
            ok = result.get("ok", False)
            payload = {"success": ok, "brightness": brightness}
            if not ok and result.get("error"):
                payload["error"] = result["error"]
            status = 200 if ok else 502
            return Response(
                status_code=status,
                description=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )


    @app.post("/api/v1/release/test")
    async def set_release_test(request):
        """Hold the weight release energised for the momentary test button.

        Lua drops the output ~10 s after the last request, so the caller has
        to keep re-asserting ``active`` for as long as it wants the test.
        """
        try:
            data = json.loads(request.body) if request.body else {}
            active = bool(data.get("active", False))
            result = await release_service.set_release_test(active)
            ok = result.get("ok", False)
            payload = {"success": ok, "active": active}
            if not ok and result.get("error"):
                payload["error"] = result["error"]
            return Response(
                status_code=200 if ok else 502,
                description=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/sensors/barometer/calibrate")
    async def calibrate_barometer(request):
        """Trigger a surface pressure calibration via MAVLink."""
        try:
            result = await barometer_service.calibrate_surface()
            status = 200 if result.get("success") else 502
            return Response(
                status_code=status,
                description=json.dumps(result),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/tracker/gps")
    async def get_tracker_gps(request):
        """Get GPS data from the Artemis Global Tracker."""
        try:
            gps = await tracker_service.get_gps_data()
            if gps is None:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": "Tracker not connected or no GPS data"}),
                    headers={"Content-Type": "application/json"},
                )
            return json.dumps(gps)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/tracker/version")
    async def get_tracker_version(request):
        """Return the AGT firmware version, IMEI and compatibility status.

        Returns ``{version, imei, min_required, compatible, known}``.  When
        the version isn't cached yet, an AGT_DEBUG (MAV_CMD_USER_3) request
        is dispatched and briefly awaited unless ``request=false`` is passed.
        """
        try:
            do_request = request.query_params.get("request", "true") != "false"
            version = await tracker_service.get_version(request=do_request)
            return json.dumps(version)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/tracker/iridium-test")
    async def trigger_iridium_test(request):
        """Send COMMAND_LONG to AGT to trigger Iridium test."""
        try:
            result = await tracker_service.send_iridium_test()
            return json.dumps(result)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/tracker/debug")
    async def trigger_debug(request):
        """Send AGT_DEBUG (MAV_CMD_USER_3) to dump version + IMEI + GPS diag.

        Returns ``{accepted, error, latest_id}``.  The AGT's reply burst is
        buffered like any other STATUSTEXT, so the frontend polls
        ``/api/v1/tracker/iridium-status?since_id=<latest_id>`` for the
        ``Doris AGT ...`` / ``GPS: ...`` lines.
        """
        try:
            result = await tracker_service.send_debug()
            return json.dumps(result)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/tracker/iridium-status")
    async def get_iridium_status(request):
        """Poll AGT STATUSTEXT messages newer than ``since_id``.

        Returns ``{messages: [...], latest_id: int}`` where each message
        has ``{id, text, severity, timestamp}``.  The frontend feeds the
        returned ``latest_id`` back as ``since_id`` on the next call so
        no STATUSTEXT can be missed even if mavlink2rest's HTTP cache
        moves on between polls.
        """
        try:
            try:
                since_id = int(request.query_params.get("since_id", "0"))
            except (TypeError, ValueError):
                since_id = 0
            status = await tracker_service.get_iridium_status(since_id=since_id)
            return json.dumps(status)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/sensors/:sensor_id/readings")
    async def get_sensor_readings(request):
        """Get recent readings from a specific sensor."""
        try:
            sensor_id = request.path_params.get("sensor_id")
            readings = await sensor_service.get_sensor_readings(sensor_id)
            return json.dumps([r.model_dump(mode="json") for r in readings])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.put("/api/v1/sensors/:sensor_id/config")
    async def configure_sensor(request):
        """Update sensor configuration."""
        try:
            sensor_id = request.path_params.get("sensor_id")
            data = json.loads(request.body)

            config = SensorConfig(
                sensor_id=sensor_id,
                sample_rate=data.get("sample_rate", 1.0),
                enabled=data.get("enabled", True),
                calibration_file=data.get("calibration_file"),
            )

            success = await sensor_service.configure_sensor(config)
            return json.dumps({"success": success})
        except json.JSONDecodeError:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON"}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
