"""Tests for `pymod.protocol.pdu` — encode/decode per FC, exceptions, registry."""

from __future__ import annotations

import struct

import pytest

from pymod.errors import (
    IllegalDataAddress,
    IllegalDataValue,
    IllegalFunction,
    ModbusExceptionResponse,
    ModbusProtocolError,
    SlaveDeviceBusy,
    SlaveDeviceFailure,
)
from pymod.protocol.pdu import (
    FC_READ_COILS,
    FC_READ_HOLDING_REGISTERS,
    MAX_READ_COILS,
    MAX_READ_REGISTERS,
    MAX_WRITE_COILS,
    MAX_WRITE_REGISTERS,
    CustomCodec,
    decode_read_coils,
    decode_read_discrete_inputs,
    decode_read_holding_registers,
    decode_read_input_registers,
    decode_write_multiple_coils,
    decode_write_multiple_registers,
    decode_write_single_coil,
    decode_write_single_register,
    detect_exception,
    encode_read_coils,
    encode_read_discrete_inputs,
    encode_read_holding_registers,
    encode_read_input_registers,
    encode_write_multiple_coils,
    encode_write_multiple_registers,
    encode_write_single_coil,
    encode_write_single_register,
    get_codec,
    register_codec,
    unregister_codec,
)


# ---------- FC01 read coils ----------


class TestReadCoils:
    def test_request_encoding_matches_spec(self) -> None:
        # Modbus spec example: read 19 coils starting at 0x0013.
        assert encode_read_coils(0x0013, 19) == bytes.fromhex("01 0013 0013".replace(" ", ""))

    def test_response_decoding_matches_spec(self) -> None:
        # Spec example: 19 coils packed into 3 bytes (CD 6B 05).
        # CD = 1100 1101; LSB first => coils[0..7] = [1,0,1,1,0,0,1,1]
        # 6B = 0110 1011; LSB first => coils[8..15] = [1,1,0,1,0,1,1,0]
        # 05 = 0000 0101; only 3 bits used => coils[16..18] = [1,0,1]
        pdu = bytes.fromhex("01 03 CD 6B 05".replace(" ", ""))
        coils = decode_read_coils(pdu, 19)
        assert coils == [
            True, False, True, True, False, False, True, True,
            True, True, False, True, False, True, True, False,
            True, False, True,
        ]

    def test_round_trip_random(self) -> None:
        # encode_read_coils only encodes the request, but a self-consistent
        # response can be built and decoded for any pattern.
        pattern = [bool(i % 3) for i in range(50)]
        byte_count = (len(pattern) + 7) // 8
        buf = bytearray(byte_count)
        for i, v in enumerate(pattern):
            if v:
                buf[i // 8] |= 1 << (i % 8)
        pdu = bytes([FC_READ_COILS, byte_count]) + bytes(buf)
        assert decode_read_coils(pdu, len(pattern)) == pattern

    def test_count_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_read_coils(0, 0)

    def test_count_over_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_read_coils(0, MAX_READ_COILS + 1)

    def test_address_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_read_coils(0x10000, 1)
        with pytest.raises(ValueError):
            encode_read_coils(-1, 1)

    def test_response_length_mismatch_raises(self) -> None:
        # Claims 3 bytes but only carries 2.
        pdu = bytes.fromhex("01 03 CD 6B".replace(" ", ""))
        with pytest.raises(ModbusProtocolError):
            decode_read_coils(pdu, 19)

    def test_response_byte_count_mismatch_raises(self) -> None:
        # Claims 4 bytes for 19 coils (should be 3).
        pdu = bytes.fromhex("01 04 CD 6B 05 00".replace(" ", ""))
        with pytest.raises(ModbusProtocolError):
            decode_read_coils(pdu, 19)


# ---------- FC02 read discrete inputs ----------


class TestReadDiscreteInputs:
    def test_request_encoding(self) -> None:
        # Same envelope as FC01 with FC byte = 02.
        assert encode_read_discrete_inputs(0x00C4, 0x0016) == bytes.fromhex("02 00C4 0016".replace(" ", ""))

    def test_decode_matches_spec(self) -> None:
        # Spec example: 22 discrete inputs in 3 bytes (AC DB 35).
        # AC = 1010 1100; LSB first => [0,0,1,1,0,1,0,1]
        # DB = 1101 1011; LSB first => [1,1,0,1,1,0,1,1]
        # 35 = 0011 0101; first 6 bits => [1,0,1,0,1,1]
        pdu = bytes.fromhex("02 03 AC DB 35".replace(" ", ""))
        inputs = decode_read_discrete_inputs(pdu, 22)
        assert inputs[:8] == [False, False, True, True, False, True, False, True]
        assert inputs[8:16] == [True, True, False, True, True, False, True, True]
        assert inputs[16:22] == [True, False, True, False, True, True]


# ---------- FC03 read holding registers ----------


class TestReadHoldingRegisters:
    def test_request_encoding_matches_spec(self) -> None:
        # Spec example: read 3 holding regs starting at 0x006B.
        assert encode_read_holding_registers(0x006B, 3) == bytes.fromhex("03 006B 0003".replace(" ", ""))

    def test_response_decoding_matches_spec(self) -> None:
        # Spec example response: 3 registers = 0x022B, 0x0000, 0x0064.
        pdu = bytes.fromhex("03 06 022B 0000 0064".replace(" ", ""))
        assert decode_read_holding_registers(pdu, 3) == [0x022B, 0x0000, 0x0064]

    def test_round_trip_max_count(self) -> None:
        regs = list(range(MAX_READ_REGISTERS))
        body = struct.pack(f">{len(regs)}H", *regs)
        pdu = bytes([FC_READ_HOLDING_REGISTERS, len(body)]) + body
        assert decode_read_holding_registers(pdu, len(regs)) == regs

    def test_count_over_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_read_holding_registers(0, MAX_READ_REGISTERS + 1)


# ---------- FC04 read input registers ----------


class TestReadInputRegisters:
    def test_request_encoding(self) -> None:
        assert encode_read_input_registers(0x0008, 0x0001) == bytes.fromhex("04 0008 0001".replace(" ", ""))

    def test_decode(self) -> None:
        pdu = bytes.fromhex("04 02 000A".replace(" ", ""))
        assert decode_read_input_registers(pdu, 1) == [0x000A]


# ---------- FC05 write single coil ----------


class TestWriteSingleCoil:
    def test_encode_on(self) -> None:
        # ON = 0xFF00.
        assert encode_write_single_coil(0x00AC, True) == bytes.fromhex("05 00AC FF00".replace(" ", ""))

    def test_encode_off(self) -> None:
        assert encode_write_single_coil(0x00AC, False) == bytes.fromhex("05 00AC 0000".replace(" ", ""))

    def test_decode_echo(self) -> None:
        # Slaves echo the request as their normal response.
        addr, value = decode_write_single_coil(bytes.fromhex("05 00AC FF00".replace(" ", "")))
        assert addr == 0x00AC and value is True

    def test_decode_invalid_value_raises(self) -> None:
        with pytest.raises(ModbusProtocolError):
            decode_write_single_coil(bytes.fromhex("05 00AC 1234".replace(" ", "")))


# ---------- FC06 write single register ----------


class TestWriteSingleRegister:
    def test_encode(self) -> None:
        assert encode_write_single_register(0x0001, 0x0003) == bytes.fromhex("06 0001 0003".replace(" ", ""))

    def test_decode_echo(self) -> None:
        addr, value = decode_write_single_register(bytes.fromhex("06 0001 0003".replace(" ", "")))
        assert (addr, value) == (0x0001, 0x0003)

    def test_value_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_write_single_register(0, 0x10000)
        with pytest.raises(ValueError):
            encode_write_single_register(0, -1)


# ---------- FC15 write multiple coils ----------


class TestWriteMultipleCoils:
    def test_encode_matches_spec(self) -> None:
        # Spec example: write 10 coils starting at 0x0013, pattern CD 01.
        # bits LSB-first: 1,0,1,1,0,0,1,1, 1,0
        values = [True, False, True, True, False, False, True, True, True, False]
        encoded = encode_write_multiple_coils(0x0013, values)
        assert encoded == bytes.fromhex("0F 0013 000A 02 CD 01".replace(" ", ""))

    def test_decode_response(self) -> None:
        start, count = decode_write_multiple_coils(bytes.fromhex("0F 0013 000A".replace(" ", "")))
        assert (start, count) == (0x0013, 0x000A)

    def test_count_over_limit(self) -> None:
        with pytest.raises(ValueError):
            encode_write_multiple_coils(0, [False] * (MAX_WRITE_COILS + 1))


# ---------- FC16 write multiple registers ----------


class TestWriteMultipleRegisters:
    def test_encode_matches_spec(self) -> None:
        # Spec example: write 2 registers starting at 0x0001 with values 0x000A, 0x0102.
        encoded = encode_write_multiple_registers(0x0001, [0x000A, 0x0102])
        assert encoded == bytes.fromhex("10 0001 0002 04 000A 0102".replace(" ", ""))

    def test_decode_response(self) -> None:
        start, count = decode_write_multiple_registers(bytes.fromhex("10 0001 0002".replace(" ", "")))
        assert (start, count) == (0x0001, 0x0002)

    def test_count_over_limit(self) -> None:
        with pytest.raises(ValueError):
            encode_write_multiple_registers(0, [0] * (MAX_WRITE_REGISTERS + 1))

    def test_register_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_write_multiple_registers(0, [0x10000])


# ---------- Exception response decoding ----------


class TestExceptionResponses:
    def test_illegal_data_address_for_fc03(self) -> None:
        # FC03 | 0x80 = 0x83, code 0x02 = ILLEGAL_DATA_ADDRESS.
        pdu = bytes.fromhex("83 02")
        exc = detect_exception(pdu, FC_READ_HOLDING_REGISTERS)
        assert isinstance(exc, IllegalDataAddress)
        assert exc.code == 0x02

    def test_illegal_function(self) -> None:
        pdu = bytes.fromhex("81 01")  # FC01 + exception bit, code 0x01.
        exc = detect_exception(pdu, FC_READ_COILS)
        assert isinstance(exc, IllegalFunction)

    def test_each_documented_code(self) -> None:
        cases = [
            (0x01, IllegalFunction),
            (0x02, IllegalDataAddress),
            (0x03, IllegalDataValue),
            (0x04, SlaveDeviceFailure),
            (0x06, SlaveDeviceBusy),
        ]
        for code, cls in cases:
            pdu = bytes([0x80 | FC_READ_COILS, code])
            exc = detect_exception(pdu, FC_READ_COILS)
            assert isinstance(exc, cls)
            assert exc.code == code

    def test_unknown_exception_code_falls_back_to_slave_device_failure(self) -> None:
        pdu = bytes.fromhex("81 7F")  # FC01 exception, unknown code 0x7F.
        exc = detect_exception(pdu, FC_READ_COILS)
        assert isinstance(exc, ModbusExceptionResponse)
        assert exc.code == 0x7F

    def test_decode_read_raises_exception_response(self) -> None:
        pdu = bytes.fromhex("83 02")
        with pytest.raises(IllegalDataAddress):
            decode_read_holding_registers(pdu, 1)

    def test_unrelated_function_code_is_protocol_error(self) -> None:
        # Got FC04 in a context expecting FC03.
        pdu = bytes.fromhex("04 02 0000")
        with pytest.raises(ModbusProtocolError):
            detect_exception(pdu, FC_READ_HOLDING_REGISTERS)

    def test_truncated_exception_raises(self) -> None:
        # FC + exception bit but no exception code byte.
        with pytest.raises(ModbusProtocolError):
            detect_exception(bytes.fromhex("83"), FC_READ_HOLDING_REGISTERS)

    def test_empty_pdu_raises(self) -> None:
        with pytest.raises(ModbusProtocolError):
            detect_exception(b"", FC_READ_HOLDING_REGISTERS)


# ---------- Custom function code registry ----------


class TestCustomCodecRegistry:
    def teardown_method(self) -> None:
        # Keep the registry clean between tests.
        for code in (0x41, 0x42, 0x64):
            unregister_codec(code)

    def test_register_and_get(self) -> None:
        codec = CustomCodec(code=0x41, encode=lambda: b"\x41", decode=lambda pdu: pdu)
        register_codec(codec)
        assert get_codec(0x41) is codec

    def test_cannot_overlap_standard_fc(self) -> None:
        for reserved in (0x01, 0x03, 0x10):
            with pytest.raises(ValueError):
                register_codec(CustomCodec(code=reserved, encode=lambda: b"", decode=lambda p: p))

    def test_cannot_register_exception_bit(self) -> None:
        with pytest.raises(ValueError):
            register_codec(CustomCodec(code=0x83, encode=lambda: b"", decode=lambda p: p))

    def test_cannot_register_twice(self) -> None:
        register_codec(CustomCodec(code=0x42, encode=lambda: b"", decode=lambda p: p))
        with pytest.raises(ValueError):
            register_codec(CustomCodec(code=0x42, encode=lambda: b"", decode=lambda p: p))

    def test_unregister_known_and_unknown(self) -> None:
        register_codec(CustomCodec(code=0x64, encode=lambda: b"", decode=lambda p: p))
        unregister_codec(0x64)
        assert get_codec(0x64) is None
        # Unregistering an unregistered code is a no-op.
        unregister_codec(0x64)
