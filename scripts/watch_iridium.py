"""Stream STATUSTEXT messages from the new buffered backend until
PASSED/FAILED or timeout.  Set TRIGGER=1 env to also trigger a fresh
test before polling.
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://192.168.68.75:8095"


def get(path, timeout=15):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def post(path, timeout=30):
    req = urllib.request.Request(f"{BASE}{path}", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    if os.environ.get("TRIGGER") == "1":
        print("triggering iridium test …")
        res = post("/api/v1/tracker/iridium-test")
        print(f"trigger response: {res}")
        if not res.get("accepted"):
            return 1
        since = res.get("latest_id", 0)
    else:
        s0 = get("/api/v1/tracker/iridium-status?since_id=0")
        # Show what's already buffered, then start tailing from the latest id
        for m in s0.get("messages", []):
            ts = m.get("timestamp", "")[11:19]
            print(f"  [{ts}] id={m['id']:<3} sev={m['severity']} text={m['text']!r}")
        since = s0.get("latest_id", 0)
        print(f"-- tailing since id={since} --")

    deadline = time.time() + 60 * 14
    while time.time() < deadline:
        try:
            s = get(f"/api/v1/tracker/iridium-status?since_id={since}")
        except Exception as e:
            print(f"  poll error: {e}")
            time.sleep(3)
            continue
        msgs = s.get("messages", [])
        for m in msgs:
            ts = m.get("timestamp", "")[11:19]
            print(f"  [{ts}] id={m['id']:<3} sev={m['severity']} text={m['text']!r}")
            t = m.get("text", "")
            if "IRIDIUM" in t and ("PASSED" in t or "FAILED" in t):
                print(f"\nresult: {t!r}")
                return 0
        if msgs:
            since = s.get("latest_id", since)
        time.sleep(3)
    print("timeout (no PASSED/FAILED in 14 min)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
