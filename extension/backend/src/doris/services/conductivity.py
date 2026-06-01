"""External conductivity probe (AD5933) service.

Reads a Blue Robotics-style "CProbe" conductivity cell over the
Navigator's i2c6 bus and publishes the result as a NAMED_VALUE_FLOAT
into the MAVLink stream via mavlink2rest.  This is a direct port of the
``measureImpedance()`` sweep from the Arduino ``CTDController09.ino``
sketch onto the Raspberry Pi (the DORIS container already has raw
``/dev/i2c-*`` access via Privileged + ``/dev:/dev``), so no custom
ArduSub firmware/driver is required.

Bus sharing: i2c6 is just ``/dev/i2c-6``.  ArduSub polls the Bar100
(Keller LD @ 0x40) and Celsius (TSYS01 @ 0x77) on it; the Linux i2c
core serializes every transaction, and the AD5933 answers only to its
own address (0x0D), so the probe coexists with ArduSub's sensors.

Calibration: the per-probe ``gain`` (and optional linear CB/CC) are
read once on the bench and stored in DORIS config, so the live bus
never touches the probe's cal EEPROM (@ 0x50, which also collides with
the Navigator's onboard ID EEPROM address).
"""

import asyncio
import logging
import math
import struct
import time
from datetime import UTC, datetime

import httpx

from ..config import blueos_services, settings
from ..models.sensors import ConductivityCalibration, ConductivityReading

logger = logging.getLogger(__name__)

# ── AD5933 register map (datasheet, mirrors the .ino) ───────────────
_CONTROL_REGISTER = (0x80, 0x81)
_START_FREQUENCY_REGISTER = (0x82, 0x83, 0x84)
_FREQ_INCREMENT_REGISTER = (0x85, 0x86, 0x87)
_NUM_INCREMENTS_REGISTER = (0x88, 0x89)
_NUM_SETTLING_CYCLES_REGISTER = (0x8A, 0x8B)
_STATUS_REGISTER = 0x8F
_REAL_DATA_REGISTER = (0x94, 0x95)
_IMAG_DATA_REGISTER = (0x96, 0x97)
_ADDRESS_PTR = 0xB0

# Control-register D15..D12 command codes.
_INITIALIZE = 0b0001
_START_SWEEP = 0b0010
_POWER_DOWN = 0b1010
_STANDBY = 0b1011

# Status register (& 7): bit1 = valid impedance data.
_VALID_IMPEDANCE_DATA = 2

# AD5933 internal clock: 16.776 MHz (matches CLOCK_SPEED in the sketch).
_CLOCK_SPEED = 16.776e6

# Lower-nibble of the control register selects the output voltage range
# + PGA gain.  Values copied verbatim from setControlRegister2().
_RANGE_BITS = {
    1: 0b0001,  # 2 V
    2: 0b0111,  # 1 V
    3: 0b0101,  # 400 mV
    4: 0b0011,  # 200 mV
}

# Bound the status poll so a missing/unpowered probe can't hang the loop.
_SWEEP_POLL_TIMEOUT_S = 2.0
_SWEEP_POLL_INTERVAL_S = 0.005


def _to_signed16(value: int) -> int:
    """Interpret a 16-bit register pair as two's-complement.

    The AVR sketch stored the real/imag pair in a 16-bit ``int``, so the
    sign is significant before squaring (treating it as unsigned would
    blow up the magnitude for negative values).
    """
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _read_eeprom_float(bus, eeprom_addr: int, offset: int) -> float:
    """Read a 4-byte little-endian float from the cal EEPROM.

    Mirrors ``readI2CByte()`` in the sketch: a 1-byte-addressed read of
    four consecutive bytes, reassembled as an IEEE-754 float (the AVR
    ``union`` stored ``float``/``double`` as 4 little-endian bytes).
    """
    raw = bytes(bus.read_byte_data(eeprom_addr, offset + i) for i in range(4))
    return struct.unpack("<f", raw)[0]


def _frequency_code(freq_hz: float, byte_num: int) -> int:
    """24-bit start/increment frequency code byte (datasheet p.14)."""
    value = int((freq_hz / (_CLOCK_SPEED / 4.0)) * (2**27))
    return (value >> (16 - 8 * byte_num)) & 0xFF


class AD5933:
    """Minimal AD5933 single-point impedance reader over smbus2.

    ``bus`` is any object exposing ``write_byte_data(addr, reg, val)`` and
    ``read_byte(addr)`` (i.e. :class:`smbus2.SMBus`), which makes the
    register sequencing trivially unit-testable with a fake bus.
    """

    def __init__(self, bus, addr: int) -> None:
        self._bus = bus
        self._addr = addr

    def _set_byte(self, reg: int, value: int) -> None:
        self._bus.write_byte_data(self._addr, reg, value & 0xFF)

    def _get_byte(self, reg: int) -> int:
        # Point the AD5933 address pointer, then read one byte back.
        self._bus.write_byte_data(self._addr, _ADDRESS_PTR, reg & 0xFF)
        return self._bus.read_byte(self._addr) & 0xFF

    def _check_status(self) -> int:
        return self._get_byte(_STATUS_REGISTER) & 7

    def _set_control_command(self, code: int) -> None:
        # Preserve the lower nibble (range/PGA), set the upper nibble.
        rx = self._get_byte(_CONTROL_REGISTER[0]) & 0x0F
        rx |= code << 4
        self._set_byte(_CONTROL_REGISTER[0], rx)
        time.sleep(0.001)

    def _set_range(self, range_setting: int) -> None:
        rx = self._get_byte(_CONTROL_REGISTER[0]) & 0xF0
        rx |= _RANGE_BITS.get(range_setting, _RANGE_BITS[3])
        self._set_byte(_CONTROL_REGISTER[0], rx)
        time.sleep(0.010)

    def _control_reset(self) -> None:
        # Reset + internal system clock (datasheet p.24).
        self._set_byte(_CONTROL_REGISTER[1], 0x10)

    def _configure(self, settling_cycles: int, start_freq: float) -> None:
        # Settling times (decode bits per datasheet; n<=511 -> decode 0).
        n = min(settling_cycles, 2047)
        if n > 1023:
            decode, n = 3, n // 4
        elif n > 511:
            decode, n = 1, n // 2
        else:
            decode = 0
        self._set_byte(_NUM_SETTLING_CYCLES_REGISTER[0], (n >> 8) + (decode << 1))
        self._set_byte(_NUM_SETTLING_CYCLES_REGISTER[1], n & 0xFF)
        # Start frequency (24-bit).
        for i in range(3):
            self._set_byte(_START_FREQUENCY_REGISTER[i], _frequency_code(start_freq, i))
        # Frequency increment 100 Hz, 0 increments (single point).
        for i in range(3):
            self._set_byte(_FREQ_INCREMENT_REGISTER[i], _frequency_code(100.0, i))
        self._set_byte(_NUM_INCREMENTS_REGISTER[0], 0)
        self._set_byte(_NUM_INCREMENTS_REGISTER[1], 0)

    def measure(self, frequency_hz: float, range_setting: int, settling_cycles: int) -> tuple[int, int]:
        """Run one impedance point; return signed (real, imag).

        Raises :class:`TimeoutError` if the DFT never reports valid data
        (probe missing/unpowered).
        """
        self._control_reset()
        self._configure(settling_cycles, frequency_hz)
        self._set_range(range_setting)
        self._set_control_command(_STANDBY)
        self._set_control_command(_INITIALIZE)
        time.sleep(0.015)  # >= 10 ms settle per datasheet
        self._set_control_command(_START_SWEEP)

        deadline = time.monotonic() + _SWEEP_POLL_TIMEOUT_S
        while self._check_status() < _VALID_IMPEDANCE_DATA:
            if time.monotonic() > deadline:
                self._set_control_command(_POWER_DOWN)
                raise TimeoutError("AD5933 DFT did not complete (probe present?)")
            time.sleep(_SWEEP_POLL_INTERVAL_S)

        real = _to_signed16(
            (self._get_byte(_REAL_DATA_REGISTER[0]) << 8) | self._get_byte(_REAL_DATA_REGISTER[1])
        )
        imag = _to_signed16(
            (self._get_byte(_IMAG_DATA_REGISTER[0]) << 8) | self._get_byte(_IMAG_DATA_REGISTER[1])
        )
        self._set_control_command(_POWER_DOWN)
        return real, imag


def compute_reading(
    real: int,
    imag: int,
    gain: float,
    apply_linear_cal: bool = False,
    cal_cb: float = 0.0,
    cal_cc: float = 0.0,
) -> ConductivityReading:
    """Turn a raw (real, imag) pair into a :class:`ConductivityReading`.

    Mirrors the sketch: ``RawConductance = gain * magnitude * 1000`` (mS).
    """
    magnitude = math.sqrt(float(real) ** 2 + float(imag) ** 2)
    raw_conductance_ms = gain * magnitude * 1000.0
    conductivity_uscm = None
    if apply_linear_cal:
        conductivity_uscm = 1000.0 * (cal_cb * raw_conductance_ms + cal_cc)
    return ConductivityReading(
        raw_conductance_ms=raw_conductance_ms,
        magnitude=magnitude,
        real=real,
        imag=imag,
        conductivity_uscm=conductivity_uscm,
        valid=True,
        timestamp=datetime.now(UTC),
    )


class ConductivityService:
    """Polls the AD5933 probe and publishes NAMED_VALUE_FLOAT telemetry."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._boot_ms = time.monotonic()
        self.latest: ConductivityReading | None = None
        self.last_error: str | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    @property
    def enabled(self) -> bool:
        return settings.conductivity_enabled

    def _measure_once(self) -> ConductivityReading:
        """Blocking I2C transaction; intended for ``asyncio.to_thread``."""
        from smbus2 import SMBus  # imported lazily so dev hosts without it still load

        bus = SMBus(settings.conductivity_i2c_bus)
        try:
            chip = AD5933(bus, settings.conductivity_ad5933_addr)
            real, imag = chip.measure(
                settings.conductivity_frequency_hz,
                settings.conductivity_range,
                settings.conductivity_settling_cycles,
            )
        finally:
            bus.close()
        return compute_reading(
            real,
            imag,
            settings.conductivity_gain,
            settings.conductivity_apply_linear_cal,
            settings.conductivity_cal_cb,
            settings.conductivity_cal_cc,
        )

    async def read_once(self) -> ConductivityReading:
        """Take a single measurement (used by the one-shot API route)."""
        reading = await asyncio.to_thread(self._measure_once)
        self.latest = reading
        self.last_error = None
        return reading

    def _read_calibration_blocking(self) -> ConductivityCalibration:
        """Blocking EEPROM read of the probe's calibration coefficients."""
        from smbus2 import SMBus

        bus = SMBus(settings.conductivity_i2c_bus)
        try:
            eep = settings.conductivity_eeprom_addr
            serial_f = _read_eeprom_float(bus, eep, 0)
            cal_cb = _read_eeprom_float(bus, eep, 4)
            cal_cc = _read_eeprom_float(bus, eep, 8)
            gain = _read_eeprom_float(bus, eep, 12)
        finally:
            bus.close()

        serial_number = int(round(serial_f)) if math.isfinite(serial_f) else 0

        # Mirror the sketch's serial-number special cases.
        suggested_gain = gain
        suggested_freq = 70000.0
        suggested_range = 3
        if serial_number == 9:
            suggested_gain = 4.69 * gain
            suggested_range = 4
        if serial_number in (4, 7):
            suggested_freq = 47500.0
            suggested_range = 2

        return ConductivityCalibration(
            serial_number=serial_number,
            gain=gain,
            cal_cb=cal_cb,
            cal_cc=cal_cc,
            suggested_gain=suggested_gain,
            suggested_frequency_hz=suggested_freq,
            suggested_range=suggested_range,
        )

    async def read_calibration(self) -> ConductivityCalibration:
        """Read the probe's gain/CB/CC from its EEPROM (bench-only helper).

        This is the only path that touches the cal EEPROM (@ 0x50).  Run it
        once on the bench, then copy ``suggested_*`` into the DORIS config so
        the live loop never has to access 0x50.
        """
        return await asyncio.to_thread(self._read_calibration_blocking)

    async def _publish(self, reading: ConductivityReading) -> None:
        """Inject the reading as NAMED_VALUE_FLOAT via mavlink2rest."""
        value = (
            reading.conductivity_uscm
            if reading.conductivity_uscm is not None
            else reading.raw_conductance_ms
        )
        time_boot_ms = int((time.monotonic() - self._boot_ms) * 1000.0)
        payload = {
            "header": {
                "system_id": settings.conductivity_src_system,
                "component_id": settings.conductivity_src_component,
                "sequence": 0,
            },
            "message": {
                "type": "NAMED_VALUE_FLOAT",
                "time_boot_ms": time_boot_ms,
                "name": settings.conductivity_named_float[:10],
                "value": float(value),
            },
        }
        resp = await self.client.post(f"{blueos_services.mavlink2rest}/mavlink", json=payload)
        resp.raise_for_status()

    async def _run(self) -> None:
        interval = max(settings.conductivity_publish_interval_s, 0.1)
        logger.info(
            "Conductivity service started (i2c-%d @ 0x%02X, %.0f Hz, range %d, every %.2fs)",
            settings.conductivity_i2c_bus,
            settings.conductivity_ad5933_addr,
            settings.conductivity_frequency_hz,
            settings.conductivity_range,
            interval,
        )
        if settings.conductivity_gain <= 0.0:
            logger.warning(
                "DORIS_CONDUCTIVITY_GAIN is 0; published conductance will be 0. "
                "Set the bench-measured probe gain to get physical values."
            )
        while not self._stop:
            try:
                reading = await asyncio.to_thread(self._measure_once)
                self.latest = reading
                self.last_error = None
                await self._publish(reading)
            except Exception as e:  # noqa: BLE001 - loop must never die
                self.last_error = str(e)
                logger.warning("Conductivity read/publish failed: %s", e)
            await asyncio.sleep(interval)

    def start(self) -> None:
        """Start the background poll loop if enabled and not already running."""
        if not self.enabled:
            logger.info("Conductivity service disabled (DORIS_CONDUCTIVITY_ENABLED=0)")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = False
        self._task = asyncio.get_event_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._client is not None and not self._client.is_closed:
            await self.client.aclose()


# Module-level singleton shared by the poll loop and the API routes.
conductivity_service = ConductivityService()
