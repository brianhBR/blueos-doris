# DORIS - Deep Ocean Research and Imaging System
# BlueOS Extension Dockerfile

# Stage 1: Build frontend (on native platform - output is platform-independent)
FROM --platform=$BUILDPLATFORM node:20-slim AS frontend-builder

WORKDIR /frontend

# Install dependencies (increased timeout for slow ARM emulation)
COPY frontend/package.json frontend/yarn.lock ./
RUN yarn config set network-timeout 600000 && yarn install --frozen-lockfile

# Copy source and build
COPY frontend/ ./
RUN yarn build

# Stage 2: Python backend with frontend
FROM python:3.11-slim

WORKDIR /app

# Install build dependencies for uvloop (required by Robyn on Linux)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy ONLY dependency files first (for better layer caching)
COPY backend/pyproject.toml backend/README.md ./

# Create minimal src structure for editable install
RUN mkdir -p src/doris && echo '__version__ = "0.1.0"' > src/doris/__init__.py

# Install Python dependencies (this layer is cached unless pyproject.toml changes)
RUN python -m pip install --extra-index-url https://www.piwheels.org/simple -e . \
    && apt-get purge -y build-essential python3-dev \
    && apt-get autoremove -y \
    && rm -rf /root/.cache/pip

# NOW copy the actual source code (changes here don't invalidate dependency cache)
COPY backend/src ./src
COPY backend/scripts ./scripts
COPY backend/frames ./frames

# Copy WiFi driver (out-of-tree 88x2bu for USB WiFi adapter)
COPY driver/ ./driver/

# Copy built frontend to be served by backend
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create data directory for persistent storage
RUN mkdir -p /data/logs

# Expose the API port
EXPOSE 8095/tcp

# BlueOS Extension Labels
LABEL version="1.0.0"

ARG IMAGE_NAME=doris

LABEL permissions='\
{\
  "ExposedPorts": {\
    "8095/tcp": {}\
  },\
  "HostConfig": {\
    "Privileged": true,\
    "Binds": [\
      "/dev:/dev:rw",\
      "/root/.config/blueos/ardupilot-manager/firmware:/tmp/storage/firmware",\
      "/usr/blueos/extensions/doris/configurations:/tmp/storage/configurations",\
      "/usr/blueos/extensions/doris/notifications:/tmp/storage/notifications",\
      "/usr/blueos/extensions/doris/dives:/tmp/storage/dives",\
      "/usr/blueos/userdata:/tmp/storage/userdata",\
      "/usr/blueos/extensions/doris/nginx:/tmp/nginx",\
      "/etc/avahi:/tmp/avahi",\
      "/sys:/sys:rw"\
    ],\
    "ExtraHosts": [\
      "host.docker.internal:host-gateway"\
    ],\
    "PortBindings": {\
      "8095/tcp": [\
        {\
          "HostPort": "8095"\
        }\
      ]\
    }\
  }\
}'

ARG AUTHOR="Patrick José Pereira"
ARG AUTHOR_EMAIL="patrickelectric@gmail.com"
LABEL authors='[\
    {\
        "name": "Patrick José Pereira",\
        "email": "patrickelectric@gmail.com"\
    }\
]'

LABEL company='{\
    "about": "Deep Ocean Research and Imaging System",\
    "name": "Blue Robotics",\
    "email": "support@bluerobotics.com"\
}'

LABEL type="device-integration"

ARG REPO=blueos-doris-extension
ARG OWNER=bluerobotics
LABEL readme='https://raw.githubusercontent.com/bluerobotics/blueos-doris-extension/{tag}/extension/README.md'
LABEL links='{\
    "website": "https://bluerobotics.com",\
    "source": "https://github.com/bluerobotics/blueos-doris-extension"\
}'

LABEL requirements="core >= 1.1"

# Environment: BlueOS services are accessed via host.docker.internal
ENV DORIS_BLUEOS_ADDRESS=http://host.docker.internal

# Run the backend server
ENTRYPOINT ["python", "-m", "doris.main"]
