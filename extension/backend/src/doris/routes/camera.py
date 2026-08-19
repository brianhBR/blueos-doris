"""Camera settings + preset API routes.

Live settings and the preset library are backed by the br4kcam-manager
proxy and on-disk persistence.  Preset payloads use the camera's native
JSON keys so downloaded files re-apply directly.
"""

import json
import logging
from urllib.parse import unquote

from robyn import Response, Robyn

from ..models.camera import CameraPreset, CameraSettingsBundle
from ..services.br4kcam import Br4kcamError
from ..services.camera_presets import CameraPresetService, NoCameraError

logger = logging.getLogger(__name__)


def _json(payload: str | dict | list, status_code: int = 200) -> Response:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return Response(
        status_code=status_code,
        description=body,
        headers={"Content-Type": "application/json"},
    )


def _error(message: str, status_code: int = 500) -> Response:
    return _json({"error": message}, status_code)


def _camera_error_status(exc: Exception) -> int:
    """502 when the manager/camera is unreachable, 404 when no camera."""
    if isinstance(exc, NoCameraError):
        return 404
    if isinstance(exc, Br4kcamError):
        return 502
    return 500


def register_camera_routes(app: Robyn) -> None:
    """Register /api/v1/camera/* settings and preset endpoints."""

    svc = CameraPresetService()
    storage = svc.storage

    # ── discovery + live settings ────────────────────────────────
    @app.get("/api/v1/camera/cameras")
    async def list_cameras(request):
        try:
            data = await svc.client.list_cameras()
            return _json(data)
        except Br4kcamError as e:
            return _error(str(e), 502)

    @app.get("/api/v1/camera/settings")
    async def get_settings(request):
        try:
            bundle = await svc.read_live()
            return _json(bundle.model_dump_json(by_alias=True))
        except Exception as e:
            logger.warning("camera get_settings failed: %s", e)
            return _error(str(e), _camera_error_status(e))

    @app.post("/api/v1/camera/settings")
    async def apply_settings(request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return _error("Invalid JSON", 400)
        try:
            bundle = CameraSettingsBundle.model_validate(data)
        except Exception as e:
            return _error(str(e), 400)
        try:
            fresh = await svc.apply_bundle(bundle)
            return _json(fresh.model_dump_json(by_alias=True))
        except Exception as e:
            logger.warning("camera apply_settings failed: %s", e)
            return _error(str(e), _camera_error_status(e))

    @app.post("/api/v1/camera/settings/recommended")
    async def apply_recommended(request):
        try:
            uuid = await svc._require_uuid()
            result = await svc.client.apply_recommended(uuid)
            return _json({"success": True, "result": result})
        except Exception as e:
            logger.warning("camera apply_recommended failed: %s", e)
            return _error(str(e), _camera_error_status(e))

    @app.post("/api/v1/camera/restart")
    async def restart_camera(request):
        try:
            uuid = await svc._require_uuid()
            result = await svc.client.restart(uuid)
            return _json({"success": True, "result": result})
        except Exception as e:
            logger.warning("camera restart failed: %s", e)
            return _error(str(e), _camera_error_status(e))

    # ── preset library ───────────────────────────────────────────
    @app.get("/api/v1/camera/presets")
    async def list_presets(request):
        try:
            presets = await storage.list_camera_presets()
            return _json([p.model_dump(mode="json") for p in presets])
        except Exception as e:
            return _error(str(e), 500)

    @app.post("/api/v1/camera/presets")
    async def create_preset(request):
        """Create/overwrite a preset from a body bundle, or snapshot the camera.

        Body ``{"name": "...", "snapshot": true}`` reads the live camera
        settings; otherwise the body is a full preset (name + groups).
        """
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return _error("Invalid JSON", 400)
        name = (data.get("name") or "").strip()
        if not name:
            return _error("Missing preset name", 400)
        try:
            if data.get("snapshot"):
                saved = await svc.snapshot_to_preset(name)
            else:
                preset = CameraPreset.model_validate(data)
                saved = await storage.save_camera_preset(preset)
            return _json(saved.model_dump_json(by_alias=True))
        except (Br4kcamError, NoCameraError) as e:
            return _error(str(e), _camera_error_status(e))
        except Exception as e:
            return _error(str(e), 400)

    @app.post("/api/v1/camera/presets/import")
    async def import_preset(request):
        """Import a preset from an uploaded JSON file body."""
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return _error("Invalid JSON", 400)
        try:
            preset = CameraPreset.model_validate(data)
            saved = await storage.save_camera_preset(preset)
            return _json(saved.model_dump_json(by_alias=True))
        except Exception as e:
            return _error(f"Invalid preset file: {e}", 400)

    @app.get("/api/v1/camera/presets/:name")
    async def get_preset(request):
        name = unquote(request.path_params.get("name", ""))
        if not name:
            return _error("Missing preset name", 400)
        preset = await storage.load_camera_preset(name)
        if preset is None:
            return _error(f"Preset '{name}' not found", 404)
        return _json(preset.model_dump_json(by_alias=True))

    @app.put("/api/v1/camera/presets/:name")
    async def update_preset(request):
        name = unquote(request.path_params.get("name", ""))
        if not name:
            return _error("Missing preset name", 400)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return _error("Invalid JSON", 400)
        # Path name wins over any name in the body.
        data["name"] = name
        try:
            preset = CameraPreset.model_validate(data)
            saved = await storage.save_camera_preset(preset)
            return _json(saved.model_dump_json(by_alias=True))
        except Exception as e:
            return _error(str(e), 400)

    @app.delete("/api/v1/camera/presets/:name")
    async def delete_preset(request):
        name = unquote(request.path_params.get("name", ""))
        if not name:
            return _error("Missing preset name", 400)
        deleted = await storage.delete_camera_preset(name)
        if not deleted:
            return _error(f"Preset '{name}' not found", 404)
        return _json({"success": True})

    @app.post("/api/v1/camera/presets/:name/apply")
    async def apply_preset(request):
        name = unquote(request.path_params.get("name", ""))
        if not name:
            return _error("Missing preset name", 400)
        try:
            fresh = await svc.apply_preset(name)
            if fresh is None:
                return _error(f"Preset '{name}' not found", 404)
            return _json(fresh.model_dump_json(by_alias=True))
        except Exception as e:
            logger.warning("camera apply_preset failed: %s", e)
            return _error(str(e), _camera_error_status(e))

    @app.get("/api/v1/camera/presets/:name/download")
    async def download_preset(request):
        name = unquote(request.path_params.get("name", ""))
        if not name:
            return _error("Missing preset name", 400)
        preset = await storage.load_camera_preset(name)
        if preset is None:
            return _error(f"Preset '{name}' not found", 404)
        filename = f"{storage._slug(name)}.doris-campreset.json"
        return Response(
            status_code=200,
            description=preset.model_dump_json(by_alias=True, indent=2),
            headers={
                "Content-Type": "application/json",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # ── active preset pointer ────────────────────────────────────
    @app.get("/api/v1/camera/active-preset")
    async def get_active_preset(request):
        active = await storage.get_active_preset()
        return _json(active.model_dump_json())

    @app.put("/api/v1/camera/active-preset")
    async def set_active_preset(request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return _error("Invalid JSON", 400)
        name = data.get("name")
        if name is not None:
            name = str(name).strip() or None
        active = await storage.set_active_preset(name)
        return _json(active.model_dump_json())
