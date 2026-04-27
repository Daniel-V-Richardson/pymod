"""Modbus TCP server — callback-based, datastore-free.

The server owns no data. The host application supplies callbacks that
read from and write to whatever store it already has (BACnet object
cache, MQTT mirror, in-memory dict — pymod doesn't care).

Authorization is implicit in the area: `INPUT_REGISTER` and
`DISCRETE_INPUT` are read-only because no write FC targets them.
`HOLDING_REGISTER` and `COIL` are read-write. The host decides which data
to expose through which area; if no `on_write` callback is supplied at
all, every write FC returns ILLEGAL_FUNCTION.

Callback exception semantics:

* Raise a `ModbusExceptionResponse` subclass (e.g. `IllegalDataAddress`)
  to surface that specific Modbus exception code on the wire.
* Raise anything else and the server logs the exception at ERROR and
  responds with `SLAVE_DEVICE_FAILURE` (0x04).

Callbacks may be sync or async — `inspect.isawaitable` handles both.

Usage:

    async def on_read(area, address, count):
        return host_store.read(area, address, count)

    async def on_write(area, address, values):
        host_store.write(area, address, values)

    server = pymod.Server(host="0.0.0.0", port=502,
                          on_read=on_read, on_write=on_write)
    async with server:
        await asyncio.Future()  # run forever
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Self, cast

from ._types import Area
from .errors import (
    IllegalDataValue,
    IllegalFunction,
    ModbusError,
    ModbusExceptionResponse,
    ModbusProtocolError,
    SlaveDeviceFailure,
)
from .protocol.adu_tcp import (
    MBAP_MIN_PEEK_BYTES,
    decode_mbap_frame,
    encode_mbap_frame,
    expected_frame_length,
)
from .protocol.pdu import (
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE_REGISTERS,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE_REGISTER,
    decode_request_read_coils,
    decode_request_read_discrete_inputs,
    decode_request_read_holding_registers,
    decode_request_read_input_registers,
    decode_request_write_multiple_coils,
    decode_request_write_multiple_registers,
    decode_request_write_single_coil,
    decode_request_write_single_register,
    encode_exception_response,
    encode_response_read_coils,
    encode_response_read_discrete_inputs,
    encode_response_read_holding_registers,
    encode_response_read_input_registers,
    encode_response_write_multiple_coils,
    encode_response_write_multiple_registers,
    encode_response_write_single_coil,
    encode_response_write_single_register,
)

_logger = logging.getLogger("pymod.server")
_wire = logging.getLogger("pymod.wire")

ReadCallback = Callable[
    [Area, int, int],
    Awaitable[Sequence[int | bool]] | Sequence[int | bool],
]
WriteCallback = Callable[
    [Area, int, Sequence[int | bool]],
    Awaitable[None] | None,
]

# Per-spec exception codes used directly by the server when no specific
# callback exception applies.
_GATEWAY_TARGET_FAILED_TO_RESPOND = 0x0B


class Server:
    """Modbus TCP server with callback-based data access."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 502,
        on_read: ReadCallback,
        on_write: WriteCallback | None = None,
        unit_id: int | None = None,
        max_connections: int = 32,
    ) -> None:
        if max_connections < 1:
            raise ValueError(f"max_connections must be >= 1, got {max_connections}")
        self._host = host
        self._port = port
        self._on_read = on_read
        self._on_write = on_write
        self._unit_id = unit_id
        self._max_connections = max_connections

        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._client_tasks: set[asyncio.Task[None]] = set()

    # ---------- public API ----------

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._on_client, self._host, self._port
        )
        sockets = self._server.sockets
        if sockets:
            sockname = sockets[0].getsockname()
            _logger.info("listening on %s", sockname)

    async def stop(self) -> None:
        # Drop active client connections.
        writers = list(self._connections)
        self._connections.clear()
        for w in writers:
            try:
                w.close()
            except Exception:
                pass
        for w in writers:
            try:
                await w.wait_closed()
            except Exception:
                pass
        # Cancel any in-flight handler tasks.
        tasks = list(self._client_tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._client_tasks.clear()
        # Shut down listener.
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    @property
    def port(self) -> int:
        """Bound port. Useful when port=0 was used (ephemeral port)."""
        if self._server is None:
            return self._port
        sockets = self._server.sockets
        if not sockets:
            return self._port
        return cast(int, sockets[0].getsockname()[1])

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # ---------- per-connection handler ----------

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        if len(self._connections) >= self._max_connections:
            _logger.warning(
                "rejecting connection from %s: max_connections=%d reached",
                peer,
                self._max_connections,
            )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        self._connections.add(writer)
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        _logger.debug(
            "client connected: %s (active=%d)", peer, len(self._connections)
        )
        try:
            while True:
                header = await reader.readexactly(MBAP_MIN_PEEK_BYTES)
                total = expected_frame_length(header)
                rest = await reader.readexactly(total - MBAP_MIN_PEEK_BYTES)
                frame = header + rest
                if _wire.isEnabledFor(logging.DEBUG):
                    _wire.debug("RX from %s: %s", peer, frame.hex())
                try:
                    tid, uid, request_pdu = decode_mbap_frame(frame)
                except ModbusProtocolError:
                    _logger.exception("malformed MBAP frame from %s; dropping", peer)
                    continue
                response_pdu = await self._dispatch(uid, request_pdu)
                response_frame = encode_mbap_frame(tid, uid, response_pdu)
                if _wire.isEnabledFor(logging.DEBUG):
                    _wire.debug("TX to %s: %s", peer, response_frame.hex())
                writer.write(response_frame)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("unexpected error in handler for %s", peer)
        finally:
            self._connections.discard(writer)
            if task is not None:
                self._client_tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass
            _logger.debug(
                "client disconnected: %s (active=%d)",
                peer,
                len(self._connections),
            )

    # ---------- request dispatch ----------

    async def _dispatch(self, uid: int, pdu: bytes) -> bytes:
        if len(pdu) == 0:
            return encode_exception_response(0, IllegalFunction.code)
        fc = pdu[0]
        if self._unit_id is not None and uid != self._unit_id:
            return encode_exception_response(fc, _GATEWAY_TARGET_FAILED_TO_RESPOND)
        try:
            return await self._handle(fc, pdu)
        except ModbusExceptionResponse as e:
            return encode_exception_response(fc, e.code)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("callback raised unhandled exception on FC %#04x", fc)
            return encode_exception_response(fc, SlaveDeviceFailure.code)

    async def _handle(self, fc: int, pdu: bytes) -> bytes:
        # Read FCs.
        if fc == FC_READ_COILS:
            start, count = decode_request_read_coils(pdu)
            values = await self._call_read(Area.COIL, start, count)
            return encode_response_read_coils(_to_bool_list(values, count))
        if fc == FC_READ_DISCRETE_INPUTS:
            start, count = decode_request_read_discrete_inputs(pdu)
            values = await self._call_read(Area.DISCRETE_INPUT, start, count)
            return encode_response_read_discrete_inputs(_to_bool_list(values, count))
        if fc == FC_READ_HOLDING_REGISTERS:
            start, count = decode_request_read_holding_registers(pdu)
            values = await self._call_read(Area.HOLDING_REGISTER, start, count)
            return encode_response_read_holding_registers(_to_int_list(values, count))
        if fc == FC_READ_INPUT_REGISTERS:
            start, count = decode_request_read_input_registers(pdu)
            values = await self._call_read(Area.INPUT_REGISTER, start, count)
            return encode_response_read_input_registers(_to_int_list(values, count))

        # Write FCs require on_write.
        if fc in (
            FC_WRITE_SINGLE_COIL,
            FC_WRITE_SINGLE_REGISTER,
            FC_WRITE_MULTIPLE_COILS,
            FC_WRITE_MULTIPLE_REGISTERS,
        ):
            if self._on_write is None:
                return encode_exception_response(fc, IllegalFunction.code)
            if fc == FC_WRITE_SINGLE_COIL:
                coil_addr, coil_value = decode_request_write_single_coil(pdu)
                await self._call_write(Area.COIL, coil_addr, [coil_value])
                return encode_response_write_single_coil(coil_addr, coil_value)
            if fc == FC_WRITE_SINGLE_REGISTER:
                reg_addr, reg_value = decode_request_write_single_register(pdu)
                await self._call_write(Area.HOLDING_REGISTER, reg_addr, [reg_value])
                return encode_response_write_single_register(reg_addr, reg_value)
            if fc == FC_WRITE_MULTIPLE_COILS:
                start, bits = decode_request_write_multiple_coils(pdu)
                await self._call_write(Area.COIL, start, bits)
                return encode_response_write_multiple_coils(start, len(bits))
            if fc == FC_WRITE_MULTIPLE_REGISTERS:
                start, regs = decode_request_write_multiple_registers(pdu)
                await self._call_write(Area.HOLDING_REGISTER, start, regs)
                return encode_response_write_multiple_registers(start, len(regs))

        # Unknown / unsupported function code.
        return encode_exception_response(fc, IllegalFunction.code)

    # ---------- callback adapters ----------

    async def _call_read(
        self,
        area: Area,
        address: int,
        count: int,
    ) -> Sequence[int | bool]:
        result: Any = self._on_read(area, address, count)
        if inspect.isawaitable(result):
            result = await result
        return cast("Sequence[int | bool]", result)

    async def _call_write(
        self,
        area: Area,
        address: int,
        values: Sequence[int | bool],
    ) -> None:
        assert self._on_write is not None
        result: Any = self._on_write(area, address, values)
        if inspect.isawaitable(result):
            await result


# ---------- helpers ----------


def _to_bool_list(values: Sequence[int | bool], expected_count: int) -> list[bool]:
    if len(values) != expected_count:
        raise SlaveDeviceFailure(
            f"on_read returned {len(values)} values, expected {expected_count}"
        )
    return [bool(v) for v in values]


def _to_int_list(values: Sequence[int | bool], expected_count: int) -> list[int]:
    if len(values) != expected_count:
        raise SlaveDeviceFailure(
            f"on_read returned {len(values)} values, expected {expected_count}"
        )
    out: list[int] = []
    for v in values:
        iv = int(v)
        if not 0 <= iv <= 0xFFFF:
            raise SlaveDeviceFailure(f"on_read returned out-of-range register value {iv}")
        out.append(iv)
    return out


# Re-export ModbusError for callers who want to subclass / catch.
__all__ = [
    "ReadCallback",
    "Server",
    "WriteCallback",
    "ModbusError",
    "IllegalDataValue",
]
