"""MBAP framing for Modbus TCP.

MBAP header (7 bytes):

    transaction id (2)  |  protocol id = 0x0000 (2)  |  length (2)  |  unit id (1)

followed by the PDU. The `length` field counts unit id (1) + PDU bytes.
The transaction id is echoed by the slave; the TCP transport uses it to
demultiplex pipelined responses.
"""

from __future__ import annotations

import struct

from ..errors import ModbusProtocolError

MBAP_HEADER_LENGTH = 7
MBAP_LENGTH_FIELD_OFFSET = 4
MBAP_LENGTH_FIELD_SIZE = 2
MBAP_PROTOCOL_ID = 0x0000

# Minimum bytes needed before we can read the `length` field.
MBAP_MIN_PEEK_BYTES = MBAP_LENGTH_FIELD_OFFSET + MBAP_LENGTH_FIELD_SIZE  # 6


def encode_mbap_frame(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    """Wrap a PDU in an MBAP header.

    Parameters
    ----------
    transaction_id : 0..0xFFFF
    unit_id        : 0..0xFF
    pdu            : function-code byte plus per-FC payload
    """
    if not 0 <= transaction_id <= 0xFFFF:
        raise ValueError(f"transaction_id out of range: {transaction_id}")
    if not 0 <= unit_id <= 0xFF:
        raise ValueError(f"unit_id out of range: {unit_id}")
    if len(pdu) == 0:
        raise ValueError("empty PDU")
    length = 1 + len(pdu)
    if length > 0xFFFF:
        raise ValueError(f"PDU too large for MBAP: {len(pdu)} bytes")
    return struct.pack(">HHHB", transaction_id, MBAP_PROTOCOL_ID, length, unit_id) + pdu


def decode_mbap_frame(frame: bytes) -> tuple[int, int, bytes]:
    """Unwrap an MBAP frame, returning ``(transaction_id, unit_id, pdu)``.

    Raises ``ModbusProtocolError`` for any header malformation.
    """
    if len(frame) < MBAP_HEADER_LENGTH:
        raise ModbusProtocolError(f"MBAP frame too short: {len(frame)} bytes")
    tid, pid, length, uid = struct.unpack(">HHHB", frame[:MBAP_HEADER_LENGTH])
    if pid != MBAP_PROTOCOL_ID:
        raise ModbusProtocolError(f"unexpected MBAP protocol id: {pid:#06x}")
    pdu = frame[MBAP_HEADER_LENGTH:]
    expected_pdu_length = length - 1  # subtract the unit id byte
    if expected_pdu_length != len(pdu):
        raise ModbusProtocolError(
            f"MBAP length mismatch: header says {length} (PDU expected {expected_pdu_length}), "
            f"got {len(pdu)} bytes"
        )
    if len(pdu) == 0:
        raise ModbusProtocolError("empty PDU")
    return tid, uid, pdu


def expected_frame_length(header_prefix: bytes) -> int:
    """Given at least the first 6 bytes of an MBAP frame, return the total
    expected frame length (header + PDU).

    The TCP transport uses this after reading the first 6 bytes to know
    exactly how many more bytes constitute the rest of the frame.
    """
    if len(header_prefix) < MBAP_MIN_PEEK_BYTES:
        raise ModbusProtocolError(
            f"need at least {MBAP_MIN_PEEK_BYTES} bytes to read MBAP length, got {len(header_prefix)}"
        )
    length: int = struct.unpack(
        ">H",
        header_prefix[
            MBAP_LENGTH_FIELD_OFFSET : MBAP_LENGTH_FIELD_OFFSET + MBAP_LENGTH_FIELD_SIZE
        ],
    )[0]
    # Total = (tid + pid + length-field) + length-field-value
    #       = 6                          + length
    return MBAP_MIN_PEEK_BYTES + length
