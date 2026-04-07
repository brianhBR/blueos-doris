"""Mission API routes."""

import json
from datetime import datetime, timezone

from robyn import Response, Robyn

from ..models.missions import (
    CameraSettings,
    Mission,
    MissionConfig,
    MissionStatus,
    MissionSummary,
    TriggerConfig,
    TriggerType,
)

# In-memory mission storage (replace with database in production)
_missions: dict[str, Mission] = {}
_mission_counter = 0


def register_mission_routes(app: Robyn) -> None:
    """Register mission-related API routes."""

    @app.get("/api/v1/missions")
    async def get_missions(request):
        """Get list of all missions."""
        try:
            status_filter = request.query_params.get("status")

            missions = list(_missions.values())

            if status_filter:
                missions = [m for m in missions if m.status.value == status_filter]

            # Sort by created_at descending
            missions.sort(key=lambda m: m.created_at, reverse=True)

            # Return summaries
            summaries = []
            for m in missions:
                duration = ""
                if m.duration_seconds:
                    hours = m.duration_seconds // 3600
                    minutes = (m.duration_seconds % 3600) // 60
                    duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"

                summaries.append(
                    MissionSummary(
                        id=m.id,
                        name=m.name,
                        status=m.status,
                        date=m.created_at,
                        duration=duration,
                        location=m.location,
                        max_depth=m.max_depth,
                        image_count=m.image_count,
                        video_count=m.video_count,
                    )
                )

            return json.dumps([s.model_dump(mode="json") for s in summaries])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/missions/:mission_id")
    async def get_mission(request):
        """Get a specific mission by ID."""
        try:
            mission_id = request.path_params.get("mission_id")

            if mission_id in _missions:
                return json.dumps(_missions[mission_id].model_dump(mode="json"))

            return Response(
                status_code=404,
                description=json.dumps({"error": "Mission not found"}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/missions")
    async def create_mission(request):
        """Create a new mission."""
        global _mission_counter
        try:
            data = json.loads(request.body)

            # Parse trigger configs
            start_trigger = TriggerConfig(
                trigger_type=TriggerType(data.get("start_trigger", {}).get("trigger_type", "manual")),
                value=data.get("start_trigger", {}).get("value"),
                unit=data.get("start_trigger", {}).get("unit"),
            )

            end_trigger = TriggerConfig(
                trigger_type=TriggerType(data.get("end_trigger", {}).get("trigger_type", "duration")),
                value=data.get("end_trigger", {}).get("value", 60),
                unit=data.get("end_trigger", {}).get("unit", "seconds"),
            )

            camera_data = data.get("camera_settings", {})
            camera_settings = CameraSettings(
                resolution=camera_data.get("resolution", "4K"),
                frame_rate=camera_data.get("frame_rate", 30),
                focus=camera_data.get("focus", "auto"),
            )

            config = MissionConfig(
                name=data.get("name", "Untitled Mission"),
                start_trigger=start_trigger,
                end_trigger=end_trigger,
                timelapse_enabled=data.get("timelapse_enabled", False),
                timelapse_interval=data.get("timelapse_interval", 60),
                camera_settings=camera_settings,
                lighting_brightness=data.get("lighting_brightness", 75),
                sensors_enabled=data.get("sensors_enabled", []),
            )

            _mission_counter += 1
            mission_id = f"mission-{_mission_counter:03d}"

            mission = Mission(
                id=mission_id,
                name=config.name,
                status=MissionStatus.PENDING,
                config=config,
                created_at=datetime.now(timezone.utc),
            )

            _missions[mission_id] = mission

            return Response(
                status_code=201,
                description=json.dumps(mission.model_dump(mode="json")),
                headers={"Content-Type": "application/json"},
            )
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

    @app.post("/api/v1/missions/:mission_id/start")
    async def start_mission(request):
        """Start a pending mission."""
        try:
            mission_id = request.path_params.get("mission_id")

            if mission_id not in _missions:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": "Mission not found"}),
                    headers={"Content-Type": "application/json"},
                )

            mission = _missions[mission_id]

            if mission.status != MissionStatus.PENDING:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Mission is not in pending state"}),
                    headers={"Content-Type": "application/json"},
                )

            mission.status = MissionStatus.ACTIVE
            mission.started_at = datetime.now(timezone.utc)

            return json.dumps(mission.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/missions/:mission_id/stop")
    async def stop_mission(request):
        """Stop an active mission."""
        try:
            mission_id = request.path_params.get("mission_id")

            if mission_id not in _missions:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": "Mission not found"}),
                    headers={"Content-Type": "application/json"},
                )

            mission = _missions[mission_id]

            if mission.status != MissionStatus.ACTIVE:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Mission is not active"}),
                    headers={"Content-Type": "application/json"},
                )

            mission.status = MissionStatus.COMPLETED
            mission.completed_at = datetime.now(timezone.utc)

            if mission.started_at:
                mission.duration_seconds = int(
                    (mission.completed_at - mission.started_at).total_seconds()
                )

            return json.dumps(mission.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.delete("/api/v1/missions/:mission_id")
    async def delete_mission(request):
        """Delete a mission."""
        try:
            mission_id = request.path_params.get("mission_id")

            if mission_id not in _missions:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": "Mission not found"}),
                    headers={"Content-Type": "application/json"},
                )

            del _missions[mission_id]

            return json.dumps({"success": True})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

