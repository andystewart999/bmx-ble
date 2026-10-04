# bmx-ble

Python library for BMx battery monitor BLE advertisements and GATT notifications.
The initial release supports BM2; the package name allows additional BM models
to be supported later.

It decodes legacy percentage advertisements and encrypted enhanced voltage and
percentage advertisements, validates the BM2 notification characteristic, and
tries an active GATT reading before falling back to cached advertisement data.

## Installation

```bash
python -m pip install bmx-ble
```

## Use

```python
from bmx_ble import BM2Protocol

monitor = BM2Protocol()
monitor.process_advertisement(service_info.manufacturer_data)
reading = await monitor.async_poll(ble_device)  # None allows passive fallback.
print(reading.voltage, reading.percentage, reading.generation)
```

Supply a `bleak.backends.device.BLEDevice` for active reading. Connection
failures propagate when no usable advertisement has been cached.

## Development

```bash
python -m pip install -e '.[test]'
python -m pytest
python -m build
```

The Home Assistant integration is kept separately from this library.

## Publishing

Create a public repository with this source and an enabled issue tracker.
Set up PyPI Trusted Publishing for the repository's release workflow, tag a
release matching the version in `pyproject.toml`, then run the publish workflow.
Confirm the project name is available on PyPI before the first release.

## License

MIT; see `LICENSE`.

## Battery chemistry interpretation (0.2.0)

Protocol readings remain available as `BM2Reading`. Battery calculations live
in `bmx_ble.battery` and have no NumPy or Home Assistant dependency:

```python
from bmx_ble.battery import Battery, get_battery_profile, interpret_reading

profile = get_battery_profile(Battery.leadacid)
raw_reading = await monitor.async_poll(ble_device)
reading = interpret_reading(raw_reading, profile)
print(reading.voltage, reading.percentage, reading.status)
```

From 0.3.0, `get_battery_profile()` accepts `Battery` enum members and their stable
string values, such as `"automatic"`, `"leadacid"` and `"lifepo4"`. Store these
identifiers in configuration; display labels can change independently. Labels
such as `"Lead-acid"` are not accepted as identifiers. Custom batteries use
`custom_battery_profile()` and explicit thresholds.
Profiles and their curves are immutable. `Automatic (via BM2)` preserves the
monitor's reported percentage and status instead of applying a voltage curve.
Unknown status codes produce `"unknown"` and do not imply charging. Status codes
4 (charging) and 8 (floating) set the interpreted charging flag.

Use `custom_battery_profile()` for a custom curve:

```python
from bmx_ble.battery import custom_battery_profile

profile = custom_battery_profile(
    battery_chemistry="My battery",
    critical_voltage=11.0,
    low_voltage=11.5,
    fifty_percent_voltage=12.3,
    hundred_percent_voltage=12.8,
    floating_voltage=13.5,
    charging_voltage=14.4,
)
```

All six custom thresholds must be finite and strictly increasing; invalid
profiles raise `BatteryConfigurationError`. The four discharge points map to
0, 20, 50 and 100 percent. Linear interpolation clamps at the endpoints and
truncates fractional percentages, matching the integration's previous behaviour.

`interpret_reading()` returns `BatteryReading` with `battery_chemistry`,
`voltage`, `percentage`, `status` and `charging`. Missing protocol values remain
missing so consumers can preserve their last readings. In automatic mode,
advertisement readings without a status do not invent one. For a configured
curve, a reading with both voltage and percentage gets a calculated percentage
and status. The input `BM2Reading` is not modified.

The original protocol API is unchanged in this release.
