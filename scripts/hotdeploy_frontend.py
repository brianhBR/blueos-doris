"""Hot-deploy the built Vite frontend into the running DORIS container.

Tars up extension/frontend/dist/ and PUTs it over /app/frontend/dist/
in the container, then restarts the container so any cached assets
get re-served.
"""
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from pathlib import Path

DOCKER_API = "http://192.168.68.75:2375"
REPO = Path(__file__).resolve().parents[1]
DIST_DIR = REPO / "extension/frontend/dist"
TARGET_PATH = "/app/frontend"  # tar contains "dist/..." so it lands at /app/frontend/dist/


def curl(args, timeout=60):
    p = subprocess.run(
        ["curl.exe", "-sS", "--max-time", str(timeout), *args],
        capture_output=True,
    )
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def find_doris():
    rc, body, err = curl(["-X", "GET", f"{DOCKER_API}/containers/json"])
    if rc != 0:
        raise SystemExit(f"docker ps failed: {err}")
    for c in json.loads(body):
        if any("doris" in n.lower() for n in c.get("Names", [])):
            return c["Id"][:12], c["Names"], c["Image"]
    raise SystemExit("DORIS container not found")


def make_dist_tar() -> bytes:
    """Tar the dist directory with a top-level 'dist/' so when extracted
    into /app/frontend/ it lands as /app/frontend/dist/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(str(DIST_DIR), arcname="dist", recursive=True)
    return buf.getvalue()


def main():
    if not DIST_DIR.is_dir():
        raise SystemExit(f"dist not found at {DIST_DIR} — run npm run build first")

    # Default: skip container restart (static dist files don't need it; the
    # browser just needs a hard-refresh).  Pass --restart to also bounce the
    # container, which is necessary if you also changed Python backend code.
    restart = "--restart" in sys.argv

    cid, names, image = find_doris()
    print(f"[hotdeploy] DORIS container={cid} names={names} image={image}")

    tar_bytes = make_dist_tar()
    print(f"[hotdeploy] tar size: {len(tar_bytes) / 1024:.1f} KB")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp:
        tmp.write(tar_bytes)
        tmp_path = tmp.name
    try:
        url = f"{DOCKER_API}/containers/{cid}/archive?path={urllib.parse.quote(TARGET_PATH)}"
        print(f"[hotdeploy] PUT -> {url}")
        rc, body, err = curl(
            [
                "-X", "PUT",
                "-H", "Content-Type: application/x-tar",
                "--data-binary", f"@{tmp_path}",
                url,
            ],
            timeout=120,
        )
        if rc != 0:
            raise SystemExit(f"upload failed rc={rc} err={err}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if restart:
        print(f"[hotdeploy] restarting container {cid} ...")
        rc, body, err = curl(["-X", "POST", f"{DOCKER_API}/containers/{cid}/restart?t=5"], timeout=30)
        if rc != 0:
            print(f"[hotdeploy] restart failed: rc={rc} err={err}")
            return 1
        print("[hotdeploy] done. Hard-refresh the page (Ctrl+Shift+R).")
    else:
        print("[hotdeploy] done (no restart). Hard-refresh the page (Ctrl+Shift+R).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
