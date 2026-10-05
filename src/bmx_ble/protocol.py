"""BM2 BLE advertisements and active GATT protocol (no Home Assistant dependency)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from bleak import BleakError, BLEDevice
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
    retry_bluetooth_connection_error,
)
from Crypto.Cipher import AES

_LOGGER = logging.getLogger(__name__)
GATT_TIMEOUT = 20
ADVERTISEMENT_MAX_AGE = 180
BM2_CHARACTERISTIC = "{0000fff4-0000-1000-8000-00805f9b34fb}"
VALID_BATTERY_STATUSES = frozenset({0, 1, 2, 4, 8})

# The BM2 GATT notification and the newer encrypted manufacturer advertisement
# use the same AES-128-CBC key and a zero IV.
BM2_AES_KEY = bytes(
    [108, 101, 97, 103, 101, 110, 100, 255, 254, 49, 56, 56, 50, 52, 54, 54]
)
BM2_AES_IV = bytes(16)

# The useful manufacturer-data BODY is 14 bytes in Home Assistant.  HA keeps
# the two-byte manufacturer identifier separately as the dict key, so those
# two bytes are prepended before decrypting the resulting 16-byte block.
BM2_ENHANCED_ADVERTISEMENT_PAYLOAD_LENGTH = 14

# Legacy BM2s use an iBeacon-shaped Apple manufacturer record.  Home Assistant
# removes the 0x004C manufacturer ID, leaving a 23-byte body:
#   02 15 + fixed 16-byte UUID + major(2) + minor(2) + percentage(1)
BM2_LEGACY_MANUFACTURER_ID = 0x004C
BM2_LEGACY_PAYLOAD_LENGTH = 23
BM2_LEGACY_PREFIX = bytes.fromhex("0215655f83caae16a10a702e31f30d58dd82")

# Sanity bounds are aligned to what the BM2 itself supports
# 10-15 V range so unusual 12 V battery chemistries/states are not rejected.
BM2_MIN_VALID_VOLTAGE = 6.0
BM2_MAX_VALID_VOLTAGE = 20.0
BM2_MIN_VALID_PERCENTAGE = 0
BM2_MAX_VALID_PERCENTAGE = 100


class BM2Generation(StrEnum):
    """General BM2 protocol generation inferred from advertisement format."""

    UNKNOWN = "Unknown"
    LEGACY = "Legacy (percentage advertisement)"
    ENHANCED = "Enhanced (voltage + percentage advertisement)"


@dataclass(frozen=True)
class BM2Reading:
    """A decoded BM2 reading from a notification or advertisement."""

    voltage: float | None
    percentage: int | None
    status: int | None
    source: str
    generation: BM2Generation = BM2Generation.UNKNOWN


class BM2Protocol:
    """Decode BM2 packets and read FFF4 notifications via Bleak.

    Home Assistant is responsible for scan discovery, poll scheduling and entities.
    """

    def __init__(self) -> None:
        """Create an independent BM2 reader with no cached telemetry."""
        self._gattdata: bytes | None = None
        self._ignore_advertisement = False
        self._advertisement_reading: BM2Reading | None = None
        self._advertisement_time: float | None = None
        self._bm2_generation = BM2Generation.UNKNOWN

    @property
    def bm2_generation(self) -> BM2Generation:
        """Return the most capable advertisement format seen so far."""
        return self._bm2_generation

    @property
    def ignore_advertisement(self) -> bool:
        """Return whether an active notification read is in progress."""
        return self._ignore_advertisement

    def process_advertisement(
        self, manufacturer_data: dict[int, bytes], *, raw: bytes | None = None
    ) -> None:
        """Cache telemetry from the current packet, not merged scanner history."""
        if raw is not None:
            manufacturer_data = self._raw_manufacturer_data(raw)
        elif len(manufacturer_data) > 1:
            # Without raw bytes the merged records have no reliable chronology.
            self._advertisement_reading = None
            self._advertisement_time = None
            return

        reading = self._decode_advertisement(manufacturer_data)
        if reading is None:
            return
        if reading.generation is BM2Generation.ENHANCED:
            self._bm2_generation = BM2Generation.ENHANCED
        elif self._bm2_generation is BM2Generation.UNKNOWN:
            self._bm2_generation = reading.generation
        # A fresh percentage-only packet must not refresh an older voltage.
        self._advertisement_reading = reading
        self._advertisement_time = monotonic()

    @staticmethod
    def _raw_manufacturer_data(raw: bytes) -> dict[int, bytes]:
        """Extract manufacturer records from length-prefixed Bluetooth AD fields."""
        records: dict[int, bytes] = {}
        offset = 0
        while offset < len(raw):
            length = raw[offset]
            if length == 0:
                break
            end = offset + length + 1
            if end > len(raw):
                return {}
            if raw[offset + 1] == 0xFF and length >= 3:
                manufacturer_id = int.from_bytes(raw[offset + 2 : offset + 4], "little")
                # Multiple records with the same ID cannot be represented safely.
                if manufacturer_id in records:
                    return {}
                records[manufacturer_id] = raw[offset + 4 : end]
            offset = end
        return records

    @staticmethod
    def _decrypt(data: bytes) -> bytes:
        """Decrypt one complete 16-byte BM2 AES block."""
        if len(data) != AES.block_size:
            raise ValueError(
                f"BM2 encrypted payload must be {AES.block_size} bytes; "
                f"received {len(data)}"
            )

        cipher = AES.new(BM2_AES_KEY, AES.MODE_CBC, BM2_AES_IV)
        return cipher.decrypt(data)

    # ADVERTISEMENT FALLBACK:
    def _decode_advertisement(
        self,
        manufacturer_data: dict[int, bytes],
    ) -> BM2Reading | None:
        """Decode either known BM2 advertisement generation.

        Enhanced/newer format:
            - any 14-byte manufacturer-data body
            - prepend the two-byte manufacturer ID (little-endian)
            - AES decrypt the resulting 16-byte block
            - decrypted bytes 6-7 = voltage * 100, big-endian
            - decrypted byte 8 = battery percentage

        Legacy/older format:
            - Apple manufacturer ID 0x004C
            - 23-byte iBeacon-shaped body
            - fixed BM2 UUID prefix
            - final byte = battery percentage
            - no voltage is present in the advertisement
        """

        # Prefer the enhanced packet when both formats are advertised.
        for manufacturer_id, payload in manufacturer_data.items():
            if len(payload) != BM2_ENHANCED_ADVERTISEMENT_PAYLOAD_LENGTH:
                continue

            encrypted = manufacturer_id.to_bytes(2, byteorder="little") + payload

            try:
                decrypted = self._decrypt(encrypted)
            except ValueError:
                continue

            voltage = int.from_bytes(decrypted[6:8], byteorder="big") / 100.0
            percentage = decrypted[8]

            # Packet length alone is not enough to identify BM2 telemetry.
            if not BM2_MIN_VALID_VOLTAGE <= voltage <= BM2_MAX_VALID_VOLTAGE:
                continue
            if not (BM2_MIN_VALID_PERCENTAGE <= percentage <= BM2_MAX_VALID_PERCENTAGE):
                continue

            return BM2Reading(
                voltage=voltage,
                percentage=percentage,
                status=None,
                source="advertisement",
                generation=BM2Generation.ENHANCED,
            )

        # Legacy packet: Home Assistant exposes 0x004C as the dict key, so the
        # payload itself begins at the iBeacon 0x02 0x15 marker.
        legacy_payload = manufacturer_data.get(BM2_LEGACY_MANUFACTURER_ID)

        if (
            legacy_payload is not None
            and len(legacy_payload) == BM2_LEGACY_PAYLOAD_LENGTH
            and legacy_payload.startswith(BM2_LEGACY_PREFIX)
        ):
            percentage = legacy_payload[-1]

            if BM2_MIN_VALID_PERCENTAGE <= percentage <= BM2_MAX_VALID_PERCENTAGE:
                return BM2Reading(
                    voltage=None,
                    percentage=percentage,
                    status=None,
                    source="advertisement",
                    generation=BM2Generation.LEGACY,
                )

        return None

    def _decode_gatt(self, data: bytes) -> BM2Reading:
        """Decode the BM2 GATT notification payload."""
        decrypted = self._decrypt(data)

        # Preserve the currently proven GATT byte/nibble mapping from the
        # existing integration:
        #   voltage    = decrypted hex chars [2:5] / 100
        #   status     = decrypted hex char  [5:6]
        #   percentage = decrypted hex chars [6:8]
        raw = decrypted.hex()

        return BM2Reading(
            voltage=int(raw[2:5], 16) / 100.0,
            percentage=int(raw[6:8], 16),
            status=int(raw[5:6], 16),
            source="gatt",
            generation=BM2Generation.UNKNOWN,
        )

    @retry_bluetooth_connection_error()
    async def _get_payload(
        self,
        client: BleakClientWithServiceCache,
    ) -> BM2Reading:
        """Read and decode the active BM2 GATT notification."""
        self._gattdata = None
        self._ignore_advertisement = True

        try:
            await client.start_notify(
                BM2_CHARACTERISTIC,
                self.notification_handler,
            )

            ticks = 0
            while self._gattdata is None and ticks < GATT_TIMEOUT * 4:
                await asyncio.sleep(0.25)
                ticks += 1

        finally:
            # Always attempt to stop notification handling and, importantly,
            # always clear the ignore flag even if Bleak throws.
            try:
                await client.stop_notify(BM2_CHARACTERISTIC)
            finally:
                self._ignore_advertisement = False

        if self._gattdata is None:
            # CHANGED:
            # The previous implementation silently returned here.  Raising
            # makes a no-notification timeout a genuine failed active read,
            # allowing async_poll() to use the cached advertisement.
            raise BleakError(
                f"Timed out waiting for BM2 GATT notification from {client.address}"
            )

        _LOGGER.debug(
            "Successfully read characteristic %s",
            BM2_CHARACTERISTIC,
        )
        return self._decode_gatt(self._gattdata)

    def notification_handler(self, sender, data: bytearray) -> None:
        """Bluetooth notification handler."""
        self._gattdata = bytes(data)

    async def async_validate_active(self, ble_device: BLEDevice) -> bool:
        """Positively validate a BM2 using its active GATT protocol.

        This is intended for config-flow validation only.

        Returns:
            True:
                FFF4 exists, a notification was received, it decrypted with
                the BM2 key, and the decoded values are plausible.

            False:
                The device is positively incompatible (for example FFF4 is
                absent, or a notification decrypts to implausible BM2 data).

        Connection errors and notification timeouts deliberately propagate.
        The config flow treats those as "could not validate" rather than
        incorrectly declaring that the device is not a BM2.
        """
        client: BleakClientWithServiceCache | None = None

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.address,
            )

            target_uuid = BM2_CHARACTERISTIC.lower().strip("{}")

            characteristic_found = any(
                characteristic.uuid.lower().strip("{}") == target_uuid
                for service in client.services
                for characteristic in service.characteristics
            )

            if not characteristic_found:
                _LOGGER.debug(
                    "BM2 validation failed for %s: characteristic %s not found",
                    ble_device.address,
                    BM2_CHARACTERISTIC,
                )
                return False

            reading = await self._get_payload(client)

            if reading.voltage is None or reading.percentage is None:
                return False

            if not (BM2_MIN_VALID_VOLTAGE <= reading.voltage <= BM2_MAX_VALID_VOLTAGE):
                return False

            if not (
                BM2_MIN_VALID_PERCENTAGE
                <= reading.percentage
                <= BM2_MAX_VALID_PERCENTAGE
            ):
                return False

            return not (
                reading.status is not None
                and reading.status not in VALID_BATTERY_STATUSES
            )

        finally:
            if client is not None:
                await client.disconnect()

    async def async_poll(
        self,
        ble_device: BLEDevice | None,
    ) -> BM2Reading:
        """Prefer an active GATT read and fall back to advertisement data.

        Passing ble_device=None means Home Assistant heard the device through a
        passive scanner/proxy but currently has no connectable Bluetooth path.
        """
        client: BleakClientWithServiceCache | None = None

        try:
            if ble_device is None:
                raise BleakError("No connectable Bluetooth path is currently available")

            _LOGGER.debug(
                "Connecting to Bluetooth device %s",
                ble_device.address,
            )

            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.address,
            )

            _LOGGER.debug(
                "Connected to BM2 device %s",
                ble_device.address,
            )

            reading = await self._get_payload(client)
            return reading

        except Exception as ex:
            # ADVERTISEMENT FALLBACK:
            # Active connection/read failed.  If the immediately preceding
            # advertisement contained usable telemetry, publish it instead.
            if (
                self._advertisement_reading is not None
                and self._advertisement_time is not None
                and monotonic() - self._advertisement_time <= ADVERTISEMENT_MAX_AGE
            ):
                address = ble_device.address if ble_device is not None else "unknown"
                _LOGGER.debug(
                    "Active BM2 read failed for %s (%s); using cached "
                    "advertisement data",
                    address,
                    ex,
                )
                return self._advertisement_reading

            # No usable passive fallback exists, so preserve the failure.
            raise

        finally:
            if client is not None:
                try:
                    await client.disconnect()
                finally:
                    _LOGGER.debug("Disconnected from active Bluetooth client")
