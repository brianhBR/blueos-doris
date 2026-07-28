# Changelog

## bh-0.6

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
