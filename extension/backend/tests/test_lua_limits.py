"""Guards on doris.lua against the compile-time limits ArduPilot actually ships.

ArduPilot vendors Lua with MAXVARS lowered from upstream's 200 to 100
(libraries/AP_Scripting/lua/src/lparser.c), so a script that compiles on a
desktop Lua can still be rejected on the vehicle. That failure is loud but
misleading: the compiler names the declaration that happens to occupy slot 101,
which sends you looking at a function that is not the problem. Crossing the
ceiling is also invisible in review, because it depends on the running total of
everything declared above.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doris.lua"

# lparser.c: #define MAXVARS 100
ARDUPILOT_MAXVARS = 100


def main_chunk_locals() -> list[tuple[int, str]]:
    """Return (line, name) for every local the main chunk holds open.

    Locals belonging to the main chunk are the ones declared at column 0;
    anything indented is inside a function or a do block and its register is
    reused once that scope closes, so it does not count against the total.
    """
    slots: list[tuple[int, str]] = []
    for lineno, raw in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.startswith("local"):
            continue
        fn = re.match(r"local\s+function\s+([A-Za-z_]\w*)", raw)
        if fn:
            slots.append((lineno, fn.group(1)))
            continue
        names = re.match(r"local\s+([^=]+?)(?:=|$)", raw)
        if not names:
            continue
        for name in names.group(1).split(","):
            name = name.strip()
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                slots.append((lineno, name))
    return slots


def test_main_chunk_fits_ardupilots_local_limit():
    slots = main_chunk_locals()
    assert len(slots) <= ARDUPILOT_MAXVARS, (
        f"doris.lua declares {len(slots)} locals in the main chunk, over "
        f"ArduPilot's limit of {ARDUPILOT_MAXVARS}. The vehicle will refuse to "
        f"load it, blaming {slots[ARDUPILOT_MAXVARS][1]!r} on line "
        f"{slots[ARDUPILOT_MAXVARS][0]}. Group related values into a table "
        f"instead of adding another top-level local."
    )


def test_local_count_keeps_room_to_grow():
    """Fail while there is still somewhere to put a fix, not at the cliff edge.

    The main chunk sat at 99 of 100 for a while, so the next feature to land
    broke the script rather than this margin.
    """
    slots = main_chunk_locals()
    headroom = ARDUPILOT_MAXVARS - len(slots)
    assert headroom >= 10, (
        f"only {headroom} local slots left of {ARDUPILOT_MAXVARS}. Collapse a "
        f"group of top-level locals into a table before adding more."
    )


def test_parameter_handles_stay_in_one_table():
    """Parameter handles were 29 separate locals and were most of the overrun."""
    body = SCRIPT.read_text(encoding="utf-8")
    stragglers = re.findall(r"^local\s+DORIS_[A-Z_0-9]+\s*=\s*Parameter", body, re.M)
    assert not stragglers, (
        f"{len(stragglers)} Parameter handles are top-level locals again; they "
        f"belong in the prm table, which costs one slot however many it holds."
    )


def test_every_referenced_parameter_handle_exists():
    """A typo'd prm key is nil, and nil:get() only fails once that line runs."""
    body = SCRIPT.read_text(encoding="utf-8")
    table = re.search(r"^local prm = \{\n(.*?)^\}", body, re.S | re.M)
    assert table, "prm table not found"
    defined = {
        key
        for key, name in re.findall(
            r"^\s*([A-Z_0-9]+)\s*=\s*Parameter\(\"DORIS_([A-Z_0-9]+)\"\)",
            table.group(1),
            re.M,
        )
        if key == name
    }
    referenced = set(re.findall(r"\bprm\.([A-Z_0-9]+)", body))
    assert referenced <= defined, f"undefined prm keys: {sorted(referenced - defined)}"


def test_parameter_keys_match_their_parameter_names():
    """prm.FOO must be DORIS_FOO, so the code reads like the param list."""
    body = SCRIPT.read_text(encoding="utf-8")
    table = re.search(r"^local prm = \{\n(.*?)^\}", body, re.S | re.M)
    assert table
    pairs = re.findall(
        r"^\s*([A-Z_0-9]+)\s*=\s*Parameter\(\"DORIS_([A-Z_0-9]+)\"\)",
        table.group(1),
        re.M,
    )
    assert pairs, "no Parameter handles found in the prm table"
    assert [(k, n) for k, n in pairs if k != n] == []


def test_recovery_does_not_finalize_the_dive_early():
    """BlueOS must keep the active dive open for the AGT surface dwell."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "/api/v1/dive/finalize" not in body
    recovery = body.split("elseif state == STATE_RECOVERY then", 1)[1]
    assert "state =" not in recovery.split("return update, UPDATE_INTERVAL_MS", 1)[0]


def test_telemetry_runs_before_terminal_recovery_dispatch():
    """STATE=4 and DORS telemetry continue on every terminal recovery tick."""
    body = SCRIPT.read_text(encoding="utf-8")
    update = body.split("function update()", 1)[1]
    assert update.index("update_telemetry(now_ms)") < update.index(
        "elseif state == STATE_RECOVERY then"
    )
