"""RTU framing over a TCP socket.

The transport is plain TCP, but the wire framing is the same RTU format
used on serial lines: slave-address byte, PDU, CRC16. There is no MBAP
header and no transaction id, so requests are strictly sequential — one
in flight at a time, serialized internally.

This is the protocol spoken by serial-to-Ethernet converters (Moxa NPort,
USR, etc.) commonly found in brownfield industrial deployments.

The CRC and frame-length logic is shared with the serial transport via
`_rtu_response.read_rtu_response_async` and `protocol.adu_rtu`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Self

from ..errors import (
    ModbusConnectionError,
    ModbusError,
    ModbusProtocolError,
    ModbusTimeoutError,
)
from ..protocol.adu_rtu import decode_rtu_frame, encode_rtu_frame
from ._rtu_response import read_rtu_response_async

_logger = logging.getLogger("pymod.transport.rtu_over_tcp")
_wire = logging.getLogger("pymod.wire")


class RtuOverTcpTransport:
    """RTU framing over a TCP connection. Sequential — no pipelining."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout_s: float = 3.0,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout_s = connect_timeout_s

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._closed = False
        self._lock = asyncio.Lock()

    # ---------- public API ----------

    async def connect(self) -> None:
        if self._closed:
            raise ModbusError("transport is closed")
        async with self._lock:
            await self._open_locked()

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            await self._teardown_locked(reason="closed by user")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def send_and_receive(
        self,
        pdu: bytes,
        *,
        unit_id: int,
        timeout_s: float,
    ) -> bytes:
        if self._closed:
            raise ModbusError("transport is closed")
        if len(pdu) == 0:
            raise ValueError("empty PDU")
        async with self._lock:
            await self._open_locked()
            assert self._writer is not None and self._reader is not None
            frame = encode_rtu_frame(unit_id, pdu)
            if _wire.isEnabledFor(logging.DEBUG):
                _wire.debug("TX unit=%d frame=%s", unit_id, frame.hex())
            try:
                self._writer.write(frame)
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                await self._teardown_locked(reason=f"send failed: {e}")
                raise ModbusConnectionError(f"send failed: {e}") from e

            try:
                response_frame = await asyncio.wait_for(
                    read_rtu_response_async(self._reader.readexactly),
                    timeout_s,
                )
            except asyncio.TimeoutError as e:
                # Don't tear down — the connection may still be healthy and
                # a stale response could still drain on the next call.
                # However stale bytes ARE a hazard; safer to drop and
                # reconnect lazily.
                await self._teardown_locked(reason="response timeout")
                raise ModbusTimeoutError(
                    f"no response within {timeout_s}s"
                ) from e
            except asyncio.IncompleteReadError as e:
                await self._teardown_locked(reason="connection lost mid-response")
                raise ModbusConnectionError(
                    f"connection lost mid-response: {e}"
                ) from e
            except (ConnectionResetError, OSError) as e:
                await self._teardown_locked(reason=f"connection lost: {e}")
                raise ModbusConnectionError(f"connection lost: {e}") from e

            if _wire.isEnabledFor(logging.DEBUG):
                _wire.debug("RX frame=%s", response_frame.hex())
            uid, response_pdu = decode_rtu_frame(response_frame)
            if uid != unit_id:
                raise ModbusProtocolError(
                    f"unit id mismatch in response: got {uid}, expected {unit_id}"
                )
            return response_pdu

    # ---------- internal ----------

    async def _open_locked(self) -> None:
        if self._connected:
            return
        if self._closed:
            raise ModbusError("transport is closed")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                self._connect_timeout_s,
            )
        except asyncio.TimeoutError as e:
            raise ModbusConnectionError(
                f"connect timeout to {self._host}:{self._port} "
                f"after {self._connect_timeout_s}s"
            ) from e
        except OSError as e:
            raise ModbusConnectionError(
                f"connect failed to {self._host}:{self._port}: {e}"
            ) from e
        self._reader = reader
        self._writer = writer
        self._connected = True
        _logger.info("connected to %s:%d (RTU/TCP)", self._host, self._port)

    async def _teardown_locked(self, *, reason: str) -> None:
        self._connected = False
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        _logger.debug("teardown: %s", reason)
