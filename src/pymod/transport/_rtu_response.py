"""FC-aware RTU response reader.

Modbus RTU frames have no length prefix on the wire — the receiver must
infer total length from the function code (and, for read responses, from
the byte-count byte that follows it). Both the serial transport and the
RTU-over-TCP transport need this logic, so it lives here in a single place.

Two entry points: one async (for the TCP-based transport, reading from a
StreamReader) and one sync (for the serial transport's executor thread).
The dispatch logic is identical; only the I/O primitive differs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..errors import ModbusProtocolError

EXCEPTION_BIT = 0x80
_READ_FCS = frozenset({0x01, 0x02, 0x03, 0x04})
_WRITE_FCS = frozenset({0x05, 0x06, 0x0F, 0x10})

AsyncReadExactly = Callable[[int], Awaitable[bytes]]
SyncReadExactly = Callable[[int], bytes]


def _unknown_fc_error(fc_byte: int) -> ModbusProtocolError:
    return ModbusProtocolError(
        f"unknown function code in response: {fc_byte:#04x}; "
        "custom FC support over RTU not yet implemented"
    )


async def read_rtu_response_async(read_exactly: AsyncReadExactly) -> bytes:
    """Read a complete RTU response frame from an async byte source.

    `read_exactly(n)` must return exactly n bytes or raise (e.g.
    `asyncio.IncompleteReadError`, `ConnectionError`). CRC validation is
    the caller's responsibility — this function only handles framing.
    """
    head = await read_exactly(2)
    fc_byte = head[1]
    if fc_byte & EXCEPTION_BIT:
        return head + await read_exactly(3)
    if fc_byte in _READ_FCS:
        bc = await read_exactly(1)
        return head + bc + await read_exactly(bc[0] + 2)
    if fc_byte in _WRITE_FCS:
        return head + await read_exactly(6)
    raise _unknown_fc_error(fc_byte)


def read_rtu_response_sync(read_exactly: SyncReadExactly) -> bytes:
    """Synchronous variant for the serial transport's executor thread."""
    head = read_exactly(2)
    fc_byte = head[1]
    if fc_byte & EXCEPTION_BIT:
        return head + read_exactly(3)
    if fc_byte in _READ_FCS:
        bc = read_exactly(1)
        return head + bc + read_exactly(bc[0] + 2)
    if fc_byte in _WRITE_FCS:
        return head + read_exactly(6)
    raise _unknown_fc_error(fc_byte)
