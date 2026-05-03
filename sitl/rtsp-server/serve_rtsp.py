#!/usr/bin/env python3
"""Minimal gst-rtsp-server for SITL dev/CI: serves a H.264 test pattern.

Produces an RTSP stream at ``rtsp://0.0.0.0:8554/test`` that looks
enough like the real RadCam to exercise the DORIS extension's
``ip_camera_recorder`` pipeline end-to-end without a physical
camera.  Used by ``sitl/docker-compose.yml`` and anywhere else we
need a local RTSP source for development.

Key properties matched to the real camera:

* H.264 video (``x264enc`` with zerolatency tune);
* Fixed GOP (``key-int-max=30`` -> 1 keyframe / sec at 30 fps) so
  splitmuxsink's ``split-now`` rotations are bounded;
* ``config-interval=1`` on ``rtph264pay`` so SPS/PPS appear on the
  wire periodically (the recorder pipeline uses
  ``h264parse config-interval=-1`` to cope with the real camera that
  only signals parameter sets in the SDP; this test server signals
  them in-band too, which is a strict superset).

Env vars (all optional):
    SITL_RTSP_PORT      default 8554
    SITL_RTSP_MOUNT     default /test
    SITL_RTSP_PATTERN   default ball
    SITL_RTSP_BITRATE   default 1000 (kbps)
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer  # noqa: E402


def main() -> int:
    Gst.init(None)

    port = os.environ.get("SITL_RTSP_PORT", "8554")
    mount = os.environ.get("SITL_RTSP_MOUNT", "/test")
    pattern = os.environ.get("SITL_RTSP_PATTERN", "ball")
    bitrate = os.environ.get("SITL_RTSP_BITRATE", "1000")

    server = GstRtspServer.RTSPServer()
    server.set_service(port)

    mounts = server.get_mount_points()
    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(
        "( videotestsrc is-live=true pattern={pattern} "
        " ! video/x-raw,width=640,height=360,framerate=30/1 "
        " ! x264enc tune=zerolatency bitrate={bitrate} speed-preset=superfast "
        "   key-int-max=30 bframes=0 "
        " ! rtph264pay name=pay0 pt=96 config-interval=1 )".format(
            pattern=pattern, bitrate=bitrate,
        )
    )
    factory.set_shared(True)
    mounts.add_factory(mount, factory)

    if server.attach(None) == 0:
        print("error: could not attach RTSP server", file=sys.stderr)
        return 1

    print(
        f"RTSP test stream ready at rtsp://0.0.0.0:{port}{mount} "
        f"(pattern={pattern}, bitrate={bitrate}kbps)",
        flush=True,
    )
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
