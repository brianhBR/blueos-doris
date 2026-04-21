#!/bin/bash
set -euo pipefail

# DORIS SITL Test Environment
#
# Opens a tmux session with:
#   Window 0, left pane  — ArduSub SITL + MAVProxy  (launch_sitl.sh)
#   Window 0, right pane — sitl_monitor.py
#
# With --docker, adds Window 1:
#   Window 1 — docker compose (mavlink2rest + DORIS UI on :8095)
#
# Usage:
#   ./sitl/start_sitl_test.sh            # basic SITL test
#   ./sitl/start_sitl_test.sh --wipe     # fresh EEPROM
#   ./sitl/start_sitl_test.sh --docker   # include DORIS extension UI
#
# Once running:
#   Ctrl-b 0 / Ctrl-b 1    switch windows
#   Ctrl-b ←/→             switch panes within a window
#   Ctrl-b d               detach
#   tmux attach -t doris-sitl    reattach
#   tmux kill-session -t doris-sitl    stop everything
#
# DORIS UI (with --docker): http://localhost:8095
# QGC TCP connection:       localhost:5773
# mavlink2rest WebSocket:   ws://localhost:6040/ws

SITL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="doris-sitl"

DOCKER_FLAG=false
WIPE_FLAG=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Start the DORIS SITL test environment in a tmux session.

Options:
  --docker   Also start the DORIS extension UI (mavlink2rest + docker compose)
  --wipe     Wipe ArduSub EEPROM before starting (fresh parameter defaults)
  -h, --help Show this help
EOF
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --docker) DOCKER_FLAG=true ;;
        --wipe)   WIPE_FLAG=true ;;
        -h|--help) usage ;;
        *) echo "ERROR: Unknown option: $arg"; usage ;;
    esac
done

if ! command -v tmux &>/dev/null; then
    echo "ERROR: tmux is required. Install with: sudo apt install tmux"
    exit 1
fi

if [ "$DOCKER_FLAG" = true ] && ! command -v docker &>/dev/null; then
    echo "ERROR: --docker requires Docker."
    exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Killing existing session: $SESSION"
    tmux kill-session -t "$SESSION"
    sleep 1
fi

SITL_ARGS=""
[ "$WIPE_FLAG" = true ] && SITL_ARGS="--wipe"

SITL_CMD="bash ${SITL_ROOT}/launch_sitl.sh ${SITL_ARGS}"
MONITOR_CMD="bash -c 'until ss -ulnp 2>/dev/null | grep -q :14551 || nc -uz 127.0.0.1 14551 2>/dev/null; do sleep 1; done; python3 ${SITL_ROOT}/sitl_monitor.py udp:127.0.0.1:14551'"
DOCKER_CMD="docker compose -f ${SITL_ROOT}/docker-compose.yml up --build"

tmux new-session -d -s "$SESSION" -x 220 -y 50
tmux rename-window -t "$SESSION:0" "sitl"
tmux send-keys -t "$SESSION:0" "$SITL_CMD" Enter
tmux split-window -t "$SESSION:0" -h -p 45
tmux send-keys -t "$SESSION:0.1" "$MONITOR_CMD" Enter
tmux select-pane -t "$SESSION:0.0"

if [ "$DOCKER_FLAG" = true ]; then
    tmux new-window -t "$SESSION:1" -n "docker"
    tmux send-keys -t "$SESSION:1" "cd ${SITL_ROOT} && $DOCKER_CMD" Enter
    tmux select-window -t "$SESSION:0"
fi

if [ "$DOCKER_FLAG" = true ]; then
    cat <<EOF

Starting DORIS SITL test environment (session: $SESSION)
  Window 0 (sitl):    ArduSub SITL + MAVProxy | sitl_monitor.py
  Window 1 (docker):  docker compose + DORIS extension UI

  DORIS UI            http://localhost:8095  (once Docker build completes)
  mavlink2rest WS     ws://localhost:6040/ws
  QGC TCP             localhost:5773

  Ctrl-b 0 / Ctrl-b 1    switch windows
  Ctrl-b ←/→             switch panes
  Ctrl-b d               detach
  tmux attach -t $SESSION

EOF
else
    cat <<EOF

Starting DORIS SITL test environment (session: $SESSION)
  Ctrl-b ←/→    switch panes
  Ctrl-b d       detach
  tmux attach -t $SESSION

EOF
fi

tmux attach -t "$SESSION"
