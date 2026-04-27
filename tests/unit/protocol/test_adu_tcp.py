"""Tests for `pymod.protocol.adu_tcp` — MBAP framing."""

from __future__ import annotations

import struct

import pytest

from pymod.errors import ModbusProtocolError
from pymod.protocol.adu_tcp import (
    MBAP_HEADER_LENGTH,
    MBAP_MIN_PEEK_BYTES,
    decode_mbap_frame,
    encode_mbap_frame,
    expected_frame_length,
)


class TestEncode:
    def test_basic_frame(self) -> None:
        # PDU: read 3 holding regs at 0x006B (FC03 example).
        pdu = bytes.fromhex("03 006B 0003".replace(" ", ""))
        frame = encode_mbap_frame(transaction_id=0x0001, unit_id=0x11, pdu=pdu)
        # tid=0001, pid=0000, length=0006 (1 unit_id + 5 PDU), unit=11.
        assert frame == bytes.fromhex("0001 0000 0006 11 03 006B 0003".replace(" ", ""))

    def test_transaction_id_bounds(self) -> None:
        with pytest.raises(ValueError):
            encode_mbap_frame(transaction_id=-1, unit_id=0, pdu=b"\x03")
        with pytest.raises(ValueError):
            encode_mbap_frame(transaction_id=0x10000, unit_id=0, pdu=b"\x03")

    def test_unit_id_bounds(self) -> None:
        with pytest.raises(ValueError):
            encode_mbap_frame(transaction_id=0, unit_id=-1, pdu=b"\x03")
        with pytest.raises(ValueError):
            encode_mbap_frame(transaction_id=0, unit_id=0x100, pdu=b"\x03")

    def test_empty_pdu_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_mbap_frame(transaction_id=0, unit_id=0, pdu=b"")


class TestDecode:
    def test_round_trip(self) -> None:
        for tid, uid, pdu in [
            (0x0001, 0x01, b"\x03\x00\x00\x00\x0a"),
            (0xFFFF, 0xFF, b"\x10\x00\x00\x00\x02\x04\x00\x0a\x01\x02"),
            (0x1234, 0x05, b"\x83\x02"),  # exception response.
        ]:
            frame = encode_mbap_frame(tid, uid, pdu)
            assert decode_mbap_frame(frame) == (tid, uid, pdu)

    def test_short_frame_raises(self) -> None:
        with pytest.raises(ModbusProtocolError):
            decode_mbap_frame(b"\x00" * (MBAP_HEADER_LENGTH - 1))

    def test_unexpected_protocol_id(self) -> None:
        # Build a frame with protocol id != 0.
        frame = struct.pack(">HHHB", 1, 0x1234, 2, 1) + b"\x03"
        with pytest.raises(ModbusProtocolError):
            decode_mbap_frame(frame)

    def test_length_mismatch_underclaims(self) -> None:
        # Header says length=3 (1 uid + 2 PDU bytes), but PDU is 5 bytes.
        frame = struct.pack(">HHHB", 1, 0, 3, 1) + b"\x03\x00\x00\x00\x0a"
        with pytest.raises(ModbusProtocolError):
            decode_mbap_frame(frame)

    def test_length_mismatch_overclaims(self) -> None:
        # Header says length=10 but PDU is 1 byte.
        frame = struct.pack(">HHHB", 1, 0, 10, 1) + b"\x03"
        with pytest.raises(ModbusProtocolError):
            decode_mbap_frame(frame)

    def test_empty_pdu_after_header_raises(self) -> None:
        # length=1 means just unit id, no PDU.
        frame = struct.pack(">HHHB", 1, 0, 1, 1)
        with pytest.raises(ModbusProtocolError):
            decode_mbap_frame(frame)


class TestExpectedFrameLength:
    def test_matches_actual_after_encode(self) -> None:
        for pdu_len in (1, 5, 12, 250):
            pdu = bytes(pdu_len)
            frame = encode_mbap_frame(0x1234, 0x05, pdu)
            assert expected_frame_length(frame[:MBAP_MIN_PEEK_BYTES]) == len(frame)

    def test_can_use_more_than_minimum_prefix(self) -> None:
        pdu = b"\x03\x00\x00\x00\x0a"
        frame = encode_mbap_frame(1, 1, pdu)
        # Pass the entire frame; only the length field is read.
        assert expected_frame_length(frame) == len(frame)

    def test_too_short_prefix_raises(self) -> None:
        with pytest.raises(ModbusProtocolError):
            expected_frame_length(b"\x00" * (MBAP_MIN_PEEK_BYTES - 1))
