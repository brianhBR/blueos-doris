"""DORIS Backend - Main application entry point."""

import os

from robyn import ALLOW_CORS, Robyn
from robyn.openapi import Contact, OpenAPI, OpenAPIInfo

from .config import settings
from .routes import (
    register_blueos_routes,
    register_media_routes,
    register_mission_routes,
    register_network_routes,
    register_sensor_routes,
    register_system_routes,
)


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

    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🐬 DORIS Backend Server                                     ║
║   Deep Ocean Research and Imaging System                      ║
║                                                               ║
║   Server:  http://{settings.host}:{settings.port}                          ║
║   API Docs: http://{settings.host}:{settings.port}/docs                     ║
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

