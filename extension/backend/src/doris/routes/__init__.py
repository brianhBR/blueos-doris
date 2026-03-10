"""API routes for DORIS backend."""

from .blueos import register_blueos_routes
from .media import register_media_routes
from .missions import register_mission_routes
from .network import register_network_routes
from .sensors import register_sensor_routes
from .system import register_system_routes

__all__ = [
    "register_blueos_routes",
    "register_system_routes",
    "register_network_routes",
    "register_sensor_routes",
    "register_mission_routes",
    "register_media_routes",
]

