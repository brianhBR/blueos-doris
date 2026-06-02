"""Vehicle arming-status API route.

Exposes the autopilot's armed state and any failing pre-arm checks so the
frontend can show a "waiting to arm" banner with the reason (issues #44,
#8).
"""

import json

from robyn import Response, Robyn

from ..services.arming import ArmingService


def register_arming_routes(app: Robyn) -> None:
    """Register the vehicle arming-status endpoint."""

    arming_service = ArmingService()

    @app.get("/api/v1/vehicle/arming")
    async def get_arming_status(request):
        """Return armed state plus current failing pre-arm checks."""
        try:
            status = await arming_service.get_status()
            return json.dumps(status)
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
