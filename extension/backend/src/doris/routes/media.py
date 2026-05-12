"""Media API routes."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote, unquote

from robyn import Response, Robyn

from ..models.media import MediaType
from ..services.storage import StorageService, USB_MEDIA_PREFIX, media_abs_path_from_download_id

# Where the static streaming endpoints live. The actual file streaming is
# handled by Robyn's ``serve_directory`` (backed by ``actix_files::Files``)
# which the application mounts at startup; this module just emits redirects
# to those URLs from the canonical, query-style download endpoint.
_RAW_INTERNAL_PREFIX = "/api/v1/media/raw/internal"
_RAW_USB_PORTABLE_PREFIX = "/api/v1/media/raw/usb"
_RAW_USB_HOST_MNT_PREFIX = "/api/v1/media/raw/host_mnt"

_USB_PORTABLE_BASE = Path(os.environ.get("DORIS_USB_MOUNT_POINT", "/mnt/usb"))
_USB_HOST_MNT_BASE = Path("/mnt")


def _quote_segments(rel: str) -> str:
    """URL-encode a relative path piece-by-piece, preserving "/" separators."""
    return "/".join(quote(part, safe="") for part in rel.strip("/").split("/"))


def _streaming_url_for(file_id: str, abs_path: Path, data_root: Path) -> str | None:
    """Map a download id to the URL of its streaming static endpoint.

    Returns ``None`` if the file is outside any of the mounted streaming
    roots, so the caller can fall back to a 500 / 404 instead of issuing a
    redirect that would 404.
    """
    if file_id.startswith(USB_MEDIA_PREFIX):
        rest = file_id[len(USB_MEDIA_PREFIX):]
        idx = rest.find(":")
        if idx < 0:
            return None
        key, _ = rest[:idx], rest[idx + 1:]
        if key == "portable":
            try:
                rel = abs_path.resolve().relative_to(_USB_PORTABLE_BASE.resolve()).as_posix()
            except ValueError:
                return None
            return f"{_RAW_USB_PORTABLE_PREFIX}/{_quote_segments(rel)}"
        if key == "host_mnt":
            try:
                rel = abs_path.resolve().relative_to(_USB_HOST_MNT_BASE.resolve()).as_posix()
            except ValueError:
                return None
            return f"{_RAW_USB_HOST_MNT_PREFIX}/{_quote_segments(rel)}"
        return None
    try:
        rel = abs_path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError:
        return None
    return f"{_RAW_INTERNAL_PREFIX}/{_quote_segments(rel)}"


def register_media_routes(app: Robyn) -> None:
    """Register media-related API routes."""

    storage_service = StorageService()

    @app.get("/api/v1/media/files")
    async def get_media_files(request):
        """Get list of media files with optional filtering."""
        try:
            raw_mission_id = request.query_params.get("mission_id", None)
            mission_id = unquote(raw_mission_id) if raw_mission_id else None
            media_type_str = request.query_params.get("type", None)
            limit = int(request.query_params.get("limit", "50"))
            offset = int(request.query_params.get("offset", "0"))

            media_type = None
            if media_type_str:
                media_type = MediaType(media_type_str)

            files = await storage_service.get_media_files(
                mission_id=mission_id,
                media_type=media_type,
                limit=limit,
                offset=offset,
            )

            return json.dumps([f.model_dump(mode="json") for f in files])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/media/missions")
    async def get_missions_with_media(request):
        """Get list of missions that have media."""
        try:
            missions = await storage_service.get_missions_with_media()
            return json.dumps([m.model_dump(mode="json") for m in missions])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/media/download")
    async def download_file(request):
        """Resolve a download id to its streaming static URL and 302 there.

        The ``/api/v1/media/raw/...`` mounts (registered in ``main.py``)
        are served by Robyn via ``actix_files::Files``, which streams from
        disk in chunks, supports ``Range`` for resumable downloads and
        HTML5 video seeking, and never buffers the full file in RAM. The
        previous implementation here read the entire file into memory
        before responding, which on a Pi blew up for multi-GB recordings
        ("Failed to read file: out of memory") and otherwise produced a
        multi-second "dead time" between the user clicking the download
        button and the browser's download tray opening.

        The ``<a download="...">`` element on the frontend follows the
        redirect and forces an ``attachment`` save irrespective of the
        ``Content-Disposition`` header on the static endpoint, so videos
        keep playing inline in the in-app preview while the explicit
        Download button still saves them.
        """
        try:
            file_path = unquote(request.query_params.get("path", ""))
            if not file_path:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Missing 'path' parameter"}),
                    headers={"Content-Type": "application/json"},
                )

            abs_path = media_abs_path_from_download_id(file_path, storage_service.root)
            if abs_path is None:
                return Response(
                    status_code=404,
                    description=json.dumps({"error": "File not found"}),
                    headers={"Content-Type": "application/json"},
                )

            location = _streaming_url_for(file_path, abs_path, storage_service.root)
            if location is None:
                return Response(
                    status_code=500,
                    description=json.dumps({
                        "error": "File is outside the streaming roots; cannot redirect"
                    }),
                    headers={"Content-Type": "application/json"},
                )

            filename = abs_path.name
            return Response(
                status_code=302,
                description="",
                headers={
                    "Location": location,
                    # Belt-and-suspenders for clients that don't honour the
                    # frontend's `<a download>` attribute (e.g. curl -OJL).
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-store",
                },
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.head("/api/v1/media/download")
    async def download_file_head(request):
        """Cheap preflight used by the UI to verify the file is still there
        and to surface its size before we hand the GET off to the browser.
        Returns the relevant headers (sans body) so the frontend can show
        "Preparing 3.4 GB…" instead of a silent multi-second stall.
        """
        try:
            file_path = unquote(request.query_params.get("path", ""))
            if not file_path:
                return Response(status_code=400, description="", headers={})
            abs_path = media_abs_path_from_download_id(file_path, storage_service.root)
            if abs_path is None:
                return Response(status_code=404, description="", headers={})
            filename = abs_path.name
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            size = abs_path.stat().st_size
            return Response(
                status_code=200,
                description="",
                headers={
                    "Content-Type": content_type,
                    # Robyn's `Response` overwrites Content-Length from the
                    # body length, which is 0 here. The custom X-Doris-*
                    # headers are what the frontend reads.
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Accept-Ranges": "bytes",
                    "X-Doris-File-Size": str(size),
                    "X-Doris-File-Name": filename,
                },
            )
        except Exception:
            return Response(status_code=500, description="", headers={})

    @app.delete("/api/v1/media/files")
    async def delete_file(request):
        """Delete a media file by its relative path (passed as ?path=...)."""
        try:
            file_path = unquote(request.query_params.get("path", ""))
            if not file_path:
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Missing 'path' parameter"}),
                    headers={"Content-Type": "application/json"},
                )
            success = await storage_service.delete_file(file_path)
            return json.dumps({"success": success})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/media/sync/status")
    async def get_sync_status(request):
        """Get cloud sync status."""
        try:
            status = await storage_service.get_sync_status()
            return json.dumps(status.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/media/sync/start")
    async def start_sync(request):
        """Start cloud sync."""
        try:
            success = await storage_service.start_sync()
            return json.dumps({"success": success})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
