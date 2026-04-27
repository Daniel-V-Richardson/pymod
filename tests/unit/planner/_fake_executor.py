"""Fake `PduExecutor` for planner tests.

Decodes request PDUs, serves canned data from per-area dicts, and records
every PDU sent so tests can assert on coalescing and splitting decisions.

Per-call failures can be injected by registering a (fc, start) → exception
mapping — the executor pops the entry the first time the matching request
is seen, mimicking a single transient failure.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Any

from pymod.errors import IllegalDataAddress, ModbusError
from pymod.protocol.pdu import (
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE_REGISTERS,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE_REGISTER,
)


def _bit_response(values: Sequence[bool], fc: int) -> bytes:
    byte_count = (len(values) + 7) // 8
    buf = bytearray(byte_count)
    for i, v in enumerate(values):
        if v:
            buf[i // 8] |= 1 << (i % 8)
    return bytes([fc, byte_count]) + bytes(buf)


def _register_response(values: Sequence[int], fc: int) -> bytes:
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([fc, len(body)]) + body


class FakeExecutor:
    """Stateful fake. Construct with per-area data; call `failures` to
    register one-shot failures keyed by (fc, start)."""

    def __init__(
        self,
        *,
        holding: dict[int, int] | None = None,
        input_regs: dict[int, int] | None = None,
        coils: dict[int, bool] | None = None,
        discrete: dict[int, bool] | None = None,
    ) -> None:
        self.holding: dict[int, int] = dict(holding) if holding else {}
        self.input_regs: dict[int, int] = dict(input_regs) if input_regs else {}
        self.coils: dict[int, bool] = dict(coils) if coils else {}
        self.discrete: dict[int, bool] = dict(discrete) if discrete else {}
        self.sent_pdus: list[bytes] = []
        self._failures: dict[tuple[int, int], ModbusError] = {}

    def fail_once(self, fc: int, start: int, error: ModbusError) -> None:
        self._failures[(fc, start)] = error

    async def __call__(self, pdu: bytes) -> bytes:
        self.sent_pdus.append(pdu)
        fc = pdu[0]
        if fc in (
            FC_READ_HOLDING_REGISTERS,
            FC_READ_INPUT_REGISTERS,
            FC_READ_COILS,
            FC_READ_DISCRETE_INPUTS,
            FC_WRITE_SINGLE_REGISTER,
            FC_WRITE_SINGLE_COIL,
        ):
            start = struct.unpack(">H", pdu[1:3])[0]
            err = self._failures.pop((fc, start), None)
            if err is not None:
                raise err
        elif fc in (FC_WRITE_MULTIPLE_REGISTERS, FC_WRITE_MULTIPLE_COILS):
            start = struct.unpack(">H", pdu[1:3])[0]
            err = self._failures.pop((fc, start), None)
            if err is not None:
                raise err

        if fc == FC_READ_HOLDING_REGISTERS:
            return self._read_registers(pdu, fc, self.holding)
        if fc == FC_READ_INPUT_REGISTERS:
            return self._read_registers(pdu, fc, self.input_regs)
        if fc == FC_READ_COILS:
            return self._read_bits(pdu, fc, self.coils)
        if fc == FC_READ_DISCRETE_INPUTS:
            return self._read_bits(pdu, fc, self.discrete)
        if fc == FC_WRITE_SINGLE_REGISTER:
            addr, value = struct.unpack(">HH", pdu[1:5])
            self.holding[addr] = value
            return pdu  # echo
        if fc == FC_WRITE_SINGLE_COIL:
            addr, coded = struct.unpack(">HH", pdu[1:5])
            self.coils[addr] = (coded == 0xFF00)
            return pdu  # echo
        if fc == FC_WRITE_MULTIPLE_REGISTERS:
            start, count = struct.unpack(">HH", pdu[1:5])
            byte_count = pdu[5]
            for i in range(count):
                self.holding[start + i] = int.from_bytes(
                    pdu[6 + 2 * i : 8 + 2 * i], "big"
                )
            return bytes([fc]) + struct.pack(">HH", start, count)
        if fc == FC_WRITE_MULTIPLE_COILS:
            start, count = struct.unpack(">HH", pdu[1:5])
            byte_count = pdu[5]
            data = pdu[6 : 6 + byte_count]
            for i in range(count):
                self.coils[start + i] = bool((data[i // 8] >> (i % 8)) & 1)
            return bytes([fc]) + struct.pack(">HH", start, count)
        raise AssertionError(f"FakeExecutor: unhandled FC {fc:#04x}")

    # ---------- helpers ----------

    @staticmethod
    def _read_registers(pdu: bytes, fc: int, store: dict[int, int]) -> bytes:
        start, count = struct.unpack(">HH", pdu[1:5])
        regs = []
        for i in range(count):
            addr = start + i
            if store and addr not in store:
                raise IllegalDataAddress(f"no register at {addr}")
            regs.append(store.get(addr, 0))
        return _register_response(regs, fc)

    @staticmethod
    def _read_bits(pdu: bytes, fc: int, store: dict[int, bool]) -> bytes:
        start, count = struct.unpack(">HH", pdu[1:5])
        bits = []
        for i in range(count):
            addr = start + i
            if store and addr not in store:
                raise IllegalDataAddress(f"no bit at {addr}")
            bits.append(store.get(addr, False))
        return _bit_response(bits, fc)


def request_summaries(executor: FakeExecutor) -> list[tuple[int, int, int]]:
    """Decode every recorded PDU into ``(fc, start, count)`` for assertions."""
    out: list[tuple[int, int, int]] = []
    for pdu in executor.sent_pdus:
        fc = pdu[0]
        if fc in (
            FC_READ_HOLDING_REGISTERS,
            FC_READ_INPUT_REGISTERS,
            FC_READ_COILS,
            FC_READ_DISCRETE_INPUTS,
        ):
            start, count = struct.unpack(">HH", pdu[1:5])
            out.append((fc, start, count))
        elif fc == FC_WRITE_SINGLE_REGISTER or fc == FC_WRITE_SINGLE_COIL:
            start = struct.unpack(">H", pdu[1:3])[0]
            out.append((fc, start, 1))
        elif fc == FC_WRITE_MULTIPLE_REGISTERS or fc == FC_WRITE_MULTIPLE_COILS:
            start, count = struct.unpack(">HH", pdu[1:5])
            out.append((fc, start, count))
    return out
