"""A minimal Modbus RTU slave used by both the SerialTransport and
RtuOverTcpTransport test suites.

Receives a fully framed RTU request (address + PDU + CRC), returns a
fully framed response, or returns None to simulate a non-responding slave.
Behavior is configurable via the constructor: register values, which unit
ids to answer for, and a flag to return a Modbus exception for unknown
addresses.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from pymod.errors import ModbusCRCError, ModbusProtocolError
from pymod.protocol.adu_rtu import decode_rtu_frame, encode_rtu_frame


@dataclass
class RtuSlave:
    """Minimal request/response slave for tests."""

    holding: dict[int, int] = field(default_factory=dict)
    coils: dict[int, bool] = field(default_factory=dict)
    accept_units: frozenset[int] | None = None  # None = accept all
    drop_next: int = 0  # number of upcoming requests to silently drop

    def __call__(self, frame: bytes) -> bytes | None:
        if self.drop_next > 0:
            self.drop_next -= 1
            return None
        try:
            unit, pdu = decode_rtu_frame(frame)
        except (ModbusCRCError, ModbusProtocolError):
            return None
        if self.accept_units is not None and unit not in self.accept_units:
            return None  # not addressed to us, stay silent
        fc = pdu[0]
        if fc == 0x03:
            return self._fc03(unit, pdu)
        if fc == 0x06:
            return self._fc06(unit, pdu)
        if fc == 0x10:
            return self._fc10(unit, pdu)
        # Illegal function exception.
        return encode_rtu_frame(unit, bytes([fc | 0x80, 0x01]))

    def _fc03(self, unit: int, pdu: bytes) -> bytes:
        start, count = struct.unpack(">HH", pdu[1:5])
        regs = []
        for i in range(count):
            addr = start + i
            if self.holding and addr not in self.holding:
                # Illegal data address.
                return encode_rtu_frame(unit, bytes([0x83, 0x02]))
            regs.append(self.holding.get(addr, 0))
        body = b"".join(v.to_bytes(2, "big") for v in regs)
        response_pdu = bytes([0x03, len(body)]) + body
        return encode_rtu_frame(unit, response_pdu)

    def _fc06(self, unit: int, pdu: bytes) -> bytes:
        addr, value = struct.unpack(">HH", pdu[1:5])
        self.holding[addr] = value
        return encode_rtu_frame(unit, pdu)

    def _fc10(self, unit: int, pdu: bytes) -> bytes:
        start, count = struct.unpack(">HH", pdu[1:5])
        byte_count = pdu[5]
        if byte_count != 2 * count:
            return encode_rtu_frame(unit, bytes([0x90, 0x03]))
        for i in range(count):
            v = int.from_bytes(pdu[6 + 2 * i : 8 + 2 * i], "big")
            self.holding[start + i] = v
        response_pdu = bytes([0x10]) + struct.pack(">HH", start, count)
        return encode_rtu_frame(unit, response_pdu)


# Convenience for tests that want a non-stateful echo-style behavior.
def make_simple_slave(holding: dict[int, int] | None = None) -> Callable[[bytes], bytes | None]:
    return RtuSlave(holding=dict(holding) if holding else {})
