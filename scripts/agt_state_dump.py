"""Dump everything mavlink2rest knows about the AGT (component 191).

Useful for diagnosing a stuck or silent Iridium test: shows the most
recent message of every type the AGT has emitted plus the COMMAND_ACK
the AGT sent for the test command.
"""
import json
import sys
import urllib.request

BASE = "http://blueos-wifi.local:6040"


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
        return json.loads(r.read())


def main():
    data = get("/mavlink/vehicles/1/components/191")
    msgs = data.get("messages", {})
    print(f"AGT (component 191) — {len(msgs)} message types cached\n")
    for name in sorted(msgs):
        entry = msgs[name]
        status = entry.get("status", {}).get("time", {})
        msg = entry.get("message", {})
        line = f"  {name:<14} counter={status.get('counter', 0):<6} freq={status.get('frequency') or '-':<10} last={status.get('last_update', '?')}"
        print(line)
        if name == "STATUSTEXT":
            text = msg.get("text", "")
            if isinstance(text, list):
                text = "".join(c for c in text if c != "\x00")
            print(f"     text: {text!r}")
            print(f"     severity: {msg.get('severity')}")
        if name == "COMMAND_ACK":
            print(f"     command: {msg.get('command')}")
            print(f"     result: {msg.get('result')}")
            print(f"     progress: {msg.get('progress')}")


if __name__ == "__main__":
    sys.exit(main() or 0)
