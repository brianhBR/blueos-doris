# Minimal RTSP test server (gst-rtsp-server) for SITL / dev / CI.
#
# Serves a videotestsrc-backed H.264 stream on rtsp://:8554/test so
# the DORIS extension's in-process ip_camera_recorder pipeline can
# be validated end-to-end (start, split-now rotation, EOS shutdown)
# without a physical RadCam in the loop.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-gi \
        python3-gst-1.0 \
        gir1.2-gstreamer-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        gir1.2-gst-rtsp-server-1.0 \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        gstreamer1.0-x \
        gstreamer1.0-rtsp \
    && rm -rf /var/lib/apt/lists/*

COPY serve_rtsp.py /usr/local/bin/serve_rtsp.py
RUN chmod +x /usr/local/bin/serve_rtsp.py

EXPOSE 8554/tcp

CMD ["python3", "/usr/local/bin/serve_rtsp.py"]
