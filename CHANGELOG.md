# Changelog

## bh-0.6

- A named float's name now ends at its terminator instead of absorbing the
  padding behind it.  The field is a fixed ten characters and a sender is free
  to leave anything in the bytes past the terminator; AGT firmware through
  v0.3.0 left fragments of the adjacent string literals there, sending
  `AGT_CAP\0RE`, `REL_STAT\0P` and `PWR_SHDN\0v`.  Deleting every NUL rather
  than truncating at the first turned those into `AGT_CAPRE`, `REL_STATP` and
  `PWR_SHDNv`, so every message in the safe-surface protocol was dropped as
  unknown: no capability advertisement, no release status, and the AGT release
  path shown as unavailable while the AGT was announcing it once a second.  AGT
  v0.3.1 clears its padding and is the actual fix, but truncating is what the
  wire format means and it cannot be broken by a sender that does not.

- `doris.lua` loads on the vehicle again.  ArduPilot vendors Lua with `MAXVARS`
  lowered from upstream's 200 to 100, so the 107 locals this chunk had grown to
  compiled fine on a desktop Lua and were rejected on the autopilot with
  `doris.lua:894: too many local variables (limit is 100) in main function near
  'ipcam_start'`.  That line is where the count crossed 100, not where anything
  is wrong, which is why the message pointed at unrelated camera code.  The 29
  `DORIS_*` `Parameter` handles now live in one `prm` table instead of a local
  apiece, putting the chunk at 79 of 100.  Parameter names, indices and defaults
  are untouched, so existing parameter files still load.  A test now counts the
  chunk's locals and fails while there are still ten slots left, since the count
  had sat at 99 and the next feature to land was always going to break it.

- Deploying `doris.lua` no longer writes through the live file.  The copy
  truncated the script the autopilot reads and streamed ~75 KB back into it, so
  a script load landing inside that window compiled whatever prefix was on disk
  and failed with a syntax error at an arbitrary line.  A concurrent reader was
  measured observing the destination at zero bytes.  Since the file is only
  rewritten when its hash changes, this could only surface on the first boot
  after an upgrade.  The script is now written beside its destination and renamed
  into place, so a reader sees either the whole old file or the whole new one.
  This was found while chasing the local-variable failure above and is a real
  race, but it was not that failure's cause.

- Surfacing no longer waits on a GPS fix.  `ASCENT` previously ended only when a
  3D fix arrived alongside shallow depth, and measured over four dives that fix
  took 17 s, 6.8 min, 30.4 min, and 38.7 min after the vehicle was already
  floating, with `RECOVERY` following within a second every time.  A fix was the
  sole thing gating the end of the mission.  The fix remains the fast path, but
  depth alone now also qualifies: every sample in a `DORIS_SRF_SEC` window
  (default 30 s) must be shallower than `DORIS_SRF_DPT` (default 1.5 m), and the
  window must span at least 0.02 m so a frozen depth channel — which reads
  shallow and perfectly steady, exactly like floating — cannot qualify.  Across
  146 surface windows the quietest still held 0.070 m of spread.
- Added `DORIS_SRF_DPT` and `DORIS_SRF_SEC` as parameters 39 and 40, and grew
  the `DORIS_` table from 38 to 40 slots.  Existing indices are unchanged, so
  saved parameter files stay valid.
- EKF vertical velocity is deliberately not used for surface detection.  It
  remains the right signal for the sustained-ascent check, but at the surface it
  drifts badly: averaged over 30 s it implied up to 17.3 m of vertical travel
  while depth moved at most 0.36 m.
- Mirrored the drop-weight release to both controllers.  Lua remains the
  mission-policy owner: it drives the Navigator relay on `DORIS_RELAY_CH` and
  publishes the requested state as MAVLink `NAMED_VALUE_FLOAT RELAY` every
  500 ms, which AGT firmware v0.3.0 or newer uses to drive its own output.  The
  same software therefore runs on legacy Navigator-wired systems and on
  AGT-wired systems.
- Added AGT capability, physical release state, and mismatch status to the
  backend, and replaced the mission-start gate with a two-path check: a mission
  is blocked only when neither release path is usable, and a single degraded
  path is reported as a warning.
- Added a source-validated AGT shutdown handshake. Host shutdown is
  opt-in (`DORIS_AGT_SHUTDOWN_ENABLED=true`) and disabled by default.  Because
  cutting host power also disables the Navigator release output, enabling it
  additionally requires a healthy AGT release path.
- Kept the DORIS frame at profile v6 (`RELAY1_FUNCTION=1`, `RELAY1_PIN=14`,
  `SERVO14_FUNCTION=-1`), so upgrading does not change autopilot parameters or
  disturb an existing installation.
- Fixed frame re-application so a future frame version bump is applied to
  systems that already have an older profile recorded.
- Deferred all post-dive processing behind a **Process Dive** button on the
  Previous Dives page.  Reaching the surface now only quiesces the dive: the
  recorder is stopped so the last video segment closes cleanly, the dive stamp
  and bottom mode are recorded, and the dive is closed and marked pending.  That
  runs in seconds instead of minutes, which matters because the AGT holds
  payload power up until it completes.  Video building, USB copies, and
  telemetry parsing now happen on deck, when an operator asks for them.
- The processing job reports each step separately so a run is visible while it
  happens, copies the telemetry `.mcap`, autopilot BIN logs, videos, photos,
  extension logs, RadCam Spy session logs, and the dive record to the USB stick,
  verifies every copy, and only then declares the stick safe to remove.  Steps
  that need a USB stick are skipped rather than failed, so processing without
  one still builds videos and enriches the dive record.
- Raw `.ts` video segments now survive until the MP4s built from them have been
  verified, instead of being deleted as soon as ffmpeg reported success.
- Fixed dives being recorded as **cancelled** after a successful mission.
  Closing a dive previously happened only inside the `/dive/status` poll, which
  requires a browser to be open and which sees neither an active nor a completed
  mission once the AGT has cut power and the operator has power cycled on deck.
  Recovery is now witnessed by the backend, both when Lua reports it and from
  the MAVLink stream, and the dive record is closed at that point.
- Dive records and mission state are written atomically, so a power cut cannot
  leave a truncated file that is unreadable on the next boot.
- USB verification now compares each copy against its source size instead of
  rejecting anything empty.  A log that the source system left empty is not a
  failed copy, and treating it as one failed the whole run at the last step
  after every file had already been written.  Truncated copies are still caught.
- RadCam Spy collection skips sessions that were opened but never written, and
  matches session filenames exactly rather than looking for any date-like run of
  digits in the name.
- `.ndjson` counts as a data file, so collected RadCam Spy logs appear in the
  Data tab rather than sitting unlisted on the stick.

### RadCam Spy logs

Collecting RadCam Spy session logs prefers a read-only bind mount and falls back
to that extension's HTTP API when the mount is absent.  To enable the mount, add
this to the Doris extension's Custom Settings under `HostConfig.Binds`:

```
"/usr/blueos/extensions/radcam-spy:/tmp/storage/radcam_spy:ro"
```

Without it, the logs are still collected whenever RadCam Spy is running; if it
is stopped and unmounted, that step reports skipped and the rest of the job
proceeds.

### Wiring compatibility

Both controllers command the release, but only one may be wired to the
actuator.  Never connect the release actuator to both Navigator SERVO14 and AGT
Relay 2: two independent outputs driving one relay input is not a supported
electrical configuration.

- Legacy systems: leave the actuator on Navigator SERVO14 and keep
  `DORIS_RELAY_CH=0`.  The AGT release request is harmless because its output
  is unwired, and older AGT firmware simply ignores the `RELAY` message.
- AGT-wired systems: move the actuator to AGT Relay 2, flash AGT firmware
  v0.3.0 or newer, and set `DORIS_RELAY_CH=-1` to leave the Navigator output
  idle.  Only this configuration may enable `DORIS_AGT_SHUTDOWN_ENABLED`.

The two paths do not release on identical timing.  Lua de-energizes the
Navigator relay as soon as it confirms a sustained ascent, while the AGT holds
its output until the release minimum hold time has elapsed and it has
independently qualified the surface.
