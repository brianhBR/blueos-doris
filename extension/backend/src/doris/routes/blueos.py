"""BlueOS integration routes."""

import json

from robyn import Robyn

# Service metadata for BlueOS Helper discovery
SERVICE_METADATA = {
    "name": "DORIS",
    "description": "Deep Ocean Research and Imaging System",
    "icon": "mdi-submarine",
    "company": "Blue Robotics",
    "version": "1.0.0",
    "webpage": "/",
    "api": "/docs",
    "new_page": False,
}


def register_blueos_routes(app: Robyn) -> None:
    """Register BlueOS integration routes."""

    @app.get("/register_service")
    async def register_service(request):
        """
        BlueOS service registration endpoint.

        BlueOS Helper calls this endpoint to discover extension metadata.
        Returns service information for display in BlueOS UI.
        """
        return json.dumps(SERVICE_METADATA)


