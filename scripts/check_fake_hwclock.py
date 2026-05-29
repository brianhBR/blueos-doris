"""One-off diagnostic: check fake-hwclock / RTC status on the DORIS vehicle host.

Queries the BlueOS Commander API (host commands) and reports whether
fake-hwclock is installed/active, whether a hardware RTC exists, and what
the persisted clock data looks like.
"""

import json
import urllib.parse
import urllib.request

COMMANDER = "http://blueos-wifi.local:9100"
TIMEOUT = 8


def host(cmd: str) -> dict:
    url = (
        f"{COMMANDER}/v1.0/command/host?command="
        f"{urllib.parse.quote(cmd)}&i_know_what_i_am_doing=true"
    )
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


CHECKS = {
    "which fake-hwclock": "which fake-hwclock || echo MISSING",
    "fake-hwclock save status": "fake-hwclock 2>&1 || echo NO_BIN",
    "fake-hwclock.data": "cat /etc/fake-hwclock.data 2>&1 || echo NO_DATA",
    "systemd unit": "systemctl is-enabled fake-hwclock 2>&1; systemctl is-active fake-hwclock 2>&1",
    "cron hooks": "ls -la /etc/cron.hourly/ 2>&1 | grep -i hwclock || echo NO_CRON",
    "rtc devices": "ls -la /dev/rtc* 2>&1 || echo NO_RTC",
    "rtc in dmesg": "dmesg 2>/dev/null | grep -i rtc | tail -n 20 || echo NO_DMESG_RTC",
    "timedatectl": "timedatectl 2>&1 || echo NO_TIMEDATECTL",
    "current date": "date -u",
}


def main() -> None:
    for label, cmd in CHECKS.items():
        print(f"\n===== {label} =====")
        try:
            res = host(cmd)
            rc = res.get("return_code")
            out = (res.get("stdout") or "").strip()
            err = (res.get("stderr") or "").strip()
            print(f"[return_code={rc}]")
            if out:
                print(out)
            if err:
                print("STDERR:", err)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
