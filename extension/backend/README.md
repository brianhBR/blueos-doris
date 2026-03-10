# DORIS Backend

Python backend for the DORIS (Deep Ocean Research and Imaging System) BlueOS extension.

## Features

- **System Monitoring**: Battery, storage, and GPS location tracking
- **Network Management**: WiFi scanning, connection, and configuration
- **Sensor Integration**: Module status, sensor readings, and configuration
- **Mission Control**: Create, start, stop, and manage data collection missions
- **Media Management**: Browse, download, and sync captured media files

## Tech Stack

- **Framework**: [Robyn](https://robyn.tech/) - High-performance Python web framework with Rust runtime
- **Package Manager**: [uv](https://docs.astral.sh/uv/) - Fast Python package installer
- **Data Validation**: [Pydantic](https://docs.pydantic.dev/) - Data validation using Python type annotations
- **HTTP Client**: [httpx](https://www.python-httpx.org/) - Async HTTP client for BlueOS API communication

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Install dependencies
cd backend
uv sync

# Install with dev dependencies
uv sync --all-extras
```

## Running

### Development

```bash
# Run with uv
uv run python -m doris.main

# Or activate venv and run directly
source .venv/bin/activate
python -m doris.main
```

### Using the CLI entry point

```bash
uv run doris
```

The server will start at `http://0.0.0.0:8090` by default.

## Configuration

Configuration is done via environment variables with the `DORIS_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `DORIS_HOST` | `0.0.0.0` | Server bind address |
| `DORIS_PORT` | `8090` | Server port |
| `DORIS_DEBUG` | `false` | Enable debug mode |
| `DORIS_BLUEOS_ADDRESS` | `http://localhost` | BlueOS base URL |

You can also create a `.env` file in the backend directory.

## API Endpoints

### System
- `GET /api/v1/system/status` - Complete system status
- `GET /api/v1/system/battery` - Battery information
- `GET /api/v1/system/storage` - Storage information
- `GET /api/v1/system/location` - GPS location
- `GET /api/v1/health` - Health check

### Network
- `GET /api/v1/network` - Network info and available networks
- `GET /api/v1/network/status` - Connection status
- `GET /api/v1/network/scan` - Scan for WiFi networks
- `POST /api/v1/network/connect` - Connect to a network
- `POST /api/v1/network/disconnect` - Disconnect from network
- `DELETE /api/v1/network/saved/:ssid` - Forget saved network

### Sensors
- `GET /api/v1/sensors/modules` - List connected modules
- `GET /api/v1/sensors/:id/readings` - Get sensor readings
- `PUT /api/v1/sensors/:id/config` - Configure sensor

### Missions
- `GET /api/v1/missions` - List all missions
- `GET /api/v1/missions/:id` - Get mission details
- `POST /api/v1/missions` - Create new mission
- `POST /api/v1/missions/:id/start` - Start mission
- `POST /api/v1/missions/:id/stop` - Stop mission
- `DELETE /api/v1/missions/:id` - Delete mission

### Media
- `GET /api/v1/media/files` - List media files
- `GET /api/v1/media/missions` - List missions with media
- `GET /api/v1/media/sync/status` - Cloud sync status
- `POST /api/v1/media/sync/start` - Start cloud sync
- `DELETE /api/v1/media/files/:id` - Delete media file

## Project Structure

```
backend/
├── pyproject.toml          # Project configuration
├── README.md               # This file
├── src/
│   └── doris/
│       ├── __init__.py
│       ├── main.py         # Application entry point
│       ├── config.py       # Configuration settings
│       ├── models/         # Pydantic data models
│       │   ├── system.py
│       │   ├── network.py
│       │   ├── sensors.py
│       │   ├── missions.py
│       │   └── media.py
│       ├── services/       # BlueOS API clients
│       │   ├── base.py
│       │   ├── system.py
│       │   ├── network.py
│       │   ├── sensors.py
│       │   ├── camera.py
│       │   └── storage.py
│       └── routes/         # API route handlers
│           ├── system.py
│           ├── network.py
│           ├── sensors.py
│           ├── missions.py
│           └── media.py
└── tests/                  # Test files
    └── test_models.py
```

## BlueOS Integration

The backend communicates with various BlueOS services:

| Service | Port | Description |
|---------|------|-------------|
| WiFi Manager | 9000 | Network management |
| Camera Manager | 6020 | Camera control |
| MAVLink2Rest | 6040 | Vehicle telemetry |
| Ping Service | 9110 | Sonar sensors |
| File Browser | 7777 | File management |
| Helper | 81 | System utilities |
| linux2rest | 6030 | System info |

## Testing

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=doris
```

## License

MIT License

