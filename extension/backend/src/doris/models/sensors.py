"""Sensor and module models."""

from datetime import datetime

from pydantic import BaseModel


class ModuleInfo(BaseModel):
    """Information about a connected module."""

    id: str
    name: str
    type: str  # Module type (camera, sensor, light)
    status: str  # Connection status for frontend (connected/disconnected/error)
    module_status: str  # Human-readable status message
    last_reading: str | None = None  # Last reading timestamp
    power_usage: float = 0.0  # percentage
    sample_rate: float | None = None  # Hz, for sensors
    firmware_version: str | None = None


class VideoStream(BaseModel):
    """A video stream from the Camera Manager."""

    id: str
    name: str
    running: bool
    error: str | None = None
    encode: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    endpoints: list[str] = []
    source_type: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None


class SensorReading(BaseModel):
    """A sensor data reading."""

    sensor_id: str
    sensor_name: str
    value: float
    unit: str
    timestamp: datetime
    quality: float = 1.0  # 0-1, data quality indicator


class SensorConfig(BaseModel):
    """Configuration for a sensor."""

    sensor_id: str
    sample_rate: float  # Hz
    enabled: bool = True
    calibration_file: str | None = None


class ConductivityReading(BaseModel):
    """A single AD5933 conductivity-probe measurement.

    ``raw_conductance_ms`` mirrors the CProbe sketch's ``RawConductance``
    (gain * |Z⁻¹| magnitude * 1000, in millimhos / mS).  ``conductivity_uscm``
    is the optional linear-calibrated value (µS/cm) when CB/CC are set.
    """

    raw_conductance_ms: float
    magnitude: float
    real: int
    imag: int
    conductivity_uscm: float | None = None
    valid: bool = True
    timestamp: datetime


class ConductivityCalibration(BaseModel):
    """Per-probe calibration read from the cell's EEPROM (GetCoeffs()).

    ``gain`` / ``cal_cb`` / ``cal_cc`` are the raw little-endian floats
    stored at EEPROM offsets 12 / 4 / 8.  The ``suggested_*`` fields apply
    the sketch's serial-number special cases (serNo 9 -> gain*4.69, range 4;
    serNo 4/7 -> 47500 Hz, range 2) so the operator can copy them straight
    into the DORIS_CONDUCTIVITY_* env vars.
    """

    serial_number: int
    gain: float
    cal_cb: float
    cal_cc: float
    suggested_gain: float
    suggested_frequency_hz: float
    suggested_range: int

