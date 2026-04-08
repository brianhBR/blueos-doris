"""DORIS Backend - Main application entry point."""

import asyncio
import logging
import os

import httpx
from robyn import ALLOW_CORS, Robyn
from robyn.openapi import Contact, OpenAPI, OpenAPIInfo

from .config import settings
from .services.network import NetworkService
from .routes import (
    register_artemis_routes,
    register_attitude_routes,
    register_blueos_routes,
    register_configuration_routes,
    register_dive_routes,
    register_frame_routes,
    register_media_routes,
    register_mission_routes,
    register_network_routes,
    register_notification_routes,
    register_sensor_routes,
    register_system_routes,
)
from .services.external_storage import start_external_storage_setup
from .services.frame import FrameService
from .services.mdns import ensure_wifi_driver, restart_avahi, setup_doris_local, start_hotspot_dns
from .utils import deploy_artemis_svl, deploy_lua_scripts, disable_usb_autosuspend, restart_firmware


AGT_COMPONENT_ID = 191
AGT_REBOOT_DELAY_S = 3


async def _reboot_agt(logger: logging.Logger) -> None:
    """Send MAV_CMD_USER_5 (31014) to reboot the Artemis Global Tracker."""
    await asyncio.sleep(AGT_REBOOT_DELAY_S)

    from .config import blueos_services
    url = f"{blueos_services.mavlink2rest}/mavlink"
    payload = {
        "header": {"system_id": 255, "component_id": 0, "sequence": 0},
        "message": {
            "type": "COMMAND_LONG",
            "target_system": 1,
            "target_component": AGT_COMPONENT_ID,
            "command": {"type": "MAV_CMD_USER_5"},
            "confirmation": 0,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "param5": 0.0,
            "param6": 0.0,
            "param7": 0.0,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("AGT reboot command sent (MAV_CMD_USER_5)")
    except Exception as e:
        logger.warning("AGT reboot command failed (non-critical): %s", e)


def create_app() -> Robyn:
    """Create and configure the Robyn application."""

    # Create app with OpenAPI documentation
    app = Robyn(
        file_object=__file__,
        openapi=OpenAPI(
            info=OpenAPIInfo(
                title="DORIS API",
                description="Deep Ocean Research and Imaging System - BlueOS Extension API",
                version="0.1.0",
                contact=Contact(
                    name="Patrick José Pereira",
                    url="https://github.com/patrickelectric",
                    email="patrickelectric@gmail.com",
                ),
            ),
        ),
    )

    # Enable CORS for frontend
    ALLOW_CORS(app, origins=["*"])

    # Register BlueOS integration routes (must be first for /register_service)
    register_blueos_routes(app)

    # Register API routes
    register_system_routes(app)
    register_network_routes(app)
    register_sensor_routes(app)
    register_mission_routes(app)
    register_media_routes(app)
    register_configuration_routes(app)
    register_dive_routes(app)
    register_frame_routes(app)
    register_notification_routes(app)

    # Register WebSocket routes
    register_attitude_routes(app)
    register_artemis_routes(app)

    # Configure secondary WiFi interface as hotspot on startup
    logger = logging.getLogger(__name__)

    @app.startup_handler
    async def on_startup():
        disable_usb_autosuspend(logger)

        lua_deployed = deploy_lua_scripts(logger)
        deploy_artemis_svl(logger)

        if lua_deployed:
            await restart_firmware(logger)

        frame_service = FrameService()
        try:
            await frame_service.apply_frame_if_needed()
        except Exception as e:
            logger.warning("Frame setup skipped: %s", e)

        try:
            await ensure_wifi_driver()
        except Exception as e:
            logger.warning("WiFi driver check skipped: %s", e)

        try:
            await setup_doris_local()
        except Exception as e:
            logger.warning("doris.local setup skipped: %s", e)

        network_service = NetworkService()
        try:
            await network_service.configure_hotspot()
        except Exception as e:
            logger.warning("Hotspot configuration skipped: %s", e)

        try:
            await start_hotspot_dns()
        except Exception as e:
            logger.warning("Hotspot DNS skipped: %s", e)

        try:
            await restart_avahi()
        except Exception as e:
            logger.warning("Avahi restart skipped: %s", e)

        try:
            start_external_storage_setup()
        except Exception as e:
            logger.warning("External storage setup skipped: %s", e)

        # Reboot AGT after startup to fix boot-order GPS issue.
        # When the Pi and AGT power on simultaneously, the AGT may
        # start before the MAVLink router is ready, causing GPS data
        # to never flow.  A deferred reboot lets it reconnect cleanly.
        asyncio.get_event_loop().create_task(_reboot_agt(logger))

    # Serve frontend static files if they exist
    # Check multiple possible locations for frontend dist
    possible_paths = [
        # Docker container path
        "/app/frontend/dist",
        # Local development path (relative to this file)
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend", "dist")),
        # Alternative local path
        os.path.abspath(os.path.join(os.getcwd(), "frontend", "dist")),
    ]

    frontend_dist = None
    for path in possible_paths:
        if os.path.exists(path):
            frontend_dist = path
            break

    if frontend_dist:
        print(f"✅ Serving frontend from: {frontend_dist}")
        app.serve_directory(
            route="/",
            directory_path=frontend_dist,
            index_file="index.html",
            show_files_listing=False,
        )
    else:
        print(f"⚠️ Frontend not found in any of: {possible_paths}")

    return app


def run() -> None:
    """Run the DORIS backend server."""
    app = create_app()

    print_server_address = f"http://{settings.host}:{settings.port}".ljust(35)
    print_api_docs_address = f"http://{settings.host}:{settings.port}/docs".ljust(35)
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🐬 DORIS Backend Server                                     ║
║   Deep Ocean Research and Imaging System                      ║
║                                                               ║
║   Server: {print_server_address}                 ║
║   API Docs: {print_api_docs_address}               ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    app.start(
        host=settings.host,
        port=settings.port,
    )


# Allow running directly with: python -m doris.main
if __name__ == "__main__":
    run()

