"""RTU framing.

Frame: ``slave_address (1) | PDU | crc16 (2, little-endian)``.

The CRC is the Modbus variant of CRC-16: polynomial 0xA001 (reversed
0x8005), initial value 0xFFFF, no final XOR, both input and output reflected.

The 3.5-character inter-frame gap is the serial transport's responsibility.
This module is pure: it neither sleeps nor reads from a socket.
"""

from __future__ import annotations

import struct

from ..errors import ModbusCRCError, ModbusProtocolError

# Minimum frame: address (1) + at least one PDU byte (FC) + CRC (2) = 4.
RTU_MIN_FRAME_LENGTH = 4


def _build_crc16_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return tuple(table)


_CRC16_TABLE: tuple[int, ...] = _build_crc16_table()


def crc16(data: bytes) -> int:
    """Modbus CRC-16. Returns a 16-bit integer."""
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc


def encode_rtu_frame(unit_id: int, pdu: bytes) -> bytes:
    """Wrap a PDU in an RTU frame: address + PDU + CRC16 (LE)."""
    if not 0 <= unit_id <= 0xFF:
        raise ValueError(f"unit_id out of range: {unit_id}")
    if len(pdu) == 0:
        raise ValueError("empty PDU")
    body = bytes([unit_id]) + pdu
    return body + struct.pack("<H", crc16(body))


def decode_rtu_frame(frame: bytes) -> tuple[int, bytes]:
    """Unwrap an RTU frame, returning ``(unit_id, pdu)``.

    Raises ``ModbusCRCError`` on CRC mismatch and ``ModbusProtocolError``
    if the frame is too short to be parsed.
    """
    if len(frame) < RTU_MIN_FRAME_LENGTH:
        raise ModbusProtocolError(f"RTU frame too short: {len(frame)} bytes")
    body = frame[:-2]
    crc_bytes = frame[-2:]
    expected = crc16(body)
    actual = struct.unpack("<H", crc_bytes)[0]
    if expected != actual:
        raise ModbusCRCError(f"CRC mismatch: expected {expected:#06x}, got {actual:#06x}")
    return body[0], body[1:]
