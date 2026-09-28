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
