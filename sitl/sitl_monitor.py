#!/usr/bin/env python3
"""DORIS SITL mission monitor — remote-vehicle edition.

Assumed runtime topology:
    * BlueOS on the vehicle runs ArduSub SITL (executing the DORIS Lua script)
      alongside the DORIS extension backend.
    * A DORIS dive profile has already been loaded AND activated via the
      extension UI / API (``DORIS_START=1``, script sits at ``MISSION_START``).
    * BlueOS is configured with a ``udpout`` MAVLink endpoint pointing at this
      host (default: ``192.168.1.53:14551``), so the monitor binds ``udpin``
      on the same port.
    * The extension REST API is reachable at
      ``http://<vehicle-ip>:8095/``.

What this monitor does:
    * Leaves the extension-loaded DORIS profile alone (no ``DORIS_*`` writes).
    * Applies light SITL-environment param overrides and runs a power /
      battery / GPS-loss simulation.
    * Waits for the operator to type ``deploy`` and then applies negative
      ``SIM_BUOYANCY`` to start the descent.
    * The Lua script still emits ``"DIVE: IPcam recording started/stopped"``
      STATUSTEXT at the correct trigger points, but in SITL mode it
      short-circuits the onboard HTTP POST. This monitor bridges those
      announcements to the vehicle's real ``POST /rec/start`` /
      ``POST /rec/stop`` endpoints.
    * A background thread polls ``GET /rec/status`` at 2 Hz for ground-truth
      recording state.
    * Per-phase dive windows (DESCENT / ON_BOTTOM / ASCENT) are measured
      against actual recording windows; a coverage report is printed when
      RECOVERY is reached (or when the monitor is stopped).

Commands (type in this pane while the monitor is running):
    deploy      Sink the vehicle (sets SIM_BUOYANCY = -19.6 N)
    surface     Emergency surface (sets SIM_BUOYANCY = +19.6 N)
    rec on      Force /rec/start on the vehicle (debug)
    rec off     Force /rec/stop on the vehicle (debug)
    stop        Set DORIS_START=0 to cancel the mission
    q / quit    Exit the monitor (prints coverage report first)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pymavlink import mavutil

# ── state machine constants ──────────────────────────────────────────────────

STATE_CONFIG        = -1
STATE_MISSION_START =  0
STATE_DESCENT       =  1
STATE_ON_BOTTOM     =  2
STATE_ASCENT        =  3
STATE_RECOVERY      =  4

STATE_NAMES = {
    -1: "CONFIG", 0: "MISSION_START", 1: "DESCENT", 2: "ON_BOTTOM",
    3: "ASCENT", 4: "RECOVERY",
}

# Only DESCENT/ON_BOTTOM/ASCENT contribute to the coverage report.
PHASE_STATES = (STATE_DESCENT, STATE_ON_BOTTOM, STATE_ASCENT)

DEPLOY_BUOYANCY  = -19.6   # 2 kg net negative (sink)
SURFACE_BUOYANCY = +19.6   # 2 kg net positive (rise)

# ── power / battery / GPS simulation ─────────────────────────────────────────

BATT_CAPACITY_WH  = 300.0
IDLE_DRAW_W       = 15.0
LIGHTS_MAX_DRAW_W = 35.0
LIGHTS_PWM_MIN    = 1100
LIGHTS_PWM_MAX    = 1900
VOLT_FULL         = 16.8
VOLT_EMPTY        = 14.0
POWER_UPDATE_INTERVAL = 10.0
GPS_LOSS_DEPTH        = 2.0


def _lights_fraction(pwm: int) -> float:
    frac = (pwm - LIGHTS_PWM_MIN) / (LIGHTS_PWM_MAX - LIGHTS_PWM_MIN)
    return max(0.0, min(1.0, frac))


def _power_draw_w(ch13_pwm: int) -> float:
    return IDLE_DRAW_W + _lights_fraction(ch13_pwm) * LIGHTS_MAX_DRAW_W


def _wh_to_voltage(wh_remaining: float) -> float:
    soc = max(0.0, min(1.0, wh_remaining / BATT_CAPACITY_WH))
    return VOLT_EMPTY + soc * (VOLT_FULL - VOLT_EMPTY)


# ── shared command queue ─────────────────────────────────────────────────────

_command: str | None = None
_command_lock = threading.Lock()
_quit_event   = threading.Event()


def _stdin_reader():
    """Background thread: reads lines from stdin and stores the last command."""
    global _command
    while not _quit_event.is_set():
        try:
            line = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        with _command_lock:
            _command = line
        if line in ("q", "quit"):
            _quit_event.set()
            break


def _take_command() -> str | None:
    global _command
    with _command_lock:
        cmd, _command = _command, None
    return cmd


# ── MAVLink helpers ──────────────────────────────────────────────────────────

def set_param(mav, name: str, val: float, *, timeout: float = 3.0) -> bool:
    pid = name.encode().ljust(16, b"\x00")
    mav.mav.param_set_send(mav.target_system, mav.target_component, pid, val, 9)
    ack = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=timeout)
    if ack and ack.param_id.rstrip("\x00") == name:
        print("  %s = %.3f" % (name, ack.param_value))
        return True
    print("  %s — no ack (param may not exist or vehicle is slow)" % name)
    return False


def _set_param_noack(mav, name: str, val: float) -> None:
    pid = name.encode().ljust(16, b"\x00")
    mav.mav.param_set_send(mav.target_system, mav.target_component, pid, val, 9)


# ── vehicle extension HTTP helpers ───────────────────────────────────────────

class ExtensionClient:
    """Minimal stdlib HTTP client for the DORIS extension REST API."""

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str) -> tuple[int, dict | None, str | None]:
        url = self.base + path
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(body), None
                except json.JSONDecodeError:
                    return resp.status, None, body
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return e.code, None, body or str(e)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            return 0, None, str(e)

    def rec_start(self, split_duration: int = 1800) -> tuple[bool, str]:
        code, js, err = self._request("POST", f"/rec/start?split_duration={split_duration}")
        if code == 200 and js and js.get("success"):
            return True, ""
        return False, err or (js.get("message") if js else f"HTTP {code}")

    def rec_stop(self) -> tuple[bool, str]:
        code, js, err = self._request("POST", "/rec/stop")
        if code == 200 and js and js.get("success"):
            return True, ""
        return False, err or (js.get("message") if js else f"HTTP {code}")

    def rec_status(self) -> tuple[bool | None, dict | None, str]:
        code, js, err = self._request("GET", "/rec/status")
        if code == 200 and js is not None:
            return bool(js.get("recording")), js, ""
        return None, None, err or f"HTTP {code}"

    def dive_mission(self) -> dict | None:
        _, js, _ = self._request("GET", "/api/v1/dive/mission")
        if js is None:
            return None
        return js.get("mission")


# ── coverage tracking ────────────────────────────────────────────────────────

@dataclass
class PhaseWindow:
    state: int
    start: float
    end: float | None = None

    @property
    def duration(self) -> float:
        return (self.end if self.end is not None else time.time()) - self.start


@dataclass
class Interval:
    start: float
    end: float | None = None

    def overlap(self, a: float, b: float) -> float:
        s = self.start
        e = self.end if self.end is not None else b
        lo = max(s, a)
        hi = min(e, b)
        return max(0.0, hi - lo)


@dataclass
class CoverageTracker:
    phases: list[PhaseWindow] = field(default_factory=list)
    recording_intervals: list[Interval] = field(default_factory=list)
    _open_phase: PhaseWindow | None = None
    _open_rec: Interval | None = None

    def on_state(self, now: float, new_state: int) -> None:
        if self._open_phase is not None and self._open_phase.state == new_state:
            return
        if self._open_phase is not None:
            self._open_phase.end = now
            self.phases.append(self._open_phase)
            self._open_phase = None
        if new_state in PHASE_STATES:
            self._open_phase = PhaseWindow(state=new_state, start=now)

    def on_recording(self, now: float, active: bool) -> None:
        if active and self._open_rec is None:
            self._open_rec = Interval(start=now)
        elif (not active) and self._open_rec is not None:
            self._open_rec.end = now
            self.recording_intervals.append(self._open_rec)
            self._open_rec = None

    def finalize(self, now: float) -> None:
        if self._open_phase is not None:
            self._open_phase.end = now
            self.phases.append(self._open_phase)
            self._open_phase = None
        if self._open_rec is not None:
            self._open_rec.end = now
            self.recording_intervals.append(self._open_rec)
            self._open_rec = None

    def coverage_for(self, phase: PhaseWindow) -> tuple[float, float, float]:
        """Returns (recorded_seconds, start_gap, end_gap)."""
        assert phase.end is not None
        a, b = phase.start, phase.end
        recorded = 0.0
        first_on = None
        last_on = None
        for iv in self.recording_intervals:
            ov = iv.overlap(a, b)
            if ov > 0:
                recorded += ov
                s = max(iv.start, a)
                e = min(iv.end if iv.end is not None else b, b)
                if first_on is None or s < first_on:
                    first_on = s
                if last_on is None or e > last_on:
                    last_on = e
        start_gap = (first_on - a) if first_on is not None else (b - a)
        end_gap   = (b - last_on) if last_on is not None else (b - a)
        return recorded, start_gap, end_gap

    def report(self, t0: float = 0.0) -> str:
        """Render the coverage table. All times in the "Recording windows"
        block are reported relative to ``t0`` (defaults to absolute epoch)."""
        lines: list[str] = []
        lines.append("")
        lines.append("═══════════════════════════════════════════════════════════════════════")
        lines.append("                   VIDEO COVERAGE REPORT")
        lines.append("═══════════════════════════════════════════════════════════════════════")
        header = "  %-12s  %9s  %9s  %8s  %9s  %8s" % (
            "Phase", "Phase(s)", "Video(s)", "Cov %", "StartGap", "EndGap")
        lines.append(header)
        lines.append("  " + "─" * (len(header) - 2))

        total_phase = 0.0
        total_rec   = 0.0
        for p in self.phases:
            if p.state not in PHASE_STATES:
                continue
            dur = (p.end or p.start) - p.start
            rec, sg, eg = self.coverage_for(p)
            pct = (100.0 * rec / dur) if dur > 0 else 0.0
            total_phase += dur
            total_rec += rec
            lines.append("  %-12s  %9.1f  %9.1f  %7.1f%%  %9.2f  %8.2f" % (
                STATE_NAMES.get(p.state, f"?{p.state}"),
                dur, rec, pct, sg, eg,
            ))
        total_pct = (100.0 * total_rec / total_phase) if total_phase > 0 else 0.0
        lines.append("  " + "─" * (len(header) - 2))
        lines.append("  %-12s  %9.1f  %9.1f  %7.1f%%" % (
            "TOTAL", total_phase, total_rec, total_pct))

        if self.recording_intervals or self._open_rec is not None:
            lines.append("")
            lines.append("  Recording windows (start → end, duration, seconds since t0):")
            intervals = list(self.recording_intervals)
            if self._open_rec is not None:
                intervals.append(self._open_rec)
            for iv in intervals:
                s = iv.start - t0
                e_abs = iv.end
                if e_abs is None:
                    lines.append("    %7.1f s → %7s s   ( open )" % (s, "(open)"))
                else:
                    e = e_abs - t0
                    dur = e - s
                    lines.append("    %7.1f s → %7.1f s   (%5.1f s)" % (s, e, dur))
        lines.append("═══════════════════════════════════════════════════════════════════════")
        return "\n".join(lines)


# ── recorder polling thread ──────────────────────────────────────────────────

def _recorder_poll_loop(client: ExtensionClient, tracker: CoverageTracker,
                       t0: float, events: list[tuple[float, str, str]]) -> None:
    prev_active: bool | None = None
    while not _quit_event.is_set():
        active, js, err = client.rec_status()
        now = time.time()
        rel = now - t0
        if active is None:
            if prev_active is not None:
                events.append((rel, "rec", "status error: %s" % err))
            prev_active = None
        else:
            tracker.on_recording(now, active)
            if prev_active is None or prev_active != active:
                detail = ""
                if js:
                    pid = js.get("pid")
                    base = js.get("base_stamp")
                    restarts = js.get("restarts")
                    detail = " pid=%s base=%s restarts=%s" % (pid, base, restarts)
                events.append((rel, "rec",
                               "→ recording %s%s" % ("ON" if active else "OFF", detail)))
                prev_active = active
        time.sleep(0.5)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mav", default="udpin:0.0.0.0:14551",
                        help="pymavlink connection string "
                             "(default: udpin:0.0.0.0:14551, matches the "
                             "'sitl tester home' udpout on the vehicle)")
    parser.add_argument("--vehicle-ip", default="192.168.1.73",
                        help="vehicle IP (default: 192.168.1.73)")
    parser.add_argument("--extension-port", type=int, default=8095,
                        help="DORIS extension HTTP port (default: 8095)")
    parser.add_argument("--no-env-setup", action="store_true",
                        help="skip SITL env param overrides at startup")
    args = parser.parse_args()

    ext_base = "http://%s:%d" % (args.vehicle_ip, args.extension_port)
    ext = ExtensionClient(ext_base)

    print("Vehicle extension: %s" % ext_base)
    mission = ext.dive_mission()
    if mission:
        print("  Active mission:  %s  (profile_id=%s, status=%s)" % (
            mission.get("configuration_name"),
            mission.get("profile_id"),
            mission.get("status"),
        ))
    else:
        print("  WARNING: no active mission reported by extension.")

    print("MAVLink connection: %s" % args.mav)
    mav = mavutil.mavlink_connection(args.mav)
    print("  Waiting for ArduSub heartbeat (ignoring router/bridge beacons) ...")
    # BlueOS's mavlink router / mavp2p sends its own heartbeat (sys=0, type=18
    # ONBOARD_CONTROLLER). If we lock target_system onto that, every param_set
    # is ignored by ArduSub (sys=1). Loop until we see a SUBMARINE heartbeat.
    MAV_TYPE_SUBMARINE = 12
    deadline = time.time() + 25.0
    hb = None
    while time.time() < deadline:
        m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=2.0)
        if m is None:
            continue
        if m.type == MAV_TYPE_SUBMARINE:
            hb = m
            mav.target_system = m.get_srcSystem()
            mav.target_component = m.get_srcComponent()
            break
        else:
            print("  (ignoring heartbeat sys=%d comp=%d type=%d)"
                  % (m.get_srcSystem(), m.get_srcComponent(), m.type))
    if hb is None:
        print("  ERROR: no ArduSub (SUBMARINE) heartbeat. Is SITL running and "
              "is the 'sitl tester home' udpout enabled?")
        sys.exit(1)
    print("  ArduSub heartbeat from system %d component %d (autopilot=%d)"
          % (mav.target_system, mav.target_component, hb.autopilot))

    # ── optional SITL-env belt-and-suspenders ──────────────────────────────
    if not args.no_env_setup:
        sitl_params = {
            "SIM_BATT_VOLTAGE": _wh_to_voltage(BATT_CAPACITY_WH),
            "BATT_FS_LOW_ACT":  0,
            "BATT_FS_CRT_ACT":  0,
            "BATT_LOW_VOLT":    0,
            "BATT_CRT_VOLT":    0,
            "FS_PRESS_ENABLE":  0,
            "BRD_SAFETYENABLE": 0,
            "ARMING_CHECK":     0,
        }
        print("Applying SITL environment overrides (safe / idempotent) ...")
        for name, val in sitl_params.items():
            set_param(mav, name, val, timeout=1.5)

    print("\nWaiting for MISSION_START / deploy ...")
    print("  Power sim: %.0fW idle, up to %.0fW with full lights (CH13)\n"
          % (IDLE_DRAW_W, IDLE_DRAW_W + LIGHTS_MAX_DRAW_W))
    print("  Type 'deploy' + Enter once state is MISSION_START.\n")

    threading.Thread(target=_stdin_reader, daemon=True).start()

    # ── coverage + recorder polling ────────────────────────────────────────
    t0 = time.time()
    tracker = CoverageTracker()
    events: list[tuple[float, str, str]] = []
    poll_thread = threading.Thread(
        target=_recorder_poll_loop, args=(ext, tracker, t0, events), daemon=True)
    poll_thread.start()

    header = "%6s  %-14s  %7s  %7s  %7s  %6s  %5s  %5s  %6s  %-6s" % (
        "Time", "State", "Depth", "DscRate", "AscRate",
        "BattV", "Relay", "Watt", "BattWh", "Rec")
    print(header)
    print("-" * len(header))

    last_state    = STATE_CONFIG
    deploy_ready  = False
    deployed      = False
    depth = dsc = asc = bv = 0.0
    rl = 0
    ch13_pwm      = LIGHTS_PWM_MIN
    batt_wh       = BATT_CAPACITY_WH
    gps_disabled  = False
    last_row           = 0.0
    last_power_update  = 0.0
    timeout            = 7200
    event_cursor       = 0

    def _flush_events(cur: int) -> int:
        while cur < len(events):
            rel, kind, msg = events[cur]
            print("%6.1f  [%s] %s" % (rel, kind, msg))
            cur += 1
        return cur

    try:
        while not _quit_event.is_set() and time.time() - t0 < timeout:
            elapsed = time.time() - t0

            msg = mav.recv_match(
                type=["NAMED_VALUE_FLOAT", "STATUSTEXT", "SERVO_OUTPUT_RAW"],
                blocking=True, timeout=0.25,
            )
            if msg is not None:
                mtype = msg.get_type()
                if mtype == "STATUSTEXT":
                    text = msg.text.rstrip("\x00")
                    lower = text.lower()
                    if "ipcam recording started" in lower:
                        print("%6.1f  *** Lua → START recording ***" % elapsed)
                        ok, err = ext.rec_start(split_duration=1800)
                        tag = "ok" if ok else ("FAIL: " + err)
                        events.append((elapsed, "bridge", "POST /rec/start → %s" % tag))
                    elif "ipcam recording stopped" in lower:
                        print("%6.1f  *** Lua → STOP recording ***" % elapsed)
                        ok, err = ext.rec_stop()
                        tag = "ok" if ok else ("FAIL: " + err)
                        events.append((elapsed, "bridge", "POST /rec/stop  → %s" % tag))
                    elif "ipcam" in lower or "recording" in lower:
                        print("%6.1f  REC-STATUS: %s" % (elapsed, text))
                    else:
                        print("%6.1f  STATUS: %s" % (elapsed, text))
                elif mtype == "SERVO_OUTPUT_RAW":
                    ch13_pwm = getattr(msg, "servo13_raw", ch13_pwm)
                else:
                    name = msg.name.rstrip("\x00")
                    if name == "STATE":
                        new_state = int(msg.value)
                        if new_state != last_state:
                            tracker.on_state(time.time(), new_state)
                            print("%6.1f  STATE %s → %s" % (
                                elapsed,
                                STATE_NAMES.get(last_state, str(last_state)),
                                STATE_NAMES.get(new_state, str(new_state)),
                            ))
                        last_state = new_state
                    elif name == "DEPTH":
                        depth = msg.value
                    elif name == "DSC_RATE":
                        dsc = msg.value
                    elif name == "ASC_RATE":
                        asc = msg.value
                    elif name == "BATT_V":
                        bv = msg.value
                    elif name == "RELAY":
                        rl = int(msg.value)

            event_cursor = _flush_events(event_cursor)

            # GPS loss simulation: disable underwater so the Lua's surface-
            # detect (gps:status(0) >= 3) only fires after the vehicle
            # actually surfaces. The correct SITL param on ArduSub 4.7.x is
            # SIM_GPS1_ENABLE (1=on, 0=off); the older SIM_GPS1_DISABLE does
            # not exist on this build.
            if not gps_disabled and depth > GPS_LOSS_DEPTH:
                _set_param_noack(mav, "SIM_GPS1_ENABLE", 0.0)
                gps_disabled = True
                print("%6.1f  [gps] GPS disabled at %.1f m depth" % (elapsed, depth))
            elif gps_disabled and depth < GPS_LOSS_DEPTH:
                _set_param_noack(mav, "SIM_GPS1_ENABLE", 1.0)
                gps_disabled = False
                print("%6.1f  [gps] GPS re-enabled at %.1f m depth" % (elapsed, depth))

            # Battery discharge simulation
            if elapsed - last_power_update >= POWER_UPDATE_INTERVAL:
                dt_h = (elapsed - last_power_update) / 3600.0
                watts = _power_draw_w(ch13_pwm)
                batt_wh = max(0.0, batt_wh - watts * dt_h)
                _set_param_noack(mav, "SIM_BATT_VOLTAGE", _wh_to_voltage(batt_wh))
                last_power_update = elapsed

            # Deploy prompt
            if last_state >= STATE_MISSION_START and not deploy_ready:
                deploy_ready = True
                print("\n%6.1f  *** MISSION_START — vehicle armed and ready ***" % elapsed)
                print("        Type 'deploy' + Enter to start descent\n")

            # Operator commands
            cmd = _take_command()
            if cmd == "deploy" and deploy_ready and not deployed:
                print("\n%6.1f  [deploy] SIM_BUOYANCY = %.1f N — vehicle sinking\n"
                      % (elapsed, DEPLOY_BUOYANCY))
                set_param(mav, "SIM_BUOYANCY", DEPLOY_BUOYANCY, timeout=2.0)
                deployed = True
            elif cmd == "deploy" and not deploy_ready:
                print("  [deploy] Not ready — waiting for MISSION_START first")
            elif cmd == "surface":
                print("\n%6.1f  [surface] SIM_BUOYANCY = %.1f N\n"
                      % (elapsed, SURFACE_BUOYANCY))
                set_param(mav, "SIM_BUOYANCY", SURFACE_BUOYANCY, timeout=2.0)
            elif cmd == "rec on":
                ok, err = ext.rec_start()
                print("  [rec on]  %s" % ("ok" if ok else err))
            elif cmd == "rec off":
                ok, err = ext.rec_stop()
                print("  [rec off] %s" % ("ok" if ok else err))
            elif cmd == "stop":
                print("\n%6.1f  [stop] DORIS_START = 0\n" % elapsed)
                set_param(mav, "DORIS_START", 0.0, timeout=2.0)
            elif cmd is not None and cmd not in ("q", "quit"):
                print("  Unknown command '%s'. Available: deploy, surface, "
                      "rec on, rec off, stop, q" % cmd)

            # Periodic status row
            if elapsed - last_row >= 3.0:
                last_row = elapsed
                sname   = STATE_NAMES.get(last_state, "?%d" % last_state)
                watts   = _power_draw_w(ch13_pwm)
                rec_state, _, _ = ext.rec_status()
                rec_tag = "?"  if rec_state is None else ("ON" if rec_state else "off")
                print("%6.1f  %-14s  %7.1f  %7.3f  %7.3f  %6.1f  %5d  %5.0f  %6.1f  %-6s"
                      % (elapsed, sname, depth, dsc, asc, bv, rl, watts, batt_wh, rec_tag))

            if last_state == STATE_RECOVERY:
                print("\n%6.1f  *** Mission complete — RECOVERY reached ***" % elapsed)
                # Give the poll thread one more tick to catch the final rec OFF
                time.sleep(1.0)
                break

    except KeyboardInterrupt:
        print("\nAborted.")
    finally:
        _quit_event.set()
        tracker.finalize(time.time())
        # Flush remaining events
        event_cursor = _flush_events(event_cursor)
        print(tracker.report())
        # Dump a machine-readable log alongside the script for later review
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(log_dir, f"sitl_coverage_{stamp}.json")
        try:
            with open(log_path, "w") as f:
                json.dump({
                    "vehicle_ip": args.vehicle_ip,
                    "t0_utc": datetime.fromtimestamp(t0, tz=timezone.utc).isoformat(),
                    "phases": [
                        {
                            "state": p.state,
                            "state_name": STATE_NAMES.get(p.state, str(p.state)),
                            "start": p.start - t0,
                            "end": (p.end - t0) if p.end else None,
                        }
                        for p in tracker.phases
                    ],
                    "recording_intervals": [
                        {
                            "start": iv.start - t0,
                            "end": (iv.end - t0) if iv.end else None,
                        }
                        for iv in tracker.recording_intervals
                    ],
                    "events": events,
                }, f, indent=2)
            print("Coverage log: %s" % log_path)
        except OSError as e:
            print("Failed to write coverage log: %s" % e)
        mav.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _quit_event.set()
        print("\nAborted.")
