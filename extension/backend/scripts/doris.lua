--[[
   DORIS dive script for ArduSub

   Mission phases:
     CONFIG -> MISSION_START -> DESCENT -> ON_BOTTOM -> ASCENT -> RECOVERY

   DORIS is a passive deep-ocean lander with no thrusters.  Descent is
   driven by static buoyancy; ascent is triggered by a drop-weight
   release.  This script manages state transitions and light control.

   DORIS_START persists across reboots via EEPROM.  When the backend
   sets DORIS_START=1 (e.g. at the lab), the value survives power
   cycles.  On boot the script enters CONFIG and runs surface pre-arm
   checks (GPS fix, battery voltage, leak sensor, mission profile).
   Only after all checks pass does it transition to MISSION_START.

   An ArduPilot arming gate (aux auth) prevents arming without a valid
   mission profile.  If the vehicle is deployed into water while still
   in CONFIG (no mission loaded, or pre-arm not yet passed), a depth-
   based deadman releases the drop-weight and transitions to ASCENT
   for recovery.  This fires regardless of DORIS_START, so a splash
   without ever pressing "Load Mission" still recovers the vehicle.

   After pre-arm the script arms the autopilot, then waits for DORIS
   to passively sink past a configurable depth gate (DORIS_DPT_GAT
   metres).  Crossing the gate marks the start of the active mission.

   Bottom detection uses the descent rate from ArduPilot's AHRS/EKF,
   averaged over a configurable window (DORIS_BTM_AVG seconds).  When
   the buffer is full and the averaged rate drops below DORIS_BTM_THR
   cm/s the vehicle is considered on-bottom.

   Lights support continuous and interval modes (DORIS_LGT_MOD).
   Bottom lights can be delayed (DORIS_BTM_DLY seconds) to allow
   settling before activating.

   In-mission failsafe monitoring runs every cycle during active
   states (MISSION_START through ASCENT).  Leak detection, low
   battery, and max-depth violations trigger an immediate weight
   release and transition to RECOVERY.

   ArduPilot's built-in failsafes (EKF, GCS, pilot input, crash
   check) are disabled at boot because GPS/GCS/pilot are unavailable
   underwater.  Leak, pressure, and temperature failsafes are set
   to warn-only so the script can decide the appropriate response.
   Battery failsafe actions are also disabled; the script monitors
   voltage directly via DORIS_MIN_VOLT.

   RECOVERY is a terminal state: the autopilot is disarmed and all
   outputs are safe.  The system stays running for data retrieval but
   is safe for a hard power-off.

   Requires: ArduSub with Lua scripting enabled (SCR_ENABLE = 1)
--]]

---@diagnostic disable: param-type-mismatch
---@diagnostic disable: need-check-nil

local MAV_SEVERITY = {
    EMERGENCY = 0,
    ALERT     = 1,
    CRITICAL  = 2,
    ERROR     = 3,
    WARNING   = 4,
    NOTICE    = 5,
    INFO      = 6,
    DEBUG     = 7,
}

local UPDATE_INTERVAL_MS = 500
local ARM_RETRY_MS       = 2000

-- VIDEO_INTERVAL first-frame gating: after ipcam_start the record clock
-- does not begin counting until the extension reports frames are
-- actually landing on disk (RTSP connect + jitter-buffer fill can take
-- anywhere from <1 s to ~15 s on this camera).  If the readiness signal
-- can't be confirmed within this many ms we start the clock anyway so a
-- status-endpoint outage can never hang a record cycle.
local IPCAM_FIRST_FRAME_TIMEOUT_MS = 30000

local surface_pressure = baro:get_pressure() or 101325

-- ArduSub SITL exposes SIM_BUOYANCY; used for depth fallback and relay tests.
local is_sitl = false

local LIGHT_PWM_MIN = 1100
local LIGHT_PWM_MAX = 1900

-- state machine
local STATE_CONFIG        = -1
local STATE_MISSION_START = 0
local STATE_DESCENT       = 1
local STATE_ON_BOTTOM     = 2
local STATE_ASCENT        = 3
local STATE_RECOVERY      = 4

-- ?????????? DORIS parameter table ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
assert(param:add_table(73, "DORIS_", 38),
       "DIVE: could not add DORIS_ param table")

-- mission control
assert(param:add_param(73, 1,  "START",   0),  "DORIS_START")
assert(param:add_param(73, 2,  "BTM_TIM", 2700), "DORIS_BTM_TIM")
assert(param:add_param(73, 3,  "DSC_LGT", 0),  "DORIS_DSC_LGT")
assert(param:add_param(73, 4,  "BTM_LGT", 0),  "DORIS_BTM_LGT")
assert(param:add_param(73, 5,  "ASC_LGT", 0),  "DORIS_ASC_LGT")
assert(param:add_param(73, 6,  "LGT_BRT", 75), "DORIS_LGT_BRT")
assert(param:add_param(73, 7,  "STATE",  -1),  "DORIS_STATE")
assert(param:add_param(73, 8,  "BTM_THR", 5),  "DORIS_BTM_THR")
assert(param:add_param(73, 9,  "BTM_AVG", 30), "DORIS_BTM_AVG")
assert(param:add_param(73, 10, "DPT_GAT", 3),  "DORIS_DPT_GAT")
assert(param:add_param(73, 11, "LGT_MOD", 0),  "DORIS_LGT_MOD")
assert(param:add_param(73, 12, "LGT_ON",  10), "DORIS_LGT_ON")
assert(param:add_param(73, 13, "LGT_OFF", 5),  "DORIS_LGT_OFF")
assert(param:add_param(73, 14, "BTM_DLY", 30), "DORIS_BTM_DLY")
-- mission profile & safety
assert(param:add_param(73, 15, "PRF_ID",   0),    "DORIS_PRF_ID")
assert(param:add_param(73, 16, "UPL_DATE", 0),    "DORIS_UPL_DATE")
assert(param:add_param(73, 17, "UPL_TIME", 0),    "DORIS_UPL_TIME")
assert(param:add_param(73, 18, "MIN_VOLT", 14.0), "DORIS_MIN_VOLT")
-- Navigator relay channel for the mirrored release output; -1 disables it.
assert(param:add_param(73, 19, "RELAY_CH", 0),    "DORIS_RELAY_CH")
assert(param:add_param(73, 20, "INJ_LEAK", 0),    "DORIS_INJ_LEAK")
assert(param:add_param(73, 21, "MAX_DPTH", 6100), "DORIS_MAX_DPTH")
assert(param:add_param(73, 22, "LGT_TST", 0),    "DORIS_LGT_TST")
assert(param:add_param(73, 23, "LOG_INTV", 1000), "DORIS_LOG_INTV")
assert(param:add_param(73, 24, "GPS_RBT",  0),    "DORIS_GPS_RBT")
-- IP camera recorder (HTTP to DORIS extension; per-phase continuous video)
assert(param:add_param(73, 25, "REC_EN",   0),    "DORIS_REC_EN")
assert(param:add_param(73, 26, "DSC_REC",  0),    "DORIS_DSC_REC")
assert(param:add_param(73, 27, "BTM_REC",  0),    "DORIS_BTM_REC")
assert(param:add_param(73, 28, "ASC_REC",  0),    "DORIS_ASC_REC")
assert(param:add_param(73, 29, "CAM_DLY",  0),    "DORIS_CAM_DLY")
-- relay safety
assert(param:add_param(73, 30, "BRN_MIN", 7200),  "DORIS_BRN_MIN")
assert(param:add_param(73, 31, "ASC_DPT", 10),    "DORIS_ASC_DPT")  -- deprecated: kept for param-file compat
-- ascent confirmation (velocity-based relay deactivation)
assert(param:add_param(73, 32, "ASC_THR", 10),    "DORIS_ASC_THR")  -- cm/s sustained upward velocity
assert(param:add_param(73, 33, "ASC_AVG", 120),   "DORIS_ASC_AVG")  -- averaging window in seconds
-- bottom-phase camera mode + interval/timelapse timings
-- BTM_CMOD: 0=off, 1=continuous video, 2=video interval (record/pause duty cycle),
--          3=timelapse (snapshot every BTM_PAUS seconds)
assert(param:add_param(73, 34, "BTM_CMOD", 0),    "DORIS_BTM_CMOD")
-- BTM_RECS: video_record window in seconds (used only when BTM_CMOD=2)
assert(param:add_param(73, 35, "BTM_RECS", 0),    "DORIS_BTM_RECS")
-- BTM_PAUS: video_pause (BTM_CMOD=2) OR capture_frequency (BTM_CMOD=3) in seconds
assert(param:add_param(73, 36, "BTM_PAUS", 0),    "DORIS_BTM_PAUS")
-- Timelapse light strobe (only used when BTM_CMOD=3): the Lua's bottom
-- dispatcher turns RC9 ON for TL_PRE_S seconds before each snapshot
-- fires (camera settle window), holds it ON for TL_PST_S seconds after
-- the snapshot returns (so the marine biologist gets a couple of well-
-- lit frames at full exposure), then drops it OFF for the rest of
-- BTM_PAUS.  Effective minimum capture frequency = PRE + PST + 1 s.
assert(param:add_param(73, 37, "TL_PRE_S", 2),    "DORIS_TL_PRE_S")
assert(param:add_param(73, 38, "TL_PST_S", 1),    "DORIS_TL_PST_S")


local DORIS_START    = Parameter("DORIS_START")
local DORIS_BTM_TIM  = Parameter("DORIS_BTM_TIM")
local DORIS_DSC_LGT  = Parameter("DORIS_DSC_LGT")
local DORIS_BTM_LGT  = Parameter("DORIS_BTM_LGT")
local DORIS_ASC_LGT  = Parameter("DORIS_ASC_LGT")
local DORIS_LGT_BRT  = Parameter("DORIS_LGT_BRT")
local DORIS_STATE    = Parameter("DORIS_STATE")
local DORIS_BTM_THR  = Parameter("DORIS_BTM_THR")
local DORIS_BTM_AVG  = Parameter("DORIS_BTM_AVG")
local DORIS_DPT_GAT  = Parameter("DORIS_DPT_GAT")
local DORIS_LGT_MOD  = Parameter("DORIS_LGT_MOD")
local DORIS_LGT_ON   = Parameter("DORIS_LGT_ON")
local DORIS_LGT_OFF  = Parameter("DORIS_LGT_OFF")
local DORIS_BTM_DLY  = Parameter("DORIS_BTM_DLY")
local DORIS_PRF_ID   = Parameter("DORIS_PRF_ID")
local DORIS_UPL_DATE = Parameter("DORIS_UPL_DATE")
local DORIS_UPL_TIME = Parameter("DORIS_UPL_TIME")
local DORIS_MIN_VOLT = Parameter("DORIS_MIN_VOLT")
local DORIS_RELAY_CH = Parameter("DORIS_RELAY_CH")
local DORIS_INJ_LEAK = Parameter("DORIS_INJ_LEAK")
local DORIS_MAX_DPTH = Parameter("DORIS_MAX_DPTH")
local DORIS_LGT_TST  = Parameter("DORIS_LGT_TST")
local DORIS_GPS_RBT  = Parameter("DORIS_GPS_RBT")
local DORIS_BRN_MIN  = Parameter("DORIS_BRN_MIN")
local DORIS_ASC_DPT  = Parameter("DORIS_ASC_DPT")  -- deprecated
local DORIS_ASC_THR  = Parameter("DORIS_ASC_THR")
local DORIS_ASC_AVG  = Parameter("DORIS_ASC_AVG")

-- GPS self-heal: reboot up to 2 times if the GPS driver never receives
-- data.  DORIS_GPS_RBT counts reboots and persists in EEPROM so the
-- limit survives script restarts.  Cleared only when pre-arm passes.
local gps_reboot_attempted = false
do
    local rbt = DORIS_GPS_RBT:get() or 0
    if rbt >= 2 then
        gps_reboot_attempted = true
        gcs:send_text(MAV_SEVERITY.INFO,
            "DIVE: GPS reboot limit reached, skipping further reboots")
    elseif rbt >= 1 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: GPS reboot count = %d", rbt))
    end
end

-- DORIS_START persists in EEPROM across reboots.
-- Cleared only in RECOVERY after a mission completes.
DORIS_STATE:set(-1)
param:set_and_save("DISARM_DELAY", 0)

-- ArduPilot arming checks: enable only checks relevant to DORIS.
-- Baro(2) + GPS(8) + INS(16) + Params(32) +
-- Board voltage(128) + Logging(1024) + System(8192) + AuxAuth(131072) = 140474
param:set_and_save("ARMING_CHECK", 140474)

-- EKF source: override ArduSub defaults (ExternalNav) to use GPS at the surface.
-- FS_EKF_ACTION=0 handles the expected GPS loss once submerged.
param:set_and_save("EK3_SRC1_POSXY", 1)  -- GPS for horizontal position
param:set_and_save("EK3_SRC1_VELXY", 1)  -- GPS for horizontal velocity

-- ArduPilot failsafe configuration: prevent autonomous disarms.
-- DORIS operates without GPS, GCS, or pilot input underwater,
-- so ArduPilot's built-in failsafe actions must not disarm.
-- The Lua script owns all critical failsafe responses via check_failsafes().
param:set_and_save("FS_EKF_ACTION", 0)   -- GPS loss expected underwater
param:set_and_save("FS_GCS_ENABLE", 0)   -- no GCS link underwater
param:set_and_save("FS_PILOT_INPUT", 0)  -- no pilot by design
param:set_and_save("FS_CRASH_CHECK", 0)  -- no motors, attitude changes are normal
param:set_and_save("FS_TERRAIN_ENAB", 0) -- not using terrain data
param:set_and_save("FS_LEAK_ENABLE", 1)  -- warn only; script handles response
param:set_and_save("FS_PRESS_ENABLE", 1) -- warn only; script treats as leak
param:set_and_save("FS_TEMP_ENABLE", 1)  -- warn only; logged for telemetry
param:set_and_save("BATT_FS_LOW_ACT", 0) -- script monitors voltage directly
param:set_and_save("BATT_FS_CRT_ACT", 0)

-- Lights: ensure servo output 13 passes through RC input channel 9.
-- RC9:set_override() sets the input; this mapping drives the physical pin.
param:set_and_save("SERVO13_FUNCTION", 59) -- 59 = RCIN9

-- Barometer: use seawater specific gravity for depth calculations.
param:set_and_save("BARO_SPEC_GRAV", 1.025)

-- ?????????? descent-rate circular buffer ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local dr = { buf = {}, idx = 0, count = 0, size = 60 }

-- ?????????? ascent-rate circular buffer (relay deactivation confirmation) ?????????????????????????????????
local ar = { buf = {}, idx = 0, count = 0, size = 240 }

local function init_ring(r, window_sec)
    r.size  = math.max(math.ceil(window_sec / (UPDATE_INTERVAL_MS / 1000.0)), 10)
    r.buf   = {}
    r.idx   = 0
    r.count = 0
    for i = 1, r.size do r.buf[i] = 0 end
end

local function add_ring_sample(r, val)
    r.idx = (r.idx % r.size) + 1
    r.buf[r.idx] = val
    if r.count < r.size then r.count = r.count + 1 end
end

local function get_ring_avg(r)
    if r.count == 0 then return nil end
    local sum = 0
    for i = 1, r.count do sum = sum + r.buf[i] end
    return sum / r.count
end

-- ?????????? runtime state ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local state             = STATE_CONFIG
local dive_start_ms     = 0
local bottom_start_ms   = 0
local ascent_start_ms   = 0
local arm_start_ms      = 0
local armed_once        = false
local bottom_delay_done = false
local recovery_done     = false
local last_update_ms    = 0
local script_start_ms   = 0

-- pre-arm state
local prearm_passed      = false
local last_prearm_log_ms = 0
local batt_voltage       = 0.0

-- relay state
local relay_active       = false

-- in-mission failsafe state
local leak_detected = false

-- light interval state
local light_on       = true
local light_cycle_ms = 0

-- light test state (auto-clears after timeout)
local lgt_tst_start_ms = 0
-- 10000 = 10000 (inlined)

-- telemetry tracking (updated every cycle by update_sensors)
-- packed into a table to stay under Lua's 200-local-variable limit
local telem = {
    depth = 0.0, max_depth = 0.0, min_temp = 999.0,
    dsc_rate = 0.0, asc_rate = 0.0, batt_pct = 0.0,
    prev_depth = 0.0, prev_depth_ms = 0, last_log_ms = 0,
    alpha = 0.3,
}

-- snapshotted config (read once at CONFIG -> MISSION_START)
-- packed into a table to stay under Lua's 100-local limit
local cfg = {
    rls_sec_ms  = 60000,
    dsc_lgt     = false,
    btm_lgt     = false,
    asc_lgt     = false,
    lgt_pwm     = LIGHT_PWM_MIN,
    btm_thr_mps = 0.05,
    dpt_gat_m   = 5.0,
    lgt_mod     = 0,
    lgt_on_ms   = 10000,
    lgt_off_ms  = 5000,
    btm_dly_ms  = 30000,
}

-- snapshotted IP camera policy (set in snapshot_config from DORIS_* params)
-- btm_cmod: 0=off  1=continuous  2=video-interval  3=timelapse
-- btm_rec_ms / btm_pau_ms carry the seconds-based DORIS_BTM_RECS / BTM_PAUS
-- in milliseconds for the on-bottom duty-cycle and timelapse timers.
local ipcam_cfg = {
    rec_en = false,
    dsc_rec = false,
    btm_rec = false,
    asc_rec = false,
    cam_btm_dly_ms = 0,
    btm_cmod = 0,
    btm_rec_ms = 0,
    btm_pau_ms = 0,
    -- TIMELAPSE light strobe (DORIS_TL_PRE_S / DORIS_TL_PST_S in ms).
    tl_pre_ms = 2000,
    tl_post_ms = 1000,
}

local RC9 = rc:get_channel(9)
if RC9 then
    gcs:send_text(MAV_SEVERITY.INFO, "DIVE: RC9 (lights) channel acquired")
else
    gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: RC9 channel is nil – lights will not work")
end

-- HTTP recorder client (Companion / BlueOS DORIS extension)
-- IPCAM: HOST=127.0.0.1, BIND_PORT=9979 (inlined)
local ipcam_recording        = false
local ipcam_btm_started      = false
-- All remaining runtime state for the interval / timelapse / finalize
-- orchestrators lives in a single table so we stay well under Lua's
-- per-closure upvalue cap.  cycle_start_ms == 0 means "duty cycle not
-- yet initialized for this bottom visit"; last_snap_ms == 0 means "no
-- timelapse snapshot fired yet"; finalize_sent flips true exactly once
-- per dive at RECOVERY entry.
local ipcam_state = {
    cycle_start_ms  = 0,
    cycle_is_record = false,
    -- VIDEO_INTERVAL first-frame gating.  ``cycle_started`` flips true
    -- once the duty cycle has begun for the current bottom visit (so the
    -- inaugural record window is only kicked off once).
    -- ``cycle_awaiting_frames`` is true between issuing ipcam_start and
    -- confirming frames are on disk; while true the record clock
    -- (``cycle_start_ms``) is held at 0 and not counted.
    -- ``cycle_wait_start_ms`` stamps when the wait began so the safety
    -- timeout can fire.
    cycle_started        = false,
    cycle_awaiting_frames = false,
    cycle_wait_start_ms  = 0,
    last_snap_ms    = 0,
    -- Timelapse: ``next_snap_ms`` is the absolute millis when the
    -- next snapshot should fire; the dispatcher sets it to ``now +
    -- tl_pre_ms`` on first entry so the very first cycle gets a full
    -- pre-roll instead of an instant snap.  ``last_snap_ms`` is
    -- reused as ``tl_last_snap_ms`` (when the most recent snapshot
    -- actually fired) to drive the post-roll light hold.
    next_snap_ms    = 0,
    finalize_sent   = false,
}

-- ?????????? helpers ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local function get_depth_m()
    local pressure = baro:get_pressure()
    local depth_baro = nil
    if pressure then
        depth_baro = (pressure - surface_pressure) / (1025.0 * 9.80665)
    end
    if not is_sitl then
        return depth_baro
    end
    -- SITL: baro depth can stay near zero at the surface; use EKF NED when sinking.
    if depth_baro and depth_baro >= 0.05 then
        return depth_baro
    end
    local pos = ahrs:get_relative_position_NED_home()
    if pos then
        local ned_depth = -pos:z()
        if ned_depth > 0 then
            return ned_depth
        end
    end
    return depth_baro
end

local function brightness_to_pwm(pct)
    if pct <= 0 then return LIGHT_PWM_MIN end
    if pct >= 100 then return LIGHT_PWM_MAX end
    return math.floor(LIGHT_PWM_MIN + (pct / 100.0) * (LIGHT_PWM_MAX - LIGHT_PWM_MIN))
end

local function reset_light_cycle(now_ms)
    light_on = true
    light_cycle_ms = now_ms
end

local function update_lights(enabled, now_ms)
    if not RC9 then return end
    if not enabled then
        RC9:set_override(LIGHT_PWM_MIN)
        return
    end
    if cfg.lgt_mod == 0 then
        RC9:set_override(cfg.lgt_pwm)
        return
    end
    -- interval mode
    local elapsed = now_ms - light_cycle_ms
    if light_on then
        if elapsed >= cfg.lgt_on_ms then
            light_on = false
            light_cycle_ms = now_ms
            RC9:set_override(LIGHT_PWM_MIN)
        else
            RC9:set_override(cfg.lgt_pwm)
        end
    else
        if elapsed >= cfg.lgt_off_ms then
            light_on = true
            light_cycle_ms = now_ms
            RC9:set_override(cfg.lgt_pwm)
        else
            RC9:set_override(LIGHT_PWM_MIN)
        end
    end
end

-- The release request is mirrored to both controllers: the AGT drives its own
-- output from the RELAY named float published by update_telemetry, and
-- ArduPilot drives DORIS_RELAY_CH.  Exactly one of the two outputs may be
-- wired to the actuator; DORIS_RELAY_CH=-1 disables the Navigator output.
local function navigator_relay_channel()
    local ch = DORIS_RELAY_CH:get()
    if not ch then return nil end
    ch = math.floor(ch)
    if ch < 0 then return nil end
    return ch
end

local function relay_target_text(ch)
    if ch then
        return string.format("AGT + Navigator CH%d", ch)
    end
    return "AGT"
end

local function activate_relay()
    relay_active = true
    local ch = navigator_relay_channel()
    if ch then
        relay:on(ch)
    end
    if is_sitl then
        -- ~2 kg net positive buoyancy for ascent after weight drop in SITL
        param:set_and_save("SIM_BUOYANCY", 19.6)
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: SITL SIM_BUOYANCY set positive (19.6 N)")
    end
    gcs:send_text(MAV_SEVERITY.WARNING,
        string.format("DIVE: Release ON at %.1fm (%s)",
            get_depth_m() or 0, relay_target_text(ch)))
end

local function deactivate_relay()
    if not relay_active then return end
    relay_active = false
    local ch = navigator_relay_channel()
    if ch then
        relay:off(ch)
    end
    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: Release OFF at %.1fm (%s)",
            get_depth_m() or 0, relay_target_text(ch)))
end

local function check_leak()
    local val = DORIS_INJ_LEAK:get()
    if val and val > 0 then
        return true
    end
    return false
end

local function validate_profile()
    local prf_id = DORIS_PRF_ID:get() or 0
    if prf_id <= 0 then
        return false, "no profile loaded (PRF_ID=0)"
    end

    local upl_date = DORIS_UPL_DATE:get() or 0
    if upl_date <= 0 then
        return false, "no upload timestamp (UPL_DATE=0)"
    end

    local rls = DORIS_BTM_TIM:get() or 0
    if rls <= 0 then
        return false, "BTM_TIM must be > 0"
    end

    local min_v = DORIS_MIN_VOLT:get() or 0
    if min_v < 10.0 or min_v > 25.0 then
        return false, string.format("MIN_VOLT %.1f out of range 10-25", min_v)
    end

    local brt = DORIS_LGT_BRT:get() or 0
    if brt < 0 or brt > 100 then
        return false, string.format("LGT_BRT %.0f out of range 0-100", brt)
    end

    return true, nil
end

local function update_profile_auth(auth_id)
    if not auth_id then return end

    local ok, reason = validate_profile()
    if ok then
        arming:set_aux_auth_passed(auth_id)
    else
        arming:set_aux_auth_failed(auth_id, reason)
    end
end

local function check_failsafes()
    if state == STATE_CONFIG or state == STATE_ASCENT or state == STATE_RECOVERY then
        return false
    end

    if check_leak() and not leak_detected then
        leak_detected = true
        gcs:send_text(MAV_SEVERITY.CRITICAL,
            "DIVE: FAILSAFE leak detected! Releasing weight")
        activate_relay()
        return true
    end

    local min_volt = DORIS_MIN_VOLT:get() or 14.0
    if batt_voltage > 1.0 and batt_voltage < min_volt then
        gcs:send_text(MAV_SEVERITY.CRITICAL,
            string.format("DIVE: FAILSAFE low battery %.1fV < %.1fV, releasing weight",
                batt_voltage, min_volt))
        activate_relay()
        return true
    end

    local max_depth = DORIS_MAX_DPTH:get() or 6100
    local depth = get_depth_m()
    if depth and depth > max_depth then
        gcs:send_text(MAV_SEVERITY.CRITICAL,
            string.format("DIVE: FAILSAFE max depth %.0fm > %.0fm, releasing weight",
                depth, max_depth))
        activate_relay()
        return true
    end

    return false
end

local function snapshot_config()
    local rls_sec = DORIS_BTM_TIM:get()
    local brt     = DORIS_LGT_BRT:get()
    local btm_thr = DORIS_BTM_THR:get()
    local btm_avg = DORIS_BTM_AVG:get()
    local dpt_gat = DORIS_DPT_GAT:get()
    local lgt_mod = DORIS_LGT_MOD:get()
    local lgt_on  = DORIS_LGT_ON:get()
    local lgt_off = DORIS_LGT_OFF:get()
    local btm_dly = DORIS_BTM_DLY:get()

    cfg.rls_sec_ms  = math.max(rls_sec, 1) * 1000
    cfg.dsc_lgt     = DORIS_DSC_LGT:get() >= 1
    cfg.btm_lgt     = DORIS_BTM_LGT:get() >= 1
    cfg.asc_lgt     = DORIS_ASC_LGT:get() >= 1
    cfg.lgt_pwm     = brightness_to_pwm(brt)
    cfg.btm_thr_mps = math.max(btm_thr, 0.1) / 100.0
    cfg.dpt_gat_m   = math.max(dpt_gat, 2.0)
    cfg.lgt_mod     = lgt_mod >= 1 and 1 or 0
    cfg.lgt_on_ms   = math.max(lgt_on, 1) * 1000
    cfg.lgt_off_ms  = math.max(lgt_off, 1) * 1000
    cfg.btm_dly_ms  = math.max(btm_dly, 0) * 1000

    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: gate=%.1fm rls=%ds thr=%.1fcm/s avg=%ds",
            cfg.dpt_gat_m, rls_sec, btm_thr, btm_avg))
    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: lights dsc=%d btm=%d asc=%d brt=%d%% pwm=%d mode=%s RC9=%s",
            cfg.dsc_lgt and 1 or 0, cfg.btm_lgt and 1 or 0,
            cfg.asc_lgt and 1 or 0, brt, cfg.lgt_pwm,
            cfg.lgt_mod == 1 and "interval" or "continuous",
            RC9 and "ok" or "NIL"))
    if cfg.lgt_mod == 1 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: interval on=%ds off=%ds", lgt_on, lgt_off))
    end
    if cfg.btm_dly_ms > 0 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: bottom light delay=%ds", btm_dly))
    end

    local function _pgv(name, default)
        local v = param:get(name)
        if v == nil then return default end
        return v
    end

    ipcam_cfg.rec_en  = (_pgv("DORIS_REC_EN", 0) >= 1.0)
    ipcam_cfg.dsc_rec = (_pgv("DORIS_DSC_REC", 0) >= 1.0)
    ipcam_cfg.btm_rec = (_pgv("DORIS_BTM_REC", 0) >= 1.0)
    ipcam_cfg.asc_rec = (_pgv("DORIS_ASC_REC", 0) >= 1.0)
    ipcam_cfg.cam_btm_dly_ms = math.max(0, math.floor((_pgv("DORIS_CAM_DLY", 0)) * 1000.0))

    -- Bottom-phase camera mode + interval/timelapse timings.
    -- Back-compat: a profile that predates DORIS_BTM_CMOD (value 0) but
    -- has DORIS_BTM_REC=1 is treated as continuous video on bottom, so
    -- old configurations keep working unchanged.
    ipcam_cfg.btm_cmod   = math.floor(_pgv("DORIS_BTM_CMOD", 0))
    if ipcam_cfg.btm_cmod < 0 or ipcam_cfg.btm_cmod > 3 then
        ipcam_cfg.btm_cmod = 0
    end
    if ipcam_cfg.btm_cmod == 0 and ipcam_cfg.btm_rec then
        ipcam_cfg.btm_cmod = 1
    end
    ipcam_cfg.btm_rec_ms = math.max(0, math.floor(_pgv("DORIS_BTM_RECS", 0) * 1000.0))
    ipcam_cfg.btm_pau_ms = math.max(0, math.floor(_pgv("DORIS_BTM_PAUS", 0) * 1000.0))
    -- Timelapse strobe windows; default 2 s pre-roll / 1 s post-roll
    -- if the operator's profile predates these params.
    ipcam_cfg.tl_pre_ms  = math.max(0, math.floor(_pgv("DORIS_TL_PRE_S", 2) * 1000.0))
    ipcam_cfg.tl_post_ms = math.max(0, math.floor(_pgv("DORIS_TL_PST_S", 1) * 1000.0))

    if ipcam_cfg.rec_en then
        local mode_name = "off"
        if     ipcam_cfg.btm_cmod == 1 then mode_name = "continuous"
        elseif ipcam_cfg.btm_cmod == 2 then mode_name = "interval"
        elseif ipcam_cfg.btm_cmod == 3 then mode_name = "timelapse"
        end
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: IPcam dsc=%d btm=%s asc=%d camDly=%dms",
                ipcam_cfg.dsc_rec and 1 or 0, mode_name,
                ipcam_cfg.asc_rec and 1 or 0,
                ipcam_cfg.cam_btm_dly_ms))
        if ipcam_cfg.btm_cmod == 2 then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: IPcam interval rec=%ds pause=%ds",
                    math.floor(ipcam_cfg.btm_rec_ms / 1000),
                    math.floor(ipcam_cfg.btm_pau_ms / 1000)))
        elseif ipcam_cfg.btm_cmod == 3 then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: IPcam timelapse every %ds",
                    math.floor(ipcam_cfg.btm_pau_ms / 1000)))
        end
    end
end

-- ── telemetry & dataflash (single function to stay under Lua's 200-local limit) ──

local function update_telemetry(now_ms)
    -- sensor tracking
    local d = get_depth_m()
    if d then
        telem.depth = d
        if d > telem.max_depth then
            telem.max_depth = d
        end
        if telem.prev_depth_ms > 0 then
            local dt = (now_ms - telem.prev_depth_ms) / 1000.0
            if dt > 0.01 then
                local delta = d - telem.prev_depth
                local rate = delta / dt
                local a = telem.alpha
                if rate > 0 then
                    telem.dsc_rate = telem.dsc_rate * (1.0 - a) + rate * a
                    telem.asc_rate = telem.asc_rate * (1.0 - a)
                else
                    telem.asc_rate = telem.asc_rate * (1.0 - a) + (-rate) * a
                    telem.dsc_rate = telem.dsc_rate * (1.0 - a)
                end
            end
        end
        telem.prev_depth    = d
        telem.prev_depth_ms = now_ms
    end

    local temp = baro:get_temperature()
    if temp and temp < telem.min_temp then
        telem.min_temp = temp
    end

    local pct = battery:capacity_remaining_pct(0)
    if pct then
        telem.batt_pct = pct
    end

    -- named floats (recorded into .mcap by BlueOS recorder)
    local mission_time_s = dive_start_ms > 0
        and (now_ms - dive_start_ms) / 1000.0 or 0
    local bottom_time_s = bottom_start_ms > 0
        and (now_ms - bottom_start_ms) / 1000.0 or 0

    gcs:send_named_float('STATE',    state)
    gcs:send_named_float('DEPTH',    telem.depth)
    gcs:send_named_float('MAX_DPTH', telem.max_depth)
    gcs:send_named_float('MIN_TEMP', telem.min_temp)
    gcs:send_named_float('DSC_RATE', telem.dsc_rate)
    gcs:send_named_float('ASC_RATE', telem.asc_rate)
    gcs:send_named_float('BTM_TIME', bottom_time_s)
    gcs:send_named_float('BATT_V',   batt_voltage)
    gcs:send_named_float('BATT_PCT', telem.batt_pct)
    gcs:send_named_float('MSN_TIME', mission_time_s)
    gcs:send_named_float('RELAY',    relay_active and 1 or 0)
    local ar_avg = get_ring_avg(ar) or 0
    gcs:send_named_float('ASC_VEL',  ar_avg)

    -- dataflash logging (written to ArduPilot .bin log)
    local interval = param:get("DORIS_LOG_INTV") or 1000
    if now_ms - telem.last_log_ms >= interval then
        telem.last_log_ms = now_ms
        logger:write('DORS',
            'Sta,Dep,MaxD,Tmp,DscR,AscR,BatV,BatP,Msn,Rly,AscV',
            'fffffffffff',
            state, telem.depth, telem.max_depth, telem.min_temp,
            telem.dsc_rate, telem.asc_rate, batt_voltage, telem.batt_pct,
            mission_time_s, relay_active and 1 or 0, ar_avg)
    end
end

local function ipcam_http_send(first_line, host, port)
    -- SITL short-circuit removed: the Lua now drives /rec/* and
    -- /api/v1/dive/finalize directly in both SITL and production so
    -- the same code path is exercised at every stage.  On BlueOS the
    -- autopilot container and the extension container share the host
    -- network namespace, so 127.0.0.1:8095 resolves to the extension.
    local sock = Socket(0)
    if not sock:bind("0.0.0.0", 0) then
        gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: IPcam bind failed")
        sock:close()
        return false
    end
    if not sock:connect(host, port) then
        gcs:send_text(MAV_SEVERITY.WARNING,
            string.format("DIVE: IPcam connect failed %s:%d", host, port))
        sock:close()
        return false
    end
    local req = string.format("%s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n",
        first_line, host)
    sock:send(req, string.len(req))
    sock:close()
    return true
end

-- Bounded HTTP GET that DOES read the response (unlike the fire-and-
-- forget ipcam_http_send above).  Returns the raw response text
-- (headers + body) or nil on any failure.  Kept deliberately small and
-- non-blocking-friendly: a single ``pollin`` with a short timeout so it
-- never stalls the dive update loop (which also services failsafes).
-- Only used to poll /rec/status during the brief first-frame wait.
local function ipcam_http_get(path, host, port)
    local sock = Socket(0)
    if not sock then return nil end
    if not sock:bind("0.0.0.0", 0) then sock:close(); return nil end
    if not sock:connect(host, port) then sock:close(); return nil end
    local req = string.format(
        "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, host)
    sock:send(req, string.len(req))
    local body = nil
    -- Localhost replies in <few ms; cap the wait at 100 ms.  The status
    -- payload is small and ``frames_flowing`` sits near the front of the
    -- JSON, so a single recv of the first chunk is sufficient.
    if sock:pollin(100) then
        local data = sock:recv(4096)
        if data and #data > 0 then body = data end
    end
    sock:close()
    return body
end

-- Query the recorder's readiness.  Returns true if frames are confirmed
-- on disk, false if the recorder explicitly reports not-yet, and nil if
-- the status couldn't be read / parsed (caller treats nil as "keep
-- waiting" up to the safety timeout).
local function ipcam_frames_flowing()
    local body = ipcam_http_get("/rec/status", "127.0.0.1", 8095)
    if not body then return nil end
    if body:find('"frames_flowing"%s*:%s*true') then return true end
    if body:find('"frames_flowing"%s*:%s*false') then return false end
    return nil
end

local function ipcam_http_start(host, port, seg_s, phase)
    local s = math.max(1, math.floor(seg_s))
    local line = string.format(
        "POST /rec/start?split_duration=%d&phase=%s", s, phase or "manual")
    return ipcam_http_send(line, host, port)
end

local function ipcam_http_stop(host, port)
    return ipcam_http_send("POST /rec/stop", host, port)
end

local function ipcam_http_rotate(host, port, phase, seg_s)
    -- Optional seg_s retunes the live splitmuxsink so the
    -- descent->bottom rotation can clamp continuous-mode bottom to
    -- 5 min chunks and the bottom->ascent rotation can restore the
    -- larger default.  Omitted (== nil/<=0) -> recorder keeps its
    -- prior segment policy.
    local q = string.format("?phase=%s", phase or "manual")
    if seg_s and seg_s > 0 then
        q = q .. string.format("&split_duration=%d", math.floor(seg_s))
    end
    return ipcam_http_send("POST /rec/rotate" .. q, host, port)
end

local function ipcam_http_snapshot(host, port, phase)
    return ipcam_http_send(
        string.format("POST /rec/snapshot?phase=%s", phase or "manual"),
        host, port)
end

local function ipcam_http_finalize(host, port, btm_cmod)
    -- bottom_mode tells the extension how to slice the on_bottom
    -- phase: 1=continuous (one MP4 per 5-min chunk), 2=interval (one
    -- MP4 per ipcam start/stop cycle), other=legacy concat-into-one.
    local q = ""
    if btm_cmod and btm_cmod > 0 then
        q = string.format("?bottom_mode=%d", math.floor(btm_cmod))
    end
    return ipcam_http_send("POST /api/v1/dive/finalize" .. q, host, port)
end

local function ipcam_start(phase, seg_s)
    if ipcam_recording then return end
    if not ipcam_cfg.rec_en then return end
    local s = seg_s
    if not s or s <= 0 then s = 1800 end
    if ipcam_http_start("127.0.0.1", 8095, s, phase or "manual") then
        ipcam_recording = true
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: IPcam recording started (%s, seg=%ds)",
                phase or "manual", math.floor(s)))
    else
        gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: IPcam recording start FAILED")
    end
end

local function ipcam_stop()
    if not ipcam_recording then return end
    ipcam_http_stop("127.0.0.1", 8095)
    ipcam_recording = false
    gcs:send_text(MAV_SEVERITY.INFO, "DIVE: IPcam recording stopped")
end

-- Zero-gap phase rotation: next .ts file will be tagged <phase>.  The
-- rtspsrc+muxer pipeline stays live; splitmuxsink fires split-now and
-- closes the current fragment at the next keyframe.  ``seg_s`` (if
-- given) also retunes max-size-time on the live splitmuxsink so the
-- caller can switch chunk granularity at the rotate point.
local function ipcam_rotate(phase, seg_s)
    if not ipcam_cfg.rec_en then return end
    if not ipcam_recording then
        -- No active pipeline; fall back to a fresh start so the caller's
        -- phase + segment request is still honored.
        ipcam_start(phase, seg_s)
        return
    end
    if ipcam_http_rotate("127.0.0.1", 8095, phase or "manual", seg_s) then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: IPcam rotate -> %s (seg=%ds)",
                phase or "manual",
                math.floor((seg_s and seg_s > 0) and seg_s or 0)))
    else
        gcs:send_text(MAV_SEVERITY.WARNING,
            string.format("DIVE: IPcam rotate -> %s FAILED", phase or "manual"))
    end
end

-- Single JPEG capture from the RTSP camera, saved on the extension side
-- to radcam_<stamp>_<phase>_<seq>.jpg.  The recorder must NOT be alive
-- when this is called (camera allows only one RTSP session) -- the Lua
-- orchestrator enforces that for TIMELAPSE mode by ensuring the
-- pipeline is stopped for the whole on_bottom phase.
local function ipcam_snapshot(phase)
    if not ipcam_cfg.rec_en then return end
    if ipcam_recording then
        -- Snapshot would 409; the bottom dispatcher is supposed to stop
        -- the recorder before switching into timelapse mode.  Log once.
        return
    end
    ipcam_http_snapshot("127.0.0.1", 8095, phase or "manual")
end

-- Fire a single POST /api/v1/dive/finalize at RECOVERY entry so the
-- extension turns the dive's .ts segments into per-phase MP4s (+
-- manifest JSON) on the device.  Fire-and-forget; the dive script is
-- done by this point and the HTTP response isn't needed.  We pass
-- ipcam_cfg.btm_cmod so the extension knows how to slice the
-- on_bottom phase (1=continuous chunk-per-5min, 2=interval session-
-- per-cycle, other=legacy concat-into-one).
local function ipcam_finalize()
    if ipcam_http_finalize("127.0.0.1", 8095, ipcam_cfg.btm_cmod) then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: IPcam finalize requested (btm_cmod=%d)",
                math.floor(ipcam_cfg.btm_cmod or 0)))
    else
        gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: IPcam finalize POST FAILED")
    end
end

-- Unified phase-entry handler for the phases that use continuous video:
-- DESCENT, ASCENT, and on_bottom when BTM_CMOD=1 (continuous).  Does the
-- right thing whether we're not-yet-recording (start), already recording
-- in a different phase (rotate), or the phase is disabled (stop).
-- ``seg_s`` (optional) clamps splitmuxsink's max-size-time at this
-- transition.  All continuous-video phases (descent, on_bottom in
-- CONTINUOUS mode, ascent) pass 300 so splitmuxsink rotates every
-- 5 minutes and finalize chunks each phase into 5-min MP4s (#33).
-- Omit / pass nil to keep the prior policy.
local function ipcam_begin_phase(phase_enabled, phase_name, seg_s)
    if not ipcam_cfg.rec_en then return end
    if phase_enabled then
        if ipcam_recording then
            ipcam_rotate(phase_name, seg_s)
        else
            ipcam_start(phase_name, seg_s)
        end
    elseif ipcam_recording then
        ipcam_stop()
    end
end

-- ?????????? main loop ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
function update()
    local now_ms = millis():tofloat()

    if last_update_ms == 0 then
        script_start_ms = now_ms
    end
    last_update_ms = now_ms
    DORIS_STATE:set(state)

    -- read battery voltage each cycle for pre-arm and telemetry
    local v = battery:voltage(0)
    if v then batt_voltage = v end

    update_telemetry(now_ms)

    -- light test: when DORIS_LGT_TST > 0, override lights to that % brightness.
    -- Auto-clears after 10000 ms so lights don't stay stuck on
    -- if the "off" PARAM_SET is lost.
    local lgt_tst = DORIS_LGT_TST:get() or 0
    if lgt_tst > 0 and RC9 then
        if lgt_tst_start_ms == 0 then
            lgt_tst_start_ms = now_ms
        end
        if now_ms - lgt_tst_start_ms > 10000 then
            DORIS_LGT_TST:set(0)
            RC9:set_override(LIGHT_PWM_MIN)
            lgt_tst_start_ms = 0
        else
            RC9:set_override(brightness_to_pwm(lgt_tst))
            return update, UPDATE_INTERVAL_MS
        end
    else
        -- Test just ended (DORIS_LGT_TST went back to 0). In CONFIG state no
        -- dive branch calls update_lights(), so the last RC9 override stays
        -- latched and the light stays on. Drive RC9 down once on the
        -- transition so the light actually turns off.
        if lgt_tst_start_ms ~= 0 and RC9 then
            RC9:set_override(LIGHT_PWM_MIN)
        end
        lgt_tst_start_ms = 0
    end

    -- cancel: if DORIS_START was cleared while the mission is active, abort.
    -- Returns to CONFIG so the vehicle can be re-armed for another dive
    -- without a script restart.
    if state >= STATE_MISSION_START and state <= STATE_ASCENT then
        if DORIS_START:get() <= 0 then
            gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: CANCELLED by operator")
            deactivate_relay()
            if RC9 then RC9:set_override(LIGHT_PWM_MIN) end
            ipcam_stop()
            arming:disarm()
            prearm_passed = false
            armed_once = false
            state = STATE_CONFIG
            return update, UPDATE_INTERVAL_MS
        end
    end

    -- keep arming gate in sync with profile validity
    update_profile_auth(arm_auth_id)

    -- ??????????????? CONFIG ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    if state == STATE_CONFIG then
        -- GPS self-heal: reboot if GPS has no fix after 30s (max 2 attempts)
        if not gps_reboot_attempted then
            local boot_age_s = (now_ms - script_start_ms) / 1000.0
            if boot_age_s >= 30 then
                local gps_stat = gps:status(0)
                if not gps_stat or gps_stat < 3 then
                    local rbt = (DORIS_GPS_RBT:get() or 0) + 1
                    gcs:send_text(MAV_SEVERITY.WARNING,
                        string.format("DIVE: No GPS fix after 30s, reboot %d/2",
                            rbt))
                    DORIS_GPS_RBT:set_and_save(rbt)
                    vehicle:reboot(false)
                    return update, UPDATE_INTERVAL_MS
                else
                    gps_reboot_attempted = true
                end
            end
        end

        -- Deadman: deployed into water while still in CONFIG.  Fires
        -- regardless of DORIS_START so an unstarted vehicle (operator
        -- never pressed "Load Mission") still drops its weight.  Goes
        -- to ASCENT (not RECOVERY) so the relay stays on for BRN_MIN
        -- and the vehicle is properly monitored to the surface.
        local cfg_depth = get_depth_m()
        if cfg_depth and cfg_depth > 2.0 and not prearm_passed then
            local start_val = DORIS_START:get() or 0
            gcs:send_text(MAV_SEVERITY.CRITICAL,
                string.format(
                    "DIVE: DEPLOYED in CONFIG (START=%d, depth=%.1fm)! Emergency weight release + ASCENT",
                    start_val, cfg_depth))
            activate_relay()
            ascent_start_ms = now_ms
            init_ring(ar, DORIS_ASC_AVG:get() or 120)
            reset_light_cycle(now_ms)
            ipcam_stop()
            recovery_done = false
            state = STATE_ASCENT
            return update, UPDATE_INTERVAL_MS
        end

        if DORIS_START:get() >= 1 then
            -- Surface pre-arm checks
            local min_volt = DORIS_MIN_VOLT:get() or 14.0
            local gps_ok = false
            local gps_stat = gps:status(0)
            if gps_stat and gps_stat >= 3 then
                gps_ok = true
            end
            local batt_ok = batt_voltage >= min_volt
            local leak_ok = not check_leak()
            local profile_ok, profile_reason = validate_profile()

            if gps_ok and batt_ok and leak_ok and profile_ok then
                prearm_passed = true
                gps_reboot_attempted = true
                DORIS_GPS_RBT:set_and_save(0)
                surface_pressure = baro:get_pressure() or surface_pressure
                local num_sats = gps:num_sats(0) or 0
                local prf_id = DORIS_PRF_ID:get() or 0
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: Pre-arm PASSED (GPS %d sats, %.1fV, profile #%d, Pref=%.0fPa)",
                        num_sats, batt_voltage, prf_id, surface_pressure))
                snapshot_config()
                vehicle:set_mode(19)
                armed_once          = false
                recovery_done       = false
                arm_start_ms        = now_ms
                telem.max_depth     = 0.0
                telem.min_temp      = 999.0
                telem.dsc_rate      = 0.0
                telem.asc_rate      = 0.0
                telem.prev_depth    = 0.0
                telem.prev_depth_ms = 0
                state = STATE_MISSION_START
            else
                if now_ms - last_prearm_log_ms > 5000 then
                    last_prearm_log_ms = now_ms
                    local reasons = {}
                    if not gps_ok then reasons[#reasons + 1] = "GPS" end
                    if not batt_ok then
                        reasons[#reasons + 1] = string.format("BATT(%.1fV<%.1fV)",
                            batt_voltage, min_volt)
                    end
                    if not leak_ok then reasons[#reasons + 1] = "LEAK" end
                    if not profile_ok then
                        reasons[#reasons + 1] = string.format("PROFILE(%s)",
                            profile_reason)
                    end
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: Pre-arm waiting: %s",
                            table.concat(reasons, ", ")))
                end
            end
        end

    -- ??????????????? MISSION_START (arm + depth gate) ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_MISSION_START then
        if check_failsafes() then
            ascent_start_ms = now_ms
            init_ring(ar, DORIS_ASC_AVG:get() or 120)
            reset_light_cycle(now_ms)
            -- 5-min (300s) chunking for ascent too (#33).
            ipcam_begin_phase(ipcam_cfg.asc_rec, "ascent", 300)
            state = STATE_ASCENT
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            if armed_once then
                gcs:send_text(MAV_SEVERITY.WARNING,
                    "DIVE: disarmed in gate phase, re-arming")
                arming:arm()
                arm_start_ms = now_ms
            else
                if math.fmod(now_ms - arm_start_ms, ARM_RETRY_MS)
                   < UPDATE_INTERVAL_MS then
                    arming:arm()
                end
                if math.fmod(now_ms - arm_start_ms, 5000)
                   < UPDATE_INTERVAL_MS then
                    gcs:send_text(MAV_SEVERITY.WARNING,
                        "DIVE: waiting for arming checks to pass")
                end
            end
        else
            if not armed_once then
                armed_once = true
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: armed, sinking to gate (%.1fm)",
                        cfg.dpt_gat_m))
            end
            local depth = get_depth_m()
            if depth then
                if math.fmod(now_ms - arm_start_ms, 5000)
                   < UPDATE_INTERVAL_MS then
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: depth=%.2fm / gate=%.1fm",
                            depth, cfg.dpt_gat_m))
                end
                if depth >= cfg.dpt_gat_m then
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: gate crossed (%.2fm), mission started",
                            depth))
                    dive_start_ms = now_ms
                    init_ring(dr, DORIS_BTM_AVG:get())
                    reset_light_cycle(now_ms)
                    -- Descent rotates at 5-min (300s) boundaries so
                    -- finalize chunks it into 5-min MP4s like the
                    -- bottom phase, instead of one long file (#33).
                    ipcam_begin_phase(ipcam_cfg.dsc_rec, "descent", 300)
                    state = STATE_DESCENT
                end
            end
        end

    -- ??????????????? DESCENT ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_DESCENT then
        if check_failsafes() then
            ascent_start_ms = now_ms
            init_ring(ar, DORIS_ASC_AVG:get() or 120)
            reset_light_cycle(now_ms)
            -- 5-min (300s) chunking for ascent too (#33).
            ipcam_begin_phase(ipcam_cfg.asc_rec, "ascent", 300)
            state = STATE_ASCENT
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed during descent, re-arming")
            arming:arm()
        end

        update_lights(cfg.dsc_lgt, now_ms)

        local elapsed = now_ms - dive_start_ms
        local vel = ahrs:get_velocity_NED()
        if vel then
            local drate = vel:z()
            add_ring_sample(dr, drate)
            local avg = get_ring_avg(dr)

            if math.fmod(elapsed, 5000) < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format(
                        "DIVE: dsc %.0fs rate=%.3f avg=%.3f m/s depth=%.1fm [%d/%d]",
                        elapsed / 1000.0, drate, avg or 0,
                        get_depth_m() or 0, dr.count, dr.size))
            end

            if avg and dr.count >= dr.size and avg < cfg.btm_thr_mps then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format(
                        "DIVE: bottom detected! avg=%.4f m/s depth=%.1fm t=%.0fs",
                        avg, get_depth_m() or 0, elapsed / 1000.0))
                bottom_start_ms   = now_ms
                bottom_delay_done = cfg.btm_dly_ms <= 0
                reset_light_cycle(now_ms)
                ipcam_btm_started = false
                -- Reset interval/timelapse timers so the on_bottom
                -- loop below can establish its own cadence.
                ipcam_state.cycle_start_ms       = 0
                ipcam_state.cycle_is_record      = false
                ipcam_state.cycle_started        = false
                ipcam_state.cycle_awaiting_frames = false
                ipcam_state.cycle_wait_start_ms  = 0
                ipcam_state.last_snap_ms         = 0
                ipcam_state.next_snap_ms         = 0
                -- For CONTINUOUS bottom mode with no camera delay we do
                -- a zero-gap rotate-or-start right now so the moment
                -- the vehicle settles, the first "on_bottom" .ts starts.
                -- Pass seg=300 so splitmuxsink rotates at 5-min
                -- boundaries (lossless) for the rest of the bottom
                -- phase, producing on_bottom_chunkNN.mp4 outputs.
                -- For INTERVAL / TIMELAPSE / OFF (or any mode w/ a
                -- camera delay) we stop any descent recording and let
                -- the on_bottom loop own the cadence.
                local want_now = (ipcam_cfg.btm_cmod == 1)
                    and (ipcam_cfg.cam_btm_dly_ms <= 0)
                if want_now then
                    ipcam_begin_phase(true, "on_bottom", 300)
                    ipcam_btm_started = ipcam_recording
                elseif ipcam_recording then
                    ipcam_stop()
                end
                state = STATE_ON_BOTTOM
            end
        end

    -- ??????????????? ON_BOTTOM ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_ON_BOTTOM then
        if check_failsafes() then
            ascent_start_ms = now_ms
            init_ring(ar, DORIS_ASC_AVG:get() or 120)
            reset_light_cycle(now_ms)
            -- Keep 5-min (300s) chunking for ascent (#33); the bottom
            -- continuous path already set splitmuxsink to 300s.
            ipcam_begin_phase(ipcam_cfg.asc_rec, "ascent", 300)
            state = STATE_ASCENT
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed on bottom, re-arming")
            arming:arm()
        end

        local bottom_elapsed = now_ms - bottom_start_ms
        local cam_delay_done = ipcam_cfg.cam_btm_dly_ms <= 0
            or bottom_elapsed >= ipcam_cfg.cam_btm_dly_ms

        -- Lights on bottom:
        --   CONTINUOUS  -> follow cfg.btm_lgt directly
        --   INTERVAL    -> gate by live recorder state so the light
        --                  literally follows the duty cycle (off
        --                  during the pause window)
        --   TIMELAPSE   -> strobe: light ON only inside the pre-roll
        --                  + post-roll windows around each snapshot
        --                  (driven by tl_strobe_active below)
        local bottom_lgt_eff = cfg.btm_lgt
        if ipcam_cfg.btm_cmod == 1 and ipcam_cfg.rec_en then
            -- CONTINUOUS: the operator wants the light on cfg.btm_dly_ms
            -- (light delay) after *video recording actually starts*, not
            -- after bottom detection.  The RTSP connect + first-frame
            -- latency (plus the one-time GStreamer init on the first dive
            -- recording) can push real frames several seconds past bottom
            -- detection, so we gate the light-delay clock on first-frame
            -- just like INTERVAL mode.  cycle_start_ms is held at 0 until
            -- the recorder confirms frames are on disk (set in the bottom
            -- camera dispatcher below), so the light stays off through the
            -- connect window and then lights cfg.btm_dly_ms later.
            bottom_lgt_eff = cfg.btm_lgt
                and not ipcam_state.cycle_awaiting_frames
                and ipcam_state.cycle_start_ms > 0
                and (now_ms - ipcam_state.cycle_start_ms) >= cfg.btm_dly_ms
        elseif ipcam_cfg.btm_cmod == 2 then
            -- INTERVAL: the camera starts each cycle, the light comes on
            -- cfg.btm_dly_ms (light delay) after the record clock starts,
            -- and both turn off together when the record window ends.  The
            -- record clock is first-frame gated (held at 0 while awaiting
            -- frames), so the light stays off until recording truly begins;
            -- it is then lit for (btm_rec_ms - btm_dly_ms).
            bottom_lgt_eff = cfg.btm_lgt
                and ipcam_state.cycle_is_record
                and not ipcam_state.cycle_awaiting_frames
                and ipcam_state.cycle_start_ms > 0
                and (now_ms - ipcam_state.cycle_start_ms) >= cfg.btm_dly_ms
        elseif ipcam_cfg.btm_cmod == 3 then
            local pre_active = ipcam_state.next_snap_ms > 0
                and (ipcam_state.next_snap_ms - now_ms) <= ipcam_cfg.tl_pre_ms
            local post_active = ipcam_state.last_snap_ms > 0
                and (now_ms - ipcam_state.last_snap_ms) < ipcam_cfg.tl_post_ms
            bottom_lgt_eff = cfg.btm_lgt and (pre_active or post_active)
        end

        if ipcam_cfg.btm_cmod == 2
           or (ipcam_cfg.btm_cmod == 1 and ipcam_cfg.rec_en) then
            -- INTERVAL and CONTINUOUS-recording own their light timing via
            -- the first-frame-anchored bottom_lgt_eff above.  The one-time
            -- bottom settling delay (bottom_delay_done, measured from
            -- bottom detection) is replaced by the recording-start anchor
            -- so the light tracks the camera, not the detection instant.
            update_lights(bottom_lgt_eff, now_ms)
        elseif not bottom_delay_done then
            update_lights(false, now_ms)
            if bottom_elapsed >= cfg.btm_dly_ms then
                bottom_delay_done = true
                reset_light_cycle(now_ms)
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: settling delay done (%.0fs), lights active",
                        cfg.btm_dly_ms / 1000.0))
            end
        else
            update_lights(bottom_lgt_eff, now_ms)
        end

        -- Bottom camera dispatcher: OFF / CONTINUOUS / VIDEO_INTERVAL / TIMELAPSE
        if ipcam_cfg.btm_cmod == 0 then
            -- OFF: make sure we aren't recording (could be carryover from descent).
            if ipcam_recording then ipcam_stop() end
        elseif ipcam_cfg.btm_cmod == 1 then
            -- CONTINUOUS: start once after camera delay, keep running.
            -- seg=300 -> splitmuxsink rotates every 5 minutes so the
            -- finalize step can produce on_bottom_chunkNN.mp4 files.  The
            -- recorder may already be live here (the zero-gap start fired
            -- at bottom detection when there was no camera delay) or not
            -- (a camera delay stopped descent recording); either way we
            -- run a first-frame wait so the bottom light's delay clock
            -- (cycle_start_ms) is anchored to real frames on disk rather
            -- than bottom detection.
            if ipcam_cfg.rec_en and cam_delay_done then
                if not ipcam_state.cycle_started then
                    ipcam_state.cycle_started         = true
                    ipcam_state.cycle_awaiting_frames = true
                    ipcam_state.cycle_wait_start_ms   = now_ms
                    ipcam_state.cycle_start_ms        = 0
                    if not ipcam_recording then
                        ipcam_begin_phase(true, "on_bottom", 300)
                    end
                    ipcam_btm_started = ipcam_recording
                elseif ipcam_state.cycle_awaiting_frames then
                    -- Hold the light-delay clock until frames are confirmed
                    -- on disk (or the safety timeout fires).  Recording is
                    -- already running; we only gate the light here.
                    local ff = ipcam_frames_flowing()
                    local waited = now_ms - ipcam_state.cycle_wait_start_ms
                    if ff == true then
                        ipcam_state.cycle_awaiting_frames = false
                        ipcam_state.cycle_start_ms        = now_ms
                        reset_light_cycle(now_ms)
                        gcs:send_text(MAV_SEVERITY.INFO,
                            string.format(
                                "DIVE: IPcam frames flowing after %.1fs, lights +%ds",
                                waited / 1000.0,
                                math.floor(cfg.btm_dly_ms / 1000)))
                    elseif waited >= IPCAM_FIRST_FRAME_TIMEOUT_MS then
                        ipcam_state.cycle_awaiting_frames = false
                        ipcam_state.cycle_start_ms        = now_ms
                        reset_light_cycle(now_ms)
                        gcs:send_text(MAV_SEVERITY.WARNING,
                            string.format(
                                "DIVE: IPcam first-frame wait timed out (%.0fs), "
                                .. "starting light clock anyway",
                                waited / 1000.0))
                    end
                end
            end
        elseif ipcam_cfg.btm_cmod == 2 then
            -- VIDEO_INTERVAL: duty-cycle record for btm_rec_ms, pause
            -- btm_pau_ms.  The record clock is gated on first-frame: the
            -- window only starts counting once the extension confirms
            -- frames are on disk, so a slow RTSP connect (observed up to
            -- ~15 s) no longer eats into the recorded clip length.  The
            -- light comes on btm_dly_ms into the record window (handled in
            -- the light block above) and goes off with the camera at the
            -- end of the window.
            if cam_delay_done and ipcam_cfg.btm_rec_ms > 0
               and ipcam_cfg.btm_pau_ms > 0 then
                if not ipcam_state.cycle_started then
                    -- Inaugural record window for this bottom visit: kick
                    -- off the recorder, then wait for frames before the
                    -- clock starts.
                    ipcam_state.cycle_started         = true
                    ipcam_state.cycle_is_record       = true
                    ipcam_state.cycle_awaiting_frames = true
                    ipcam_state.cycle_wait_start_ms   = now_ms
                    ipcam_state.cycle_start_ms        = 0
                    ipcam_begin_phase(true, "on_bottom")
                    ipcam_btm_started = ipcam_recording
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: IPcam interval start (rec %ds, light +%ds)",
                            math.floor(ipcam_cfg.btm_rec_ms / 1000),
                            math.floor(cfg.btm_dly_ms / 1000)))
                elseif ipcam_state.cycle_awaiting_frames then
                    -- Hold the record clock until frames are confirmed on
                    -- disk (or the safety timeout fires).  The light delay
                    -- is measured from this point, so the light stays off
                    -- during the wait.
                    local ff = ipcam_frames_flowing()
                    local waited = now_ms - ipcam_state.cycle_wait_start_ms
                    if ff == true then
                        ipcam_state.cycle_awaiting_frames = false
                        ipcam_state.cycle_start_ms        = now_ms
                        gcs:send_text(MAV_SEVERITY.INFO,
                            string.format(
                                "DIVE: IPcam frames flowing after %.1fs, "
                                .. "recording %ds (light +%ds)",
                                waited / 1000.0,
                                math.floor(ipcam_cfg.btm_rec_ms / 1000),
                                math.floor(cfg.btm_dly_ms / 1000)))
                    elseif waited >= IPCAM_FIRST_FRAME_TIMEOUT_MS then
                        -- Fail-safe: never hang a cycle on a missing
                        -- readiness signal -- start the clock anyway.
                        ipcam_state.cycle_awaiting_frames = false
                        ipcam_state.cycle_start_ms        = now_ms
                        gcs:send_text(MAV_SEVERITY.WARNING,
                            string.format(
                                "DIVE: IPcam first-frame wait timed out "
                                .. "(%.0fs), starting clock anyway",
                                waited / 1000.0))
                    end
                else
                    local cycle_elapsed = now_ms - ipcam_state.cycle_start_ms
                    if ipcam_state.cycle_is_record
                       and cycle_elapsed >= ipcam_cfg.btm_rec_ms then
                        ipcam_state.cycle_is_record = false
                        ipcam_state.cycle_start_ms  = now_ms
                        if ipcam_recording then ipcam_stop() end
                        gcs:send_text(MAV_SEVERITY.INFO,
                            string.format("DIVE: IPcam interval pause (%ds)",
                                math.floor(ipcam_cfg.btm_pau_ms / 1000)))
                    elseif (not ipcam_state.cycle_is_record)
                       and cycle_elapsed >= ipcam_cfg.btm_pau_ms then
                        -- Begin the next record window: start the
                        -- recorder, then re-enter the first-frame wait so
                        -- this window's clock is gated too.
                        ipcam_state.cycle_is_record       = true
                        ipcam_state.cycle_awaiting_frames = true
                        ipcam_state.cycle_wait_start_ms   = now_ms
                        ipcam_state.cycle_start_ms        = 0
                        if not ipcam_recording then
                            ipcam_start("on_bottom")
                        end
                        gcs:send_text(MAV_SEVERITY.INFO,
                            string.format("DIVE: IPcam interval record (%ds, light +%ds)",
                                math.floor(ipcam_cfg.btm_rec_ms / 1000),
                                math.floor(cfg.btm_dly_ms / 1000)))
                    end
                end
            end
        elseif ipcam_cfg.btm_cmod == 3 then
            -- TIMELAPSE with light strobe.  Snapshots are mutually
            -- exclusive with the recorder pipeline, so make sure it
            -- isn't running, then drive a three-window state machine
            -- around each capture-frequency tick:
            --   1. light OFF for (PAUS - PRE - POST) seconds
            --   2. light ON for PRE seconds before the snap fires
            --   3. snapshot fires; light remains ON for POST seconds
            -- The light is driven up above by ``bottom_lgt_eff``,
            -- which inspects ``next_snap_ms`` and ``last_snap_ms``;
            -- this branch is responsible only for scheduling.
            if ipcam_recording then ipcam_stop() end
            if cam_delay_done and ipcam_cfg.btm_pau_ms > 0 then
                -- Effective period floor is pre+post so the pre and
                -- post windows can run back-to-back without negative
                -- idle time.  When the operator sets capture_frequency
                -- below this floor the light just stays on continuously
                -- across snaps; the floor only protects against
                -- nonsensical schedules (e.g. pre=2 post=1 freq=1 s).
                local min_period = ipcam_cfg.tl_pre_ms + ipcam_cfg.tl_post_ms
                if min_period < 1 then min_period = 1 end
                local period_ms = ipcam_cfg.btm_pau_ms
                if period_ms < min_period then period_ms = min_period end

                if ipcam_state.next_snap_ms == 0 then
                    -- First entry: schedule the inaugural snap pre_ms
                    -- in the future so the operator gets a full pre-
                    -- roll window before the very first capture.
                    ipcam_state.next_snap_ms = now_ms + ipcam_cfg.tl_pre_ms
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format(
                            "DIVE: IPcam timelapse strobe pre=%ds post=%ds period=%ds",
                            math.floor(ipcam_cfg.tl_pre_ms / 1000),
                            math.floor(ipcam_cfg.tl_post_ms / 1000),
                            math.floor(period_ms / 1000)))
                elseif now_ms >= ipcam_state.next_snap_ms then
                    ipcam_snapshot("on_bottom")
                    ipcam_state.last_snap_ms = now_ms
                    ipcam_state.next_snap_ms = now_ms + period_ms
                end
            end
        end

        if math.fmod(bottom_elapsed, 30000) < UPDATE_INTERVAL_MS then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: on bottom %.0fs / %ds",
                    bottom_elapsed / 1000.0, cfg.rls_sec_ms / 1000))
        end

        if bottom_elapsed >= cfg.rls_sec_ms then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: release triggered (%.1fs on bottom)",
                    bottom_elapsed / 1000.0))
            activate_relay()
            ascent_start_ms = now_ms
            init_ring(ar, DORIS_ASC_AVG:get() or 120)
            reset_light_cycle(now_ms)
            -- Keep 5-min (300s) chunking for ascent (#33); the bottom
            -- continuous path already set splitmuxsink to 300s.
            ipcam_begin_phase(ipcam_cfg.asc_rec, "ascent", 300)
            state = STATE_ASCENT
        end

    -- ??????????????? ASCENT ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_ASCENT then
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed during ascent, re-arming")
            arming:arm()
        end

        update_lights(cfg.asc_lgt, now_ms)

        -- Relay stays on for at least DORIS_BRN_MIN seconds (default 2 hrs).
        -- After that, deactivate only when EKF-filtered vertical velocity
        -- confirms sustained ascent over DORIS_ASC_AVG seconds (default 120s).
        -- This replaces the old depth-based check which was vulnerable to
        -- single-sample baro glitches that caused premature relay deactivation.
        if relay_active then
            local vel = ahrs:get_velocity_NED()
            if vel then
                add_ring_sample(ar, -vel:z())
            end

            local burn_elapsed = now_ms - ascent_start_ms
            local brn_min_ms = (DORIS_BRN_MIN:get() or 7200) * 1000
            if burn_elapsed >= brn_min_ms then
                local avg = get_ring_avg(ar)
                local thr = (DORIS_ASC_THR:get() or 10) / 100.0
                if avg and ar.count >= ar.size and avg >= thr then
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format(
                            "DIVE: sustained ascent %.3f m/s > %.3f thr (%ds window), relay off",
                            avg, thr, ar.size * UPDATE_INTERVAL_MS / 1000))
                    deactivate_relay()
                end
            end
        end

        local depth = get_depth_m()
        if depth then
            if math.fmod(now_ms - ascent_start_ms, 30000)
               < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: ascending, depth=%.2fm", depth))
            end
            -- Surface arrival requires BOTH a GPS fix AND a shallow depth.
            -- GPS fix alone is not enough: a transient sat acquisition at
            -- mid-water (e.g. while the antenna briefly clears, or a cached
            -- fix) would otherwise prematurely deactivate the burn relay,
            -- stop recording, and disarm into RECOVERY.  cfg.dpt_gat_m is
            -- the same depth gate used to declare "underwater" at mission
            -- start, so we use it symmetrically here for "above water".
            local gps_stat = gps:status(0)
            if gps_stat and gps_stat >= 3 and depth < cfg.dpt_gat_m then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: surface reached (GPS fix, depth=%.2fm < gate=%.1fm)",
                        depth, cfg.dpt_gat_m))
                deactivate_relay()
                ipcam_stop()
                state = STATE_RECOVERY
            end
        end

    -- ??????????????? RECOVERY (terminal) ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_RECOVERY then
        if RC9 then RC9:set_override(LIGHT_PWM_MIN) end
        arming:disarm()
        DORIS_START:set_and_save(0)
        -- Fire dive-finalize exactly once on first RECOVERY tick so the
        -- extension stops the recorder, records the bottom mode, and closes
        -- the dive record.  This is deliberately quick: the AGT holds payload
        -- power up until it returns, and the heavy video/USB work waits for
        -- the operator to press Process Dive on deck.  Done before
        -- recovery_done so a slow disarm cannot delay it.
        if not ipcam_state.finalize_sent then
            ipcam_finalize()
            ipcam_state.finalize_sent = true
        end
        if not recovery_done and not arming:is_armed() then
            deactivate_relay()
            local total = dive_start_ms > 0
                and (now_ms - dive_start_ms) / 1000.0 or 0
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: mission complete (%.1fs), safe for power-off",
                    total))
            recovery_done = true
        end
    end

    return update, UPDATE_INTERVAL_MS
end

-- ?????????? initialization ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local function detect_sitl()
    local ok, val = pcall(function() return param:get("SIM_BUOYANCY") end)
    if ok and val ~= nil then
        is_sitl = true
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: SITL detected (SIM_BUOYANCY=%.1f)", val))
    end
end

detect_sitl()

arm_auth_id = arming:get_aux_auth_id()
if not arm_auth_id then
    gcs:send_text(MAV_SEVERITY.WARNING,
        "DIVE: could not get aux auth ID (profile pre-arm gate disabled)")
end

do
    local prf = DORIS_PRF_ID:get() or 0
    if prf > 0 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: script loaded, profile #%d (uploaded %d), START=%d",
                prf, DORIS_UPL_DATE:get() or 0, DORIS_START:get() or 0))
    else
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: script loaded, no profile loaded, START=%d",
                DORIS_START:get() or 0))
    end
end

return update()
