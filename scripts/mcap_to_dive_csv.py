"""Convert raw BlueOS recorder ``.mcap`` files into the DORIS dive-data CSV
format (``doris_dive_export`` v2), without needing a running extension.

This is a standalone wrapper around the real conversion logic in
``extension/backend/src/doris/services/mcap_telemetry.py`` (loaded directly
from its file so we don't have to install the full backend's dependencies --
only the ``mcap`` package is required). Because a bare recorder file has no
associated "dive" database record, the DIVE DATA header section is filled in
with values derived from the .mcap itself (first/last telemetry timestamp,
first GPS fix, filename) instead of DORIS app metadata such as dive name,
username or mission status -- those fields are left blank.

Usage:
    py scripts/mcap_to_dive_csv.py <mcap_file_or_dir> [<more> ...] [-o OUT_DIR]

Examples:
    py scripts/mcap_to_dive_csv.py recorder_20260704_020457.mcap
    py scripts/mcap_to_dive_csv.py "G:\\Shared drives\\...\\Fkt260629_DORIS00_03" -o out\\csv
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
_MCAP_TELEMETRY_PATH = (
    REPO_ROOT / "extension" / "backend" / "src" / "doris" / "services" / "mcap_telemetry.py"
)

# Distinctive strings that must be present in the real DORIS mcap_telemetry.py.
# Cheap sanity check against loading the wrong file (e.g. if this script is
# copied out of the repo alongside a differently-sourced same-named module) --
# fail with a clear message instead of silently running unrelated code.
_REQUIRED_MARKERS = (
    "doris_dive_export",
    "def summarize_mcap",
    "def build_dive_csv",
    "def dive_csv_filename",
)


def _load_mcap_telemetry() -> ModuleType:
    """Import mcap_telemetry.py directly from its file.

    We deliberately avoid ``import doris.services.mcap_telemetry`` because
    ``doris.services.__init__`` pulls in the full backend (httpx, pydantic,
    robyn, ...); loading the file directly keeps this script's only
    dependency on the ``mcap`` package.
    """
    if not _MCAP_TELEMETRY_PATH.is_file():
        raise SystemExit(f"Could not find mcap_telemetry.py at {_MCAP_TELEMETRY_PATH}")
    source = _MCAP_TELEMETRY_PATH.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in _REQUIRED_MARKERS if marker not in source]
    if missing:
        raise SystemExit(
            f"{_MCAP_TELEMETRY_PATH} does not look like the DORIS mcap_telemetry.py module "
            f"(missing: {', '.join(missing)})."
        )
    spec = importlib.util.spec_from_file_location("doris_mcap_telemetry", _MCAP_TELEMETRY_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Failed to load module spec for {_MCAP_TELEMETRY_PATH}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses' field-type resolution looks the module up in sys.modules
    # while exec_module() is running, so it must be registered first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _iter_mcap_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.mcap")))
        elif p.is_file():
            out.append(p)
        else:
            print(f"warning: path not found, skipping: {p}", file=sys.stderr)
    return out


def _first_position(mt: ModuleType, summary) -> tuple[float | None, float | None]:
    """First (lat, lon) seen in the telemetry frames, in chronological order."""
    for frame in summary.frames:
        lat = frame.values.get("latitude")
        lon = frame.values.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
    return None, None


def _synthetic_dive_record(mt: ModuleType, mcap_path: Path, summary, dive_name: str | None) -> dict:
    started_at = ended_at = None
    if summary.frames:
        started_at = datetime.fromtimestamp(
            summary.frames[0].log_time_ns / 1e9, tz=timezone.utc
        ).isoformat()
        ended_at = datetime.fromtimestamp(
            summary.frames[-1].log_time_ns / 1e9, tz=timezone.utc
        ).isoformat()
    lat, lon = _first_position(mt, summary)
    return {
        "dive_name": dive_name or mcap_path.stem,
        "username": "",
        "configuration": "",
        "status": "",
        "profile_id": "",
        "started_at": started_at,
        "ended_at": ended_at,
        "latitude": lat,
        "longitude": lon,
    }


def convert_one(mt: ModuleType, mcap_path: Path, out_dir: Path | None, dive_name: str | None) -> Path:
    print(f"Parsing {mcap_path} ...")
    summary = mt.summarize_mcap(mcap_path)
    print(
        f"  messages_seen={summary.messages_seen} telemetry_rows={len(summary.frames)}"
        f" extra_columns={summary.extra_columns or '[]'}"
    )
    if not summary.frames:
        print(
            "  warning: no autopilot telemetry frames decoded (empty/corrupt file, "
            "or no mavlink/1/1/* topics)",
            file=sys.stderr,
        )

    record = _synthetic_dive_record(mt, mcap_path, summary, dive_name)
    csv_text = mt.build_dive_csv(record, summary, mcap_path.name)

    dest_dir = out_dir if out_dir is not None else mcap_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = mt.dive_csv_filename(record, mcap_path.stem)
    dest = dest_dir / filename
    dest.write_bytes(csv_text.encode("utf-8"))
    print(f"  wrote {dest}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "paths",
        nargs="+",
        help="One or more .mcap files and/or directories to scan recursively for *.mcap",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default=None,
        help="Directory to write CSVs to (default: alongside each source .mcap)",
    )
    parser.add_argument(
        "--dive-name",
        default=None,
        help="Override dive_name recorded in the CSV header (default: mcap filename stem)",
    )
    args = parser.parse_args()

    mt = _load_mcap_telemetry()
    files = _iter_mcap_files(args.paths)
    if not files:
        raise SystemExit("No .mcap files found for the given paths.")

    out_dir = Path(args.out_dir) if args.out_dir else None
    written: list[Path] = []
    for mcap_path in files:
        try:
            written.append(convert_one(mt, mcap_path, out_dir, args.dive_name))
        except Exception as e:  # noqa: BLE001 - report and continue with remaining files
            print(f"  error: failed to convert {mcap_path}: {e}", file=sys.stderr)

    print(f"\nDone: {len(written)}/{len(files)} file(s) converted.")


if __name__ == "__main__":
    main()
