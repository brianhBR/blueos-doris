"""Tests for the AD5933 conductivity-probe math and register sequencing."""

import math

from doris.services.conductivity import (
    _CLOCK_SPEED,
    AD5933,
    _frequency_code,
    _to_signed16,
    compute_reading,
)


def test_to_signed16():
    """16-bit register pairs are interpreted as two's-complement."""
    assert _to_signed16(0x0000) == 0
    assert _to_signed16(0x7FFF) == 32767
    assert _to_signed16(0x8000) == -32768
    assert _to_signed16(0xFF00) == -256
    assert _to_signed16(0xFFFF) == -1


def test_frequency_code_roundtrip():
    """The 3 frequency-code bytes reassemble to the datasheet 24-bit value."""
    freq = 70000.0
    expected = int((freq / (_CLOCK_SPEED / 4.0)) * (2**27))
    hi = _frequency_code(freq, 0)
    mid = _frequency_code(freq, 1)
    lo = _frequency_code(freq, 2)
    assert (hi << 16) | (mid << 8) | lo == expected & 0xFFFFFF
    for b in (hi, mid, lo):
        assert 0 <= b <= 0xFF


def test_compute_reading_raw_conductance():
    """RawConductance = gain * magnitude * 1000 (mS); sign-independent magnitude."""
    real, imag = -256, 256
    magnitude = math.sqrt(real**2 + imag**2)
    reading = compute_reading(real, imag, gain=2.0)
    assert reading.magnitude == magnitude
    assert reading.raw_conductance_ms == 2.0 * magnitude * 1000.0
    assert reading.conductivity_uscm is None
    assert reading.valid is True


def test_compute_reading_linear_cal():
    """Linear cal applies conductivity_uscm = 1000*(CB*raw + CC)."""
    reading = compute_reading(
        100, 0, gain=1.0, apply_linear_cal=True, cal_cb=0.5, cal_cc=0.1
    )
    raw = 1.0 * 100.0 * 1000.0
    assert reading.conductivity_uscm == 1000.0 * (0.5 * raw + 0.1)


class _FakeBus:
    """In-memory AD5933 that mimics the address-pointer read protocol."""

    def __init__(self, registers):
        self.registers = dict(registers)
        self._ptr = 0
        self.writes = []

    def write_byte_data(self, addr, reg, value):
        if reg == 0xB0:  # ADDRESS_PTR
            self._ptr = value
        else:
            self.registers[reg] = value
            self.writes.append((reg, value))

    def read_byte(self, addr):
        return self.registers.get(self._ptr, 0)


def test_ad5933_measure_reads_signed_registers():
    """measure() returns the signed real/imag from the data registers."""
    bus = _FakeBus(
        {
            0x8F: 0x02,  # status: valid impedance data
            0x94: 0xFF,
            0x95: 0x00,  # real = 0xFF00 -> -256
            0x96: 0x01,
            0x97: 0x00,  # imag = 0x0100 -> 256
        }
    )
    chip = AD5933(bus, 0x0D)
    real, imag = chip.measure(frequency_hz=70000.0, range_setting=3, settling_cycles=128)
    assert real == -256
    assert imag == 256
