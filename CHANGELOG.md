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
