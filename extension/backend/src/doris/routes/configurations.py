"""Configuration API routes."""

import json
import logging
from urllib.parse import unquote

from robyn import Response, Robyn

from ..models.configuration import CameraType, DeploymentConfiguration
from ..services.storage import StorageService

logger = logging.getLogger(__name__)


def _coerce_non_bottom_camera_modes(config: DeploymentConfiguration) -> None:
    """Coerce descent/ascent camera modes to CONTINUOUS_VIDEO if they are set
    to TIMELAPSE or VIDEO_INTERVAL.

    The Lua dispatcher only implements timelapse + video-interval for the
    bottom phase (see scripts/doris.lua and services/dive._ipcam_phase_enabled,
    which only returns 1.0 when camera_type == CONTINUOUS_VIDEO). For descent
    and ascent it only honours a single record-the-whole-phase boolean. The UI
    now hides those modes for non-bottom phases, but coerce here as a
    belt-and-braces guard against direct API submissions of legacy or
    hand-crafted payloads.
    """
    invalid_modes = (CameraType.TIMELAPSE, CameraType.VIDEO_INTERVAL)
    for phase_name in ("descent", "ascent"):
        phase = getattr(config, phase_name)
        if phase.camera.camera_type in invalid_modes:
            logger.warning(
                "Coercing %s camera_type %s -> continuous-video "
                "(only supported for bottom phase)",
                phase_name,
                phase.camera.camera_type,
            )
            phase.camera.camera_type = CameraType.CONTINUOUS_VIDEO


def register_configuration_routes(app: Robyn) -> None:
    """Register configuration CRUD API routes."""

    storage_service = StorageService()

    @app.get("/api/v1/configurations")
    async def list_configurations(request):
        """List all saved configurations."""
        try:
            configs = await storage_service.list_configurations()
            return json.dumps([c.model_dump(mode="json") for c in configs])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/configurations/:name")
    async def get_configuration(request):
        """Load a configuration by name."""
        try:
            name = unquote(request.path_params.get("name", ""))
            if not name:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Missing configuration name"}),
                    headers={"Content-Type": "application/json"},
                )

            config = await storage_service.load_configuration(name)
            if config is None:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": f"Configuration '{name}' not found"}),
                    headers={"Content-Type": "application/json"},
                )

            return config.model_dump_json()
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/configurations")
    async def save_configuration(request):
        """Save a new or overwrite an existing configuration."""
        try:
            data = json.loads(request.body)
            config = DeploymentConfiguration.model_validate(data)
            _coerce_non_bottom_camera_modes(config)
            saved = await storage_service.save_configuration(config)
            return saved.model_dump_json()
        except json.JSONDecodeError:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON"}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=400,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.delete("/api/v1/configurations/:name")
    async def delete_configuration(request):
        """Delete a configuration by name."""
        try:
            name = unquote(request.path_params.get("name", ""))
            if not name:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Missing configuration name"}),
                    headers={"Content-Type": "application/json"},
                )

            deleted = await storage_service.delete_configuration(name)
            if not deleted:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": f"Configuration '{name}' not found"}),
                    headers={"Content-Type": "application/json"},
                )

            return json.dumps({"success": True})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
