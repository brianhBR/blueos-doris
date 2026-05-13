"""Hot-deploy the updated tracker.py + sensors.py route to the running
DORIS extension container and restart it.

Mirrors the workflow described in `.cursor/rules/doris-hardware-ops.mdc`:
build a tar archive in memory, PUT it to the container's /app path, then
POST a /restart.
"""
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

DOCKER_API = "http://192.168.68.75:2375"  # blueos-wifi.local; mDNS sometimes fails on Windows
REPO = Path(__file__).resolve().parents[1]

UPLOADS = [
    (REPO / "extension/backend/src/doris/services/tracker.py", "/app/src/doris/services"),
    (REPO / "extension/backend/src/doris/routes/sensors.py", "/app/src/doris/routes"),
]


def http(method, path, body=None, headers=None, timeout=30):
    url = f"{DOCKER_API}{path}"
    data = body if isinstance(body, bytes) else (body.encode() if body else None)
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_doris():
    raw = http("GET", "/containers/json")
    for c in json.loads(raw):
        if any("doris" in n.lower() for n in c.get("Names", [])):
            return c["Id"][:12], c["Names"]
    raise SystemExit("DORIS container not found")


def make_tar(files: list[tuple[str, bytes]]) -> bytes:
    """files = list of (basename, content_bytes)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def upload(cid: str, dest_path: str, files: list[tuple[str, bytes]]):
    tar = make_tar(files)
    http(
        "PUT",
        f"/containers/{cid}/archive?path={urllib.parse.quote(dest_path)}",
        body=tar,
        headers={"Content-Type": "application/x-tar"},
        timeout=30,
    )


def main():
    import urllib.parse  # noqa: F401  (used inside upload via globals)

    cid, names = find_doris()
    print(f"[hotdeploy] DORIS container={cid} names={names}")

    # Group by destination path so we PUT each archive once
    by_dest: dict[str, list[tuple[str, bytes]]] = {}
    for src, dest in UPLOADS:
        if not src.is_file():
            print(f"[hotdeploy]   missing source: {src}")
            return 1
        by_dest.setdefault(dest, []).append((src.name, src.read_bytes()))
        print(f"[hotdeploy]   queued {src} -> {dest}/{src.name}")

    for dest, files in by_dest.items():
        print(f"[hotdeploy] uploading {len(files)} file(s) to {dest}")
        upload(cid, dest, files)

    print(f"[hotdeploy] restarting container {cid} …")
    http("POST", f"/containers/{cid}/restart?t=5", timeout=30)
    print("[hotdeploy] done.")


if __name__ == "__main__":
    import urllib.parse  # used by upload()
    sys.exit(main() or 0)
