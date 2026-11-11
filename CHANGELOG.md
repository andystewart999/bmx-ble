# Changelog

## 0.4.1

- Add `bmx_ble.battery.BatteryStatus`, a `StrEnum` of the interpreted statuses. `BatteryReading.status` is now a `BatteryStatus` (or `None`); members still compare equal to the previous string values.

## 0.4.0

- Breaking: remove the advertisement reading cache and the passive fallback in `async_poll()`.
- Breaking: `async_poll()` requires a `BLEDevice` and returns the GATT reading or raises.
- `process_advertisement()` returns the reading carried by that advertisement, or `None`.
- Add `bmx_ble.battery.needs_active_read()` to decide when a GATT connection is needed.
- `ignore_advertisement` now covers the whole of `async_poll()`, including connection setup.
- Without raw bytes, decode the Legacy record from merged manufacturer data instead of returning nothing.

## 0.3.4

- Reject malformed FFF4 notifications during active validation instead of raising `ValueError`.

## 0.3.3

- Declare `bluetooth-sensor-state-data` and `sensor-state-data` as runtime dependencies.

## 0.3.2

- Decode current raw manufacturer advertisements instead of merged scanner history.
- Reject ambiguous manufacturer dictionaries when raw bytes are unavailable.
- Expire cached passive readings after 180 seconds.
- Keep detected generation when legacy packets return, without refreshing an old voltage.
- Add regression tests using captured BM2 ciphertext.

## 0.3.0

- Accept stable battery identifiers and Battery enum members in get_battery_profile().
- Replace display-label lookup with stable identifiers; chemistry labels are no longer accepted as configuration values.
- Preserve battery curves and protocol APIs.

## 0.2.0

- Add immutable battery chemistry profiles and custom profile validation.
- Add percentage interpolation and voltage-based status derivation in pure Python.
- Add interpretation of raw BM2 readings, retaining partial advertisement data.
- Treat only known charging and floating status codes as charging.
- Include a PEP 561 marker for consumers using type checking.
- Preserve the existing protocol API and established predefined curves.
