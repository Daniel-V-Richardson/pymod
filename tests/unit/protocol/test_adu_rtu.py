"""Tests for `pymod.protocol.adu_rtu` — RTU framing and CRC16."""

from __future__ import annotations

import random
import struct

import pytest

from pymod.errors import ModbusCRCError, ModbusProtocolError
from pymod.protocol.adu_rtu import (
    RTU_MIN_FRAME_LENGTH,
    crc16,
    decode_rtu_frame,
    encode_rtu_frame,
)


def _crc16_reference(data: bytes) -> int:
    """Bit-by-bit reference implementation of the Modbus CRC16.

    Used to cross-check the table-driven `crc16` for randomized inputs.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class TestCRC16:
    def test_empty_input_returns_init_value(self) -> None:
        assert crc16(b"") == 0xFFFF

    def test_known_modbus_frame(self) -> None:
        # `01 03 00 00 00 0A` is a request: read 10 holding regs at 0 from slave 1.
        # Cross-checked against the bit-by-bit reference impl below.
        body = bytes.fromhex("0103000000 0A".replace(" ", ""))
        assert crc16(body) == _crc16_reference(body)

    def test_matches_reference_for_known_short_inputs(self) -> None:
        for hex_data in [
            "00",
            "FF",
            "0103",
            "01030000000A",
            "010F0013000A02CD01",
            "1000010002040000FFFF",
            "FF" * 250,
        ]:
            data = bytes.fromhex(hex_data)
            assert crc16(data) == _crc16_reference(data)

    def test_matches_reference_random(self) -> None:
        rng = random.Random(0xDEADBEEF)
        for _ in range(200):
            n = rng.randint(0, 253)
            data = bytes(rng.randint(0, 255) for _ in range(n))
            assert crc16(data) == _crc16_reference(data)


class TestRtuFraming:
    def test_round_trip(self) -> None:
        pdu = bytes.fromhex("03 0000 000A".replace(" ", ""))
        frame = encode_rtu_frame(0x01, pdu)
        unit, decoded = decode_rtu_frame(frame)
        assert unit == 0x01
        assert decoded == pdu

    def test_round_trip_random(self) -> None:
        rng = random.Random(7)
        for _ in range(100):
            unit = rng.randint(0, 255)
            n = rng.randint(1, 250)
            pdu = bytes(rng.randint(0, 255) for _ in range(n))
            frame = encode_rtu_frame(unit, pdu)
            assert decode_rtu_frame(frame) == (unit, pdu)

    def test_encode_appends_crc_little_endian(self) -> None:
        pdu = bytes.fromhex("03 0000 000A".replace(" ", ""))
        frame = encode_rtu_frame(0x01, pdu)
        body = bytes([0x01]) + pdu
        expected_crc = crc16(body)
        assert frame[-2:] == struct.pack("<H", expected_crc)

    def test_corrupted_frame_raises_crc_error(self) -> None:
        pdu = bytes.fromhex("03 0000 000A".replace(" ", ""))
        frame = bytearray(encode_rtu_frame(0x01, pdu))
        frame[3] ^= 0xFF  # flip a byte inside the PDU.
        with pytest.raises(ModbusCRCError):
            decode_rtu_frame(bytes(frame))

    def test_short_frame_raises(self) -> None:
        with pytest.raises(ModbusProtocolError):
            decode_rtu_frame(b"\x01\x03\xff")  # 3 bytes < RTU_MIN_FRAME_LENGTH

    def test_min_frame_length(self) -> None:
        # Minimum: addr(1) + FC(1) + CRC(2) = 4. Synthesize and round-trip.
        body = b"\x01\x03"
        frame = body + struct.pack("<H", crc16(body))
        assert len(frame) == RTU_MIN_FRAME_LENGTH
        unit, pdu = decode_rtu_frame(frame)
        assert unit == 0x01 and pdu == b"\x03"

    def test_unit_id_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_rtu_frame(-1, b"\x03")
        with pytest.raises(ValueError):
            encode_rtu_frame(0x100, b"\x03")

    def test_empty_pdu_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_rtu_frame(0x01, b"")
