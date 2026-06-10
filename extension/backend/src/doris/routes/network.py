"""Network API routes."""

import json

from robyn import Response, Robyn

from ..models.network import NetworkCredentials
from ..services.network import get_network_service


def register_network_routes(app: Robyn) -> None:
    """Register network-related API routes."""

    network_service = get_network_service()

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

    # ── WLAN AP <-> STA mode switching ───────────────────────────────
    #
    # The external Realtek radio (uap0) is the only reliable interface
    # in the assembled vehicle. These three endpoints let the user flip
    # it between hotspot mode (default) and client (STA) mode without
    # having to power-cycle to recover, since the actual mode-flip
    # happens asynchronously and the user's browser session dies the
    # moment the AP goes down. The status endpoint is what the
    # post-failure UI polls when the user reconnects to the hotspot.

    @app.get("/api/v1/network/wlan/status")
    async def get_wlan_status(request):
        """Return AP/STA intent and the most recent switch attempt."""
        try:
            state = await network_service.get_wlan_state()
            return json.dumps(state.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/network/wlan/connect")
    async def switch_to_sta(request):
        """Initiate an asynchronous switch from AP mode to STA mode.

        Returns immediately with the new ``sta_pending`` state. The
        actual outcome is observable via ``GET /network/wlan/status``
        after the user's browser regains a connection (either by
        finding DORIS on the new WLAN or by reconnecting to the
        restored hotspot on failure).
        """
        try:
            data = json.loads(request.body)
            ssid = data.get("ssid")
            password = data.get("password") or ""
            if not ssid:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "ssid is required"}),
                    headers={"Content-Type": "application/json"},
                )
            state = await network_service.begin_switch_to_sta(ssid, password)
            return json.dumps(state.model_dump(mode="json"))
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

    @app.post("/api/v1/network/wlan/disconnect")
    async def switch_to_ap(request):
        """Tear down any STA association and put the external radio
        back into hotspot mode. Returns immediately."""
        try:
            state = await network_service.begin_switch_to_ap()
            return json.dumps(state.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
