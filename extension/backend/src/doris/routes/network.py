"""Network API routes."""

import json

from robyn import Response, Robyn

from ..models.network import NetworkCredentials
from ..services.network import NetworkService


def register_network_routes(app: Robyn) -> None:
    """Register network-related API routes."""

    network_service = NetworkService()

    @app.get("/api/v1/network")
    async def get_network_info(request):
        """Get current network information and available networks."""
        try:
            info = await network_service.get_network_info()
            return json.dumps(info.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/network/status")
    async def get_connection_status(request):
        """Get current connection status."""
        try:
            status = await network_service.get_connection_status()
            return json.dumps(status.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/network/scan")
    async def scan_networks(request):
        """Scan for available WiFi networks."""
        try:
            networks = await network_service.scan_networks()
            return json.dumps([n.model_dump(mode="json") for n in networks])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/network/connect")
    async def connect_to_network(request):
        """Connect to a WiFi network."""
        try:
            data = json.loads(request.body)
            credentials = NetworkCredentials(
                ssid=data.get("ssid"),
                password=data.get("password"),
            )
            status = await network_service.connect(credentials)
            return json.dumps(status.model_dump(mode="json"))
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

    @app.post("/api/v1/network/disconnect")
    async def disconnect_from_network(request):
        """Disconnect from current network."""
        try:
            status = await network_service.disconnect()
            return json.dumps(status.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.delete("/api/v1/network/saved/:ssid")
    async def forget_network(request):
        """Forget a saved network."""
        try:
            ssid = request.path_params.get("ssid")
            success = await network_service.forget_network(ssid)
            return json.dumps({"success": success})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
