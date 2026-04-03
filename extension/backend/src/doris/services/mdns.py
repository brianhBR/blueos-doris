"""DORIS mDNS and nginx setup.

On startup, configures:
  1. Avahi hostname → "doris" so doris.local resolves to the device IP
  2. nginx redirect so http://doris.local/ → http://doris.local:8095/
"""

import logging
import re
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

AVAHI_CONF = Path("/tmp/avahi/avahi-daemon.conf")
NGINX_CONF = Path("/tmp/nginx/doris-redirect.conf")

NGINX_REDIRECT_CONTENT = """\
if ($host = "doris.local") {
    rewrite ^/$ http://doris.local:8095/ redirect;
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
    """Set avahi hostname to 'doris'. Returns True if the file was changed."""
    if not AVAHI_CONF.is_file():
        logger.info("Avahi config not found at %s, skipping", AVAHI_CONF)
        return False

    content = AVAHI_CONF.read_text()
    if re.search(r"^host-name=doris\s*$", content, re.MULTILINE):
        logger.info("Avahi hostname already set to 'doris'")
        return False

    new_content = re.sub(r"^host-name=.*$", "host-name=doris", content, flags=re.MULTILINE)
    if new_content == content:
        logger.warning("Could not find host-name line in avahi config")
        return False

    AVAHI_CONF.write_text(new_content)
    logger.info("Avahi hostname changed to 'doris'")
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


async def setup_doris_local() -> None:
    """Configure doris.local mDNS and nginx redirect.

    Called once during DORIS backend startup.
    """
    avahi_changed = _setup_avahi_hostname()
    if avahi_changed:
        # stop/sleep/start instead of restart: BlueOS runs a second mDNS stack
        # (Beacon's zeroconf) on the same host. An immediate restart causes avahi's
        # hostname probe to collide with stale multicast state from zeroconf,
        # resulting in false conflicts (doris-2, doris-3, …). The 3s pause lets
        # the multicast group clear before avahi re-probes.
        ok = await _run_host_command(
            "sudo systemctl stop avahi-daemon && sleep 3 && sudo systemctl start avahi-daemon"
        )
        if ok:
            logger.info("avahi-daemon restarted")

    nginx_written = _setup_nginx_conf()

    symlink_cmd = f"docker exec blueos-core ln -sf {NGINX_SYMLINK_SRC} {NGINX_SYMLINK_DST}"
    reload_cmd = "docker exec blueos-core sh -c 'kill -HUP $(cat /var/run/nginx.pid)'"

    if nginx_written:
        await _run_host_command(symlink_cmd)
        await _run_host_command(reload_cmd)
        logger.info("nginx symlink created and config reloaded")
    else:
        await _run_host_command(symlink_cmd)
