"""Hot-deploy tracker.py + sensors.py route via curl.exe.

urllib was hitting ConnectionResetError on the larger PUT body; curl.exe
handles it cleanly and is what we use everywhere else for vehicle ops.
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

UPLOADS = [
    (REPO / "extension/backend/src/doris/services/tracker.py", "/app/src/doris/services"),
    (REPO / "extension/backend/src/doris/routes/sensors.py", "/app/src/doris/routes"),
]


def curl(args, timeout=30):
    """Run curl.exe and return (rc, stdout_bytes, stderr_str)."""
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


def make_tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def upload(cid, dest_path, files):
    tar = make_tar(files)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp:
        tmp.write(tar)
        tmp_path = tmp.name
    try:
        url = f"{DOCKER_API}/containers/{cid}/archive?path={urllib.parse.quote(dest_path)}"
        rc, body, err = curl(
            [
                "-X", "PUT",
                "-H", "Content-Type: application/x-tar",
                "--data-binary", f"@{tmp_path}",
                url,
            ],
            timeout=60,
        )
        if rc != 0:
            raise SystemExit(f"upload failed: rc={rc} err={err}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main():
    cid, names, image = find_doris()
    print(f"[hotdeploy] DORIS container={cid} names={names} image={image}")

    by_dest = {}
    for src, dest in UPLOADS:
        if not src.is_file():
            print(f"[hotdeploy]   missing source: {src}")
            return 1
        by_dest.setdefault(dest, []).append((src.name, src.read_bytes()))
        print(f"[hotdeploy]   queued {src.name} -> {dest}/{src.name} ({src.stat().st_size} bytes)")

    for dest, files in by_dest.items():
        print(f"[hotdeploy] uploading {len(files)} file(s) to {dest}")
        upload(cid, dest, files)

    print(f"[hotdeploy] restarting container {cid} ...")
    rc, body, err = curl(["-X", "POST", f"{DOCKER_API}/containers/{cid}/restart?t=5"], timeout=30)
    if rc != 0:
        print(f"[hotdeploy] restart failed: rc={rc} err={err}")
        return 1
    print("[hotdeploy] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
