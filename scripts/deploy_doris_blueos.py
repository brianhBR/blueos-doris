#!/usr/bin/env python3
"""
Build the DORIS BlueOS extension on a Raspberry Pi (aarch64) and replace the
running extension container. Requires: paramiko, local extension/ tree.

Usage:
  python scripts/deploy_doris_blueos.py

Env overrides:
  DORIS_DEPLOY_HOST (default blueos-wifi.local)
  DORIS_DEPLOY_USER (default pi)
  DORIS_DEPLOY_PASSWORD (default raspberry)
  DORIS_ENABLE_DOCKER_TCP=1  — after deploy, open Docker API on 0.0.0.0:2375 (insecure; LAN only)
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import paramiko

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION = REPO_ROOT / "extension"

HOST = os.environ.get("DORIS_DEPLOY_HOST", "blueos-wifi.local")
USER = os.environ.get("DORIS_DEPLOY_USER", "pi")
PASSWORD = os.environ.get("DORIS_DEPLOY_PASSWORD", "raspberry")
ENABLE_DOCKER_TCP = os.environ.get("DORIS_ENABLE_DOCKER_TCP", "").strip().lower() in ("1", "true", "yes")
CONTAINER_NAME = "extension-sailorman225blueosdorismaster"
IMAGE_TAG = "bluerobotics/blueos-doris:dev"


def make_extension_tarball() -> Path:
    """Zip extension/ into a tar.gz under temp (excludes bulky artifacts)."""
    tmp = Path(tempfile.mkdtemp(prefix="doris_deploy_"))
    out = tmp / "extension.tgz"
    with tarfile.open(out, "w:gz") as tf:
        for path in EXTENSION.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(EXTENSION)
            parts = rel.parts
            if parts[0] in ("node_modules", "dist", ".venv", "__pycache__"):
                continue
            if any(p == "node_modules" or p == "__pycache__" for p in parts):
                continue
            arcname = Path("extension") / rel
            tf.add(path, arcname=arcname.as_posix(), recursive=False)
    return out


def sftp_put(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    sftp.put(str(local), remote)


def ssh_exec(client: paramiko.SSHClient, cmd: str, timeout: int = 3600) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    return rc, stdout.read().decode(), stderr.read().decode()


def main() -> int:
    if not EXTENSION.is_dir():
        print("extension/ not found next to repo root", file=sys.stderr)
        return 1

    print("Packaging extension tree...")
    tgz = make_extension_tarball()
    print(f"  archive: {tgz} ({tgz.stat().st_size // 1024} KiB)")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for attempt in range(1, 4):
        print(f"Connecting {USER}@{HOST} (attempt {attempt}/3)...")
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=90,
                           banner_timeout=60, auth_timeout=60)
            break
        except Exception as e:
            print(f"  SSH connect failed: {e}")
            if attempt == 3:
                print("Giving up after 3 attempts.", file=sys.stderr)
                return 1
            time.sleep(5)

    remote_tgz = "/tmp/doris_extension_deploy.tgz"
    print(f"Uploading to {remote_tgz}")
    sftp = client.open_sftp()
    try:
        sftp_put(sftp, tgz, remote_tgz)
    finally:
        sftp.close()

    shutil.rmtree(tgz.parent, ignore_errors=True)

    script = f"""
set -e
rm -rf /tmp/doris_extension_build
mkdir -p /tmp/doris_extension_build
tar xzf {remote_tgz} -C /tmp/doris_extension_build
cd /tmp/doris_extension_build/extension
echo "=== docker build (this may take several minutes) ==="
sed -i 's/FROM --platform=$BUILDPLATFORM node:20-slim/FROM node:20-slim/' Dockerfile
echo $(date +%s) > frontend/.build_timestamp
sudo -H docker build -t {IMAGE_TAG} .
rm -f {remote_tgz}
"""
    print("Building image on device...")
    rc, out, err = ssh_exec(client, script, timeout=3600)
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if rc != 0:
        print(f"docker build failed (exit {rc})", file=sys.stderr)
        client.close()
        return rc

    print("Checking WiFi driver (88x2bu for Realtek RTL8822BU)...")
    driver_script = r"""
set +e
KVER=$(uname -r)
DRIVER_REPO="https://github.com/morrownr/88x2bu-20210702.git"
DRIVER_DIR="/opt/doris-drivers/88x2bu"
BLACKLIST="/etc/modprobe.d/blacklist-rtw88.conf"

# Skip if 88x2bu is already loaded
if lsmod | grep -q 88x2bu; then
    echo "88x2bu driver already loaded, skipping."
    exit 0
fi

# Skip if the module is already installed for this kernel
if sudo modprobe -n 88x2bu 2>/dev/null; then
    echo "88x2bu module available for kernel $KVER, loading..."
    sudo modprobe 88x2bu 2>/dev/null
    # Blacklist the in-kernel driver
    grep -q 'blacklist rtw88_8822bu' "$BLACKLIST" 2>/dev/null || {
        echo 'blacklist rtw88_8822bu' | sudo tee "$BLACKLIST" > /dev/null
        echo 'blacklist rtw88_8822b'  | sudo tee -a "$BLACKLIST" > /dev/null
        echo 'blacklist rtw88_usb'    | sudo tee -a "$BLACKLIST" > /dev/null
    }
    echo "88x2bu loaded from installed module."
    exit 0
fi

# Check build tools
if ! command -v gcc >/dev/null || ! command -v make >/dev/null; then
    echo "WARN: gcc/make not found, cannot build WiFi driver."
    exit 0
fi
if [ ! -d "/lib/modules/$KVER/build" ]; then
    echo "WARN: Kernel headers not found for $KVER, cannot build WiFi driver."
    exit 0
fi

echo "Building 88x2bu driver for kernel $KVER..."
sudo rm -rf "$DRIVER_DIR"
sudo mkdir -p /opt/doris-drivers
sudo git clone --depth 1 "$DRIVER_REPO" "$DRIVER_DIR" 2>&1 | tail -3
cd "$DRIVER_DIR"
sudo make -j4 KSRC="/lib/modules/$KVER/build" 2>&1 | tail -5
if [ $? -ne 0 ]; then
    echo "WARN: 88x2bu build failed."
    exit 0
fi
sudo make install KSRC="/lib/modules/$KVER/build" 2>&1 | tail -3

# Blacklist the in-kernel rtw88 driver
echo 'blacklist rtw88_8822bu' | sudo tee "$BLACKLIST" > /dev/null
echo 'blacklist rtw88_8822b'  | sudo tee -a "$BLACKLIST" > /dev/null
echo 'blacklist rtw88_usb'    | sudo tee -a "$BLACKLIST" > /dev/null

# Try to swap drivers now (if adapter is present)
if [ -d /sys/bus/usb/devices/1-1 ]; then
    sudo rmmod rtw88_8822bu 2>/dev/null
    echo 0 | sudo tee /sys/bus/usb/devices/1-1/authorized > /dev/null
    sleep 2
    echo 1 | sudo tee /sys/bus/usb/devices/1-1/authorized > /dev/null
    sleep 4
    if lsmod | grep -q 88x2bu; then
        echo "88x2bu driver installed and loaded successfully."
    else
        echo "88x2bu installed but not yet loaded (will activate on next reboot)."
    fi
else
    echo "88x2bu installed (USB adapter not detected, will load on next boot)."
fi
"""
    rc, out, err = ssh_exec(client, driver_script, timeout=600)
    print(out)
    if err.strip():
        for line in err.strip().split("\n"):
            if "warning" not in line.lower():
                print(line, file=sys.stderr)

    print("Replacing container...")
    stop_script = f"""
set +e
# Free host TCP 8095 if another container (e.g. Kraken-managed extension) still holds it.
for id in $(sudo docker ps -aq); do
  if sudo docker inspect "$id" --format '{{{{json .HostConfig.PortBindings}}}}' 2>/dev/null | grep -q '"8095/tcp"'; then
    echo "Removing $id (published 8095/tcp)"
    sudo docker update --restart=no "$id" 2>/dev/null
    sudo docker rm -f "$id" 2>/dev/null
  fi
done
# BlueOS often sets --restart on extension containers; without docker update
# --restart=no, the daemon can resurrect the old container between rm and run,
# causing "Conflict. The container name is already in use".
NM='{CONTAINER_NAME}'
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  ids=$(sudo docker ps -aq -f "name=$NM")
  if [ -z "$ids" ]; then
    break
  fi
  for id in $ids; do
    sudo docker update --restart=no "$id" 2>/dev/null
    sudo docker rm -f "$id" 2>/dev/null
  done
  sleep 1
done
RUN_RC=1
for _ in 1 2 3 4 5; do
  sudo docker run -d \\
  --restart unless-stopped \\
  --name "$NM" \\
  -p 8095:8095 \\
  --privileged \\
  -v /dev:/dev:rw \\
  -v /root/.config/blueos/ardupilot-manager/firmware:/tmp/storage/firmware \\
  -v /usr/blueos/extensions/doris/configurations:/tmp/storage/configurations \\
  -v /usr/blueos/extensions/doris/notifications:/tmp/storage/notifications \\
  -v /usr/blueos/extensions/doris/dives:/tmp/storage/dives \\
  -v /usr/blueos/userdata/recorder:/tmp/storage/recorder \\
  -v /usr/blueos/extensions/doris/nginx:/tmp/nginx \\
  -v /etc/avahi:/tmp/avahi \\
  --add-host=host.docker.internal:host-gateway \\
  -e MAV_SYSTEM_ID=1 \\
  -e DORIS_BLUEOS_ADDRESS=http://host.docker.internal \\
  {IMAGE_TAG}
  RUN_RC=$?
  if [ "$RUN_RC" -eq 0 ]; then
    break
  fi
  echo "docker run failed ($RUN_RC), clearing name and retrying..."
  for id in $(sudo docker ps -aq -f "name=$NM"); do
    sudo docker update --restart=no "$id" 2>/dev/null
    sudo docker rm -f "$id" 2>/dev/null
  done
  sleep 2
done
exit $RUN_RC
"""
    rc, out, err = ssh_exec(client, stop_script, timeout=240)
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if rc != 0:
        print(f"docker run failed (exit {rc})", file=sys.stderr)
        client.close()
        return rc

    restart_ap = r"""
set +e
for i in $(seq 1 18); do
  code=$(curl -sS -m 12 -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/v1.0/restart 2>/dev/null)
  if [ "$code" = "200" ] || [ "$code" = "204" ]; then echo "autopilot restart ok ($code)"; exit 0; fi
  sleep 5
done
docker exec blueos-core curl -sS -m 90 -X POST http://127.0.0.1:8000/v1.0/restart && exit 0
exit 1
"""

    print("Restarting autopilot (ArduPilot Manager)...")
    rc, out, err = ssh_exec(client, restart_ap, timeout=180)
    print(out or err)

    if ENABLE_DOCKER_TCP:
        print("Enabling Docker TCP (0.0.0.0:2375)...")
        tcp_script = r"""
set -e
if systemctl show -p ExecStart docker 2>/dev/null | grep -q 'tcp://0.0.0.0:2375'; then
  echo "Docker TCP already configured."
  exit 0
fi
DOCKERD=$(command -v dockerd)
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/doris-docker-tcp.conf > /dev/null <<'UNIT'
[Service]
ExecStart=
ExecStart=__DOCKERD__ -H fd:// -H tcp://0.0.0.0:2375 --containerd=/run/containerd/containerd.sock
UNIT
sudo sed -i "s|__DOCKERD__|$DOCKERD|g" /etc/systemd/system/docker.service.d/doris-docker-tcp.conf
sudo systemctl daemon-reload
sudo systemctl restart docker
echo "Docker listening on tcp://0.0.0.0:2375 (no TLS — restrict with firewall if needed)."
"""
        rc, out, err = ssh_exec(client, tcp_script, timeout=180)
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
        if rc != 0:
            print(f"Docker TCP setup failed (exit {rc}); fix docker.service.d manually.", file=sys.stderr)
        print("Waiting for Docker stack after TCP enable...")
        time.sleep(25)
        print("Restarting autopilot again after Docker restart...")
        rc, out, err = ssh_exec(client, restart_ap, timeout=180)
        print(out or err)

    print("Waiting for DORIS health...")
    time.sleep(8)
    rc, out, err = ssh_exec(
        client,
        "curl -sS -m 20 http://127.0.0.1:8095/api/v1/health || true",
        timeout=60,
    )
    print(out or err)

    client.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
