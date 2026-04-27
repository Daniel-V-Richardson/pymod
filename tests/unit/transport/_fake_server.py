"""In-process Modbus TCP server for transport tests.

Plain-asyncio server that reads MBAP frames, calls a user-supplied async
handler with the request PDU, and writes the response PDU back wrapped in
MBAP with the original transaction id echoed.

Requests are processed concurrently — the handler can sleep on one request
without blocking others, which is how we test pipelined out-of-order
responses on the client side.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Self

from pymod.protocol.adu_tcp import (
    MBAP_MIN_PEEK_BYTES,
    decode_mbap_frame,
    encode_mbap_frame,
    expected_frame_length,
)

PduHandler = Callable[[bytes], Awaitable[bytes]]


class FakeModbusServer:
    """Minimal MBAP echo server. Bind to an ephemeral port; pass `port`
    into the transport under test."""

    def __init__(self, handler: PduHandler) -> None:
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._port = 0
        self._client_writers: list[asyncio.StreamWriter] = []
        self._handler_tasks: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        sockets = self._server.sockets
        assert sockets, "server has no sockets"
        self._port = sockets[0].getsockname()[1]

    async def drop_clients(self) -> None:
        """Close all currently connected clients without stopping the listener.

        Lets tests simulate a transient network blip — the listener stays
        up so the transport's auto-reconnect logic can re-establish.
        """
        writers = list(self._client_writers)
        self._client_writers.clear()
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

    async def stop(self) -> None:
        # Drop active client connections, then shut down the listener.
        await self.drop_clients()
        for t in list(self._handler_tasks):
            if not t.done():
                t.cancel()
        for t in list(self._handler_tasks):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._handler_tasks.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._client_writers.append(writer)
        try:
            while True:
                header = await reader.readexactly(MBAP_MIN_PEEK_BYTES)
                total = expected_frame_length(header)
                rest = await reader.readexactly(total - MBAP_MIN_PEEK_BYTES)
                frame = header + rest
                task = asyncio.create_task(self._handle_request(frame, writer))
                self._handler_tasks.add(task)
                task.add_done_callback(self._handler_tasks.discard)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_request(
        self,
        frame: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        tid, uid, pdu = decode_mbap_frame(frame)
        try:
            response_pdu = await self._handler(pdu)
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if writer.is_closing():
            return
        try:
            writer.write(encode_mbap_frame(tid, uid, response_pdu))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
