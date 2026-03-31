--[[
   DORIS dive script for ArduSub

   Mission phases:
     WAIT_START -> INIT -> ARMING -> DESCENDING -> ON_BOTTOM -> SURFACING -> DISARMING -> DONE

   Configuration is received via DORIS_* MAVLink parameters that the
   backend pushes before setting DORIS_START=1.

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

-- ArduSub flight modes
local MODE_ALT_HOLD = 2
local MODE_SURFACE  = 9

-- fixed constants
local SURFACE_DEPTH_M    = 0.5
local DESCENT_THROTTLE   = 1300
local ASCENT_THROTTLE    = 1700
local NEUTRAL_THROTTLE   = 1500
local UPDATE_INTERVAL_MS = 500
local ARM_RETRY_MS       = 2000
local ARM_TIMEOUT_MS     = 10000
local WATER_DENSITY      = 1025.0
local GRAVITY            = 9.80665

-- calibrate surface pressure from the barometer at script load
local SURFACE_PRESSURE = baro:get_pressure() or 101325

-- light PWM range
local LIGHT_PWM_MIN = 1100
local LIGHT_PWM_MAX = 1900

-- state machine
local STATE_WAIT_START = -1
local STATE_INIT       = 0
local STATE_ARMING     = 1
local STATE_DESCENDING = 2
local STATE_ON_BOTTOM  = 3
local STATE_SURFACING  = 4
local STATE_DISARMING  = 5
local STATE_DONE       = 6

-- ── DORIS parameter table ──────────────────────────────────────────
local PARAM_TABLE_KEY  = 73
local PARAM_TABLE_SIZE = 7

assert(param:add_table(PARAM_TABLE_KEY, "DORIS_", PARAM_TABLE_SIZE),
       "DIVE: could not add DORIS_ param table")

assert(param:add_param(PARAM_TABLE_KEY, 1, "START",   0), "DIVE: could not add DORIS_START")
assert(param:add_param(PARAM_TABLE_KEY, 2, "DSC_DUR", 30), "DIVE: could not add DORIS_DSC_DUR")
assert(param:add_param(PARAM_TABLE_KEY, 3, "RLS_SEC", 60), "DIVE: could not add DORIS_RLS_SEC")
assert(param:add_param(PARAM_TABLE_KEY, 4, "DSC_LGT", 0), "DIVE: could not add DORIS_DSC_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 5, "BTM_LGT", 0), "DIVE: could not add DORIS_BTM_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 6, "ASC_LGT", 0), "DIVE: could not add DORIS_ASC_LGT")
assert(param:add_param(PARAM_TABLE_KEY, 7, "LGT_BRT", 75), "DIVE: could not add DORIS_LGT_BRT")

local DORIS_START   = Parameter("DORIS_START")
local DORIS_DSC_DUR = Parameter("DORIS_DSC_DUR")
local DORIS_RLS_SEC = Parameter("DORIS_RLS_SEC")
local DORIS_DSC_LGT = Parameter("DORIS_DSC_LGT")
local DORIS_BTM_LGT = Parameter("DORIS_BTM_LGT")
local DORIS_ASC_LGT = Parameter("DORIS_ASC_LGT")
local DORIS_LGT_BRT = Parameter("DORIS_LGT_BRT")

DORIS_START:set_and_save(0)

-- ── runtime state ──────────────────────────────────────────────────
local state           = STATE_WAIT_START
local dive_start_ms   = 0
local arm_start_ms    = 0
local last_update_ms  = 0
local script_start_ms = 0

-- snapshotted config (read once when DORIS_START goes to 1)
local cfg_dsc_dur_ms  = 30000
local cfg_rls_sec_ms  = 60000
local cfg_dsc_lgt     = false
local cfg_btm_lgt     = false
local cfg_asc_lgt     = false
local cfg_lgt_pwm     = LIGHT_PWM_MIN

local RC3 = rc:get_channel(3)
local RC9 = rc:get_channel(9)

-- ── helpers ────────────────────────────────────────────────────────
local function get_depth_m()
    local pressure = baro:get_pressure()
    if not pressure then return nil end
    return (pressure - SURFACE_PRESSURE) / (WATER_DENSITY * GRAVITY)
end

local function brightness_to_pwm(pct)
    if pct <= 0 then return LIGHT_PWM_MIN end
    if pct >= 100 then return LIGHT_PWM_MAX end
    return math.floor(LIGHT_PWM_MIN + (pct / 100.0) * (LIGHT_PWM_MAX - LIGHT_PWM_MIN))
end

local function set_lights(on)
    if on then
        RC9:set_override(cfg_lgt_pwm)
    else
        RC9:set_override(LIGHT_PWM_MIN)
    end
end

local function snapshot_config()
    local dsc_dur = DORIS_DSC_DUR:get()
    local rls_sec = DORIS_RLS_SEC:get()
    local brt     = DORIS_LGT_BRT:get()

    cfg_dsc_dur_ms = math.max(dsc_dur, 1) * 1000
    cfg_rls_sec_ms = math.max(rls_sec, 1) * 1000
    cfg_dsc_lgt    = DORIS_DSC_LGT:get() >= 1
    cfg_btm_lgt    = DORIS_BTM_LGT:get() >= 1
    cfg_asc_lgt    = DORIS_ASC_LGT:get() >= 1
    cfg_lgt_pwm    = brightness_to_pwm(brt)

    gcs:send_text(MAV_SEVERITY.INFO,
        string.format("DIVE: config dsc=%ds rls=%ds dsc_l=%d btm_l=%d asc_l=%d brt=%d%%",
            dsc_dur, rls_sec,
            cfg_dsc_lgt and 1 or 0,
            cfg_btm_lgt and 1 or 0,
            cfg_asc_lgt and 1 or 0,
            brt))
end

-- ── main loop ──────────────────────────────────────────────────────
function update()
    local now_ms = millis():tofloat()

    if last_update_ms == 0 then
        script_start_ms = now_ms
    end
    last_update_ms = now_ms

    -- ─── WAIT_START ────────────────────────────────────────────────
    if state == STATE_WAIT_START then
        if DORIS_START:get() >= 1 then
            gcs:send_text(MAV_SEVERITY.INFO, "DIVE: DORIS_START=1, beginning sequence")
            snapshot_config()
            state = STATE_INIT
        end

    -- ─── INIT ──────────────────────────────────────────────────────
    elseif state == STATE_INIT then
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: initialising — ALT_HOLD")
        vehicle:set_mode(MODE_ALT_HOLD)
        set_lights(cfg_dsc_lgt)
        arm_start_ms = now_ms
        state = STATE_ARMING

    -- ─── ARMING ────────────────────────────────────────────────────
    elseif state == STATE_ARMING then
        if arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.INFO, "DIVE: armed, beginning descent")
            dive_start_ms = now_ms
            state = STATE_DESCENDING
        elseif now_ms - arm_start_ms > ARM_TIMEOUT_MS then
            gcs:send_text(MAV_SEVERITY.ERROR, "DIVE: arm timeout, aborting")
            set_lights(false)
            state = STATE_DONE
        else
            if math.fmod(now_ms - arm_start_ms, ARM_RETRY_MS) < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO, "DIVE: attempting to arm")
                arming:arm()
            end
        end

    -- ─── DESCENDING ────────────────────────────────────────────────
    elseif state == STATE_DESCENDING then
        RC3:set_override(DESCENT_THROTTLE)
        set_lights(cfg_dsc_lgt)

        local elapsed = now_ms - dive_start_ms
        if elapsed >= cfg_dsc_dur_ms then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: descent done (%.1fs), on bottom", elapsed / 1000.0))
            RC3:set_override(NEUTRAL_THROTTLE)
            set_lights(cfg_btm_lgt)
            state = STATE_ON_BOTTOM
        end

    -- ─── ON_BOTTOM ─────────────────────────────────────────────────
    elseif state == STATE_ON_BOTTOM then
        RC3:set_override(NEUTRAL_THROTTLE)
        set_lights(cfg_btm_lgt)

        local total_elapsed = now_ms - dive_start_ms
        if total_elapsed >= cfg_rls_sec_ms then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: release time reached (%.1fs), surfacing", total_elapsed / 1000.0))
            set_lights(cfg_asc_lgt)
            state = STATE_SURFACING
        end

    -- ─── SURFACING ─────────────────────────────────────────────────
    elseif state == STATE_SURFACING then
        vehicle:set_mode(MODE_ALT_HOLD)
        RC3:set_override(ASCENT_THROTTLE)
        set_lights(cfg_asc_lgt)

        local depth = get_depth_m()
        if depth then
            gcs:send_text(MAV_SEVERITY.DEBUG,
                string.format("DIVE: surfacing, depth=%.2fm", depth))
            if depth <= SURFACE_DEPTH_M then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: surface reached (%.2fm), disarming", depth))
                RC3:set_override(NEUTRAL_THROTTLE)
                set_lights(false)
                state = STATE_DISARMING
            end
        end

    -- ─── DISARMING ─────────────────────────────────────────────────
    elseif state == STATE_DISARMING then
        RC3:set_override(NEUTRAL_THROTTLE)
        set_lights(false)
        arming:disarm()
        if not arming:is_armed() then
            local total = (now_ms - dive_start_ms) / 1000.0
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: complete, total time %.1fs", total))
            state = STATE_DONE
        end

    -- ─── DONE ──────────────────────────────────────────────────────
    elseif state == STATE_DONE then
        DORIS_START:set(0)
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: mission done, DORIS_START=0")
        last_update_ms = 0
        state = STATE_WAIT_START
    end

    return update, UPDATE_INTERVAL_MS
end

gcs:send_text(MAV_SEVERITY.INFO, "DIVE: script loaded, waiting for DORIS_START=1")
return update()
