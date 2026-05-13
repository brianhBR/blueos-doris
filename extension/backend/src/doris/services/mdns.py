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

# The host-side AP interface. ``hotspot_radio`` renames the Realtek
# USB radio from ``wlan1`` to ``uap0`` via udev, then ``create_ap``
# brings the AP up on ``uap0`` and assigns it the gateway IP.
HOTSPOT_INTERFACE = "uap0"

# Fallback gateway IP if the live detection from ``ip addr show
# uap0`` fails. This is the value BlueOS 1.5.x assigns; older 1.4.x
# builds used 192.168.43.1. ``_detect_hotspot_gateway()`` reads it
# live at call time so a future BlueOS renumber doesn't silently
# break our DNS setup again - we only fall back to this constant
# when the live read returns nothing (e.g. AP hasn't come up yet).
HOTSPOT_GATEWAY_FALLBACK = "192.168.42.1"

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

_avahi_config_changed: bool = False


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


async def _run_host_command(
    command: str, timeout: float = 30.0
) -> tuple[bool, str]:
    """Execute a command on the host via the Commander API.

    Commander always returns HTTP 200; the actual exit code is in the
    JSON body's ``return_code`` field. Returns ``(ok, stdout)`` so
    callers that need the output (e.g. ``_detect_hotspot_gateway``
    parsing ``ip addr show uap0``) don't need a second helper.
    """
    url = f"{blueos_services.commander}/v1.0/command/host"
    params = {"command": command, "i_know_what_i_am_doing": "true"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rc = data.get("return_code", -1)
            out = data.get("stdout", "") or ""
            err = data.get("stderr", "") or ""
            if rc != 0:
                logger.warning(
                    "Host command returned %d: %s %s",
                    rc, out[:200], err[:200],
                )
                return False, out
            return True, out
    except Exception as e:
        logger.warning("Commander command failed (%s): %s", command[:60], e)
        return False, ""


_IP_ADDR_RE = re.compile(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3})")


async def _detect_hotspot_gateway() -> str:
    """Return the IPv4 currently bound to :data:`HOTSPOT_INTERFACE`.

    BlueOS has rotated the create_ap gateway between versions
    (192.168.43.1 in older builds, 192.168.42.1 since 1.5.x). Reading
    it live is more durable than tracking the upstream change in a
    constant. Falls back to :data:`HOTSPOT_GATEWAY_FALLBACK` when the
    detection fails - typically because the AP hasn't finished coming
    up at the moment this runs - so DNS still has *an* IP to use even
    if it might be slightly off (the watchdog at the next tick will
    overwrite with the right value once the AP is up).
    """
    ok, out = await _run_host_command(
        f"ip -4 -o addr show {HOTSPOT_INTERFACE}"
    )
    if not ok:
        logger.warning(
            "Could not read %s addresses; falling back to %s for hotspot DNS",
            HOTSPOT_INTERFACE,
            HOTSPOT_GATEWAY_FALLBACK,
        )
        return HOTSPOT_GATEWAY_FALLBACK
    m = _IP_ADDR_RE.search(out)
    if not m:
        logger.warning(
            "No IPv4 visible on %s yet (output: %r); falling back to %s",
            HOTSPOT_INTERFACE,
            out[:160],
            HOTSPOT_GATEWAY_FALLBACK,
        )
        return HOTSPOT_GATEWAY_FALLBACK
    addr = m.group(1)
    logger.info("Detected hotspot gateway %s on %s", addr, HOTSPOT_INTERFACE)
    return addr


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
    actually has the gateway IP assigned. The gateway IP is detected
    live from :data:`HOTSPOT_INTERFACE` rather than hardcoded - BlueOS
    has renumbered the AP subnet between versions (192.168.43.1 →
    192.168.42.1 between 1.4.x and 1.5.x) and a stale hardcoded value
    made dnsmasq's ``bind-interfaces`` fail on every start, leaving
    ``doris.local`` unresolvable for hotspot clients that lack mDNS.

    Uses ``sudo`` so that /usr/sbin is in the PATH (Commander's default
    shell PATH omits /usr/sbin where dnsmasq lives).
    """
    gateway = await _detect_hotspot_gateway()
    conf_content = (
        f"listen-address={gateway}\n"
        "port=53\n"
        "bind-interfaces\n"
        f"no-dhcp-interface={HOTSPOT_INTERFACE}\n"
        f"address=/doris.local/{gateway}\n"
        f"address=/blueos-wifi.local/{gateway}\n"
        "no-resolv\n"
        "no-hosts\n"
    )
    # Commander's ssh transport returns rc=255 unpredictably on
    # short multi-step commands even when the underlying shell ran
    # to completion (we proved this in live testing: ``pkill ...;
    # true`` reliably returned rc=255 yet the kill happened, and
    # ``setsid dnsmasq ...`` reliably returned rc=255 yet the daemon
    # started). We therefore split the work into two short Commander
    # calls and ignore their rc - the real success signal is the
    # post-start pid-file check, not the launcher's exit code.
    write_cmd = (
        f"echo '{conf_content}' | sudo tee {HOTSPOT_DNS_CONF} > /dev/null && "
        f"sudo pkill -f 'dnsmasq.*{HOTSPOT_DNS_CONF}' 2>/dev/null; true"
    )
    await _run_host_command(write_cmd)

    # Detach dnsmasq from the Commander ssh session: ``setsid`` gives
    # it a new session, the stdio redirects close the inherited
    # ssh-side fds, and ``&`` runs it in the shell background so the
    # outer Commander call exits as soon as the fork succeeds.
    start_cmd = (
        f"sudo setsid /usr/sbin/dnsmasq --conf-file={HOTSPOT_DNS_CONF} "
        f"--pid-file={HOTSPOT_DNS_PID} "
        f"< /dev/null > /dev/null 2>&1 & "
        f"sleep 1; "
        f"sudo test -f {HOTSPOT_DNS_PID} && echo running || echo missing"
    )
    _, start_out = await _run_host_command(start_cmd)
    if "running" in start_out:
        logger.info("Hotspot DNS started on %s:53 (doris.local)", gateway)
    else:
        logger.warning(
            "Failed to start hotspot DNS server (gateway=%s, out=%r)",
            gateway,
            start_out,
        )


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

    ok, _ = await _run_host_command(
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

    Returns True only when:
      1. The destination directory exists (or was created), AND
      2. The tar PUT to ``/archive`` returned 200, AND
      3. The conf file is observable inside blueos-core after PUT, AND
      4. ``nginx -s reload`` returned rc=0.

    Any earlier failure logs a WARNING with the failing step so retry
    loops produce actionable signal (the previous version logged at
    DEBUG, which is why field reports of "attempt N/5 failed" were
    uninformative).
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            cid = await _find_core_container_id(client)
            if not cid:
                logger.warning(
                    "nginx redirect upload: %s container not found",
                    CORE_CONTAINER,
                )
                return False

            # Step 1: mkdir -p NGINX_CONF_DIR, *synchronously*. The old
            # path used Detach=True (fire-and-forget) and immediately
            # PUT'd the archive below. On a slow blueos-core boot the
            # mkdir hadn't actually run yet and the PUT failed against
            # a missing path. We now wait for the exec to finish and
            # check its exit code before continuing.
            exec_resp = await client.post(
                f"{_docker_base_url()}/containers/{cid}/exec",
                json={
                    "Cmd": ["mkdir", "-p", NGINX_CONF_DIR],
                    "AttachStdout": True,
                    "AttachStderr": True,
                },
            )
            exec_resp.raise_for_status()
            exec_id = exec_resp.json()["Id"]
            await client.post(
                f"{_docker_base_url()}/exec/{exec_id}/start",
                json={"Detach": False, "Tty": False},
            )
            inspect_resp = await client.get(
                f"{_docker_base_url()}/exec/{exec_id}/json"
            )
            inspect_resp.raise_for_status()
            mkdir_rc = inspect_resp.json().get("ExitCode")
            if mkdir_rc != 0:
                logger.warning(
                    "nginx redirect upload: mkdir -p %s exited %s",
                    NGINX_CONF_DIR, mkdir_rc,
                )
                return False

            # Step 2: PUT the tar archive.
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
            if resp.status_code != 200:
                logger.warning(
                    "nginx redirect upload: archive PUT to %s returned %d: %s",
                    NGINX_CONF_DIR, resp.status_code, resp.text[:200],
                )
                return False

        # Step 3: verify the conf file actually landed where we expect.
        # Catches the edge case where the PUT returned 200 but the tar
        # extraction landed somewhere unexpected (e.g. path traversal,
        # or Docker silently extracting to a parent dir).
        if not await _nginx_redirect_exists():
            logger.warning(
                "nginx redirect upload: archive PUT succeeded but %s "
                "is not observable in %s after upload",
                NGINX_CONF_DST, CORE_CONTAINER,
            )
            return False

        # Step 4: reload nginx and *check* the return code. The
        # previous comment said "return ignored; nginx reload errors
        # surface via 502 from the redirect itself" - but that
        # surfaces the failure to the *user* via a broken page rather
        # than to *us* via a retry, and a permanent reload failure
        # (e.g. syntax error in some other conf) would silently leave
        # nginx running the old config forever.
        reload_ok, reload_out = await _run_host_command(
            f"docker exec {CORE_CONTAINER} nginx -s reload"
        )
        if not reload_ok:
            logger.warning(
                "nginx redirect upload: 'nginx -s reload' failed: %s",
                reload_out[:200],
            )
            return False

        return True
    except Exception as exc:
        logger.warning(
            "nginx redirect upload raised %s: %s",
            type(exc).__name__, exc,
        )
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
    global _avahi_config_changed
    _avahi_config_changed = _setup_avahi_hostname()
    await _ensure_nginx_redirect()
    asyncio.get_event_loop().create_task(_nginx_redirect_watchdog())
