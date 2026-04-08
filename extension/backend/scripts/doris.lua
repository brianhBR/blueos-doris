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
   mission profile.  If the vehicle is deployed into water before
   pre-arm passes, an emergency failsafe releases the drop-weight
   for recovery.

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

local MODE_MANUAL = 19

local SURFACE_DEPTH_M    = 0.5
local UPDATE_INTERVAL_MS = 500
local ARM_RETRY_MS       = 2000
local ARM_TIMEOUT_MS     = 10000
local WATER_DENSITY      = 1025.0
local GRAVITY            = 9.80665
local GPS_FIX_3D         = 3
local DEPLOY_DEPTH_M     = 2.0
local RELAY_ASC_THRS     = 0.05
local RELAY_ASC_SAMPLES  = 6

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
local PARAM_TABLE_KEY  = 73
local PARAM_TABLE_SIZE = 23

assert(param:add_table(PARAM_TABLE_KEY, "DORIS_", PARAM_TABLE_SIZE),
       "DIVE: could not add DORIS_ param table")

-- mission control
assert(param:add_param(PARAM_TABLE_KEY, 1,  "START",   0),  "DORIS_START")
assert(param:add_param(PARAM_TABLE_KEY, 2,  "RLS_SEC", 60), "DORIS_RLS_SEC")
assert(param:add_param(PARAM_TABLE_KEY, 3,  "DSC_LGT", 0),  "DORIS_DSC_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 4,  "BTM_LGT", 0),  "DORIS_BTM_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 5,  "ASC_LGT", 0),  "DORIS_ASC_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 6,  "LGT_BRT", 75), "DORIS_LGT_BRT")
assert(param:add_param(PARAM_TABLE_KEY, 7,  "STATE",  -1),  "DORIS_STATE")
assert(param:add_param(PARAM_TABLE_KEY, 8,  "BTM_THR", 5),  "DORIS_BTM_THR")
assert(param:add_param(PARAM_TABLE_KEY, 9,  "BTM_AVG", 30), "DORIS_BTM_AVG")
assert(param:add_param(PARAM_TABLE_KEY, 10, "DPT_GAT", 3),  "DORIS_DPT_GAT")
assert(param:add_param(PARAM_TABLE_KEY, 11, "LGT_MOD", 0),  "DORIS_LGT_MOD")
assert(param:add_param(PARAM_TABLE_KEY, 12, "LGT_ON",  10), "DORIS_LGT_ON")
assert(param:add_param(PARAM_TABLE_KEY, 13, "LGT_OFF", 5),  "DORIS_LGT_OFF")
assert(param:add_param(PARAM_TABLE_KEY, 14, "BTM_DLY", 30), "DORIS_BTM_DLY")
-- mission profile & safety
assert(param:add_param(PARAM_TABLE_KEY, 15, "PRF_ID",   0),    "DORIS_PRF_ID")
assert(param:add_param(PARAM_TABLE_KEY, 16, "UPL_DATE", 0),    "DORIS_UPL_DATE")
assert(param:add_param(PARAM_TABLE_KEY, 17, "UPL_TIME", 0),    "DORIS_UPL_TIME")
assert(param:add_param(PARAM_TABLE_KEY, 18, "MIN_VOLT", 14.0), "DORIS_MIN_VOLT")
assert(param:add_param(PARAM_TABLE_KEY, 19, "RELAY_CH", 0),    "DORIS_RELAY_CH")
assert(param:add_param(PARAM_TABLE_KEY, 20, "INJ_LEAK", 0),    "DORIS_INJ_LEAK")
assert(param:add_param(PARAM_TABLE_KEY, 21, "MAX_DPTH", 6100), "DORIS_MAX_DPTH")
assert(param:add_param(PARAM_TABLE_KEY, 22, "LGT_TST", 0),    "DORIS_LGT_TST")
assert(param:add_param(PARAM_TABLE_KEY, 23, "LOG_INTV", 1000), "DORIS_LOG_INTV")

local DORIS_START    = Parameter("DORIS_START")
local DORIS_RLS_SEC  = Parameter("DORIS_RLS_SEC")
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

-- DORIS_START persists in EEPROM across reboots.
-- Cleared only in RECOVERY after a mission completes.
DORIS_STATE:set(-1)
param:set_and_save("DISARM_DELAY", 0)

-- ArduPilot arming checks: enable only checks relevant to DORIS.
-- Baro(2) + Compass(4) + GPS(8) + INS(16) + Params(32) +
-- Board voltage(128) + Logging(1024) + System(8192) + AuxAuth(131072) = 140478
param:set_and_save("ARMING_CHECK", 140478)

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

-- ?????????? descent-rate circular buffer ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local dr_buffer   = {}
local dr_idx      = 0
local dr_count    = 0
local dr_buf_size = 60

local function init_dr_buffer(window_sec)
    dr_buf_size = math.max(math.ceil(window_sec / (UPDATE_INTERVAL_MS / 1000.0)), 10)
    dr_buffer = {}
    dr_idx    = 0
    dr_count  = 0
    for i = 1, dr_buf_size do
        dr_buffer[i] = 0
    end
end

local function add_dr_sample(rate)
    dr_idx = (dr_idx % dr_buf_size) + 1
    dr_buffer[dr_idx] = rate
    if dr_count < dr_buf_size then
        dr_count = dr_count + 1
    end
end

local function get_avg_dr()
    if dr_count == 0 then return nil end
    local sum = 0
    for i = 1, dr_count do
        sum = sum + dr_buffer[i]
    end
    return sum / dr_count
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
local relay_asc_count    = 0

-- in-mission failsafe state
local leak_detected = false

-- light interval state
local light_on       = true
local light_cycle_ms = 0

-- light test state (auto-clears after timeout)
local lgt_tst_start_ms = 0
local LGT_TST_TIMEOUT  = 3000

-- telemetry tracking (updated every cycle by update_sensors)
-- packed into a table to stay under Lua's 200-local-variable limit
local telem = {
    depth = 0.0, max_depth = 0.0, min_temp = 999.0,
    dsc_rate = 0.0, asc_rate = 0.0, batt_pct = 0.0,
    prev_depth = 0.0, prev_depth_ms = 0, last_log_ms = 0,
    alpha = 0.3,
}

-- snapshotted config (read once at CONFIG -> MISSION_START)
local cfg_rls_sec_ms  = 60000
local cfg_dsc_lgt     = false
local cfg_btm_lgt     = false
local cfg_asc_lgt     = false
local cfg_lgt_pwm     = LIGHT_PWM_MIN
local cfg_btm_thr_mps = 0.05
local cfg_dpt_gat_m   = 5.0
local cfg_lgt_mod     = 0
local cfg_lgt_on_ms   = 10000
local cfg_lgt_off_ms  = 5000
local cfg_btm_dly_ms  = 30000

local RC9 = rc:get_channel(9)

-- ?????????? helpers ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
local function get_depth_m()
    local pressure = baro:get_pressure()
    local depth_baro = nil
    if pressure then
        depth_baro = (pressure - surface_pressure) / (WATER_DENSITY * GRAVITY)
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
    if cfg_lgt_mod == 0 then
        RC9:set_override(cfg_lgt_pwm)
        return
    end
    -- interval mode
    local elapsed = now_ms - light_cycle_ms
    if light_on then
        if elapsed >= cfg_lgt_on_ms then
            light_on = false
            light_cycle_ms = now_ms
            RC9:set_override(LIGHT_PWM_MIN)
        else
            RC9:set_override(cfg_lgt_pwm)
        end
    else
        if elapsed >= cfg_lgt_off_ms then
            light_on = true
            light_cycle_ms = now_ms
            RC9:set_override(cfg_lgt_pwm)
        else
            RC9:set_override(LIGHT_PWM_MIN)
        end
    end
end

local function activate_relay()
    local ch = DORIS_RELAY_CH:get()
    if not ch then return end
    ch = math.floor(ch)
    if ch < 0 then return end
    relay:on(ch)
    relay_active = true
    relay_asc_count = 0
    if is_sitl then
        -- ~2 kg net positive buoyancy for ascent after weight drop in SITL
        param:set_and_save("SIM_BUOYANCY", 19.6)
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: SITL SIM_BUOYANCY set positive (19.6 N)")
    end
    gcs:send_text(MAV_SEVERITY.WARNING,
        string.format("DIVE: Relay CH%d ON at %.1fm", ch, get_depth_m() or 0))
end

local function deactivate_relay()
    if not relay_active then return end
    local ch = DORIS_RELAY_CH:get()
    if not ch then return end
    ch = math.floor(ch)
    if ch < 0 then return end
    relay:off(ch)
    relay_active = false
    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: Relay CH%d OFF at %.1fm", ch, get_depth_m() or 0))
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

    local rls = DORIS_RLS_SEC:get() or 0
    if rls <= 0 then
        return false, "RLS_SEC must be > 0"
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
    if state == STATE_CONFIG or state == STATE_RECOVERY then
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
    local rls_sec = DORIS_RLS_SEC:get()
    local brt     = DORIS_LGT_BRT:get()
    local btm_thr = DORIS_BTM_THR:get()
    local btm_avg = DORIS_BTM_AVG:get()
    local dpt_gat = DORIS_DPT_GAT:get()
    local lgt_mod = DORIS_LGT_MOD:get()
    local lgt_on  = DORIS_LGT_ON:get()
    local lgt_off = DORIS_LGT_OFF:get()
    local btm_dly = DORIS_BTM_DLY:get()

    cfg_rls_sec_ms  = math.max(rls_sec, 1) * 1000
    cfg_dsc_lgt     = DORIS_DSC_LGT:get() >= 1
    cfg_btm_lgt     = DORIS_BTM_LGT:get() >= 1
    cfg_asc_lgt     = DORIS_ASC_LGT:get() >= 1
    cfg_lgt_pwm     = brightness_to_pwm(brt)
    cfg_btm_thr_mps = math.max(btm_thr, 0.1) / 100.0
    cfg_dpt_gat_m   = math.max(dpt_gat, 2.0)
    cfg_lgt_mod     = lgt_mod >= 1 and 1 or 0
    cfg_lgt_on_ms   = math.max(lgt_on, 1) * 1000
    cfg_lgt_off_ms  = math.max(lgt_off, 1) * 1000
    cfg_btm_dly_ms  = math.max(btm_dly, 0) * 1000

    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: gate=%.1fm rls=%ds thr=%.1fcm/s avg=%ds",
            cfg_dpt_gat_m, rls_sec, btm_thr, btm_avg))
    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: lights dsc=%d btm=%d asc=%d brt=%d%% mode=%s",
            cfg_dsc_lgt and 1 or 0, cfg_btm_lgt and 1 or 0,
            cfg_asc_lgt and 1 or 0, brt,
            cfg_lgt_mod == 1 and "interval" or "continuous"))
    if cfg_lgt_mod == 1 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: interval on=%ds off=%ds", lgt_on, lgt_off))
    end
    if cfg_btm_dly_ms > 0 then
        gcs:send_text(MAV_SEVERITY.INFO,
            string.format("DIVE: bottom light delay=%ds", btm_dly))
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

    -- dataflash logging (written to ArduPilot .bin log)
    local interval = param:get("DORIS_LOG_INTV") or 1000
    if now_ms - telem.last_log_ms >= interval then
        telem.last_log_ms = now_ms
        logger:write('DORS',
            'Sta,Dep,MaxD,Tmp,DscR,AscR,BatV,BatP,Msn,Rly',
            'ffffffffff',
            state, telem.depth, telem.max_depth, telem.min_temp,
            telem.dsc_rate, telem.asc_rate, batt_voltage, telem.batt_pct,
            mission_time_s, relay_active and 1 or 0)
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
    -- Auto-clears after LGT_TST_TIMEOUT ms so lights don't stay stuck on
    -- if the "off" PARAM_SET is lost.
    local lgt_tst = DORIS_LGT_TST:get() or 0
    if lgt_tst > 0 and RC9 then
        if lgt_tst_start_ms == 0 then
            lgt_tst_start_ms = now_ms
        end
        if now_ms - lgt_tst_start_ms > LGT_TST_TIMEOUT then
            DORIS_LGT_TST:set(0)
            RC9:set_override(LIGHT_PWM_MIN)
            lgt_tst_start_ms = 0
        else
            RC9:set_override(brightness_to_pwm(lgt_tst))
            return update, UPDATE_INTERVAL_MS
        end
    else
        lgt_tst_start_ms = 0
    end

    -- cancel: if DORIS_START was cleared while the mission is active, abort
    if state >= STATE_MISSION_START and state <= STATE_ASCENT then
        if DORIS_START:get() <= 0 then
            gcs:send_text(MAV_SEVERITY.WARNING, "DIVE: CANCELLED by operator")
            if RC9 then RC9:set_override(LIGHT_PWM_MIN) end
            state = STATE_RECOVERY
            return update, UPDATE_INTERVAL_MS
        end
    end

    -- keep arming gate in sync with profile validity
    update_profile_auth(arm_auth_id)

    -- ??????????????? CONFIG ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    if state == STATE_CONFIG then
        if DORIS_START:get() >= 1 then

            -- Emergency: deployed into water before pre-arm passed
            local depth = get_depth_m()
            if depth and depth > DEPLOY_DEPTH_M and not prearm_passed then
                gcs:send_text(MAV_SEVERITY.CRITICAL,
                    "DIVE: DEPLOYED without pre-arm! Emergency weight release")
                activate_relay()
                state = STATE_RECOVERY
                return update, UPDATE_INTERVAL_MS
            end

            -- Surface pre-arm checks
            local min_volt = DORIS_MIN_VOLT:get() or 14.0
            local gps_ok = false
            local gps_stat = gps:status(0)
            if gps_stat and gps_stat >= GPS_FIX_3D then
                gps_ok = true
            end
            local batt_ok = batt_voltage >= min_volt
            local leak_ok = not check_leak()
            local profile_ok, profile_reason = validate_profile()

            if gps_ok and batt_ok and leak_ok and profile_ok then
                prearm_passed = true
                surface_pressure = baro:get_pressure() or surface_pressure
                local num_sats = gps:num_sats(0) or 0
                local prf_id = DORIS_PRF_ID:get() or 0
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: Pre-arm PASSED (GPS %d sats, %.1fV, profile #%d, Pref=%.0fPa)",
                        num_sats, batt_voltage, prf_id, surface_pressure))
                snapshot_config()
                vehicle:set_mode(MODE_MANUAL)
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
            state = STATE_RECOVERY
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
                        cfg_dpt_gat_m))
            end
            local depth = get_depth_m()
            if depth then
                if math.fmod(now_ms - arm_start_ms, 5000)
                   < UPDATE_INTERVAL_MS then
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: depth=%.2fm / gate=%.1fm",
                            depth, cfg_dpt_gat_m))
                end
                if depth >= cfg_dpt_gat_m then
                    gcs:send_text(MAV_SEVERITY.INFO,
                        string.format("DIVE: gate crossed (%.2fm), mission started",
                            depth))
                    dive_start_ms = now_ms
                    init_dr_buffer(DORIS_BTM_AVG:get())
                    reset_light_cycle(now_ms)
                    state = STATE_DESCENT
                end
            end
        end

    -- ??????????????? DESCENT ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_DESCENT then
        if check_failsafes() then
            state = STATE_RECOVERY
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed during descent, re-arming")
            arming:arm()
        end

        update_lights(cfg_dsc_lgt, now_ms)

        local elapsed = now_ms - dive_start_ms
        local vel = ahrs:get_velocity_NED()
        if vel then
            local drate = vel:z()
            add_dr_sample(drate)
            local avg = get_avg_dr()

            if math.fmod(elapsed, 5000) < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format(
                        "DIVE: dsc %.0fs rate=%.3f avg=%.3f m/s depth=%.1fm [%d/%d]",
                        elapsed / 1000.0, drate, avg or 0,
                        get_depth_m() or 0, dr_count, dr_buf_size))
            end

            if avg and dr_count >= dr_buf_size and avg < cfg_btm_thr_mps then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format(
                        "DIVE: bottom detected! avg=%.4f m/s depth=%.1fm t=%.0fs",
                        avg, get_depth_m() or 0, elapsed / 1000.0))
                bottom_start_ms   = now_ms
                bottom_delay_done = cfg_btm_dly_ms <= 0
                reset_light_cycle(now_ms)
                state = STATE_ON_BOTTOM
            end
        end

    -- ??????????????? ON_BOTTOM ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_ON_BOTTOM then
        if check_failsafes() then
            state = STATE_RECOVERY
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed on bottom, re-arming")
            arming:arm()
        end

        local bottom_elapsed = now_ms - bottom_start_ms

        if not bottom_delay_done then
            update_lights(false, now_ms)
            if bottom_elapsed >= cfg_btm_dly_ms then
                bottom_delay_done = true
                reset_light_cycle(now_ms)
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: settling delay done (%.0fs), lights active",
                        cfg_btm_dly_ms / 1000.0))
            end
        else
            update_lights(cfg_btm_lgt, now_ms)
        end

        if math.fmod(bottom_elapsed, 30000) < UPDATE_INTERVAL_MS then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: on bottom %.0fs / %ds",
                    bottom_elapsed / 1000.0, cfg_rls_sec_ms / 1000))
        end

        if bottom_elapsed >= cfg_rls_sec_ms then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: release triggered (%.1fs on bottom)",
                    bottom_elapsed / 1000.0))
            activate_relay()
            ascent_start_ms = now_ms
            reset_light_cycle(now_ms)
            state = STATE_ASCENT
        end

    -- ??????????????? ASCENT ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_ASCENT then
        if check_failsafes() then
            state = STATE_RECOVERY
            return update, UPDATE_INTERVAL_MS
        end
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.WARNING,
                "DIVE: disarmed during ascent, re-arming")
            arming:arm()
        end

        update_lights(cfg_asc_lgt, now_ms)

        -- Confirm ascent via sustained upward velocity, then kill relay
        if relay_active then
            local vel = ahrs:get_velocity_NED()
            if vel and vel:z() < -RELAY_ASC_THRS then
                relay_asc_count = relay_asc_count + 1
                if relay_asc_count >= RELAY_ASC_SAMPLES then
                    deactivate_relay()
                end
            else
                relay_asc_count = 0
            end
        end

        local depth = get_depth_m()
        if depth then
            if math.fmod(now_ms - ascent_start_ms, 30000)
               < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: ascending, depth=%.2fm", depth))
            end
            if depth <= SURFACE_DEPTH_M then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: surface reached (%.2fm)", depth))
                state = STATE_RECOVERY
            end
        end

    -- ??????????????? RECOVERY (terminal) ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    elseif state == STATE_RECOVERY then
        if RC9 then RC9:set_override(LIGHT_PWM_MIN) end
        arming:disarm()
        DORIS_START:set_and_save(0)
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
