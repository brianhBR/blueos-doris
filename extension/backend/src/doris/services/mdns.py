"""DORIS mDNS, DNS, and nginx setup.

On startup, configures:
  1. Avahi hostname → "doris" on ALL interfaces (wired + WiFi) so
     doris.local resolves via mDNS regardless of how the client is connected.
  2. dnsmasq address record so doris.local also resolves via standard DNS
     for hotspot clients (Windows blocks mDNS on "public" networks).
  3. nginx redirect so http://doris.local/ → http://doris.local:8095/
"""

import asyncio
import io
import logging
import os
import re
import tarfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

AVAHI_CONF = Path("/tmp/avahi/avahi-daemon.conf")

# create_ap assigns this gateway IP to the hotspot interface (wlan1).
HOTSPOT_GATEWAY = "192.168.43.1"

# Separate dnsmasq instance for standard DNS (port 53) on the hotspot.
# create_ap's own dnsmasq only listens on port 5353 (mDNS), so clients
# that query doris.local via normal DNS get no answer.
HOTSPOT_DNS_CONF = "/tmp/doris-hotspot-dns.conf"
HOTSPOT_DNS_PID = "/tmp/doris-hotspot-dns.pid"

# Redirect any request to doris.local to the extension UI on port 8095.
# BlueOS runs nginx with a custom config (/home/pi/tools/nginx/nginx.conf)
# that includes /home/pi/tools/nginx/extensions/*.conf INSIDE the main
# server block.  So the redirect must be an `if` directive, not a separate
# server block.
NGINX_REDIRECT_CONTENT = """\
if ($host = "doris.local") {
    return 302 http://doris.local:8095$request_uri;
}
"""

NGINX_CONF_DST = "/home/pi/tools/nginx/extensions/doris-redirect.conf"
NGINX_CONF_DIR = os.path.dirname(NGINX_CONF_DST)
NGINX_CONF_NAME = os.path.basename(NGINX_CONF_DST)
CORE_CONTAINER = "blueos-core"

NGINX_WATCHDOG_INTERVAL_S = 30


def _docker_base_url() -> str:
    """Return the Docker API base URL derived from the BlueOS address."""
    host = urlparse(blueos_services.base_url).hostname
    return f"http://{host}:2375"


async def _find_core_container_id(client: httpx.AsyncClient) -> str | None:
    """Return the short container ID of blueos-core, or None."""
    resp = await client.get(f"{_docker_base_url()}/containers/json")
    resp.raise_for_status()
    for c in resp.json():
        for name in c.get("Names", []):
            if CORE_CONTAINER in name:
                return c["Id"][:12]
    return None


async def _run_host_command(command: str) -> bool:
    """Execute a command on the host via the Commander API."""
    url = f"{blueos_services.commander}/v1.0/command/host"
    params = {"command": command, "i_know_what_i_am_doing": "true"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Commander command failed (%s): %s", command[:60], e)
        return False


def _setup_avahi_hostname() -> bool:
    """Set avahi hostname to 'doris' on all physical network interfaces.

    Two config changes:

    1. ``host-name=doris`` — advertise as doris.local
    2. ``deny-interfaces=lo,docker0`` — skip Docker bridges and loopback
       (they cause Avahi to respond with unreachable internal IPs)

    Note: ``disallow-other-stacks=yes`` was tried but breaks the WiFi
    hotspot (create_ap's dnsmasq also binds port 5353).

    Returns True if the file was changed.
    """
    if not AVAHI_CONF.is_file():
        logger.info("Avahi config not found at %s, skipping", AVAHI_CONF)
        return False

    content = AVAHI_CONF.read_text()
    new_content = content

    SETTINGS = {
        "host-name": "doris",
        "deny-interfaces": "lo,docker0",
    }

    for key, value in SETTINGS.items():
        target = f"{key}={value}"
        # Replace existing (commented or uncommented) line
        new_content, n = re.subn(
            rf"^#?{key}=.*$", target, new_content, flags=re.MULTILINE,
        )
        # If no existing line was found, insert after [server]
        if n == 0 and target not in new_content:
            new_content = new_content.replace(
                "[server]", f"[server]\n{target}", 1,
            )

    # Remove allow-interfaces (BlueOS defaults to eth0-only)
    new_content = re.sub(
        r"^allow-interfaces=.*\n?", "", new_content, flags=re.MULTILINE
    )

    # Remove disallow-other-stacks (breaks create_ap hotspot)
    new_content = re.sub(
        r"^#?disallow-other-stacks=.*\n?", "", new_content, flags=re.MULTILINE
    )

    if new_content == content:
        logger.info("Avahi config already up to date (hostname=doris, all interfaces)")
        return False

    AVAHI_CONF.write_text(new_content)
    logger.info("Avahi config updated: hostname=doris, interface restrictions removed")
    return True



async def start_hotspot_dns() -> None:
    """Start a DNS-only dnsmasq on port 53 for the hotspot interface.

    create_ap's own dnsmasq listens on port 5353 (mDNS), not 53, so
    standard DNS queries from hotspot clients go unanswered.  This
    starts a second, minimal dnsmasq that *only* serves DNS on port 53
    bound to the hotspot gateway IP, resolving ``doris.local`` (and
    ``blueos-wifi.local``) to that same gateway.

    Must be called AFTER configure_hotspot() so the hotspot interface
    actually has the gateway IP assigned.
    """
    conf_content = (
        f"listen-address={HOTSPOT_GATEWAY}\n"
        "port=53\n"
        "bind-interfaces\n"
        "no-dhcp-interface=wlan1\n"
        f"address=/doris.local/{HOTSPOT_GATEWAY}\n"
        f"address=/blueos-wifi.local/{HOTSPOT_GATEWAY}\n"
        "no-resolv\n"
        "no-hosts\n"
    )
    cmd = (
        f"echo '{conf_content}' > {HOTSPOT_DNS_CONF} && "
        f"pkill -f 'dnsmasq.*{HOTSPOT_DNS_CONF}' 2>/dev/null; sleep 1; "
        f"dnsmasq --conf-file={HOTSPOT_DNS_CONF} --pid-file={HOTSPOT_DNS_PID}"
    )
    ok = await _run_host_command(cmd)
    if ok:
        logger.info("Hotspot DNS started on %s:53 (doris.local)", HOTSPOT_GATEWAY)
    else:
        logger.warning("Failed to start hotspot DNS server")


DRIVER_INSTALL_SCRIPT = r"""
set +e
KVER=$(uname -r)
DRIVER_REPO="https://github.com/morrownr/88x2bu-20210702.git"
DRIVER_DIR="/opt/doris-drivers/88x2bu"
BLACKLIST="/etc/modprobe.d/blacklist-rtw88.conf"

if lsmod | grep -q 88x2bu; then exit 0; fi
if modprobe -n 88x2bu 2>/dev/null; then
    modprobe 88x2bu 2>/dev/null
    grep -q 'blacklist rtw88_8822bu' "$BLACKLIST" 2>/dev/null || {
        echo 'blacklist rtw88_8822bu' > "$BLACKLIST"
        echo 'blacklist rtw88_8822b' >> "$BLACKLIST"
        echo 'blacklist rtw88_usb' >> "$BLACKLIST"
    }
    exit 0
fi
command -v gcc >/dev/null && [ -d "/lib/modules/$KVER/build" ] || exit 0
rm -rf "$DRIVER_DIR" && mkdir -p /opt/doris-drivers
git clone --depth 1 "$DRIVER_REPO" "$DRIVER_DIR" 2>&1 | tail -2
cd "$DRIVER_DIR" && make -j4 KSRC="/lib/modules/$KVER/build" 2>&1 | tail -3
[ $? -eq 0 ] && make install KSRC="/lib/modules/$KVER/build" 2>&1 | tail -2
echo 'blacklist rtw88_8822bu' > "$BLACKLIST"
echo 'blacklist rtw88_8822b' >> "$BLACKLIST"
echo 'blacklist rtw88_usb' >> "$BLACKLIST"
if [ -d /sys/bus/usb/devices/1-1 ]; then
    rmmod rtw88_8822bu 2>/dev/null
    echo 0 > /sys/bus/usb/devices/1-1/authorized; sleep 2
    echo 1 > /sys/bus/usb/devices/1-1/authorized; sleep 4
fi
"""


async def ensure_wifi_driver() -> None:
    """Install the out-of-tree 88x2bu driver if not already loaded.

    The in-kernel ``rtw88_8822bu`` driver has a TX-queue-stall bug in AP
    mode that makes the hotspot unusable.  The community ``88x2bu``
    driver (morrownr fork) is stable.  This function checks if the
    driver is already loaded and, if not, builds and installs it via
    Commander.  The build is cached in ``/opt/doris-drivers/`` and only
    runs once per kernel version.
    """
    ok = await _run_host_command(DRIVER_INSTALL_SCRIPT)
    if ok:
        logger.info("WiFi driver check complete (88x2bu)")
    else:
        logger.warning("WiFi driver install check failed (non-fatal)")


async def restart_avahi() -> None:
    """Restart Avahi so it re-probes all interfaces.

    Should be called AFTER configure_hotspot() because the hotspot setup
    churns WiFi interfaces (wlan0/wlan1/uap0 leave and rejoin).  If Avahi
    is running during that churn it withdraws address records and may not
    recover.  A clean restart after the interfaces settle gives Avahi a
    stable view of the network.

    The 5-second pause between stop and start avoids hostname-probe
    collisions with BlueOS's Beacon (zeroconf) on the same host.
    """
    ok = await _run_host_command(
        "sudo systemctl stop avahi-daemon && sleep 5 && sudo systemctl start avahi-daemon"
    )
    if ok:
        logger.info("avahi-daemon restarted (post-hotspot)")
    else:
        logger.warning("Failed to restart avahi-daemon")


async def _nginx_redirect_exists() -> bool:
    """Return True if the redirect conf exists inside blueos-core."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            cid = await _find_core_container_id(client)
            if not cid:
                return False
            resp = await client.head(
                f"{_docker_base_url()}/containers/{cid}/archive",
                params={"path": NGINX_CONF_DST},
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _upload_nginx_redirect() -> bool:
    """Upload doris-redirect.conf into blueos-core and reload nginx.

    Returns True on success.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            cid = await _find_core_container_id(client)
            if not cid:
                raise RuntimeError(f"{CORE_CONTAINER} container not found")

            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w") as tar:
                data = NGINX_REDIRECT_CONTENT.encode()
                info = tarfile.TarInfo(name=NGINX_CONF_NAME)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            tar_buf.seek(0)

            resp = await client.put(
                f"{_docker_base_url()}/containers/{cid}/archive"
                f"?path={NGINX_CONF_DIR}",
                content=tar_buf.read(),
                headers={"Content-Type": "application/x-tar"},
            )
            resp.raise_for_status()

        await _run_host_command(
            f"docker exec {CORE_CONTAINER} nginx -s reload"
        )
        return True
    except Exception as exc:
        logger.debug("nginx redirect upload failed: %s", exc)
        return False


async def _ensure_nginx_redirect() -> None:
    """Upload the redirect conf with retries for early startup."""
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        if await _upload_nginx_redirect():
            logger.info(
                "nginx doris.local redirect active (attempt %d)", attempt
            )
            return
        delay = attempt * 3
        logger.info(
            "nginx redirect attempt %d/%d failed, retrying in %ds",
            attempt, max_attempts, delay,
        )
        await asyncio.sleep(delay)

    logger.warning(
        "Failed to install nginx redirect after %d attempts", max_attempts
    )


async def _nginx_redirect_watchdog() -> None:
    """Periodically verify the redirect conf exists; re-upload if missing.

    blueos-core's container filesystem is ephemeral.  If the container
    is recreated (BlueOS update, power cycle, manual restart) after the
    DORIS extension has already started, the conf disappears and
    doris.local falls through to the default BlueOS page.  This loop
    detects that and restores the redirect within ~30 seconds.
    """
    while True:
        await asyncio.sleep(NGINX_WATCHDOG_INTERVAL_S)
        try:
            if not await _nginx_redirect_exists():
                logger.info("nginx redirect conf missing, re-uploading")
                if await _upload_nginx_redirect():
                    logger.info("nginx doris.local redirect restored")
                else:
                    logger.warning("nginx redirect restore failed, will retry")
        except Exception as exc:
            logger.debug("nginx watchdog check error: %s", exc)


async def setup_doris_local() -> None:
    """Configure doris.local resolution (mDNS) and nginx redirect.

    Called once during DORIS backend startup.  Writes Avahi and nginx
    config files but does NOT restart Avahi — call ``restart_avahi()``
    separately after configure_hotspot() has finished.  The hotspot DNS
    server (port 53) is started by ``start_hotspot_dns()`` after the
    hotspot interface is up.

    Also starts a background watchdog that re-uploads the nginx conf
    if blueos-core is ever recreated while DORIS is running.
    """
    _setup_avahi_hostname()
    await _ensure_nginx_redirect()
    asyncio.get_event_loop().create_task(_nginx_redirect_watchdog())
