"""Unit tests for persistent dmesg filters and Pi rail sample parsing."""

from __future__ import annotations

from doris.services import persistent_log as pl


SAMPLE_HOST_OK = """\
EXT5V=EXT5V_V volt(24)=5.01400000V
HDMI=HDMI_V volt(23)=4.94100000V
VDD_CORE=VDD_CORE_V volt(1)=0.72000000V
3V3_SYS=3V3_SYS_V volt(21)=3.30600000V
THROTTLED=throttled=0x0
HWMON
pmic ext_5v 5014000
pmic 3v3_sys 3306000
"""

SAMPLE_HOST_SAG = """\
EXT5V=EXT5V_V volt(24)=4.18000000V
HDMI=HDMI_V volt(23)=4.10000000V
VDD_CORE=VDD_CORE_V volt(1)=0.72000000V
3V3_SYS=3V3_SYS_V volt(21)=3.20000000V
THROTTLED=throttled=0x50005
HWMON
"""

SAMPLE_MAILBOX_FAIL = """\
EXT5V=EXT5V_V volt(24)=5.01000000V
HDMI=HDMI_V volt(23)=4.94000000V
VDD_CORE=VDD_CORE_V volt(1)=0.72000000V
3V3_SYS=3V3_SYS_V volt(21)=3.30000000V
THROTTLED=throttled=0x80000001
HWMON
"""


def test_dmesg_keeps_undervoltage_and_pmic_lines() -> None:
    assert pl._dmesg_line_matches("Under-voltage detected! (0x00050000)")
    assert pl._dmesg_line_matches("hwmon: Undervoltage")
    assert pl._dmesg_line_matches("pmic: UVLO on EXT5V")
    assert pl._dmesg_line_matches("Kernel panic - not syncing")
    assert not pl._dmesg_line_matches("random: crng init done")


def test_intervals_tighten_during_dive() -> None:
    assert pl.dmesg_interval_s(False) == pl.DMESG_INTERVAL_S
    assert pl.dmesg_interval_s(True) == pl.DMESG_INTERVAL_DIVE_S
    assert pl.power_interval_s(False) == pl.POWER_INTERVAL_S
    assert pl.power_interval_s(True) == pl.POWER_INTERVAL_DIVE_S
    assert pl.dmesg_interval_s(True) < pl.dmesg_interval_s(False)
    assert pl.power_interval_s(True) < pl.power_interval_s(False)


def test_parse_healthy_pmic_sample() -> None:
    sample = pl.parse_power_host_output(SAMPLE_HOST_OK)
    assert sample["ext5v_v"] == 5.014
    assert sample["hdmi_v"] == 4.941
    assert sample["vdd_core_v"] == 0.72
    assert sample["v3v3_sys_v"] == 3.306
    assert sample["throttled"] == "0x0"
    assert sample["hwmon"] == ["pmic ext_5v 5014000", "pmic 3v3_sys 3306000"]
    assert not pl.power_sample_is_alarm(sample)
    line = pl.format_power_sample(sample)
    assert "EXT5V=5.014" in line
    assert "throttled=0x0" in line
    assert "pmic:ext_5v:5014000" in line


def test_parse_sag_is_alarm() -> None:
    sample = pl.parse_power_host_output(SAMPLE_HOST_SAG)
    assert sample["ext5v_v"] == 4.18
    assert sample["throttled"] == "0x50005"
    assert pl.power_sample_is_alarm(sample)


def test_mailbox_error_is_not_undervolt_alarm() -> None:
    sample = pl.parse_power_host_output(SAMPLE_MAILBOX_FAIL)
    assert sample["throttled"] == "0x80000001"
    assert sample["ext5v_v"] == 5.01
    assert not pl.power_sample_is_alarm(sample)
