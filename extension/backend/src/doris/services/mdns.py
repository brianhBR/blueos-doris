"""DORIS mDNS, DNS, and nginx setup.

On startup, configures:
  1. Avahi hostname → "doris" on ALL interfaces (wired + WiFi) so
     doris.local resolves via mDNS regardless of how the client is connected.
  2. dnsmasq address record so doris.local also resolves via standard DNS
     for hotspot clients (Windows blocks mDNS on "public" networks).
  3. nginx redirect so http://doris.local/ → http://doris.local:8095/
"""

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

AVAHI_CONF = Path("/tmp/avahi/avahi-daemon.conf")

# Candidate names for the BlueOS AP/hotspot interface, in priority order.
# Older BlueOS used ``wlan1``/``wifi1``; the tony-wifi udev rule renames the
# USB Realtek to ``uap0`` so that BlueOS ``wifi-manager`` runs the AP on it
# instead of layering a virtual ``__ap`` on the onboard Broadcom. Discovering
# at runtime keeps this responder working across BlueOS upgrades that change
# either the iface name or the AP subnet (BlueOS 1.5.0-beta.36 moved from
# 192.168.43.0/24 to 192.168.42.0/24).
HOTSPOT_IFACE_CANDIDATES = ("uap0", "wlan1", "wifi1")

# Separate dnsmasq instance for standard DNS (port 53) on the hotspot.
# create_ap's own dnsmasq only listens on port 5353 (mDNS), so clients
# that query doris.local via normal DNS get no answer.
HOTSPOT_DNS_CONF = "/tmp/doris-hotspot-dns.conf"
HOTSPOT_DNS_PID = "/tmp/doris-hotspot-dns.pid"
HOTSPOT_DNS_WATCHDOG_INTERVAL_S = 30

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
CORE_CONTAINER = "blueos-core"

NGINX_WATCHDOG_INTERVAL_S = 30

_avahi_config_changed: bool = False


async def _commander_post(command: str, timeout: float = 30.0) -> dict | None:
    """POST a host command to the BlueOS Commander API.

    Returns the parsed JSON body on transport success (regardless of the
    inner ``return_code``), or None on transport failure.
    """
    url = f"{blueos_services.commander}/v1.0/command/host"
    params = {"command": command, "i_know_what_i_am_doing": "true"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Commander POST failed (%s): %s", command[:60], e)
        return None


async def _run_host_command(command: str, timeout: float = 30.0) -> bool:
    """Execute a command on the host via Commander. True iff rc == 0."""
    data = await _commander_post(command, timeout)
    if data is None:
        return False
    rc = data.get("return_code", -1)
    if rc != 0:
        out = data.get("stdout", "")
        err = data.get("stderr", "")
        logger.warning(
            "Host command returned %d: %s %s",
            rc, str(out)[:200], str(err)[:200],
        )
        return False
    return True


async def _run_host_command_capture(
    command: str, timeout: float = 30.0,
) -> tuple[bool, str]:
    """Execute a command on the host via Commander, returning (ok, stdout).

    Commander wraps stdout in a quoted string with literal ``\\n`` sequences;
    this normalises the result so callers can compare on plain text.
    """
    data = await _commander_post(command, timeout)
    if data is None:
        return False, ""
    rc = data.get("return_code", -1)
    out = str(data.get("stdout", "") or "")
    out = out.strip("'\"").replace("\\n", "\n").strip()
    return rc == 0, out


async def _discover_hotspot_iface_and_ip() -> tuple[str, str] | None:
    """Find the AP interface name and its IPv4 by probing the host.

    Tries ``HOTSPOT_IFACE_CANDIDATES`` in order; returns ``(iface, ip)``
    for the first one with an IPv4 assigned, or ``None`` if none is up.
    """
    for iface in HOTSPOT_IFACE_CANDIDATES:
        cmd = (
            f"ip -4 -o addr show dev {iface} 2>/dev/null"
            " | awk '{print $4}' | cut -d/ -f1 | head -1"
        )
        ok, ip = await _run_host_command_capture(cmd)
        if ok and ip:
            return iface, ip
    return None


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



def _expected_dns_conf(iface: str, gateway: str) -> str:
    """Render the dnsmasq config for the given AP iface and gateway IP."""
    return (
        f"listen-address={gateway}\n"
        "port=53\n"
        "bind-interfaces\n"
        f"no-dhcp-interface={iface}\n"
        f"address=/doris.local/{gateway}\n"
        f"address=/blueos-wifi.local/{gateway}\n"
        "no-resolv\n"
        "no-hosts\n"
    )


async def _hotspot_dns_already_running(expected_conf: str) -> bool:
    """True iff our dnsmasq is alive AND the on-disk conf matches.

    Identifies the daemon via its PID file (``HOTSPOT_DNS_PID``) rather
    than ``pgrep -f`` against the full command line, because the parent
    shell that runs this very check has the substring ``dnsmasq`` and the
    conf path in its own argv — a ``pgrep -f`` match would either give a
    false positive here, or (worse, for ``pkill -f``) self-terminate the
    spawn pipeline below and abort with exit 255 before dnsmasq launches.
    """
    cmd = (
        f"[ -f {HOTSPOT_DNS_PID} ] && "
        f"_pid=$(cat {HOTSPOT_DNS_PID} 2>/dev/null) && "
        f'[ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null && '
        f'tr "\\0" " " < /proc/$_pid/cmdline 2>/dev/null '
        f"| grep -q doris-hotspot-dns"
    )
    if not await _run_host_command(cmd):
        return False
    ok, on_disk = await _run_host_command_capture(
        f"sudo cat {HOTSPOT_DNS_CONF} 2>/dev/null"
    )
    if not ok:
        return False
    return on_disk.strip() == expected_conf.strip()


async def start_hotspot_dns() -> None:
    """Start a DNS-only dnsmasq on port 53 for the hotspot interface.

    ``create_ap``'s own dnsmasq listens on port 5353 (mDNS), not 53, so
    standard DNS queries from hotspot clients go unanswered. This starts a
    second, minimal dnsmasq that *only* serves DNS on port 53 bound to the
    hotspot gateway IP, resolving ``doris.local`` (and ``blueos-wifi.local``)
    to that same gateway.

    The AP interface name and gateway IP are discovered at runtime — both
    have moved across BlueOS versions (``wlan1`` -> ``uap0``, .43 -> .42)
    and would otherwise drift out of sync with the rest of the system.

    Idempotent: if a dnsmasq is already running with the expected conf,
    this is a no-op, so a periodic watchdog can call it cheaply.

    Uses ``sudo`` so that /usr/sbin is in the PATH (Commander's default
    shell PATH omits /usr/sbin where dnsmasq lives).
    """
    discovered = await _discover_hotspot_iface_and_ip()
    if discovered is None:
        logger.info(
            "Hotspot DNS: no AP interface up yet (tried %s); "
            "watchdog will retry",
            "/".join(HOTSPOT_IFACE_CANDIDATES),
        )
        return
    iface, gateway = discovered

    expected = _expected_dns_conf(iface, gateway)
    if await _hotspot_dns_already_running(expected):
        logger.debug(
            "Hotspot DNS already running on %s:53 (iface=%s)", gateway, iface,
        )
        return

    # Kill any prior instance via the PID file — *not* ``pkill -f`` against
    # the conf path, which would also match this very shell wrapper (its
    # argv contains both "dnsmasq" and the conf path) and self-terminate
    # before dnsmasq is spawned, surfacing as a confusing exit-255.
    cmd = (
        f"echo '{expected}' | sudo tee {HOTSPOT_DNS_CONF} > /dev/null && "
        f"if [ -f {HOTSPOT_DNS_PID} ]; then "
        f"  _pid=$(cat {HOTSPOT_DNS_PID} 2>/dev/null); "
        f'  [ -n "$_pid" ] && sudo kill "$_pid" 2>/dev/null; '
        f"  sleep 1; "
        f"fi; "
        f"sudo /usr/sbin/dnsmasq --conf-file={HOTSPOT_DNS_CONF} "
        f"--pid-file={HOTSPOT_DNS_PID}"
    )
    ok = await _run_host_command(cmd)
    if ok:
        logger.info(
            "Hotspot DNS started on %s:53 (iface=%s, doris.local)",
            gateway, iface,
        )
    else:
        logger.warning("Failed to start hotspot DNS server")


async def _hotspot_dns_watchdog() -> None:
    """Periodically re-assert the hotspot DNS responder.

    Calls ``start_hotspot_dns()`` (which is idempotent) every
    ``HOTSPOT_DNS_WATCHDOG_INTERVAL_S`` seconds. This catches:

    * Early-boot races where the AP interface didn't have an IP yet at
      first call.
    * BlueOS upgrades that change the AP subnet or interface name without
      restarting the DORIS extension.
    * The AP coming back after a dive (hotspot watchdog brings it up,
      we then re-bind dnsmasq to the now-present gateway IP).
    """
    while True:
        await asyncio.sleep(HOTSPOT_DNS_WATCHDOG_INTERVAL_S)
        try:
            await start_hotspot_dns()
        except Exception as exc:
            logger.debug("hotspot DNS watchdog tick error: %s", exc)


async def restart_avahi(force: bool = False) -> None:
    """Restart Avahi so it re-probes all interfaces.

    Should be called AFTER configure_hotspot() because the hotspot setup
    churns WiFi interfaces (wlan0/wlan1/uap0 leave and rejoin).  If Avahi
    is running during that churn it withdraws address records and may not
    recover.  A clean restart after the interfaces settle gives Avahi a
    stable view of the network.

    The 5-second pause between stop and start avoids hostname-probe
    collisions with BlueOS's Beacon (zeroconf) on the same host.

    When *force* is False (the default) and the Avahi config was not
    changed by ``_setup_avahi_hostname()``, the restart is skipped to
    avoid unnecessary mDNS downtime.
    """
    if not force and not _avahi_config_changed:
        logger.info("Avahi config unchanged, skipping restart")
        return

    ok = await _run_host_command(
        "sudo systemctl stop avahi-daemon && sleep 5 && sudo systemctl start avahi-daemon"
    )
    if ok:
        logger.info("avahi-daemon restarted (post-hotspot)")
    else:
        logger.warning("Failed to restart avahi-daemon")


async def _nginx_redirect_exists() -> bool:
    """Return True if doris-redirect.conf exists inside blueos-core.

    Uses ``docker exec`` over the Commander API. The new BlueOS no longer
    exposes the Docker daemon on TCP ``2375``; the Commander shell on the
    host can reach it through the unix socket.
    """
    return await _run_host_command(
        f"sudo docker exec {CORE_CONTAINER} test -f {NGINX_CONF_DST}"
    )


async def _upload_nginx_redirect() -> bool:
    """Write doris-redirect.conf into blueos-core and reload nginx.

    Streams the conf as base64 through ``docker exec`` via the Commander
    API, replacing the old direct-to-Docker-TCP archive PUT (Docker is no
    longer reachable on TCP under BlueOS 1.5.0-beta.x). Atomic: writes to
    a ``.tmp`` sibling and ``mv``s into place before reloading nginx.
    """
    encoded = base64.b64encode(NGINX_REDIRECT_CONTENT.encode("utf-8")).decode("ascii")
    inner = (
        f"mkdir -p {NGINX_CONF_DIR}"
        f" && echo {encoded} | base64 -d > {NGINX_CONF_DST}.tmp"
        f" && mv {NGINX_CONF_DST}.tmp {NGINX_CONF_DST}"
        " && nginx -s reload"
    )
    cmd = f"sudo docker exec {CORE_CONTAINER} sh -c '{inner}'"
    return await _run_host_command(cmd)


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

    Also starts two background watchdogs:
      * nginx redirect — re-uploads the conf if blueos-core is recreated.
      * hotspot DNS — re-asserts ``start_hotspot_dns()`` so a delayed AP
        bring-up or a BlueOS subnet/iface change is self-healed.
    """
    global _avahi_config_changed
    _avahi_config_changed = _setup_avahi_hostname()
    await _ensure_nginx_redirect()
    asyncio.get_event_loop().create_task(_nginx_redirect_watchdog())
    asyncio.get_event_loop().create_task(_hotspot_dns_watchdog())
