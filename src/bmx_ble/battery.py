"""Battery chemistry curves and interpretation of BM2 readings."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from types import MappingProxyType

from .protocol import BM2Reading


class BatteryConfigurationError(ValueError):
    """Invalid custom battery voltage thresholds."""


class Battery(StrEnum):
    """Pre-defined battery chemistries."""

    automatic = "automatic"
    agm = "agm"
    deepcycle = "deepcycle"
    leadacid = "leadacid"
    lifepo4 = "lifepo4"
    lithiumion = "lithiumion"
    itech120x = "itech120x"
    custom = "custom"


@dataclass(frozen=True)
class BatteryProfile:
    """Immutable battery curve and voltage thresholds."""

    battery_chemistry: str
    volts_to_percent: tuple[float, ...]
    percentages: tuple[int, ...]
    critical_voltage: float
    low_voltage: float
    floating_voltage: float
    charging_voltage: float


@dataclass(frozen=True)
class BatteryReading:
    """A chemistry-interpreted reading, ready for a consumer to publish.

    Missing fields remain None so consumers can preserve previous readings.
    A missing charging flag likewise means that no new status was available.
    """

    battery_chemistry: str
    voltage: float | None
    percentage: int | None
    status: str | None
    charging: bool | None


BATTERY_PROFILES: Mapping[Battery, BatteryProfile] = MappingProxyType(
    {
        Battery.automatic: BatteryProfile("Automatic", (), (), 0.0, 0.0, 0.0, 0.0),
        Battery.agm: BatteryProfile(
            "AGM",
            (10.5, 11.51, 11.66, 11.81, 11.95, 12.05, 12.15, 12.3, 12.5, 12.75, 12.85),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            11.66,
            11.81,
            13.2,
            14.2,
        ),
        Battery.deepcycle: BatteryProfile(
            "Deep-cycle",
            (10.5, 11.51, 11.66, 11.81, 11.95, 12.05, 12.15, 12.3, 12.5, 12.75, 12.8),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            11.66,
            12.05,
            13.6,
            14.4,
        ),
        Battery.leadacid: BatteryProfile(
            "Lead-acid",
            (10.5, 11.31, 11.58, 11.75, 11.9, 12.06, 12.2, 12.32, 12.42, 12.5, 12.7),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            12.06,
            12.2,
            13.7,
            14.5,
        ),
        Battery.lifepo4: BatteryProfile(
            "LiFePO4",
            (10.0, 12.0, 12.5, 12.8, 12.9, 13.0, 13.1, 13.2, 13.3, 13.4, 13.6),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            10.5,
            12.0,
            13.5,
            14.4,
        ),
        Battery.lithiumion: BatteryProfile(
            "Lithium-ion",
            (10.0, 12.0, 12.8, 12.9, 13.0, 13.05, 13.1, 13.2, 13.3, 13.4, 13.6),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            10.5,
            12.0,
            13.5,
            14.25,
        ),
        Battery.itech120x: BatteryProfile(
            "iTechworld 120X (LiFePO4)",
            (9.5, 10.5, 12.5, 12.7, 12.8, 12.89, 12.91, 12.99, 13.01, 13.1, 13.5),
            (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            10.5,
            12.5,
            13.5,
            14.35,
        ),
    }
)

_CHEMISTRY_OPTIONS: Mapping[str, Battery] = MappingProxyType(
    {
        "Automatic": Battery.automatic,
        "Automatic (via BM2)": Battery.automatic,
        "AGM": Battery.agm,
        "Deep-cycle": Battery.deepcycle,
        "Lead-acid": Battery.leadacid,
        "LiFePO4": Battery.lifepo4,
        "LifePO4": Battery.lifepo4,
        "Lithium-ion": Battery.lithiumion,
        "iTechworld 120X (LiFePO4)": Battery.itech120x,
        "itech120x": Battery.itech120x,
    }
)

_STATUS_NAMES: Mapping[int, str] = MappingProxyType(
    {
        0: "critical",
        1: "low",
        2: "normal",
        4: "charging",
        8: "floating",
    }
)


def get_battery_profile(chemistry: str) -> BatteryProfile:
    """Return a predefined profile, accepting existing chemistry labels."""
    return BATTERY_PROFILES[_CHEMISTRY_OPTIONS[chemistry]]


def custom_battery_profile(
    *,
    battery_chemistry: str,
    critical_voltage: float,
    low_voltage: float,
    fifty_percent_voltage: float,
    hundred_percent_voltage: float,
    floating_voltage: float,
    charging_voltage: float,
) -> BatteryProfile:
    """Build a custom profile with six finite, strictly increasing thresholds."""
    thresholds = (
        critical_voltage,
        low_voltage,
        fifty_percent_voltage,
        hundred_percent_voltage,
        floating_voltage,
        charging_voltage,
    )
    if not all(isfinite(value) for value in thresholds) or not all(
        lower < upper for lower, upper in pairwise(thresholds)
    ):
        raise BatteryConfigurationError(
            "Custom battery voltages must be finite and strictly increasing"
        )
    return BatteryProfile(
        battery_chemistry=battery_chemistry,
        volts_to_percent=thresholds[:4],
        percentages=(0, 20, 50, 100),
        critical_voltage=critical_voltage,
        low_voltage=low_voltage,
        floating_voltage=floating_voltage,
        charging_voltage=charging_voltage,
    )


def percentage_from_voltage(profile: BatteryProfile, voltage: float) -> int:
    """Interpolate percentage, truncate to an integer and clamp at endpoints."""
    volts = profile.volts_to_percent
    percentages = profile.percentages
    if not volts or len(volts) != len(percentages):
        raise BatteryConfigurationError(
            "A matching voltage/percentage curve is required"
        )
    if not isfinite(voltage):
        raise ValueError("Voltage must be finite")
    if voltage <= volts[0]:
        return percentages[0]
    for index, upper in enumerate(volts[1:], start=1):
        if voltage == upper:
            return percentages[index]
        if voltage < upper:
            lower = volts[index - 1]
            lower_percent = percentages[index - 1]
            upper_percent = percentages[index]
            # Use the same slope-first evaluation as numpy.interp.
            slope = (upper_percent - lower_percent) / (upper - lower)
            return int(lower_percent + slope * (voltage - lower))
    return percentages[-1]


def status_from_voltage(profile: BatteryProfile, voltage: float) -> int:
    """Return the BM2 status code derived from configured voltage thresholds."""
    if not isfinite(voltage):
        raise ValueError("Voltage must be finite")
    if voltage >= profile.charging_voltage:
        return 4
    if voltage >= profile.floating_voltage:
        return 8
    if voltage <= profile.critical_voltage:
        return 0
    if voltage <= profile.low_voltage:
        return 1
    return 2


def interpret_reading(reading: BM2Reading, profile: BatteryProfile) -> BatteryReading:
    """Use monitor values in automatic mode, otherwise derive them from voltage."""
    percentage = reading.percentage
    status = reading.status
    if (
        reading.voltage is not None
        and percentage is not None
        and profile.volts_to_percent
    ):
        percentage = percentage_from_voltage(profile, reading.voltage)
        status = status_from_voltage(profile, reading.voltage)
    return BatteryReading(
        battery_chemistry=profile.battery_chemistry,
        voltage=reading.voltage,
        percentage=percentage,
        status=_STATUS_NAMES.get(status, "unknown") if status is not None else None,
        charging=status in (4, 8) if status is not None else None,
    )
