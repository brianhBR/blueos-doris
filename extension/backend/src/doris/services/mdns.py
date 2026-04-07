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

# NetworkManager reads this directory when spawning dnsmasq for shared (hotspot)
# connections — if the directory exists.
DNSMASQ_SHARED_DIR = "/etc/NetworkManager/dnsmasq-shared.d"
DNSMASQ_CONF = f"{DNSMASQ_SHARED_DIR}/doris-local.conf"
HOTSPOT_GATEWAY = "10.42.0.1"

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

    Three config changes:

    1. ``host-name=doris`` — advertise as doris.local
    2. ``deny-interfaces=lo,docker0`` — skip Docker bridges and loopback
       (they cause Avahi to respond with unreachable internal IPs)
    3. ``disallow-other-stacks=yes`` — take exclusive ownership of port 5353.
       BlueOS's Beacon service runs a second mDNS stack (Python zeroconf)
       on the same host.  Without this flag Avahi prints "Detected another
       IPv4 mDNS stack … this makes mDNS unreliable" and Windows clients
       fail to resolve doris.local.

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
        "disallow-other-stacks": "yes",
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


async def _setup_hotspot_dns() -> None:
    """Add a dnsmasq address record so doris.local resolves via standard DNS.

    mDNS is unreliable on WiFi — Windows blocks it on networks classified as
    "public" (the default for a new hotspot).  Since the device IS the DNS
    server for hotspot clients, we write a dnsmasq config that makes
    ``doris.local`` resolve to the hotspot gateway IP through normal DNS.

    The config file persists on the host, so it only needs to be written once
    and is picked up by NM's dnsmasq whenever the hotspot (re)starts.
    """
    cmd = (
        f"mkdir -p {DNSMASQ_SHARED_DIR} && "
        f"echo 'address=/doris.local/{HOTSPOT_GATEWAY}' > {DNSMASQ_CONF}"
    )
    ok = await _run_host_command(cmd)
    if ok:
        logger.info("dnsmasq hotspot DNS configured: doris.local -> %s", HOTSPOT_GATEWAY)
    else:
        logger.warning("Failed to write dnsmasq hotspot DNS config")


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
    """Configure doris.local resolution (mDNS + DNS) and nginx redirect.

    Called once during DORIS backend startup.  Writes all config files but
    does NOT restart Avahi — call ``restart_avahi()`` separately after
    configure_hotspot() has finished and WiFi interfaces have settled.
    """
    _setup_avahi_hostname()

    await _setup_hotspot_dns()

    nginx_written = _setup_nginx_conf()

    symlink_cmd = f"docker exec blueos-core ln -sf {NGINX_SYMLINK_SRC} {NGINX_SYMLINK_DST}"
    reload_cmd = "docker exec blueos-core sh -c 'kill -HUP $(cat /var/run/nginx.pid)'"

    if nginx_written:
        await _run_host_command(symlink_cmd)
        await _run_host_command(reload_cmd)
        logger.info("nginx symlink created and config reloaded")
    else:
        await _run_host_command(symlink_cmd)
