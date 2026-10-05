# Changelog

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
