--[[
   Simple dive script for ArduSub
   - Arms the vehicle
   - Descends for a configurable duration
   - Ascends back to surface until depth <= target surface depth
   - Disarms the vehicle

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

-- ArduSub modes
local MODE_ALT_HOLD  = 2
local MODE_SURFACE   = 9

-- configurable header variables
local DIVE_DURATION_MS   = 10000   -- how long to descend (ms)
local SURFACE_DEPTH_M    = 0.5     -- target depth to stop ascending (m)
local DESCENT_THROTTLE   = 1300    -- RC3 PWM for descending (below 1500 = down in ArduSub)
local NEUTRAL_THROTTLE   = 1500    -- RC3 PWM for neutral buoyancy
local UPDATE_INTERVAL_MS = 500     -- main loop rate (ms)
local ARM_RETRY_MS       = 2000    -- time between arm attempts (ms)
local ARM_TIMEOUT_MS     = 10000   -- give up arming after this long (ms)
local SURFACE_PRESSURE   = 101325  -- surface pressure in Pa (standard atmospheric)
local WATER_DENSITY      = 1025.0  -- seawater density in kg/m^3
local GRAVITY            = 9.80665 -- gravitational acceleration in m/s^2

-- state machine
local STATE_WAIT_START = -1
local STATE_INIT       = 0
local STATE_ARMING     = 1
local STATE_DIVING     = 2
local STATE_SURFACING  = 3
local STATE_DISARMING  = 4
local STATE_DONE       = 5

-- DORIS_START parameter: set to 1 to begin the dive
local PARAM_TABLE_KEY = 73
assert(param:add_table(PARAM_TABLE_KEY, "DORIS_", 1), "DIVE: could not add DORIS_ param table")
assert(param:add_param(PARAM_TABLE_KEY, 1, "START", 0), "DIVE: could not add DORIS_START param")
local DORIS_START = Parameter("DORIS_START")

-- runtime variables
local state             = STATE_WAIT_START
local elapsed_time      = 0
local dive_start_ms     = 0
local arm_start_ms      = 0
local last_update_ms    = 0
local script_start_ms   = 0
local RC3 = rc:get_channel(3)

local function get_depth_m()
    local pressure = baro:get_pressure()
    if not pressure then
        return nil
    end
    return (pressure - SURFACE_PRESSURE) / (WATER_DENSITY * GRAVITY)
end

function update()
    local now_ms = millis():tofloat()

    if last_update_ms > 0 then
        elapsed_time = now_ms - script_start_ms
    else
        script_start_ms = now_ms
    end
    last_update_ms = now_ms

    if state == STATE_WAIT_START then
        if DORIS_START:get() >= 1 then
            gcs:send_text(MAV_SEVERITY.INFO, "DIVE: DORIS_START=1, beginning sequence")
            state = STATE_INIT
        end

    elseif state == STATE_INIT then
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: initialising")
        vehicle:set_mode(MODE_ALT_HOLD)
        arm_start_ms = now_ms
        state = STATE_ARMING

    elseif state == STATE_ARMING then
        if arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.INFO, "DIVE: armed, beginning descent")
            dive_start_ms = now_ms
            state = STATE_DIVING
        elseif now_ms - arm_start_ms > ARM_TIMEOUT_MS then
            gcs:send_text(MAV_SEVERITY.ERROR, "DIVE: arm timeout, aborting")
            state = STATE_DONE
        else
            if math.fmod(now_ms - arm_start_ms, ARM_RETRY_MS) < UPDATE_INTERVAL_MS then
                gcs:send_text(MAV_SEVERITY.INFO, "DIVE: attempting to arm")
                arming:arm()
            end
        end

    elseif state == STATE_DIVING then
        RC3:set_override(DESCENT_THROTTLE)

        local dive_elapsed = now_ms - dive_start_ms
        if dive_elapsed >= DIVE_DURATION_MS then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: descent complete (%.1fs), surfacing", dive_elapsed / 1000.0))
            RC3:set_override(NEUTRAL_THROTTLE)
            state = STATE_SURFACING
        end

    elseif state == STATE_SURFACING then
        vehicle:set_mode(MODE_SURFACE)

        local depth = get_depth_m()
        if depth then
            gcs:send_text(MAV_SEVERITY.DEBUG,
                string.format("DIVE: surfacing, depth=%.2fm", depth))
            if depth <= SURFACE_DEPTH_M then
                gcs:send_text(MAV_SEVERITY.INFO,
                    string.format("DIVE: reached %.2fm, disarming", depth))
                RC3:set_override(NEUTRAL_THROTTLE)
                state = STATE_DISARMING
            end
        end

    elseif state == STATE_DISARMING then
        RC3:set_override(NEUTRAL_THROTTLE)
        arming:disarm()
        if not arming:is_armed() then
            gcs:send_text(MAV_SEVERITY.INFO,
                string.format("DIVE: complete, total time %.1fs", elapsed_time / 1000.0))
            state = STATE_DONE
        end

    elseif state == STATE_DONE then
        DORIS_START:set(0)
        gcs:send_text(MAV_SEVERITY.INFO, "DIVE: complete, set DORIS_START=1 to run again")
        elapsed_time = 0
        last_update_ms = 0
        state = STATE_WAIT_START
    end

    return update, UPDATE_INTERVAL_MS
end

gcs:send_text(MAV_SEVERITY.INFO, "DIVE: script loaded, set DORIS_START=1 to begin")
return update()
