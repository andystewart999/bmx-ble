# Changelog

## 0.2.0

- Add immutable battery chemistry profiles and custom profile validation.
- Add percentage interpolation and voltage-based status derivation in pure Python.
- Add interpretation of raw BM2 readings, retaining partial advertisement data.
- Treat only known charging and floating status codes as charging.
- Include a PEP 561 marker for consumers using type checking.
- Preserve the existing protocol API and established predefined curves.
