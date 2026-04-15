"""Log viewing and download API routes."""

import json
import logging

from robyn import Response, Robyn

from ..services.persistent_log import list_log_files, read_log_bytes, read_log_file

logger = logging.getLogger(__name__)


def register_log_routes(app: Robyn) -> None:
    """Register log-related API routes."""

    @app.get("/api/v1/system/logs")
    async def get_log_files(request):
        """List available log files with sizes and timestamps."""
        try:
            files = list_log_files()
            return json.dumps({"files": files})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/system/logs/view")
    async def view_log(request):
        """View the tail of a log file.

        Query params:
            name  – log file name (default: doris.log)
            lines – number of tail lines (default: 200)
        """
        params = request.query_params
        name = params.get("name", "doris.log")
        try:
            lines = int(params.get("lines", "200"))
        except (TypeError, ValueError):
            lines = 200

        content = read_log_file(name, tail_lines=lines)
        if content is None:
            return Response(
                status_code=404,
                description=json.dumps({"error": f"Log file '{name}' not found"}),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=content,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.get("/api/v1/system/logs/download")
    async def download_log(request):
        """Download a full log file.

        Query params:
            name – log file name (default: doris.log)
        """
        params = request.query_params
        name = params.get("name", "doris.log")

        data = read_log_bytes(name)
        if data is None:
            return Response(
                status_code=404,
                description=json.dumps({"error": f"Log file '{name}' not found"}),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=data.decode("utf-8", errors="replace"),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
            },
        )
