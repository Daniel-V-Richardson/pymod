"""Async Modbus TCP transport.

One asyncio connection per (host, port). Auto-reconnect on disconnect.
Pipelined in-flight requests are demultiplexed by the MBAP transaction id:
each `send_and_receive` registers a future under the next free TID, a
background read loop dispatches incoming responses to those futures, and
the call awaits its own.

Pipelining can be opted out of (`pipeline=False`) for slaves that mishandle
interleaved responses; in that mode all calls are strictly serialized
behind an internal lock but the wire framing is unchanged.
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
from ..protocol.adu_tcp import (
    MBAP_MIN_PEEK_BYTES,
    decode_mbap_frame,
    encode_mbap_frame,
    expected_frame_length,
)

_logger = logging.getLogger("pymod.transport.tcp")
_wire = logging.getLogger("pymod.wire")


class TcpTransport:
    """Async Modbus TCP transport with optional request pipelining."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        *,
        pipeline: bool = True,
        connect_timeout_s: float = 3.0,
    ) -> None:
        self._host = host
        self._port = port
        self._pipeline = pipeline
        self._connect_timeout_s = connect_timeout_s

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._connected = False
        self._closed = False

        self._next_tid = 0
        self._pending: dict[int, asyncio.Future[bytes]] = {}

        self._connection_lock = asyncio.Lock()
        self._serial_lock: asyncio.Lock | None = None if pipeline else asyncio.Lock()

    # ---------- public API ----------

    async def connect(self) -> None:
        if self._closed:
            raise ModbusError("transport is closed")
        await self._ensure_connected()

    async def close(self) -> None:
        self._closed = True
        await self._teardown(reason="closed by user")

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
        if self._serial_lock is not None:
            async with self._serial_lock:
                return await self._send_and_receive_unlocked(pdu, unit_id, timeout_s)
        return await self._send_and_receive_unlocked(pdu, unit_id, timeout_s)

    # ---------- internal ----------

    async def _send_and_receive_unlocked(
        self,
        pdu: bytes,
        unit_id: int,
        timeout_s: float,
    ) -> bytes:
        await self._ensure_connected()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        tid = self._allocate_tid()
        self._pending[tid] = future
        try:
            writer = self._writer
            if writer is None:
                # Race: connection died between _ensure_connected returning
                # and us reading the writer. Treat as transient.
                raise ModbusConnectionError("connection unavailable")
            frame = encode_mbap_frame(tid, unit_id, pdu)
            if _wire.isEnabledFor(logging.DEBUG):
                _wire.debug("TX tid=%d unit=%d frame=%s", tid, unit_id, frame.hex())
            try:
                writer.write(frame)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                raise ModbusConnectionError(f"send failed: {e}") from e
            try:
                return await asyncio.wait_for(future, timeout_s)
            except asyncio.TimeoutError as e:
                raise ModbusTimeoutError(
                    f"no response within {timeout_s}s for tid={tid}"
                ) from e
        finally:
            self._pending.pop(tid, None)

    async def _ensure_connected(self) -> None:
        if self._connected:
            return
        async with self._connection_lock:
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
            self._read_task = asyncio.create_task(
                self._read_loop(), name=f"pymod-tcp-read-{self._host}:{self._port}"
            )
            _logger.info("connected to %s:%d", self._host, self._port)

    def _allocate_tid(self) -> int:
        # Linear probe over a 16-bit space; collisions are vanishingly rare
        # unless 65k requests are simultaneously pending, which would imply
        # other problems.
        for _ in range(0x10000):
            tid = self._next_tid
            self._next_tid = (self._next_tid + 1) & 0xFFFF
            if tid not in self._pending:
                return tid
        raise ModbusError("transaction id space exhausted")

    async def _read_loop(self) -> None:
        reader = self._reader
        assert reader is not None
        try:
            while True:
                header = await reader.readexactly(MBAP_MIN_PEEK_BYTES)
                total = expected_frame_length(header)
                rest = await reader.readexactly(total - MBAP_MIN_PEEK_BYTES)
                frame = header + rest
                if _wire.isEnabledFor(logging.DEBUG):
                    _wire.debug("RX frame=%s", frame.hex())
                try:
                    tid, _uid, pdu = decode_mbap_frame(frame)
                except ModbusProtocolError:
                    _logger.exception("malformed response frame; dropping")
                    continue
                future = self._pending.get(tid)
                if future is None:
                    _logger.warning("unmatched response tid=%d", tid)
                    continue
                if not future.done():
                    future.set_result(pdu)
        except asyncio.IncompleteReadError as e:
            _logger.info("connection closed by peer: %s", e)
        except (ConnectionResetError, OSError) as e:
            _logger.info("connection lost: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("unexpected error in read loop")
        finally:
            await self._teardown(reason="read loop ended")

    async def _teardown(self, *, reason: str) -> None:
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

        # Cancel the read task only if we are not the read task itself.
        task = self._read_task
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if task is current:
            # Read loop is running its own finally; clear the handle without
            # awaiting.
            self._read_task = None
        else:
            self._read_task = None

        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ModbusConnectionError(f"connection lost ({reason})"))
        self._pending.clear()
