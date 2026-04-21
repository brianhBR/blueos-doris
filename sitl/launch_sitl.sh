#!/bin/bash
set -euo pipefail

# DORIS SITL Launcher
# Launches ArduSub SITL with the DORIS mission script and parameters.
#
# Run from anywhere — paths are resolved relative to this script.
#
# Usage:
#   ./sitl/launch_sitl.sh [--wipe] [--gui]
#
# Prerequisites:
#   - ArduSub SITL built (set ARDUPILOT_HOME or use default)
#   - mavproxy installed: pip install mavproxy
#   - pymavlink installed: pip install pymavlink

# ============================================================================
# Configuration
# ============================================================================

ARDUPILOT_HOME="${ARDUPILOT_HOME:-/home/devcontainers/ardupilot}"
SITL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SITL_ROOT}/.." && pwd)"

DORIS_SCRIPT="${REPO_ROOT}/extension/backend/scripts/doris.lua"
DORIS_PARM="${SITL_ROOT}/params/doris.parm"
DORIS_SITL_PARM="${SITL_ROOT}/params/doris_sitl.parm"
LOG_DIR="${SITL_ROOT}/logs"

SCRIPTS_DIR="${ARDUPILOT_HOME}/scripts"
SCRIPT_SYMLINK="${SCRIPTS_DIR}/doris.lua"

GCS_OUT="udp:127.0.0.1:14550"
MONITOR_OUT="udp:127.0.0.1:14551"
M2R_OUT="tcpin:0.0.0.0:5772"
QGC_OUT="tcpin:0.0.0.0:5773"
SITL_PORT=5760

GUI_FLAG=false
WIPE_FLAG=false
BG_PIDS=()

# ============================================================================
# Argument Parsing
# ============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Launch ArduSub SITL for DORIS drop camera testing.

Options:
  --gui      Enable MAVProxy GUI (console + map), requires working X11
  --wipe     Force wipe EEPROM (reapply all default parameters)
  -h, --help Show this help message

Environment:
  ARDUPILOT_HOME   Path to ArduPilot source (default: /home/devcontainers/ardupilot)

MAVProxy outputs:
  UDP 14550  — GCS (QGroundControl auto-detect)
  UDP 14551  — sitl_monitor.py
  TCP 5772   — mavlink2rest (docker-compose.yml)
  TCP 5773   — QGC/GCS manual TCP connection
EOF
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --gui)     GUI_FLAG=true ;;
        --wipe)    WIPE_FLAG=true ;;
        -h|--help) usage ;;
        *)
            echo "ERROR: Unknown option: $arg"
            usage
            ;;
    esac
done

# ============================================================================
# Validation
# ============================================================================

if [ ! -d "${ARDUPILOT_HOME}" ]; then
    echo "ERROR: ArduPilot directory not found: ${ARDUPILOT_HOME}"
    echo "Set ARDUPILOT_HOME or install ArduPilot."
    exit 1
fi

ARDUSUB_BIN="${ARDUPILOT_HOME}/build/sitl/bin/ardusub"
if [ ! -f "${ARDUSUB_BIN}" ]; then
    echo "ERROR: ardusub binary not found: ${ARDUSUB_BIN}"
    echo "Build with: cd ${ARDUPILOT_HOME} && ./waf configure --board sitl && ./waf sub"
    exit 1
fi

if [ ! -f "${DORIS_SCRIPT}" ]; then
    echo "ERROR: DORIS mission script not found: ${DORIS_SCRIPT}"
    exit 1
fi

for pf in "${DORIS_PARM}" "${DORIS_SITL_PARM}"; do
    if [ ! -f "${pf}" ]; then
        echo "ERROR: Parameter file not found: ${pf}"
        exit 1
    fi
done

mkdir -p "${LOG_DIR}"

# ============================================================================
# Cleanup Trap
# ============================================================================

cleanup() {
    echo ""
    echo "Cleaning up ..."
    for pid in "${BG_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Stopping PID $pid"
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    if [ -L "${SCRIPT_SYMLINK}" ]; then
        echo "  Removing symlink: ${SCRIPT_SYMLINK}"
        rm -f "${SCRIPT_SYMLINK}"
    fi
    echo "Cleanup complete."
}

trap cleanup EXIT INT TERM

# ============================================================================
# Setup
# ============================================================================

echo "============================================="
echo "DORIS SITL Launcher"
echo "============================================="
echo "ArduPilot:   ${ARDUPILOT_HOME}"
echo "Script:      ${DORIS_SCRIPT}"
echo "Params:      ${DORIS_PARM}"
echo "SITL params: ${DORIS_SITL_PARM}"
echo "Logs:        ${LOG_DIR}"
echo "============================================="

# Symlink Lua script into ArduPilot scripts directory
mkdir -p "${SCRIPTS_DIR}"
if [ -e "${SCRIPT_SYMLINK}" ] && [ ! -L "${SCRIPT_SYMLINK}" ]; then
    echo "WARNING: ${SCRIPT_SYMLINK} exists and is not a symlink. Backing up."
    mv "${SCRIPT_SYMLINK}" "${SCRIPT_SYMLINK}.bak.$(date +%s)"
fi
ln -sf "${DORIS_SCRIPT}" "${SCRIPT_SYMLINK}"
echo "Symlink: ${SCRIPT_SYMLINK} -> ${DORIS_SCRIPT}"

# Wipe EEPROM if requested or if it doesn't exist yet
EEPROM_FILE="${ARDUPILOT_HOME}/eeprom.bin"
if [ "${WIPE_FLAG}" = true ] || [ ! -f "${EEPROM_FILE}" ]; then
    echo "Wiping EEPROM ..."
    rm -f "${EEPROM_FILE}"
fi

# ============================================================================
# Launch ArduSub SITL
# ============================================================================

echo ""
echo "Starting ArduSub SITL ..."
cd "${ARDUPILOT_HOME}"

"${ARDUSUB_BIN}" \
    --model vectored \
    --speedup 1 \
    --defaults "Tools/autotest/default_params/sub.parm,${DORIS_PARM},${DORIS_SITL_PARM}" \
    --sim-address=127.0.0.1 \
    -I0 \
    &>"${LOG_DIR}/ardusub_sitl.log" &
ARDUSUB_PID=$!
BG_PIDS+=("${ARDUSUB_PID}")
echo "ArduSub PID: ${ARDUSUB_PID} (log: ${LOG_DIR}/ardusub_sitl.log)"

echo "Waiting for ArduSub to bind port ${SITL_PORT} ..."
RETRIES=0
MAX_RETRIES=30
while ! ss -tlnp 2>/dev/null | grep -q ":${SITL_PORT}" && \
      ! netstat -tlnp 2>/dev/null | grep -q ":${SITL_PORT}"; do
    RETRIES=$((RETRIES + 1))
    if [ "${RETRIES}" -ge "${MAX_RETRIES}" ]; then
        echo "ERROR: ArduSub did not bind port ${SITL_PORT} within ${MAX_RETRIES}s"
        tail -20 "${LOG_DIR}/ardusub_sitl.log" 2>/dev/null || true
        exit 1
    fi
    if ! kill -0 "${ARDUSUB_PID}" 2>/dev/null; then
        echo "ERROR: ArduSub process died"
        tail -20 "${LOG_DIR}/ardusub_sitl.log" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done
echo "ArduSub ready on port ${SITL_PORT}"

# ============================================================================
# Launch MAVProxy
# ============================================================================

MAVPROXY_OPTS=()
if [ "${GUI_FLAG:-false}" = true ] && [ -n "${DISPLAY:-}" ]; then
    MAVPROXY_OPTS+=("--console" "--map")
fi
if [ ! -t 0 ]; then
    MAVPROXY_OPTS+=("--non-interactive")
fi

echo ""
echo "Starting MAVProxy ..."
echo "  GCS UDP:      ${GCS_OUT}  (QGC auto-detect)"
echo "  Monitor UDP:  ${MONITOR_OUT}  (sitl_monitor.py)"
echo "  mavlink2rest: ${M2R_OUT}  (docker-compose.yml)"
echo "  QGC TCP:      ${QGC_OUT}  (QGC → Comm Links → TCP → localhost:5773)"
echo ""

mavproxy.py \
    --master "tcp:127.0.0.1:${SITL_PORT}" \
    --sitl "127.0.0.1:5501" \
    --out "${GCS_OUT}" \
    --out "${MONITOR_OUT}" \
    --out "${M2R_OUT}" \
    --out "${QGC_OUT}" \
    "${MAVPROXY_OPTS[@]+"${MAVPROXY_OPTS[@]}"}"
