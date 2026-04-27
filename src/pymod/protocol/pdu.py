"""Per-function-code PDU codecs (pure, no I/O).

Each standard function code has an `encode_*` and `decode_*` pair operating
on `bytes`. Exception responses (FC | 0x80) are detected via
`detect_exception` and surfaced as the appropriate `ModbusExceptionResponse`
subclass.

Custom function codes are supported via `register_codec()`.

Function codes covered:
  FC01 read coils
  FC02 read discrete inputs
  FC03 read holding registers
  FC04 read input registers
  FC05 write single coil
  FC06 write single register
  FC15 write multiple coils
  FC16 write multiple registers
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..errors import (
    EXCEPTION_BY_CODE,
    IllegalDataValue,
    ModbusExceptionResponse,
    ModbusProtocolError,
    SlaveDeviceFailure,
)

# ---------- Function codes ----------

FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_COILS = 0x0F
FC_WRITE_MULTIPLE_REGISTERS = 0x10

EXCEPTION_BIT = 0x80

# Spec limits.
MAX_READ_COILS = 2000
MAX_READ_DISCRETE_INPUTS = 2000
MAX_READ_REGISTERS = 125
MAX_WRITE_COILS = 1968
MAX_WRITE_REGISTERS = 123

ADDRESS_MAX = 0xFFFF
COIL_ON = 0xFF00
COIL_OFF = 0x0000


def _check_address(addr: int) -> None:
    if not 0 <= addr <= ADDRESS_MAX:
        raise ValueError(f"address out of range: {addr}")


def _check_count(count: int, maximum: int, name: str) -> None:
    if not 1 <= count <= maximum:
        raise ValueError(f"{name} count out of range: {count} (must be 1..{maximum})")


# ---------- Exception detection ----------


def detect_exception(pdu: bytes, expected_fc: int) -> ModbusExceptionResponse | None:
    """If `pdu` is an exception response for `expected_fc`, return the
    corresponding `ModbusExceptionResponse` instance. If it is a normal
    response for `expected_fc`, return None. Anything else is a protocol
    error.
    """
    if len(pdu) == 0:
        raise ModbusProtocolError("empty PDU")
    fc = pdu[0]
    if fc == expected_fc:
        return None
    if fc == (expected_fc | EXCEPTION_BIT):
        if len(pdu) < 2:
            raise ModbusProtocolError("exception response missing exception code")
        code = pdu[1]
        cls = EXCEPTION_BY_CODE.get(code, SlaveDeviceFailure)
        return cls(f"slave returned exception {code:#04x}", code=code)
    raise ModbusProtocolError(
        f"unexpected function code {fc:#04x} (expected {expected_fc:#04x})"
    )


# ---------- FC01 / FC02 — read coils / read discrete inputs ----------


def encode_read_coils(start: int, count: int) -> bytes:
    _check_address(start)
    _check_count(count, MAX_READ_COILS, "coil")
    return struct.pack(">BHH", FC_READ_COILS, start, count)


def encode_read_discrete_inputs(start: int, count: int) -> bytes:
    _check_address(start)
    _check_count(count, MAX_READ_DISCRETE_INPUTS, "discrete input")
    return struct.pack(">BHH", FC_READ_DISCRETE_INPUTS, start, count)


def _decode_bit_response(pdu: bytes, count: int, fc: int) -> list[bool]:
    exc = detect_exception(pdu, fc)
    if exc is not None:
        raise exc
    if len(pdu) < 2:
        raise ModbusProtocolError("response too short")
    byte_count = pdu[1]
    expected = (count + 7) // 8
    if byte_count != expected:
        raise ModbusProtocolError(
            f"byte count mismatch: got {byte_count}, expected {expected}"
        )
    if len(pdu) != 2 + byte_count:
        raise ModbusProtocolError(
            f"response length mismatch: got {len(pdu)}, expected {2 + byte_count}"
        )
    bits: list[bool] = []
    for i in range(count):
        byte_idx, bit_idx = divmod(i, 8)
        bits.append(bool((pdu[2 + byte_idx] >> bit_idx) & 1))
    return bits


def decode_read_coils(pdu: bytes, count: int) -> list[bool]:
    return _decode_bit_response(pdu, count, FC_READ_COILS)


def decode_read_discrete_inputs(pdu: bytes, count: int) -> list[bool]:
    return _decode_bit_response(pdu, count, FC_READ_DISCRETE_INPUTS)


# ---------- FC03 / FC04 — read holding/input registers ----------


def encode_read_holding_registers(start: int, count: int) -> bytes:
    _check_address(start)
    _check_count(count, MAX_READ_REGISTERS, "register")
    return struct.pack(">BHH", FC_READ_HOLDING_REGISTERS, start, count)


def encode_read_input_registers(start: int, count: int) -> bytes:
    _check_address(start)
    _check_count(count, MAX_READ_REGISTERS, "register")
    return struct.pack(">BHH", FC_READ_INPUT_REGISTERS, start, count)


def _decode_register_response(pdu: bytes, count: int, fc: int) -> list[int]:
    exc = detect_exception(pdu, fc)
    if exc is not None:
        raise exc
    if len(pdu) < 2:
        raise ModbusProtocolError("response too short")
    byte_count = pdu[1]
    expected = 2 * count
    if byte_count != expected:
        raise ModbusProtocolError(
            f"byte count mismatch: got {byte_count}, expected {expected}"
        )
    if len(pdu) != 2 + byte_count:
        raise ModbusProtocolError(
            f"response length mismatch: got {len(pdu)}, expected {2 + byte_count}"
        )
    return list(struct.unpack(f">{count}H", pdu[2:]))


def decode_read_holding_registers(pdu: bytes, count: int) -> list[int]:
    return _decode_register_response(pdu, count, FC_READ_HOLDING_REGISTERS)


def decode_read_input_registers(pdu: bytes, count: int) -> list[int]:
    return _decode_register_response(pdu, count, FC_READ_INPUT_REGISTERS)


# ---------- FC05 — write single coil ----------


def encode_write_single_coil(address: int, value: bool) -> bytes:
    _check_address(address)
    coded = COIL_ON if value else COIL_OFF
    return struct.pack(">BHH", FC_WRITE_SINGLE_COIL, address, coded)


def decode_write_single_coil(pdu: bytes) -> tuple[int, bool]:
    """Returns (address, value). Slaves echo the request as the response."""
    exc = detect_exception(pdu, FC_WRITE_SINGLE_COIL)
    if exc is not None:
        raise exc
    if len(pdu) != 5:
        raise ModbusProtocolError(f"unexpected response length: {len(pdu)}")
    _, address, coded = struct.unpack(">BHH", pdu)
    if coded == COIL_ON:
        return address, True
    if coded == COIL_OFF:
        return address, False
    raise ModbusProtocolError(f"invalid coil value in response: {coded:#06x}")


# ---------- FC06 — write single register ----------


def encode_write_single_register(address: int, value: int) -> bytes:
    _check_address(address)
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"register value out of range: {value}")
    return struct.pack(">BHH", FC_WRITE_SINGLE_REGISTER, address, value)


def decode_write_single_register(pdu: bytes) -> tuple[int, int]:
    """Returns (address, value)."""
    exc = detect_exception(pdu, FC_WRITE_SINGLE_REGISTER)
    if exc is not None:
        raise exc
    if len(pdu) != 5:
        raise ModbusProtocolError(f"unexpected response length: {len(pdu)}")
    _, address, value = struct.unpack(">BHH", pdu)
    return address, value


# ---------- FC15 — write multiple coils ----------


def encode_write_multiple_coils(start: int, values: Sequence[bool]) -> bytes:
    _check_address(start)
    count = len(values)
    if not 1 <= count <= MAX_WRITE_COILS:
        raise ValueError(f"coil count out of range: {count} (must be 1..{MAX_WRITE_COILS})")
    byte_count = (count + 7) // 8
    buf = bytearray(byte_count)
    for i, v in enumerate(values):
        if v:
            buf[i // 8] |= 1 << (i % 8)
    return struct.pack(">BHHB", FC_WRITE_MULTIPLE_COILS, start, count, byte_count) + bytes(buf)


def decode_write_multiple_coils(pdu: bytes) -> tuple[int, int]:
    """Returns (start, count)."""
    exc = detect_exception(pdu, FC_WRITE_MULTIPLE_COILS)
    if exc is not None:
        raise exc
    if len(pdu) != 5:
        raise ModbusProtocolError(f"unexpected response length: {len(pdu)}")
    _, start, count = struct.unpack(">BHH", pdu)
    return start, count


# ---------- FC16 — write multiple registers ----------


def encode_write_multiple_registers(start: int, values: Sequence[int]) -> bytes:
    _check_address(start)
    count = len(values)
    if not 1 <= count <= MAX_WRITE_REGISTERS:
        raise ValueError(
            f"register count out of range: {count} (must be 1..{MAX_WRITE_REGISTERS})"
        )
    for v in values:
        if not 0 <= v <= 0xFFFF:
            raise ValueError(f"register value out of range: {v}")
    byte_count = 2 * count
    header = struct.pack(">BHHB", FC_WRITE_MULTIPLE_REGISTERS, start, count, byte_count)
    return header + struct.pack(f">{count}H", *values)


def decode_write_multiple_registers(pdu: bytes) -> tuple[int, int]:
    """Returns (start, count)."""
    exc = detect_exception(pdu, FC_WRITE_MULTIPLE_REGISTERS)
    if exc is not None:
        raise exc
    if len(pdu) != 5:
        raise ModbusProtocolError(f"unexpected response length: {len(pdu)}")
    _, start, count = struct.unpack(">BHH", pdu)
    return start, count


# ---------- Custom function code registry ----------


@dataclass(frozen=True)
class CustomCodec:
    """Codec for a custom or vendor-specific function code.

    `encode` produces the request PDU bytes (function code byte first).
    `decode` is called with the response PDU bytes (function code byte
    first) and must surface exceptions itself, typically by calling
    `detect_exception(pdu, code)`.
    """

    code: int
    encode: Callable[..., bytes]
    decode: Callable[[bytes], object]


_CUSTOM_CODECS: dict[int, CustomCodec] = {}


def register_codec(codec: CustomCodec) -> None:
    """Register a codec for a non-standard function code.

    Codes 0x01..0x10 are reserved for the standard FCs in this library.
    Codes with the exception bit (0x80) set are reserved for exception
    responses.
    """
    if 0x01 <= codec.code <= 0x10:
        raise ValueError(f"FC {codec.code:#04x} is reserved for standard codes")
    if codec.code & EXCEPTION_BIT:
        raise ValueError(f"FC {codec.code:#04x} has the exception bit set")
    if not 0 <= codec.code <= 0xFF:
        raise ValueError(f"FC {codec.code:#04x} out of byte range")
    if codec.code in _CUSTOM_CODECS:
        raise ValueError(f"FC {codec.code:#04x} already registered")
    _CUSTOM_CODECS[codec.code] = codec


def get_codec(code: int) -> CustomCodec | None:
    return _CUSTOM_CODECS.get(code)


def unregister_codec(code: int) -> None:
    """Remove a previously registered codec. Mainly for tests."""
    _CUSTOM_CODECS.pop(code, None)


# ---------- Server-side: request decoders ----------
#
# Slaves receive request PDUs and must extract their parameters. These
# decoders raise `IllegalDataValue` for shape problems so the caller
# (server) can reply with a Modbus exception response of the same code.


def decode_request_read_coils(pdu: bytes) -> tuple[int, int]:
    """Returns ``(start, count)`` for a FC01 request PDU."""
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC01 request length {len(pdu)} != 5")
    start, count = struct.unpack(">HH", pdu[1:5])
    if not 1 <= count <= MAX_READ_COILS:
        raise IllegalDataValue(f"FC01 count out of range: {count}")
    return start, count


def decode_request_read_discrete_inputs(pdu: bytes) -> tuple[int, int]:
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC02 request length {len(pdu)} != 5")
    start, count = struct.unpack(">HH", pdu[1:5])
    if not 1 <= count <= MAX_READ_DISCRETE_INPUTS:
        raise IllegalDataValue(f"FC02 count out of range: {count}")
    return start, count


def decode_request_read_holding_registers(pdu: bytes) -> tuple[int, int]:
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC03 request length {len(pdu)} != 5")
    start, count = struct.unpack(">HH", pdu[1:5])
    if not 1 <= count <= MAX_READ_REGISTERS:
        raise IllegalDataValue(f"FC03 count out of range: {count}")
    return start, count


def decode_request_read_input_registers(pdu: bytes) -> tuple[int, int]:
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC04 request length {len(pdu)} != 5")
    start, count = struct.unpack(">HH", pdu[1:5])
    if not 1 <= count <= MAX_READ_REGISTERS:
        raise IllegalDataValue(f"FC04 count out of range: {count}")
    return start, count


def decode_request_write_single_coil(pdu: bytes) -> tuple[int, bool]:
    """Returns ``(address, value)`` for a FC05 request PDU."""
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC05 request length {len(pdu)} != 5")
    _, address, coded = struct.unpack(">BHH", pdu)
    if coded == COIL_ON:
        return address, True
    if coded == COIL_OFF:
        return address, False
    raise IllegalDataValue(f"FC05 invalid coil value {coded:#06x}")


def decode_request_write_single_register(pdu: bytes) -> tuple[int, int]:
    """Returns ``(address, value)`` for a FC06 request PDU."""
    if len(pdu) != 5:
        raise IllegalDataValue(f"FC06 request length {len(pdu)} != 5")
    _, address, value = struct.unpack(">BHH", pdu)
    return address, value


def decode_request_write_multiple_coils(pdu: bytes) -> tuple[int, list[bool]]:
    """Returns ``(start, values)`` for a FC15 request PDU."""
    if len(pdu) < 6:
        raise IllegalDataValue(f"FC15 request too short: {len(pdu)}")
    start, count = struct.unpack(">HH", pdu[1:5])
    byte_count = pdu[5]
    if not 1 <= count <= MAX_WRITE_COILS:
        raise IllegalDataValue(f"FC15 count out of range: {count}")
    if byte_count != (count + 7) // 8:
        raise IllegalDataValue(
            f"FC15 byte_count {byte_count} inconsistent with count {count}"
        )
    if len(pdu) != 6 + byte_count:
        raise IllegalDataValue(
            f"FC15 length {len(pdu)} doesn't match byte_count {byte_count}"
        )
    bits: list[bool] = []
    data = pdu[6:]
    for i in range(count):
        bits.append(bool((data[i // 8] >> (i % 8)) & 1))
    return start, bits


def decode_request_write_multiple_registers(pdu: bytes) -> tuple[int, list[int]]:
    """Returns ``(start, values)`` for a FC16 request PDU."""
    if len(pdu) < 6:
        raise IllegalDataValue(f"FC16 request too short: {len(pdu)}")
    start, count = struct.unpack(">HH", pdu[1:5])
    byte_count = pdu[5]
    if not 1 <= count <= MAX_WRITE_REGISTERS:
        raise IllegalDataValue(f"FC16 count out of range: {count}")
    if byte_count != 2 * count:
        raise IllegalDataValue(
            f"FC16 byte_count {byte_count} inconsistent with count {count}"
        )
    if len(pdu) != 6 + byte_count:
        raise IllegalDataValue(
            f"FC16 length {len(pdu)} doesn't match byte_count {byte_count}"
        )
    values = list(struct.unpack(f">{count}H", pdu[6:]))
    return start, values


# ---------- Server-side: response encoders ----------


def encode_response_read_coils(values: Sequence[bool]) -> bytes:
    byte_count = (len(values) + 7) // 8
    buf = bytearray(byte_count)
    for i, v in enumerate(values):
        if v:
            buf[i // 8] |= 1 << (i % 8)
    return bytes([FC_READ_COILS, byte_count]) + bytes(buf)


def encode_response_read_discrete_inputs(values: Sequence[bool]) -> bytes:
    byte_count = (len(values) + 7) // 8
    buf = bytearray(byte_count)
    for i, v in enumerate(values):
        if v:
            buf[i // 8] |= 1 << (i % 8)
    return bytes([FC_READ_DISCRETE_INPUTS, byte_count]) + bytes(buf)


def encode_response_read_holding_registers(values: Sequence[int]) -> bytes:
    body = b"".join(int(v).to_bytes(2, "big") for v in values)
    return bytes([FC_READ_HOLDING_REGISTERS, len(body)]) + body


def encode_response_read_input_registers(values: Sequence[int]) -> bytes:
    body = b"".join(int(v).to_bytes(2, "big") for v in values)
    return bytes([FC_READ_INPUT_REGISTERS, len(body)]) + body


def encode_response_write_single_coil(address: int, value: bool) -> bytes:
    coded = COIL_ON if value else COIL_OFF
    return struct.pack(">BHH", FC_WRITE_SINGLE_COIL, address, coded)


def encode_response_write_single_register(address: int, value: int) -> bytes:
    return struct.pack(">BHH", FC_WRITE_SINGLE_REGISTER, address, value)


def encode_response_write_multiple_coils(start: int, count: int) -> bytes:
    return struct.pack(">BHH", FC_WRITE_MULTIPLE_COILS, start, count)


def encode_response_write_multiple_registers(start: int, count: int) -> bytes:
    return struct.pack(">BHH", FC_WRITE_MULTIPLE_REGISTERS, start, count)


def encode_exception_response(fc: int, exception_code: int) -> bytes:
    """Encode a Modbus exception response (FC | 0x80, code)."""
    if not 0 <= fc <= 0xFF:
        raise ValueError(f"fc out of range: {fc}")
    if not 0 <= exception_code <= 0xFF:
        raise ValueError(f"exception_code out of range: {exception_code}")
    return bytes([(fc | EXCEPTION_BIT) & 0xFF, exception_code])
