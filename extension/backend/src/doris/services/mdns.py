"""DORIS mDNS, DNS, and nginx setup.

On startup, configures:
  1. Avahi hostname → "doris" on ALL interfaces (wired + WiFi) so
     doris.local resolves via mDNS regardless of how the client is connected.
  2. dnsmasq address record so doris.local also resolves via standard DNS
     for hotspot clients (Windows blocks mDNS on "public" networks).
  3. nginx redirect so http://doris.local/ → http://doris.local:8095/
"""

import logging
import re
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

AVAHI_CONF = Path("/tmp/avahi/avahi-daemon.conf")
NGINX_CONF = Path("/tmp/nginx/doris-redirect.conf")

# create_ap assigns this gateway IP to the hotspot interface (wlan1).
HOTSPOT_GATEWAY = "192.168.43.1"

# Separate dnsmasq instance for standard DNS (port 53) on the hotspot.
# create_ap's own dnsmasq only listens on port 5353 (mDNS), so clients
# that query doris.local via normal DNS get no answer.
HOTSPOT_DNS_CONF = "/tmp/doris-hotspot-dns.conf"
HOTSPOT_DNS_PID = "/tmp/doris-hotspot-dns.pid"

# Redirect any request to doris.local to the extension UI on port 8095.
# Uses `return 302` (not `rewrite`) because BlueOS's nginx has multiple
# `location /` blocks that interfere with server-level rewrites.
NGINX_REDIRECT_CONTENT = """\
if ($host = "doris.local") {
    return 302 http://$host:8095$request_uri;
}
"""

NGINX_SYMLINK_SRC = "/usr/blueos/extensions/doris/nginx/doris-redirect.conf"
NGINX_SYMLINK_DST = "/home/pi/tools/nginx/extensions/doris-redirect.conf"


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


def _setup_nginx_conf() -> bool:
    """Write the nginx redirect conf. Returns True if the file was written/changed."""
    NGINX_CONF.parent.mkdir(parents=True, exist_ok=True)

    if NGINX_CONF.is_file() and NGINX_CONF.read_text() == NGINX_REDIRECT_CONTENT:
        logger.info("nginx redirect conf already up to date")
        return False

    NGINX_CONF.write_text(NGINX_REDIRECT_CONTENT)
    logger.info("Wrote nginx redirect conf to %s", NGINX_CONF)
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


async def setup_doris_local() -> None:
    """Configure doris.local resolution (mDNS) and nginx redirect.

    Called once during DORIS backend startup.  Writes Avahi and nginx
    config files but does NOT restart Avahi — call ``restart_avahi()``
    separately after configure_hotspot() has finished.  The hotspot DNS
    server (port 53) is started by ``start_hotspot_dns()`` after the
    hotspot interface is up.
    """
    _setup_avahi_hostname()

    nginx_written = _setup_nginx_conf()

    symlink_cmd = f"docker exec blueos-core ln -sf {NGINX_SYMLINK_SRC} {NGINX_SYMLINK_DST}"
    reload_cmd = "docker exec blueos-core sh -c 'kill -HUP $(cat /var/run/nginx.pid)'"

    if nginx_written:
        await _run_host_command(symlink_cmd)
        await _run_host_command(reload_cmd)
        logger.info("nginx symlink created and config reloaded")
    else:
        await _run_host_command(symlink_cmd)
