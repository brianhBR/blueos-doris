# DORIS - Deep Ocean Research and Imaging System

A BlueOS extension for deep ocean research and imaging operations.

## bh-0.6 safe-surface wiring

Lua owns release policy and mirrors every release request to both controllers:
it drives the Navigator relay on `DORIS_RELAY_CH` and repeatedly sends the
`RELAY` request over MAVLink, which AGT firmware v0.3.0 or newer uses to drive
AGT Relay 2. One software build therefore covers legacy Navigator-wired systems
and AGT-wired systems.

Wire the release actuator to exactly one output. Never wire it to both
Navigator SERVO14 and AGT Relay 2: two independent controller outputs driving
one relay input is not a supported electrical configuration. Set
`DORIS_RELAY_CH=-1` on AGT-wired systems to leave the Navigator output idle.

The backend blocks mission start only when neither release path is usable. A
single degraded path — for example AGT firmware older than v0.3.0, or Navigator
frame parameters that do not match — starts the mission with a warning that
release redundancy is reduced.

AGT-requested host shutdown is disabled by default. Enable it only on an
AGT-wired system and only after an end-to-end bench test, by setting
`DORIS_AGT_SHUTDOWN_ENABLED=true` on the backend service; cutting host power
takes the Navigator release output offline, so the backend refuses to start a
mission when shutdown is enabled without a healthy AGT release path. A valid
request must come from MAVLink system 1/component 192 and follow an `AGT_CAP=3`
advertisement. The backend finalizes recording, runs `sync`, sends `PWR_ACK=1`
from system 1/component 191, and only then asks BlueOS Commander to power off.

The bh-0.6 MAVLink names are `RELAY` (Lua request), `AGT_CAP=3` (bit 0 AGT
release ownership, bit 1 safe shutdown), `REL_STAT` (physical release state),
`PWR_SHDN` (`1` requests shutdown and `0` resets the request), and `PWR_ACK=1`.
Names are intentionally at most ten characters for `NAMED_VALUE_FLOAT`.

## Features

- **Real-time System Status**: Battery, storage, and CPU monitoring
- **Location Tracking**: GPS position with satellite count
- **Network Management**: WiFi scanning and connection via BlueOS
- **Mission Programming**: Configure and manage research missions
- **Sensor Configuration**: Monitor and configure connected modules
- **Media Management**: Access captured images and video
- **BlueOS Integration**: Automatic service registration and discovery

## Architecture

```
blueos-doris-extension/
├── frontend/              # Vue 3 + TypeScript web interface
├── backend/               # Python + Robyn API server
│   └── src/doris/
│       ├── routes/        # API endpoints
│       ├── services/      # BlueOS service integrations
│       └── models/        # Pydantic data models
├── .github/workflows/     # CI/CD deployment
├── Dockerfile             # BlueOS extension container
└── docker-compose.yml     # Local development setup
```

## Quick Start

### Using Docker (Recommended)

```bash
# Build the image
docker build -t doris:latest .

# Run locally
docker run -p 8095:8095 --add-host=host.docker.internal:host-gateway doris:latest
```

Then open http://localhost:8095

### Using Docker Compose

```bash
# Edit docker-compose.yml to set your BlueOS IP if needed
docker-compose up --build
```

## Development

### Prerequisites

- Node.js 20+ with Yarn
- Python 3.11+
- Docker (for container builds)

### Local Development

1. **Start the frontend dev server:**

```bash
cd frontend
yarn install
yarn dev
```

2. **Start the backend:**

```bash
cd backend
uv sync
uv run python -m doris.main
```

3. **Configure BlueOS connection:**

Set the environment variable to point to your BlueOS instance:

```bash
export DORIS_BLUEOS_ADDRESS=http://192.168.2.2
```

### Building for Production

```bash
# Build frontend
cd frontend && yarn build

# Run backend (serves frontend)
cd backend && python -m doris.main
```

## Docker

### Build Optimization

The Dockerfile is optimized for layer caching. Only changes to `pyproject.toml` will trigger a full rebuild (including `uvloop` compilation which takes ~10 minutes). Source code changes use cached dependencies for fast rebuilds (~30 seconds).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DORIS_BLUEOS_ADDRESS` | `http://host.docker.internal` | BlueOS API base URL |
| `DORIS_PORT` | `8095` | Backend server port |
| `DORIS_HOST` | `0.0.0.0` | Backend bind address |

## BlueOS Extension

When installed as a BlueOS extension, DORIS automatically connects to BlueOS services via `host.docker.internal`.

### Service Registration

DORIS implements the `/register_service` endpoint for BlueOS Helper discovery. This allows BlueOS to:

- Display DORIS in the extensions menu
- Set up automatic nginx routing
- Show service metadata (icon, description, version)

### Installation

**From Docker Hub:**
```
sailorman225/blueos-doris:latest
```

**From GitHub Container Registry:**
```
ghcr.io/brianhBR/blueos-doris:latest
```

1. In BlueOS, go to **Extensions → Install from URL**
2. Enter one of the Docker image URLs above
3. DORIS will appear in the extensions menu

### BlueOS APIs Used

| Service | Port | Purpose |
|---------|------|---------|
| WiFi Manager | 9000 | Network scanning and connection |
| MAVLink2Rest | 6040 | GPS, battery, and vehicle data |
| Linux2Rest | 6030 | System information and storage |
| Ping Service | 9110 | Sonar sensor data |
| Camera Manager | 6020 | Camera control |
| Bag of Holding | 9101 | Persistent storage |

## API Documentation

When running, API documentation is available at:

- **OpenAPI (Swagger)**: http://localhost:8095/docs

### Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/register_service` | BlueOS service registration |
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/system/status` | System status (battery, storage, location) |
| GET | `/api/v1/network` | Network information |
| GET | `/api/v1/sensors/modules` | Connected modules |
| GET | `/api/v1/missions` | List missions |
| POST | `/api/v1/missions` | Create mission |
| GET | `/api/v1/media/files` | List media files |

## Deployment

### GitHub Actions

The repository includes automated deployment to both Docker Hub and GitHub Container Registry:

```yaml
# .github/workflows/deploy.yml
# Triggers on push, pull request, and manual dispatch
```

**Required Secrets:**
- `DOCKER_USERNAME` - Docker Hub username
- `DOCKER_PASSWORD` - Docker Hub password/token

GHCR uses the built-in `GITHUB_TOKEN` (no additional secrets needed).

### Manual Deployment

```bash
# Build and tag
docker build -t sailorman225/blueos-doris:latest .

# Push to Docker Hub
docker push sailorman225/blueos-doris:latest

# Push to GHCR
docker tag sailorman225/blueos-doris:latest ghcr.io/brianhBR/blueos-doris:latest
docker push ghcr.io/brianhBR/blueos-doris:latest
```

