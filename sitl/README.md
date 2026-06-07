# DORIS SITL test environment

Reproducible, containerized ArduSub SITL for exercising the DORIS Lua dive
script and the extension backend without real hardware.

## Quick start (recommended)

From the repo root:

```bash
cd sitl
docker compose up --build
```

First run compiles ArduPilot Sub-4.7 inside the `ardusub` image (~15–20 min).
Later runs reuse the cached image and start in seconds.

| Endpoint | URL |
|---|---|
| DORIS UI / REST API | http://localhost:8095 |
| mavlink2rest WebSocket | ws://localhost:6040/ws |
| RTSP test camera | rtsp://localhost:8554/test |

### Run a dive (headless)

In a second terminal, from the repo root:

```bash
# Create a short SITL profile once (see "Configuration profiles" below)
python3 sitl/run_dive_test.py --vehicle localhost --profile sitl-interval --finalize
```

Or drive interactively with the MAVLink monitor:

```bash
python3 sitl/sitl_monitor.py --mav udpin:0.0.0.0:14551 --vehicle-ip localhost
# Wait for MISSION_START, then type: deploy
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  doris container (owns network namespace)                       │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐  │
│  │ extension    │  │ mavlink2rest│  │ ardusub (SITL+MAVProxy)│  │
│  │ :8095        │◄─┤ :6040       │◄─┤ doris.lua + params     │  │
│  │ (REST/UI)    │  │             │  │ fan-out :5772          │  │
│  └──────┬───────┘  └─────────────┘  └────────────────────────┘  │
│         │ Lua POST 127.0.0.1:8095 (production-faithful)         │
└─────────┼───────────────────────────────────────────────────────┘
          │
┌─────────┴─────────┐     UDP :14551
│  rtsp-server      │     sitl_monitor.py (host)
│  :8554/test       │
└───────────────────┘
```

The `ardusub` service shares the `doris` container's network namespace so the
Lua script's `POST 127.0.0.1:8095` hits the extension exactly as on BlueOS.

## Configuration profiles

Dive tests must load a profile via the API key `"configuration"` (not `"name"`).
Store profiles under `sitl/configurations/` (bind-mounted into the
extension). A ready-made `sitl_interval.json` profile is included for
`run_dive_test.py --profile sitl-interval`.

## Parameter files

| File | Purpose |
|---|---|
| `params/doris.parm` | Pre-Lua hardware + scripting (relay pin, SCR_ENABLE) |
| `params/doris_sitl.parm` | SITL-only sim knobs (SIM_BUOYANCY, SIM_GPS1_ENABLE, …) |

Lines marked `CONFIRM` in `doris.parm` should be checked against a real-vehicle
parameter dump before trusting relay/battery SITL behavior.

### Fresh EEPROM

```bash
WIPE=1 docker compose up ardusub
# or restart with WIPE=1 in docker-compose environment
```

## Fast inner loop (edit Lua without rebuild)

1. Edit `extension/backend/scripts/doris.lua`
2. `docker compose restart ardusub`

The script is bind-mounted; only the ArduPilot binary lives in the image.

## Native / devcontainer workflow (optional)

If you already have ArduPilot built locally:

```bash
./sitl/launch_sitl.sh              # ArduSub + MAVProxy on host
./sitl/start_sitl_test.sh --docker # tmux: SITL + monitor + docker compose
```

Point mavlink2rest at the host fan-out when not using the containerized
`ardusub` service:

```bash
M2R_CONNECT=tcpout:host.docker.internal:5772 docker compose up doris mavlink2rest rtsp-server
```

## Known SITL limitations

These are intentional gaps for Phase 1–2 of the test plan:

- **No seabed model** — the vehicle sinks indefinitely; bottom detection
  (`DESCENT → ON_BOTTOM`) requires zeroing buoyancy manually or via a future
  scenario harness.
- **Leak is param-injected** — `DORIS_INJ_LEAK` is set directly; confirm what
  bridges the real leak sensor on the vehicle.
- **`DORIS_DSC_DUR`** is pushed by the backend but not declared in the Lua
  param table (harmless no-op today; fix planned).

## Troubleshooting

| Symptom | Check |
|---|---|
| `ardusub` exits immediately | `docker compose logs ardusub` — missing param file or script mount |
| Extension can't reach autopilot | `docker compose logs mavlink2rest` — wait for SITL to bind :5772 |
| Lua IPcam POST fails | Extension must share netns with SITL (default compose layout) |
| Monitor sees no heartbeat | Ensure `MONITOR_OUT` reaches host :14551; run monitor on host |
| First build very slow | Normal — ArduPilot compile; subsequent builds use cache |
