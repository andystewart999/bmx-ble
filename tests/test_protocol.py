"""BM2 packet and connection behaviour without Home Assistant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from Crypto.Cipher import AES

from bmx_ble import BM2Generation, BM2Protocol
from bmx_ble.protocol import BM2_AES_IV, BM2_AES_KEY, BM2_CHARACTERISTIC


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


def test_enhanced_then_legacy_preserves_generation() -> None:
    """A percentage-only packet must not refresh a historical voltage."""
    monitor = BM2Protocol()
    monitor.process_advertisement(_enhanced_packet())
    monitor.process_advertisement(LEGACY_PACKET)
    assert monitor.bm2_generation is BM2Generation.ENHANCED
    assert monitor._advertisement_reading is not None
    assert monitor._advertisement_reading.voltage is None
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


STALE_LOG_PACKET = {38226: bytes.fromhex("bc966e90d5ab3778f7e3ae99d8a9")}
CURRENT_LOG_PACKET = {11628: bytes.fromhex("b1e65b448775c27f1b9e78c09d53")}
CURRENT_LOG_RAW = bytes.fromhex("0201060302f0ff11ff6c2db1e65b448775c27f1b9e78c09d53")


def _raw_packet(records: dict[int, bytes]) -> bytes:
    fields = bytearray()
    for mid, body in records.items():
        fields.extend(bytes([len(body) + 3, 0xFF]) + mid.to_bytes(2, "little") + body)
    return bytes(fields)


def test_current_raw_overrides_merged_history() -> None:
    """The real capture contains 12.88 V despite an older 10.43 V record."""
    monitor = BM2Protocol()
    merged = STALE_LOG_PACKET | CURRENT_LOG_PACKET
    monitor.process_advertisement(merged, raw=CURRENT_LOG_RAW + b"\x02\x0a\x00")
    assert monitor._advertisement_reading is not None
    assert monitor._advertisement_reading.voltage == 12.88
    assert monitor._advertisement_reading.percentage == 100
    monitor.process_advertisement(merged, raw=_raw_packet(STALE_LOG_PACKET))
    assert monitor._advertisement_reading.voltage == 10.43
    monitor.process_advertisement(merged, raw=CURRENT_LOG_RAW)
    assert monitor._advertisement_reading.voltage == 12.88


def test_raw_legacy_does_not_select_historical_voltage() -> None:
    """A current legacy packet is decoded independently of encrypted history."""
    monitor = BM2Protocol()
    monitor.process_advertisement(CURRENT_LOG_PACKET, raw=CURRENT_LOG_RAW)
    monitor.process_advertisement(
        CURRENT_LOG_PACKET | LEGACY_PACKET, raw=_raw_packet(LEGACY_PACKET)
    )
    assert monitor.bm2_generation is BM2Generation.ENHANCED
    assert monitor._advertisement_reading is not None
    assert monitor._advertisement_reading.voltage is None
    assert monitor._advertisement_reading.percentage == 48


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00",
        b"\x11\xff\x01",
        b"\x02\x01\x06",
        b"\x01\xff",
        CURRENT_LOG_RAW + CURRENT_LOG_RAW,
    ],
)
def test_malformed_or_unrelated_raw_does_not_use_history(raw: bytes) -> None:
    """Raw data is authoritative, including when it has no usable telemetry."""
    monitor = BM2Protocol()
    monitor.process_advertisement(CURRENT_LOG_PACKET, raw=raw)
    assert monitor._advertisement_reading is None


@pytest.mark.asyncio
async def test_ambiguous_history_is_not_passive_telemetry() -> None:
    """Missing raw bytes cannot establish which historical record is current."""
    monitor = BM2Protocol()
    monitor.process_advertisement(CURRENT_LOG_PACKET)
    monitor.process_advertisement(STALE_LOG_PACKET | CURRENT_LOG_PACKET)
    with pytest.raises(Exception, match="No connectable Bluetooth path"):
        await monitor.async_poll(None)


@pytest.mark.asyncio
async def test_passive_cache_expires() -> None:
    """Unrelated broadcasts must not renew cached telemetry freshness."""
    monitor = BM2Protocol()
    with patch("bmx_ble.protocol.monotonic", return_value=100):
        monitor.process_advertisement(CURRENT_LOG_PACKET, raw=CURRENT_LOG_RAW)
    with patch("bmx_ble.protocol.monotonic", return_value=280):
        assert (await monitor.async_poll(None)).voltage == 12.88
    with patch("bmx_ble.protocol.monotonic", return_value=281):
        monitor.process_advertisement(CURRENT_LOG_PACKET, raw=b"\x02\x01\x06")
        with pytest.raises(Exception, match="No connectable Bluetooth path"):
            await monitor.async_poll(None)
