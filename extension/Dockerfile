# DORIS - Deep Ocean Research and Imaging System
# BlueOS Extension Dockerfile

# Stage 1: Build frontend (on native platform - output is platform-independent)
FROM --platform=$BUILDPLATFORM node:20-slim AS frontend-builder

WORKDIR /frontend

# Install dependencies (increased timeout for slow ARM emulation)
COPY frontend/package.json frontend/yarn.lock ./
RUN yarn config set network-timeout 600000 && yarn install

# Copy source and build
COPY frontend/ ./
RUN yarn build

# Stage 2: Python backend with frontend
#
# Pin to Debian bookworm (system python = 3.11) so the apt-installed
# python3-gi (built for 3.11) is ABI-compatible with the docker-library
# /usr/local/bin/python that this image ships.  python:3.11-slim
# without a suite suffix now resolves to trixie (system python = 3.13),
# which would mismatch the app's interpreter and force a from-source
# pygobject build under emulation -- the latter deterministically
# segfaults gcc-14 in QEMU on linux/arm64 and linux/arm/v7.
FROM python:3.11-slim-bookworm

WORKDIR /app

# Runtime deps only:
#   * ffmpeg          -- snapshot capture + post-dive concat/segment
#   * gstreamer1.0-*  -- IP camera RTSP recording pipeline
#   * python3-gi      -- PyGObject for in-process Gst control
#                        (apt-shipped binary; avoids a from-source
#                         build that segfaults gcc-14 in QEMU on
#                         the emulated arm legs of buildx).
#   * gir1.2-*        -- GObject Introspection typelibs the recorder
#                        loads via gi.require_version("Gst", "1.0").
#
# ffmpeg's RTSP demuxer is incompatible with the RadCam camera
# (camera tears down the session right after the first IDR burst,
# leaving truncated stub files); GStreamer's rtspsrc keeps the
# session running and is auto-restarted on disconnect by the
# recorder watchdog.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    python3-gi \
    gir1.2-glib-2.0 \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    && rm -rf /var/lib/apt/lists/*

# Make the apt-installed python3-gi (under /usr/lib/python3/dist-packages,
# built against Debian's python3.11) importable from /usr/local/bin/python
# (the docker-library python3.11.x).  Both interpreters are CPython 3.11,
# so the cpython-311 ABI tag matches and the .so loads cleanly.
ENV PYTHONPATH=/usr/lib/python3/dist-packages

# Copy ONLY dependency files first (for better layer caching)
COPY backend/pyproject.toml backend/README.md ./

# Create minimal src structure for editable install
RUN mkdir -p src/doris && echo '__version__ = "0.1.0"' > src/doris/__init__.py

# Install Python dependencies (this layer is cached unless pyproject.toml changes).
# All wheels here have prebuilt manylinux2014 / piwheels artifacts for
# arm64 + armv7, so no compiler is needed at install time.
RUN python -m pip install --extra-index-url https://www.piwheels.org/simple -e . \
    && rm -rf /root/.cache/pip

# NOW copy the actual source code (changes here don't invalidate dependency cache)
COPY backend/src ./src
COPY backend/scripts ./scripts
COPY backend/frames ./frames

# Copy built frontend to be served by backend
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create data directory for persistent storage
RUN mkdir -p /data/logs

# Entrypoint script: fix recorder path before starting the app
COPY backend/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

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

ARG REPO=blueos-doris
ARG OWNER=bluerobotics
LABEL readme='https://raw.githubusercontent.com/brianhBR/blueos-doris/{tag}/extension/README.md'
LABEL links='{\
    "website": "https://bluerobotics.com",\
    "source": "https://github.com/brianhBR/blueos-doris"\
}'

LABEL requirements="core >= 1.1"

# Environment: BlueOS services are accessed via host.docker.internal
ENV DORIS_BLUEOS_ADDRESS=http://host.docker.internal

# Run the backend server (entrypoint fixes recorder path first)
ENTRYPOINT ["/app/entrypoint.sh"]
