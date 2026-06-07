#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Container entrypoint for ArduSub SITL (DORIS).
#
# Mirrors the host-side launch_sitl.sh, but everything runs inside the
# container. doris.lua and the param files are bind-mounted in (see
# docker-compose.yml) so editing the script + restarting the container is a
# fast inner loop — no ArduPilot rebuild needed.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ARDUPILOT="${ARDUPILOT:-/ardupilot}"
ARDUSUB_BIN="${ARDUPILOT}/build/sitl/bin/ardusub"
SCRIPTS_DIR="${ARDUPILOT}/scripts"

# Bind-mounted by docker-compose.
DORIS_SCRIPT="${DORIS_SCRIPT:-/sitl/doris.lua}"
DORIS_PARM="${DORIS_PARM:-/sitl/params/doris.parm}"
DORIS_SITL_PARM="${DORIS_SITL_PARM:-/sitl/params/doris_sitl.parm}"

# MAVLink fan-out targets (override via the compose environment block).
#   M2R_OUT      — where mavlink2rest connects (TCP server we host).
#   MONITOR_OUT  — host-side sitl_monitor.py (UDP). Needs host.docker.internal,
#                  which resolves because we share the doris container's netns
#                  (which sets extra_hosts host-gateway).
M2R_OUT="${M2R_OUT:-tcpin:0.0.0.0:5772}"
MONITOR_OUT="${MONITOR_OUT:-udpout:host.docker.internal:14551}"
SITL_SPEEDUP="${SITL_SPEEDUP:-1}"
SITL_PORT=5760

# Writable run dir so eeprom.bin / logs land somewhere we can wipe.
RUN_DIR="${SITL_RUN_DIR:-/tmp/ardusub_run}"
mkdir -p "${RUN_DIR}"
cd "${RUN_DIR}"

# Optional EEPROM wipe (fresh param defaults each boot when WIPE=1).
if [ "${WIPE:-0}" = "1" ]; then
    echo "[entrypoint] WIPE=1 — removing eeprom.bin for fresh defaults"
    rm -f "${RUN_DIR}/eeprom.bin"
fi

for f in "${DORIS_SCRIPT}" "${DORIS_PARM}" "${DORIS_SITL_PARM}"; do
    if [ ! -f "${f}" ]; then
        echo "[entrypoint] ERROR: required file not found: ${f}" >&2
        exit 1
    fi
done

mkdir -p "${SCRIPTS_DIR}"
ln -sf "${DORIS_SCRIPT}" "${SCRIPTS_DIR}/doris.lua"
echo "[entrypoint] script  : ${SCRIPTS_DIR}/doris.lua -> ${DORIS_SCRIPT}"
echo "[entrypoint] params  : sub.parm + ${DORIS_PARM} + ${DORIS_SITL_PARM}"
echo "[entrypoint] m2r out : ${M2R_OUT}"
echo "[entrypoint] mon out : ${MONITOR_OUT}"

echo "[entrypoint] starting ArduSub SITL ..."
"${ARDUSUB_BIN}" \
    --model vectored \
    --speedup "${SITL_SPEEDUP}" \
    --defaults "${ARDUPILOT}/Tools/autotest/default_params/sub.parm,${DORIS_PARM},${DORIS_SITL_PARM}" \
    --sim-address=127.0.0.1 \
    -I0 &
SUB_PID=$!

cleanup() {
    echo "[entrypoint] shutting down (ardusub pid ${SUB_PID})"
    kill "${SUB_PID}" 2>/dev/null || true
    wait "${SUB_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[entrypoint] waiting for SITL MAVLink on :${SITL_PORT} ..."
for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${SITL_PORT}") 2>/dev/null; then
        exec 3>&- 3<&-
        break
    fi
    if ! kill -0 "${SUB_PID}" 2>/dev/null; then
        echo "[entrypoint] ERROR: ardusub exited before binding :${SITL_PORT}" >&2
        exit 1
    fi
    sleep 1
done
echo "[entrypoint] SITL ready; starting MAVProxy fan-out"

# MAVProxy is the long-lived foreground process (PID 1). It connects to the
# SITL master and re-publishes the stream to mavlink2rest and the monitor.
exec mavproxy.py \
    --master "tcp:127.0.0.1:${SITL_PORT}" \
    --out "${M2R_OUT}" \
    --out "${MONITOR_OUT}" \
    --non-interactive
