"""Push DORIS into RECOVERY to see if the AGT firmware reacts.

We do two things in sequence so we can tell which signal (if any) the AGT
is actually subscribing to:

  1) PARAM_SET DORIS_STATE = 4
  2) NAMED_VALUE_FLOAT('STATE', 4.0)

The lua script writes DORIS_STATE every 500 ms based on its own internal
state variable, so step 1 will be overwritten quickly — but it'll
broadcast a PARAM_VALUE to listeners (including the AGT) before that
happens.  Step 2 mimics exactly what the lua broadcasts when it
naturally enters RECOVERY.
"""
import json
import sys
import urllib.request

M2R = "http://192.168.68.75:6040/mavlink"


def post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(M2R, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode("utf-8", errors="replace")
        print(f"  -> {r.status} {body or '(no body)'}")


def param_set_doris_state(value: float):
    name = "DORIS_STATE"
    payload = {
        "header": {"system_id": 255, "component_id": 0, "sequence": 0},
        "message": {
            "type": "PARAM_SET",
            "target_system": 1,
            "target_component": 1,  # autopilot owns the param table
            "param_id": list(name.ljust(16, "\x00")),
            "param_value": float(value),
            "param_type": {"type": "MAV_PARAM_TYPE_REAL32"},
        },
    }
    print(f"PARAM_SET {name}={value}")
    post(payload)


def named_value_float_state(value: float):
    """Broadcast NAMED_VALUE_FLOAT('STATE', value) as if from the autopilot's
    lua scripting (system_id=1, component_id=1)."""
    name = "STATE"
    payload = {
        "header": {"system_id": 1, "component_id": 1, "sequence": 0},
        "message": {
            "type": "NAMED_VALUE_FLOAT",
            "time_boot_ms": 0,
            "name": list(name.ljust(10, "\x00")),
            "value": float(value),
        },
    }
    print(f"NAMED_VALUE_FLOAT name='{name}' value={value}")
    post(payload)


def main():
    state = 4  # STATE_RECOVERY in doris.lua

    print(">> step 1: PARAM_SET DORIS_STATE = 4")
    try:
        param_set_doris_state(state)
    except Exception as e:
        print(f"  PARAM_SET failed: {e}")

    print()
    print(">> step 2: NAMED_VALUE_FLOAT('STATE', 4) — what the lua broadcasts")
    try:
        named_value_float_state(state)
    except Exception as e:
        print(f"  NAMED_VALUE_FLOAT failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
