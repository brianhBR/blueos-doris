#!/usr/bin/env python3
"""DORIS SITL mission monitor -- configures SITL params and watches the state machine.

The Lua script handles arming via surface pre-arm checks.  Once the vehicle
reaches MISSION_START (armed, waiting at depth gate) this monitor prints a
prompt and waits for the operator to type 'deploy' before applying negative
buoyancy to simulate physical deployment.

Commands (type in this pane while monitoring):
    deploy      Sink the vehicle (sets SIM_BUOYANCY = -19.6 N)
    surface     Emergency surface (sets SIM_BUOYANCY = +19.6 N)
    stop        Set DORIS_START=0 to cancel the mission
    q / quit    Exit the monitor

Power simulation:
    Idle draw:        15 W
    Lights full-on:   50 W total (35 W lights + 15 W idle)
    CH13 PWM 1100-1900 linearly scales lights power.
    Battery: 300 Wh, 4S LiPo (16.8 V full → 14.0 V empty).
    SIM_BATT_VOLTAGE is updated every 10 s to simulate discharge.
"""

import threading
import time
import sys

from pymavlink import mavutil

STATE_CONFIG        = -1
STATE_MISSION_START =  0

STATE_NAMES = {
    -1: "CONFIG", 0: "MISSION_START", 1: "DESCENT", 2: "ON_BOTTOM",
    3: "ASCENT", 4: "RECOVERY",
}

DEPLOY_BUOYANCY  = -19.6   # 2 kg net negative (sink)
SURFACE_BUOYANCY = +19.6   # 2 kg net positive (rise)

# ── power simulation constants ────────────────────────────────────────────────

BATT_CAPACITY_WH  = 300.0
IDLE_DRAW_W       = 15.0
LIGHTS_MAX_DRAW_W = 35.0   # additional above idle at full brightness
LIGHTS_PWM_MIN    = 1100
LIGHTS_PWM_MAX    = 1900
VOLT_FULL         = 16.8   # 4S LiPo full
VOLT_EMPTY        = 14.0   # 4S LiPo cutoff
POWER_UPDATE_INTERVAL = 10.0   # seconds between SIM_BATT_VOLTAGE updates
GPS_LOSS_DEPTH        = 2.0    # metres: disable GPS when deeper, re-enable when shallower


def _lights_fraction(pwm: int) -> float:
    frac = (pwm - LIGHTS_PWM_MIN) / (LIGHTS_PWM_MAX - LIGHTS_PWM_MIN)
    return max(0.0, min(1.0, frac))


def _power_draw_w(ch13_pwm: int) -> float:
    return IDLE_DRAW_W + _lights_fraction(ch13_pwm) * LIGHTS_MAX_DRAW_W


def _wh_to_voltage(wh_remaining: float) -> float:
    """Simplified linear 4S LiPo discharge curve."""
    soc = max(0.0, min(1.0, wh_remaining / BATT_CAPACITY_WH))
    return VOLT_EMPTY + soc * (VOLT_FULL - VOLT_EMPTY)


# ── shared command queue ──────────────────────────────────────────────────────

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


# ── MAVLink helpers ───────────────────────────────────────────────────────────

def set_param(mav, name, val):
    pid = name.encode().ljust(16, b"\x00")
    mav.mav.param_set_send(mav.target_system, mav.target_component, pid, val, 9)
    ack = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)
    if ack:
        print("  %s = %.1f" % (name, ack.param_value))
    else:
        print("  %s -- no ack (param may not exist)" % name)
    time.sleep(0.2)


def _set_param_noack(mav, name: str, val: float):
    """Send param_set without waiting for ack — used for periodic voltage updates."""
    pid = name.encode().ljust(16, b"\x00")
    mav.mav.param_set_send(mav.target_system, mav.target_component, pid, val, 9)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    conn = sys.argv[1] if len(sys.argv) > 1 else "udp:127.0.0.1:14551"
    mav = mavutil.mavlink_connection(conn)
    mav.wait_heartbeat(timeout=15)
    print("Connected to system %d" % mav.target_system)

    # SITL environment overrides
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
    print("Configuring SITL environment ...")
    for name, val in sitl_params.items():
        set_param(mav, name, val)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    doris_params = {
        "DORIS_PRF_ID":   9999.0,
        "DORIS_UPL_DATE": float(now.strftime("%Y%m%d")),
        "DORIS_UPL_TIME": float(now.strftime("%H%M")),
        "DORIS_BTM_TIM":  30.0,   # 30 s bottom time for SITL test
        "DORIS_DPT_GAT":  1.0,    # shallow depth gate for SITL
        "DORIS_MIN_VOLT": 10.0,   # min valid range is 10-25V; SITL battery reads ~12-13V
        "DORIS_BTM_THR":  5.0,
        "DORIS_BTM_AVG":  10.0,   # short averaging window for SITL
        "DORIS_BRN_MIN":  30.0,   # burn wire minimum: 30s instead of 30-50min
        "DORIS_ASC_AVG":  10.0,   # ascent confirmation window: 10s instead of 120s
        "DORIS_LGT_BRT":  75.0,
        # Recording enabled on all phases — Lua skips the actual HTTP call in
        # SITL but still emits "DIVE: IPcam recording started/stopped" at the
        # correct trigger point, which is what we want to verify.
        "DORIS_REC_EN":   1.0,
        "DORIS_DSC_REC":  1.0,
        "DORIS_BTM_REC":  1.0,
        "DORIS_ASC_REC":  1.0,
        "DORIS_START":    1.0,    # authorize mission
    }
    print("Setting DORIS test profile (ID 9999) ...")
    for name, val in doris_params.items():
        set_param(mav, name, val)

    print("\nWaiting for Lua pre-arm checks to pass (CONFIG -> MISSION_START) ...")
    print("  Requires: GPS 3D fix, battery >= 1.0V (SITL), no leak, valid profile\n")
    print("  Power sim: %.0fW idle, up to %.0fW with full lights (CH13)\n"
          % (IDLE_DRAW_W, IDLE_DRAW_W + LIGHTS_MAX_DRAW_W))

    # Start reading stdin in background
    threading.Thread(target=_stdin_reader, daemon=True).start()

    header = "%6s  %-14s  %7s  %7s  %7s  %6s  %5s  %5s  %6s" % (
        "Time", "State", "Depth", "DscRate", "AscRate", "BattV", "Relay", "Watt", "BattWh")
    print(header)
    print("-" * len(header))

    last_state    = STATE_CONFIG
    deploy_ready  = False
    deployed      = False
    depth = dsc = asc = bv = 0.0
    rl = 0
    ch13_pwm      = LIGHTS_PWM_MIN   # assume lights off until we hear otherwise
    batt_wh       = BATT_CAPACITY_WH
    gps_disabled  = False
    start              = time.time()
    last_row           = 0.0
    last_power_update  = 0.0
    timeout            = 7200

    while not _quit_event.is_set() and time.time() - start < timeout:
        elapsed = time.time() - start

        msg = mav.recv_match(
            type=["NAMED_VALUE_FLOAT", "STATUSTEXT", "SERVO_OUTPUT_RAW"],
            blocking=True, timeout=0.5
        )
        if msg is not None:
            mtype = msg.get_type()
            if mtype == "STATUSTEXT":
                text = msg.text.rstrip("\x00")
                if "ipcam" in text.lower() or "recording" in text.lower():
                    print("%6.1f  *** REC: %s ***" % (elapsed, text))
                else:
                    print("%6.1f  STATUS: %s" % (elapsed, text))
            elif mtype == "SERVO_OUTPUT_RAW":
                ch13_pwm = getattr(msg, "servo13_raw", ch13_pwm)
            else:
                name = msg.name.rstrip("\x00")
                if name == "STATE":
                    last_state = int(msg.value)
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

        # GPS loss simulation: disable when deeper than GPS_LOSS_DEPTH, re-enable on surface
        if not gps_disabled and depth > GPS_LOSS_DEPTH:
            _set_param_noack(mav, "SIM_GPS1_DISABLE", 1.0)
            gps_disabled = True
            print("%6.1f  [gps] GPS disabled at %.1fm depth" % (elapsed, depth))
        elif gps_disabled and depth < GPS_LOSS_DEPTH:
            _set_param_noack(mav, "SIM_GPS1_DISABLE", 0.0)
            gps_disabled = False
            print("%6.1f  [gps] GPS re-enabled at %.1fm depth" % (elapsed, depth))

        # Power simulation: drain battery and update SIM_BATT_VOLTAGE every 10 s
        if elapsed - last_power_update >= POWER_UPDATE_INTERVAL:
            dt_h = (elapsed - last_power_update) / 3600.0
            watts = _power_draw_w(ch13_pwm)
            batt_wh = max(0.0, batt_wh - watts * dt_h)
            new_voltage = _wh_to_voltage(batt_wh)
            _set_param_noack(mav, "SIM_BATT_VOLTAGE", new_voltage)
            last_power_update = elapsed

        # Print the prompt once when MISSION_START is first reached
        if last_state >= STATE_MISSION_START and not deploy_ready:
            deploy_ready = True
            print("\n%6.1f  *** MISSION_START — vehicle armed and ready ***" % elapsed)
            print("        Type 'deploy' + Enter to start descent\n")

        # Handle operator commands
        cmd = _take_command()
        if cmd == "deploy" and deploy_ready and not deployed:
            print("\n%6.1f  [deploy] Setting SIM_BUOYANCY = %.1f N — vehicle sinking\n"
                  % (elapsed, DEPLOY_BUOYANCY))
            set_param(mav, "SIM_BUOYANCY", DEPLOY_BUOYANCY)
            deployed = True
        elif cmd == "deploy" and not deploy_ready:
            print("  [deploy] Not ready — waiting for MISSION_START first")
        elif cmd == "surface":
            print("\n%6.1f  [surface] Setting SIM_BUOYANCY = %.1f N\n"
                  % (elapsed, SURFACE_BUOYANCY))
            set_param(mav, "SIM_BUOYANCY", SURFACE_BUOYANCY)
        elif cmd == "stop":
            print("\n%6.1f  [stop] Setting DORIS_START = 0\n" % elapsed)
            set_param(mav, "DORIS_START", 0.0)
        elif cmd is not None and cmd not in ("q", "quit"):
            print("  Unknown command '%s'. Available: deploy, surface, stop, q" % cmd)

        if elapsed - last_row >= 3.0:
            last_row = elapsed
            sname  = STATE_NAMES.get(last_state, "?%d" % last_state)
            watts  = _power_draw_w(ch13_pwm)
            print("%6.1f  %-14s  %7.1f  %7.3f  %7.3f  %6.1f  %5d  %5.0f  %6.1f" % (
                elapsed, sname, depth, dsc, asc, bv, rl, watts, batt_wh))

        if last_state == 4:  # RECOVERY
            print("\n%6.1f  *** Mission complete — RECOVERY reached ***" % elapsed)
            break

    _quit_event.set()
    print("\nDone monitoring.")
    mav.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _quit_event.set()
        print("\nAborted.")
