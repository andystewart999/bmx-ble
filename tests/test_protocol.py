"""BM2 packet and connection behaviour without Home Assistant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bmx_ble import BM2Generation, BM2Protocol
from bmx_ble.protocol import BM2_AES_IV, BM2_AES_KEY, BM2_CHARACTERISTIC
from Crypto.Cipher import AES


def _encrypt(plain: bytes) -> bytes:
    return AES.new(BM2_AES_KEY, AES.MODE_CBC, BM2_AES_IV).encrypt(plain)


def _enhanced_packet(
    voltage_cents: int = 1250, percentage: int = 75
) -> dict[int, bytes]:
    plain = bytearray(16)
    plain[6:8] = voltage_cents.to_bytes(2, "big")
    plain[8] = percentage
    encrypted = _encrypt(bytes(plain))
    return {int.from_bytes(encrypted[:2], "little"): encrypted[2:]}


LEGACY_PACKET = {
    0x004C: bytes.fromhex("0215655f83caae16a10a702e31f30d58dd82")
    + bytes.fromhex("00010002")
    + bytes([48])
}


def test_legacy_packet() -> None:
    """A recognised legacy frame provides only percentage."""
    monitor = BM2Protocol()
    monitor.process_advertisement(LEGACY_PACKET)
    assert monitor.bm2_generation is BM2Generation.LEGACY
    reading = monitor._advertisement_reading
    assert reading is not None
    assert reading.voltage is None
    assert reading.percentage == 48


def test_enhanced_then_legacy_preserves_voltage() -> None:
    """Alternating packets must keep known enhanced voltage and fresh percentage."""
    monitor = BM2Protocol()
    monitor.process_advertisement(_enhanced_packet())
    monitor.process_advertisement(LEGACY_PACKET)
    assert monitor.bm2_generation is BM2Generation.ENHANCED
    assert monitor._advertisement_reading is not None
    assert monitor._advertisement_reading.voltage == 12.5
    assert monitor._advertisement_reading.percentage == 48


def test_invalid_packet_is_ignored() -> None:
    """Plausible frame shape alone must not identify a BM2."""
    monitor = BM2Protocol()
    monitor.process_advertisement(_enhanced_packet(voltage_cents=500))
    monitor.process_advertisement({0x004C: b"\x02\x15" + bytes(21)})
    assert monitor.bm2_generation is BM2Generation.UNKNOWN


def test_gatt_payload() -> None:
    """GATT decryption preserves the established voltage/status mapping."""
    plain = bytes.fromhex("004e2464" + "00" * 12)
    reading = BM2Protocol()._decode_gatt(_encrypt(plain))
    assert (reading.voltage, reading.percentage, reading.status) == (12.5, 100, 4)


@pytest.mark.asyncio
async def test_passive_fallback_and_missing_data() -> None:
    """A cached packet works without a connectable path; no packet fails."""
    monitor = BM2Protocol()
    with pytest.raises(Exception, match="No connectable Bluetooth path"):
        await monitor.async_poll(None)
    monitor.process_advertisement(LEGACY_PACKET)
    reading = await monitor.async_poll(None)
    assert reading.percentage == 48


@pytest.mark.asyncio
async def test_active_validation_and_poll() -> None:
    """Validate FFF4 with a decryptable notification and return its data."""
    notification = _encrypt(bytes.fromhex("004e2464" + "00" * 12))
    client = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        services=[
            SimpleNamespace(characteristics=[SimpleNamespace(uuid=BM2_CHARACTERISTIC)])
        ],
        start_notify=AsyncMock(
            side_effect=lambda _uuid, callback: callback(0, bytearray(notification))
        ),
        stop_notify=AsyncMock(),
        disconnect=AsyncMock(),
    )
    device = SimpleNamespace(address=client.address)
    with patch(
        "bmx_ble.protocol.establish_connection", new=AsyncMock(return_value=client)
    ):
        assert await BM2Protocol().async_validate_active(device)
        reading = await BM2Protocol().async_poll(device)
    assert reading.voltage == 12.5
    assert reading.status == 4
    assert client.disconnect.await_count == 2


@pytest.mark.asyncio
async def test_active_validation_rejects_missing_characteristic() -> None:
    """Successful connection without FFF4 is a positive rejection."""
    client = SimpleNamespace(services=[], disconnect=AsyncMock())
    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
    with patch(
        "bmx_ble.protocol.establish_connection", new=AsyncMock(return_value=client)
    ):
        assert not await BM2Protocol().async_validate_active(device)
    client.disconnect.assert_awaited_once()
