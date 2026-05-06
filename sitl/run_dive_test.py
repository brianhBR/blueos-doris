#!/usr/bin/env python3
"""DORIS SITL dive test runner.

A small, focused harness for end-to-end SITL dive recording tests on
a remote BlueOS vehicle running the DORIS extension and ArduSub SITL.

What it does (in order):

1. Starts a dive via ``POST /api/v1/dive/start`` with
   ``{"configuration": <profile_name>}``.  The ``configuration`` key is
   required: any other key (such as ``name``) is silently ignored by
   the route, leaving stale ``DORIS_*`` params on the autopilot --
   most importantly the ``DORIS_BTM_TIM`` bottom-time release timer.
   This script always uses the correct key.
2. Triggers SITL buoyancy drop (``POST /api/v1/dive/sitl/simulate_drop``).
3. Polls ``/api/v1/dive/status`` and ``/api/v1/ipcam/record/status``
   every ``--poll-interval`` seconds.  On each tick it logs the dive
   state, recorder state, free-MB, rotation count, and pipeline
   restarts.  If the recorder's ``free_mb`` doesn't change for more
   than ``--freeze-threshold`` seconds, the row is annotated
   ``FROZEN <Ns>`` so silent camera stalls are visible at a glance.
4. If the Lua bottom-timer is misconfigured (e.g. the operator forgot
   to push a fresh profile) and the dive overshoots
   ``--max-bottom-min``, the runner manually rotates the recorder to
   ascent so the test can complete and any captured video is finalised
   into per-phase MP4s.
5. After the dive ends (RECOVERY or hard cap), optionally calls
   ``POST /api/v1/dive/finalize`` to produce
   ``dive_<stamp>_<phase>.mp4`` files.

Usage examples:

    # Default: vehicle at 192.168.1.73, profile "sitl-interval", 9 min cap
    ./run_dive_test.py

    # Custom vehicle / profile / cap
    ./run_dive_test.py --vehicle 192.168.1.10 \
        --profile my-profile --max-bottom-min 20

    # Skip the SITL drop (real-water testing or pre-armed runs)
    ./run_dive_test.py --no-simulate-drop

The script never writes any DORIS_* params directly; it always goes
through the extension's API so the Lua state machine sees consistent
parameter values pushed via the route's
``push_configuration_params`` path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def get(vehicle: str, path: str, timeout: float = 8.0) -> dict:
    """GET a vehicle endpoint, returning the parsed JSON body or
    a single-key error dict on failure (so callers can keep going)."""
    try:
        with urllib.request.urlopen(f"http://{vehicle}:8095{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"_error": str(e)}


def post(
    vehicle: str,
    path: str,
    body: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    data = json.dumps(body).encode() if body is not None else b""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(
        f"http://{vehicle}:8095{path}",
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.reason}"}
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"_error": str(e)}


def fmt_mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--vehicle", default="192.168.1.73",
                   help="Vehicle IP (default: 192.168.1.73)")
    p.add_argument("--profile", default="sitl-interval",
                   help="Configuration profile name (default: sitl-interval). "
                        "MUST exist on the vehicle; passed via "
                        "{'configuration': '<profile>'} so the route pushes "
                        "fresh DORIS_* params before DORIS_START=1.")
    p.add_argument("--no-simulate-drop", action="store_true",
                   help="Skip the SIM_BUOYANCY=-19.6N drop (real-water "
                        "testing or pre-armed runs).")
    p.add_argument("--poll-interval", type=float, default=15.0,
                   help="Seconds between status polls (default: 15)")
    p.add_argument("--freeze-threshold", type=float, default=30.0,
                   help="Annotate rows FROZEN <Ns> when free_mb hasn't "
                        "changed for more than this many seconds "
                        "(default: 30)")
    p.add_argument("--max-bottom-min", type=float, default=9.0,
                   help="Maximum bottom-phase wallclock minutes before the "
                        "runner manually rotates to ascent (default: 9). "
                        "Set this above the profile's release_weight.elapsed "
                        "for normal operation; lower values exercise the "
                        "manual-rotate fallback path.")
    p.add_argument("--max-dive-min", type=float, default=12.0,
                   help="Hard cap on dive wallclock minutes (default: 12). "
                        "After this the runner stops the dive whatever "
                        "state it's in.")
    p.add_argument("--finalize", action="store_true",
                   help="Call POST /api/v1/dive/finalize after the dive ends "
                        "to concatenate per-phase MP4s.")
    args = p.parse_args()

    print(f"== DORIS SITL dive test ==")
    print(f"  vehicle       = {args.vehicle}")
    print(f"  profile       = {args.profile}")
    print(f"  simulate_drop = {not args.no_simulate_drop}")
    print(f"  max_bottom    = {args.max_bottom_min:.1f} min")
    print(f"  max_dive      = {args.max_dive_min:.1f} min")
    print(f"  finalize      = {args.finalize}")
    print()

    print("[1] starting dive (key='configuration', NOT 'name')")
    start_resp = post(args.vehicle, "/api/v1/dive/start",
                      {"configuration": args.profile})
    print(f"    -> {start_resp}")
    if not start_resp.get("success"):
        print("    FAIL: dive start did not return success=true")
        return 2
    if start_resp.get("profile_id", 0) == 0:
        print("    WARNING: profile_id=0 in response.  This usually means "
              "the route didn't load the configuration (e.g. profile name "
              "wrong).  Stale DORIS_* params may apply.")
    time.sleep(2)

    if not args.no_simulate_drop:
        print("[2] simulating buoyancy drop (SITL only)")
        drop = post(args.vehicle, "/api/v1/dive/sitl/simulate_drop")
        print(f"    -> {drop}")
    else:
        print("[2] (skipped: real-water mode)")

    print("[3] polling")
    t0 = time.time()
    last_state: str | None = None
    last_phase: str | None = None
    last_free: float | None = None
    freeze_started: float | None = None
    bottom_started_at: float | None = None
    manual_ascent_fired = False
    manual_stop_fired = False
    log: list[dict] = []

    print(f"\n  {'t':>6} {'state':<14} {'rec':<3} {'phase':<10} "
          f"{'rot':>3} {'restart':>7} {'free_mb':>9} note", flush=True)

    while True:
        elapsed = time.time() - t0
        ds = get(args.vehicle, "/api/v1/dive/status")
        rs = get(args.vehicle, "/api/v1/ipcam/record/status")
        state = ds.get("doris_script_state_name", "?") or "?"
        rec = bool(rs.get("recording", False))
        phase = rs.get("phase") or "-"
        rot = int(rs.get("rotations", 0) or 0)
        restarts = int(rs.get("restarts", 0) or 0)
        free = rs.get("usb", {}).get("free_mb")

        # Freeze detection
        note = ""
        if last_free is not None and free is not None:
            if abs(float(free) - float(last_free)) < 0.5:
                if freeze_started is None:
                    freeze_started = elapsed
                if elapsed - freeze_started > args.freeze_threshold:
                    note = f"FROZEN {int(elapsed - freeze_started)}s"
            else:
                freeze_started = None
        last_free = free

        # Track when we entered ON_BOTTOM
        if state == "ON_BOTTOM" and bottom_started_at is None:
            bottom_started_at = elapsed

        print(f"  {fmt_mmss(elapsed):>6} {state:<14} {'Y' if rec else 'N':<3} "
              f"{phase:<10} {rot:>3} {restarts:>7} "
              f"{free if free is not None else '-':>9} {note}",
              flush=True)
        log.append({"t": elapsed, "state": state, "rec": rec, "phase": phase,
                    "rotations": rot, "restarts": restarts, "free_mb": free,
                    "note": note})
        last_state, last_phase = state, phase

        # Manual ascent backstop: triggered when we've been ON_BOTTOM
        # past --max-bottom-min, suggesting cfg.rls_sec_ms on the
        # autopilot is larger than the test wants.
        bottom_elapsed = (
            elapsed - bottom_started_at if bottom_started_at is not None else 0.0
        )
        if (state == "ON_BOTTOM"
                and not manual_ascent_fired
                and bottom_elapsed > args.max_bottom_min * 60):
            print(f"\n  [t={fmt_mmss(elapsed)}] bottom_elapsed="
                  f"{fmt_mmss(bottom_elapsed)} exceeds --max-bottom-min "
                  f"{args.max_bottom_min:.1f}min; manually rotating to ascent")
            r = post(args.vehicle, "/api/v1/ipcam/record/rotate?phase=ascent",
                     timeout=10)
            print(f"    rotate -> {r}\n")
            manual_ascent_fired = True

        # Hard dive cap
        if elapsed > args.max_dive_min * 60 and not manual_stop_fired \
                and state != "RECOVERY":
            print(f"\n  [t={fmt_mmss(elapsed)}] exceeded --max-dive-min "
                  f"{args.max_dive_min:.1f}min; stopping dive")
            r = post(args.vehicle, "/api/v1/dive/stop", timeout=30)
            print(f"    stop -> {r}\n")
            manual_stop_fired = True

        if state == "RECOVERY":
            print(f"\n  [t={fmt_mmss(elapsed)}] RECOVERY reached")
            break
        if manual_stop_fired and elapsed > args.max_dive_min * 60 + 60:
            print(f"\n  [t={fmt_mmss(elapsed)}] post-stop wait elapsed")
            break
        if elapsed > args.max_dive_min * 60 + 120:
            print(f"\n  [t={fmt_mmss(elapsed)}] hard exit cap")
            break

        time.sleep(args.poll_interval)

    print(f"\n== dive finished t={fmt_mmss(elapsed)}, last state={last_state} ==")

    if args.finalize:
        print("\n[4] finalize")
        fr = post(args.vehicle, "/api/v1/dive/finalize", timeout=180)
        if "_error" in fr:
            print(f"    FAIL: {fr}")
        else:
            print(f"    success={fr.get('success')}")
            for ph in fr.get("phases", []):
                print(
                    f"    {ph['phase']:<10} {ph.get('input_count', 0)} parts "
                    f"-> {ph.get('output_duration_s', 0):.1f}s "
                    f"({ph.get('output_bytes', 0) / 1_000_000:.1f} MB)"
                )
            mf = fr.get("manifest")
            if mf:
                print(f"    manifest: {mf}")

    return 0 if not log else 0


if __name__ == "__main__":
    sys.exit(main())
