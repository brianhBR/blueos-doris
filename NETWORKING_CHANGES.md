# Networking Changes Attempted (bh-0.2.x line) — Rolled Back

This document records the WiFi/networking changes that were attempted between
tags `tw-0.1.0` and `bh-0.2.4` on the `fork/master` branch and why they were
abandoned. The commits are preserved under the `bh-0.2.4` tag for reference.

## Baseline

`tw-0.1.0` is the last known-good tag for WiFi AP stability. The hotspot
comes up reliably and clients can connect. All subsequent work (bh-0.3.x)
is rebased on this tag.

## What was attempted

### 1. 88x2bu driver upgrade (`b61ad60`, `1ddb7ef`)

- Replaced the bundled `88x2bu.ko` with a newer build from
  `morrownr/88x2bu-20210702` (commit fecac34, 2026-01-08, kernel 6.6.31).
- Added SHA-256 fingerprinting so the driver auto-updates on boot if the
  installed version doesn't match the bundled one.
- Added `/etc/modprobe.d/88x2bu.conf` with AP-mode parameters
  (`rtw_switch_usb_mode`, `rtw_power_mgnt`, `rtw_vht_enable`).
- Added `extension/driver/DRIVER_VERSION` for human-readable identification.

### 2. DNS startup retries (`415cea3`, `e02f67e`)

- dnsmasq fails to bind to `192.168.43.1` when called immediately after
  `configure_hotspot()` because `create_ap` takes 10-30s to assign the IP.
- Added retry loop (up to 12 attempts, 5s apart = 60s total) waiting for
  the gateway IP to become available before starting dnsmasq.
- AP watchdog also checks if dnsmasq is running and restarts it if the AP
  is up but DNS is down.

### 3. Hotspot startup prioritization (`757bd80`, reverted in `b5b27bd`)

- Moved WiFi AP configuration to run immediately after the driver loads,
  before frame apply and nginx redirect setup (which can add 30-90s delay).
- Reverted because it caused startup ordering issues.

### 4. Gateway IP dynamic detection (`db83e27`)

- Instead of hardcoding `192.168.43.1`, detect the gateway IP dynamically
  from the AP interface.
- Fixed credential order for dnsmasq.
- Added watchdog DNS recovery.

### 5. Bulk revert and partial restore (`052a774`, `3d256e5`)

- `052a774` reverted the DNS retry, driver upgrade, and AP cycling changes
  back to `tw-0.1.0` state.
- `3d256e5` re-applied the new WiFi driver and DNS retry with "safe"
  modprobe options, but without the AP cycling and startup reordering.

## Why it was rolled back

After deploying these changes, we experienced intermittent inability to
connect to the vehicle's WiFi hotspot. The symptoms included:

- Hotspot visible but clients unable to complete WPA2 handshake
- Hotspot not appearing at all after boot
- Loss of connectivity after the container restarts

The root cause was not definitively identified, but the combination of
driver changes, startup ordering changes, and DNS retry logic introduced
instability. The multiple reverts and re-applies in the commit history
reflect the troubleshooting attempts.

## What was kept

The following non-networking features from the bh-0.2.x line were
cherry-picked onto bh-0.3.x:

- **Barometer surface calibration** (from `546cdba`) — API endpoint and
  UI button for `MAV_CMD_PREFLIGHT_CALIBRATION`
- **Persistent logging** (from `3b5fba2`) — rotating log files to
  bind-mounted storage, dmesg capture, log API endpoints
- **`.gitattributes`** (from `ecab5c4`) — LF line endings for `.sh` files

## Reference

- Stable baseline: `tw-0.1.0`
- Rolled-back work: `bh-0.2.4` tag
- Current line: `bh-0.3.x` (based on `tw-0.1.0` + recording reliability + cherry-picks)
