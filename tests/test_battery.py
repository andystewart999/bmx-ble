"""Battery models work without Home Assistant or NumPy."""

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest
from bmx_ble.battery import (
    BATTERY_PROFILES,
    Battery,
    BatteryConfigurationError,
    custom_battery_profile,
    get_battery_profile,
    interpret_reading,
    percentage_from_voltage,
    status_from_voltage,
)
from bmx_ble.protocol import BM2Reading

CUSTOM = {
    "battery_chemistry": "Test chemistry",
    "critical_voltage": 11.0,
    "low_voltage": 11.5,
    "fifty_percent_voltage": 12.3,
    "hundred_percent_voltage": 12.8,
    "floating_voltage": 13.5,
    "charging_voltage": 14.4,
}


@pytest.mark.parametrize(
    ("option", "battery"),
    [
        ("Automatic (via BM2)", Battery.automatic),
        ("Automatic", Battery.automatic),
        ("AGM", Battery.agm),
        ("Deep-cycle", Battery.deepcycle),
        ("Lead-acid", Battery.leadacid),
        ("LiFePO4", Battery.lifepo4),
        ("LifePO4", Battery.lifepo4),
        ("Lithium-ion", Battery.lithiumion),
        ("iTechworld 120X (LiFePO4)", Battery.itech120x),
        ("itech120x", Battery.itech120x),
    ],
)
def test_chemistry_selection(option: str, battery: Battery) -> None:
    """Current labels and historical aliases select the intended profile."""
    assert get_battery_profile(option) is BATTERY_PROFILES[battery]


def test_unknown_chemistry() -> None:
    """An unsupported label must not silently select another battery."""
    with pytest.raises(KeyError):
        get_battery_profile("Unsupported")


@pytest.mark.parametrize(
    ("battery", "midpoint"),
    [
        (Battery.agm, 12.05),
        (Battery.deepcycle, 12.05),
        (Battery.leadacid, 12.06),
        (Battery.lifepo4, 13.0),
        (Battery.lithiumion, 13.05),
        (Battery.itech120x, 12.89),
    ],
)
def test_predefined_curve(battery: Battery, midpoint: float) -> None:
    """Every curve preserves endpoints, knots and its established midpoint."""
    profile = BATTERY_PROFILES[battery]
    assert percentage_from_voltage(profile, midpoint) == 50
    assert percentage_from_voltage(profile, 0.0) == 0
    assert percentage_from_voltage(profile, 30.0) == 100
    for voltage, expected in zip(
        profile.volts_to_percent, profile.percentages, strict=True
    ):
        assert percentage_from_voltage(profile, voltage) == expected


@pytest.mark.parametrize(
    ("voltage", "expected"),
    [(9.0, 0), (12.06, 50), (12.13, 55), (12.137, 55), (16.0, 100)],
)
def test_percentage_interpolation(voltage: float, expected: int) -> None:
    """Interpolate linearly, truncate fractional results and clamp endpoints."""
    assert (
        percentage_from_voltage(get_battery_profile("Lead-acid"), voltage) == expected
    )


@pytest.mark.parametrize(
    ("voltage", "expected"),
    [(14.5, 4), (13.7, 8), (12.06, 0), (12.2, 1), (12.4, 2)],
)
def test_status_thresholds(voltage: float, expected: int) -> None:
    """Charging, floating, critical and low thresholds include their boundary."""
    assert status_from_voltage(get_battery_profile("Lead-acid"), voltage) == expected


def test_custom_profile() -> None:
    """Four custom percentage points use the 0, 20, 50 and 100 curve."""
    profile = custom_battery_profile(**CUSTOM)
    assert profile.battery_chemistry == "Test chemistry"
    assert profile.volts_to_percent == (11.0, 11.5, 12.3, 12.8)
    assert profile.percentages == (0, 20, 50, 100)
    assert percentage_from_voltage(profile, 12.3) == 50
    assert percentage_from_voltage(profile, 12.55) == 75
    assert status_from_voltage(profile, 13.5) == 8
    assert custom_battery_profile(**CUSTOM) is not profile


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("low_voltage", 11.0),
        ("fifty_percent_voltage", 11.5),
        ("hundred_percent_voltage", 12.3),
        ("floating_voltage", 12.8),
        ("charging_voltage", 13.5),
        ("critical_voltage", nan),
        ("fifty_percent_voltage", inf),
        ("charging_voltage", -inf),
    ],
)
def test_invalid_custom_thresholds(field: str, value: float) -> None:
    """Equal, reversed and non-finite thresholds fail with a specific error."""
    with pytest.raises(BatteryConfigurationError):
        custom_battery_profile(**(CUSTOM | {field: value}))


def test_profiles_are_immutable() -> None:
    """Consumers cannot modify a shared chemistry profile or its curve."""
    profile = get_battery_profile("AGM")
    with pytest.raises(FrozenInstanceError):
        profile.low_voltage = 99
    with pytest.raises(TypeError):
        BATTERY_PROFILES[Battery.agm] = profile
    assert isinstance(profile.volts_to_percent, tuple)


@pytest.mark.parametrize(
    ("code", "status", "charging"),
    [
        (0, "critical", False),
        (1, "low", False),
        (2, "normal", False),
        (4, "charging", True),
        (8, "floating", True),
        (99, "unknown", False),
        (None, None, None),
    ],
)
def test_automatic_reading(
    code: int | None, status: str | None, charging: bool | None
) -> None:
    """Automatic mode retains the monitor percentage and recognizes known codes."""
    raw = BM2Reading(12.5, 67, code, "active")
    interpreted = interpret_reading(raw, get_battery_profile("Automatic (via BM2)"))
    assert interpreted.battery_chemistry == "Automatic"
    assert interpreted.voltage == 12.5
    assert interpreted.percentage == 67
    assert interpreted.status == status
    assert interpreted.charging is charging
    assert raw.percentage == 67
    assert raw.status == code


@pytest.mark.parametrize("chemistry", ["Automatic", "Lead-acid"])
def test_partial_reading(chemistry: str) -> None:
    """Legacy percentage-only packets keep voltage and status missing."""
    result = interpret_reading(
        BM2Reading(None, 45, None, "advertisement"), get_battery_profile(chemistry)
    )
    assert result.percentage == 45
    assert result.voltage is None
    assert result.status is None
    assert result.charging is None


def test_voltage_without_percentage_preserves_partial_reading() -> None:
    """Keep the existing behaviour when a reading lacks percentage."""
    result = interpret_reading(
        BM2Reading(12.5, None, None, "advertisement"), get_battery_profile("Lead-acid")
    )
    assert result.voltage == 12.5
    assert result.percentage is None
    assert result.status is None


def test_chemistry_interpretation() -> None:
    """A selected chemistry replaces raw percentage and fills missing status."""
    result = interpret_reading(
        BM2Reading(12.06, 99, None, "advertisement"), get_battery_profile("Lead-acid")
    )
    assert result.percentage == 50
    assert result.status == "critical"
    assert result.charging is False


def test_custom_name_does_not_enable_automatic_mode() -> None:
    """A custom profile named Automatic still uses the configured curve."""
    profile = custom_battery_profile(**(CUSTOM | {"battery_chemistry": "Automatic"}))
    result = interpret_reading(BM2Reading(12.3, 99, None, "advertisement"), profile)
    assert result.percentage == 50
    assert result.status == "normal"


@pytest.mark.parametrize("voltage", [nan, inf, -inf])
def test_invalid_voltage(voltage: float) -> None:
    """Calculations reject non-finite voltage instead of inventing a status."""
    profile = get_battery_profile("Lead-acid")
    with pytest.raises(ValueError, match="finite"):
        percentage_from_voltage(profile, voltage)
    with pytest.raises(ValueError, match="finite"):
        status_from_voltage(profile, voltage)


def test_automatic_profile_has_no_percentage_curve() -> None:
    """Automatic mode requires the monitor percentage, not interpolation."""
    with pytest.raises(BatteryConfigurationError, match="curve"):
        percentage_from_voltage(get_battery_profile("Automatic"), 12.5)
