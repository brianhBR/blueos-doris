"""Service for Artemis SVL firmware flashing."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import serial.tools.list_ports as list_ports

from ..models.artemis import SerialPortInfo

logger = logging.getLogger(__name__)

FIRMWARE_UPLOAD_DIR = Path("/tmp/artemis_firmware")
ARTEMIS_SVL_PATH = "/usr/bin/artemis_svl.py"

# If an older SVL script exits 0 on failure, treat these log lines as failure.
_ARTEMIS_FAILURE_MARKERS = (
    "target failed to enter bootload",
    "target entered bootloader mode but firmware upload failed",
    "upload failed",
    "failed to enter bootload phase",
)


@dataclass
class FlashSession:
    """Tracks the state of an in-progress (or completed) flash operation."""

    session_id: str
    port: str
    firmware_path: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    success: bool = False
    error: str | None = None


class ArtemisService:
    """Manages serial port discovery and Artemis firmware flashing."""

    def __init__(self) -> None:
        self._sessions: dict[str, FlashSession] = {}

    def list_serial_ports(self) -> list[SerialPortInfo]:
        ports = list_ports.comports()
        return [
            SerialPortInfo(device=p.device, description=p.description, hwid=p.hwid)
            for p in sorted(ports, key=lambda p: p.device)
        ]

    def save_firmware(self, filename: str, data: bytes) -> tuple[str, int]:
        """Save uploaded firmware binary and return (path, size_bytes)."""
        FIRMWARE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name
        dest = FIRMWARE_UPLOAD_DIR / safe_name
        dest.write_bytes(data)
        return str(dest), len(data)

    def get_session(self, session_id: str) -> FlashSession | None:
        return self._sessions.get(session_id)

    def start_flash(
        self,
        port: str,
        firmware_path: str,
        baud: int = 115200,
        timeout: float = 0.5,
    ) -> str:
        """Start a flash operation in a background task. Returns the session_id."""
        session_id = uuid.uuid4().hex[:12]
        session = FlashSession(
            session_id=session_id,
            port=port,
            firmware_path=firmware_path,
        )
        self._sessions[session_id] = session
        asyncio.get_event_loop().create_task(
            self._run_flash(session, baud, timeout)
        )
        return session_id

    async def _run_flash(
        self,
        session: FlashSession,
        baud: int,
        timeout: float,
    ) -> None:
        cmd = [
            "python", ARTEMIS_SVL_PATH,
            session.port,
            "-f", session.firmware_path,
            "-b", str(baud),
            "-t", str(timeout),
            "-v",
        ]

        logger.info("Launching Artemis SVL: %s", " ".join(cmd))
        session.lines.append("Starting flash...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip("\n\r")
                if text:
                    logger.info("Artemis [%s]: %s", session.session_id, text)
                    session.lines.append(text)

            exit_code = await proc.wait()
            output_lower = "\n".join(session.lines).lower()
            failed_by_log = any(m in output_lower for m in _ARTEMIS_FAILURE_MARKERS)
            session.success = exit_code == 0 and not failed_by_log
            session.done = True

            result_msg = "Upload Successful" if session.success else "Upload Failed"
            session.lines.append(result_msg)
            logger.info("Flash %s: %s", session.session_id, result_msg)
        except Exception as e:
            logger.exception("Flash error for session %s", session.session_id)
            session.error = str(e)
            session.lines.append(f"Error: {e}")
            session.done = True
