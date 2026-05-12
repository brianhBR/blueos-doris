"""Sniff mavlink2rest WebSocket STATUSTEXT frames so we know the schema
the new tracker service needs to parse.
"""
import asyncio
import json
import sys

import websockets

URL = "ws://blueos-wifi.local:6040/ws/mavlink?filter=STATUSTEXT"


async def main():
    async with websockets.connect(URL, close_timeout=5) as ws:
        seen = 0
        try:
            async with asyncio.timeout(60):
                while True:
                    raw = await ws.recv()
                    seen += 1
                    print(f"--- frame {seen} ---")
                    try:
                        print(json.dumps(json.loads(raw), indent=2))
                    except Exception:
                        print(repr(raw))
                    if seen >= 3:
                        break
        except TimeoutError:
            print(f"timeout, saw {seen} frames")


if __name__ == "__main__":
    asyncio.run(main())
