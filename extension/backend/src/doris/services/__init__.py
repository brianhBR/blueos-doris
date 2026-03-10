"""BlueOS service clients."""

from .base import BlueOSClient
from .camera import CameraService
from .network import NetworkService
from .sensors import SensorService
from .storage import StorageService
from .system import SystemService

__all__ = [
    "BlueOSClient",
    "SystemService",
    "NetworkService",
    "SensorService",
    "CameraService",
    "StorageService",
]

