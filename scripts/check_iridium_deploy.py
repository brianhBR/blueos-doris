"""Quick diagnostic: peek inside the running DORIS container to compare
deployed iridium-test code against the workspace source.
"""
import io
import json
import sys
import tarfile
import urllib.parse
import urllib.request

DOCKER_API = "http://blueos-wifi.local:2375"


def http(method, path, body=None, headers=None, timeout=10):
    url = f"{DOCKER_API}{path}"
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_doris():
    raw = http("GET", "/containers/json")
    for c in json.loads(raw):
        if any("doris" in n.lower() for n in c.get("Names", [])):
            return c["Id"][:12]
    raise SystemExit("DORIS container not found")


def exec_cmd(cid, cmd):
    payload = json.dumps({
        "Cmd": ["sh", "-c", cmd],
        "AttachStdout": True,
        "AttachStderr": True,
    })
    res = http(
        "POST", f"/containers/{cid}/exec", payload,
        headers={"Content-Type": "application/json"},
    )
    eid = json.loads(res)["Id"]
    out = http(
        "POST", f"/exec/{eid}/start", json.dumps({"Detach": False, "Tty": False}),
        headers={"Content-Type": "application/json"}, timeout=20,
    )
    return strip_docker_stream(out).decode("utf-8", errors="replace")


def strip_docker_stream(buf: bytes) -> bytes:
    """Docker exec start returns multiplexed frames: 8-byte header + payload."""
    out = bytearray()
    i = 0
    while i + 8 <= len(buf):
        size = int.from_bytes(buf[i + 4:i + 8], "big")
        i += 8
        out.extend(buf[i:i + size])
        i += size
    return bytes(out)


def get_file(cid, path):
    q = urllib.parse.urlencode({"path": path})
    raw = http("GET", f"/containers/{cid}/archive?{q}")
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        m = t.next()
        return t.extractfile(m).read().decode("utf-8", errors="replace")


def main():
    cid = find_doris()
    print(f"[DORIS] container={cid}")

    print("\n[1] Listing /app/frontend/dist/assets …")
    print(exec_cmd(cid, "ls -la /app/frontend/dist/assets 2>&1 | head -30"))

    print("\n[2] Search deployed JS bundles for 'iridium-test' references")
    print(exec_cmd(
        cid,
        "grep -l -i 'iridium' /app/frontend/dist/assets/*.js 2>&1 || echo 'no matches'",
    ))

    print("\n[3] Snippet of deployed tracker.py (send_iridium_test)")
    try:
        src = get_file(cid, "/app/src/doris/services/tracker.py")
        for line in src.splitlines():
            if "iridium" in line.lower() or "MAV_CMD_USER_4" in line:
                print(f"  {line}")
    except Exception as e:
        print(f"  (failed to read: {e})")

    print("\n[4] Recent doris logs mentioning iridium")
    raw = http(
        "GET",
        f"/containers/{cid}/logs?stdout=true&stderr=true&tail=400",
        timeout=10,
    )
    body = strip_docker_stream(raw).decode("utf-8", errors="replace")
    for line in body.splitlines():
        if "iridium" in line.lower() or "tracker" in line.lower():
            print(f"  {line}")


if __name__ == "__main__":
    sys.exit(main() or 0)
