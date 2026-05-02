#!/usr/bin/env python3
"""Stitch a dive's `radcam_<stamp>_part<NN>_00000.ts` segments into one
continuous video per dive phase (DESCENT / ON_BOTTOM / ASCENT), using ffmpeg
stream-copy (no re-encode).

Inputs
------
* A coverage log JSON written by ``sitl_monitor.py``. It contains
  ``t0_utc``, ``phases`` and ``recording_intervals`` (all in seconds relative
  to ``t0_utc``).
* A vehicle running the DORIS extension at ``http://<ip>:<port>/`` that
  exposes ``/api/v1/media/files`` and the raw download URL.

Workflow
--------
1. Each ``recording_intervals`` entry maps 1:1 to a gst-launch session and a
   single ``radcam_<base_stamp>_part*.ts`` series — ``base_stamp`` is
   derived from ``t0_utc + rec.start`` rounded to the second.
2. For each session: download all parts into ``--work-dir`` (cached across
   runs), then run::

       ffmpeg -f concat -safe 0 -i parts.txt -c copy session_<stamp>.mp4

3. For each phase in ``PHASE_STATES``: find overlapping recording windows,
   compute per-session ``(t_start, duration)`` in session-local coordinates,
   and run a stream-copy cut per chunk followed by an optional concat into
   one ``dive_<stamp>_<phase>.mp4``.
4. With ``--delete-originals``, every source ``.ts`` that was consumed is
   deleted from the vehicle via ``DELETE /api/v1/media/files``.

``ffmpeg -c copy`` cuts on the nearest keyframe ≤ the requested time, so the
output may include up to one GOP (~2 s for the IPcam recorder) of slop at
each boundary. That's fine for phase separation.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("stitch")


# ── phase constants (mirrors sitl_monitor.py) ────────────────────────────────

STATE_DESCENT   = 1
STATE_ON_BOTTOM = 2
STATE_ASCENT    = 3

PHASE_SLUGS = {
    STATE_DESCENT:   "descent",
    STATE_ON_BOTTOM: "on_bottom",
    STATE_ASCENT:    "ascent",
}

PHASE_STATES = tuple(PHASE_SLUGS.keys())

# Maximum seconds between the expected session stamp (t0 + rec.start) and the
# filename base_stamp we accept as a match. The Lua → HTTP → gst-launch chain
# adds sub-second latency, but allow a few seconds of slack.
SESSION_STAMP_TOLERANCE_S = 5

# How much to pad phase bounds when selecting overlapping recording intervals.
PHASE_OVERLAP_EPS_S = 0.05


# ── small helpers ────────────────────────────────────────────────────────────

@dataclass
class RecInterval:
    start: float
    end: float
    base_stamp: str = ""


@dataclass
class PhaseWindow:
    state: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def slug(self) -> str:
        return PHASE_SLUGS[self.state]


_FILENAME_RE = re.compile(
    r"^radcam_(?P<stamp>\d{8}_\d{6})_part(?P<part>\d+)_\d+\.ts$")


def _base_stamp_from_utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def _parse_base_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    logger.debug("exec: %s", " ".join(shlex.quote(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        logger.error("command failed (%d): %s", r.returncode,
                     " ".join(shlex.quote(c) for c in cmd))
        logger.error("stderr:\n%s", r.stderr)
        raise RuntimeError(f"ffmpeg/ffprobe failed: {cmd[0]}")
    return r


def _ffprobe_duration(path: Path) -> float | None:
    r = _run(["ffprobe", "-v", "error",
              "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)], check=False)
    if r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


# ── vehicle API client ───────────────────────────────────────────────────────

class VehicleClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _open(self, method: str, path: str):
        url = self.base + path
        req = urllib.request.Request(url, method=method)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def list_media(self, limit: int = 500) -> list[dict]:
        with self._open("GET", f"/api/v1/media/files?limit={limit}") as r:
            return json.loads(r.read().decode("utf-8"))

    def download(self, download_url_path: str, dest: Path,
                 chunk: int = 1024 * 1024) -> int:
        """Stream a file from the extension to ``dest``. Returns bytes written."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        url = self.base + download_url_path
        total = 0
        with urllib.request.urlopen(url, timeout=self.timeout * 12) as r, \
             open(tmp, "wb") as f:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                f.write(buf)
                total += len(buf)
        tmp.rename(dest)
        return total

    def delete(self, abs_id_path: str) -> bool:
        q = urllib.parse.urlencode({"path": abs_id_path})
        req = urllib.request.Request(
            self.base + "/api/v1/media/files?" + q, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
                return bool(body.get("success"))
        except urllib.error.HTTPError as e:
            logger.warning("DELETE %s → HTTP %d: %s",
                           abs_id_path, e.code, e.read()[:200])
            return False


# ── correlation: rec_interval → session base_stamp ───────────────────────────

def correlate_intervals_to_sessions(
    intervals: list[RecInterval], t0_utc: datetime,
    known_stamps: list[str],
) -> None:
    """Set ``RecInterval.base_stamp`` in-place by matching expected stamp
    (``t0 + rec.start``) to the closest known session stamp within tolerance."""
    known_dt = [(s, _parse_base_stamp(s)) for s in known_stamps]
    for iv in intervals:
        expected_dt = t0_utc + timedelta(seconds=iv.start)
        best: tuple[str, float] | None = None
        for stamp, dt in known_dt:
            delta = abs((dt - expected_dt).total_seconds())
            if best is None or delta < best[1]:
                best = (stamp, delta)
        if best and best[1] <= SESSION_STAMP_TOLERANCE_S:
            iv.base_stamp = best[0]
            logger.info("  rec %7.2f–%7.2f s  →  session %s  (|Δ|=%.2fs)",
                        iv.start, iv.end, best[0], best[1])
        else:
            logger.warning(
                "  rec %7.2f–%7.2f s  →  NO matching session (best |Δ|=%s s)",
                iv.start, iv.end,
                f"{best[1]:.2f}" if best else "none")


# ── core: concat + per-phase cut ─────────────────────────────────────────────

def concat_session_parts(parts: list[Path], out_path: Path) -> None:
    """Concatenate ``parts`` (in order) into a single MP4 via ffmpeg concat
    demuxer + stream copy."""
    list_file = out_path.with_suffix(".list.txt")
    list_file.write_text(
        "\n".join(f"file {shlex.quote(str(p.resolve()))}" for p in parts) + "\n")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "concat", "-safe", "0",
           "-i", str(list_file),
           "-c", "copy",
           "-movflags", "+faststart",
           str(out_path)]
    _run(cmd)
    list_file.unlink(missing_ok=True)


def cut_session(session_mp4: Path, start_s: float, duration_s: float,
                out_path: Path) -> None:
    """Stream-copy cut of ``session_mp4`` starting at ``start_s`` for
    ``duration_s``. Fast-seek before ``-i`` for keyframe-aligned speed."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(session_mp4)]
    if duration_s > 0:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(out_path)]
    _run(cmd)


def concat_mp4s(parts: list[Path], out_path: Path) -> None:
    """Concat several MP4s (same codecs) into one with the concat demuxer."""
    if len(parts) == 1:
        shutil.copy2(parts[0], out_path)
        return
    list_file = out_path.with_suffix(".list.txt")
    list_file.write_text(
        "\n".join(f"file {shlex.quote(str(p.resolve()))}" for p in parts) + "\n")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "concat", "-safe", "0",
           "-i", str(list_file),
           "-c", "copy",
           "-movflags", "+faststart",
           str(out_path)]
    _run(cmd)
    list_file.unlink(missing_ok=True)


# ── main pipeline ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stitch dive .ts segments into one MP4 per phase.")
    parser.add_argument("--coverage-log", required=True, type=Path,
                        help="JSON written by sitl_monitor.py")
    parser.add_argument("--vehicle-ip", default="192.168.1.73")
    parser.add_argument("--extension-port", type=int, default=8095)
    parser.add_argument("--work-dir", type=Path,
                        default=Path(__file__).parent / "logs" / "stitch_work",
                        help="scratch directory for downloads + intermediate concat")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).parent / "logs" / "stitched",
                        help="where the per-phase MP4s are written")
    parser.add_argument("--skip-download", action="store_true",
                        help="use only files already in --work-dir")
    parser.add_argument("--delete-originals", action="store_true",
                        help="after all three phase MP4s are verified, "
                             "DELETE the consumed .ts files from the vehicle")
    parser.add_argument("--keep-intermediates", action="store_true",
                        help="keep per-session concat MP4s in --work-dir")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        logger.error("ffmpeg/ffprobe not found in PATH")
        return 2

    if not args.coverage_log.is_file():
        logger.error("coverage log not found: %s", args.coverage_log)
        return 2

    cov = json.loads(args.coverage_log.read_text())
    t0_utc = datetime.fromisoformat(cov["t0_utc"])
    phases_all = cov.get("phases", [])
    intervals_raw = cov.get("recording_intervals", [])

    phases = [
        PhaseWindow(state=p["state"], start=float(p["start"]),
                    end=float(p["end"]))
        for p in phases_all
        if p["state"] in PHASE_STATES and p.get("end") is not None
    ]
    intervals = [
        RecInterval(start=float(iv["start"]),
                    end=float(iv["end"] if iv["end"] is not None
                              else max(p["end"] for p in phases_all)))
        for iv in intervals_raw
    ]

    if not phases:
        logger.error("no DESCENT/ON_BOTTOM/ASCENT phases in coverage log")
        return 2
    if not intervals:
        logger.error("no recording_intervals in coverage log")
        return 2

    logger.info("Dive t0 UTC: %s", t0_utc.isoformat())
    logger.info("Phases     : %d", len(phases))
    logger.info("Rec windows: %d", len(intervals))

    ext = VehicleClient(f"http://{args.vehicle_ip}:{args.extension_port}")

    # ── 1. list + filter vehicle media ─────────────────────────────────────
    media = ext.list_media(limit=500)
    by_stamp: dict[str, list[dict]] = {}
    for f in media:
        m = _FILENAME_RE.match(f.get("filename", ""))
        if not m:
            continue
        by_stamp.setdefault(m["stamp"], []).append({
            "filename": f["filename"],
            "part": int(m["part"]),
            "size_bytes": f.get("size_bytes") or 0,
            "id": f["id"],
            "download_url": f["download_url"],
        })
    logger.info("Vehicle has %d recording session(s) total", len(by_stamp))

    # ── 2. correlate rec_intervals → session stamps ───────────────────────
    logger.info("Correlating recording windows to session stamps ...")
    correlate_intervals_to_sessions(intervals, t0_utc, sorted(by_stamp.keys()))
    stamps_needed = sorted({iv.base_stamp for iv in intervals if iv.base_stamp})
    if not stamps_needed:
        logger.error("no recording windows could be matched to any session; "
                     "is the vehicle IP correct and are the files still on "
                     "the USB?")
        return 2

    # ── 3. download (or reuse) each needed session's parts ────────────────
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    consumed_ids: list[str] = []
    session_files: dict[str, list[Path]] = {}
    total_dl_bytes = 0

    for stamp in stamps_needed:
        parts = sorted(by_stamp.get(stamp, []), key=lambda d: d["part"])
        if not parts:
            logger.error("session %s not present on vehicle", stamp)
            return 2
        local_parts: list[Path] = []
        logger.info("Session %s: %d part(s), %s on vehicle",
                    stamp, len(parts),
                    _fmt_bytes(sum(p["size_bytes"] for p in parts)))
        for p in parts:
            dest = args.work_dir / p["filename"]
            if args.skip_download or (
                dest.exists() and dest.stat().st_size == p["size_bytes"]):
                logger.info("  %s  (cached, %s)", p["filename"],
                            _fmt_bytes(dest.stat().st_size))
            else:
                logger.info("  %s  → downloading ...", p["filename"])
                got = ext.download(p["download_url"], dest)
                total_dl_bytes += got
                logger.info("    received %s", _fmt_bytes(got))
                if p["size_bytes"] and got != p["size_bytes"]:
                    logger.warning(
                        "    size mismatch (expected %s, got %s)",
                        p["size_bytes"], got)
            local_parts.append(dest)
            consumed_ids.append(p["id"])
        session_files[stamp] = local_parts

    if total_dl_bytes:
        logger.info("Downloaded %s new bytes", _fmt_bytes(total_dl_bytes))

    # ── 4. concat parts → one combined MP4 per session ────────────────────
    session_mp4: dict[str, Path] = {}
    session_duration_s: dict[str, float] = {}
    for stamp, parts in session_files.items():
        out = args.work_dir / f"session_{stamp}.mp4"
        logger.info("Concat session %s → %s (%d part(s))",
                    stamp, out.name, len(parts))
        concat_session_parts(parts, out)
        dur = _ffprobe_duration(out)
        if dur is None:
            logger.error("failed to probe %s", out)
            return 3
        session_mp4[stamp] = out
        session_duration_s[stamp] = dur
        logger.info("  session duration: %.2f s  (expected ~%.2f s)",
                    dur, next((iv.end - iv.start
                               for iv in intervals if iv.base_stamp == stamp), 0.0))

    # ── 5. for each phase, cut overlapping parts of each session ──────────
    stamp_slug = _base_stamp_from_utc(t0_utc)
    results: list[tuple[str, Path, float, float]] = []

    for phase in phases:
        phase_slug = phase.slug
        chunks: list[Path] = []
        chunk_durations: list[float] = []
        chunk_idx = 0

        for iv in intervals:
            if not iv.base_stamp:
                continue
            ov_start = max(phase.start, iv.start) - PHASE_OVERLAP_EPS_S
            ov_end   = min(phase.end, iv.end) + PHASE_OVERLAP_EPS_S
            overlap  = ov_end - ov_start
            if overlap <= 0:
                continue
            # Clip back inside both phase and interval, then translate to
            # session-local seconds (= video time inside session mp4).
            cut_start_abs = max(phase.start, iv.start)
            cut_end_abs   = min(phase.end, iv.end)
            cut_start_local = max(0.0, cut_start_abs - iv.start)
            cut_duration    = max(0.0, cut_end_abs - cut_start_abs)

            # Guardrail: never ask for more than what the session actually has.
            sess_dur = session_duration_s.get(iv.base_stamp, float("inf"))
            cut_duration = min(cut_duration,
                               max(0.0, sess_dur - cut_start_local))

            if cut_duration <= 0.01:
                continue

            chunk_idx += 1
            chunk_path = (args.work_dir
                          / f"chunk_{phase_slug}_{chunk_idx:02d}_{iv.base_stamp}.mp4")
            logger.info(
                "Phase %-9s ← session %s  cut [%.2f s, %.2f s] (dur %.2f s)",
                phase_slug, iv.base_stamp,
                cut_start_local, cut_start_local + cut_duration,
                cut_duration)
            cut_session(session_mp4[iv.base_stamp],
                        cut_start_local, cut_duration, chunk_path)
            chunks.append(chunk_path)
            chunk_durations.append(cut_duration)

        out_path = args.output_dir / f"dive_{stamp_slug}_{phase_slug}.mp4"
        if not chunks:
            logger.warning("  phase %s has no overlapping recording — "
                           "no file produced", phase_slug)
            continue
        if len(chunks) == 1:
            shutil.move(str(chunks[0]), str(out_path))
        else:
            concat_mp4s(chunks, out_path)
            for c in chunks:
                c.unlink(missing_ok=True)

        actual_dur = _ffprobe_duration(out_path) or 0.0
        expected_rec_dur = sum(chunk_durations)
        results.append((phase_slug, out_path, actual_dur, expected_rec_dur))
        logger.info("  → %s  (%.2f s actual, %.2f s expected, %.1f%% of %.2f s phase)",
                    out_path.name, actual_dur, expected_rec_dur,
                    100.0 * actual_dur / phase.duration if phase.duration > 0 else 0,
                    phase.duration)

    # ── 6. summary ─────────────────────────────────────────────────────────
    print("\n═══ Stitch summary ═══")
    for slug, out_path, dur, expected in results:
        print(f"  {slug:<10s} {out_path.name:<40s} "
              f"{dur:7.2f} s  (expected {expected:6.2f} s, "
              f"file {_fmt_bytes(out_path.stat().st_size)})")

    # ── 7. cleanup / delete originals on vehicle ──────────────────────────
    if args.delete_originals and len(results) == len(
            [p for p in phases if any(
                max(p.start, iv.start) < min(p.end, iv.end) for iv in intervals)]):
        print("\nDeleting originals on vehicle ...")
        deleted = 0
        for file_id in consumed_ids:
            ok = ext.delete(file_id)
            if ok:
                deleted += 1
            logger.info("  DELETE %s  → %s", file_id, "ok" if ok else "FAIL")
        print(f"  deleted {deleted}/{len(consumed_ids)} files from vehicle")

    if not args.keep_intermediates:
        for mp4 in session_mp4.values():
            mp4.unlink(missing_ok=True)

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
