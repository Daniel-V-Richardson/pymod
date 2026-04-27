"""In-process RTU-over-TCP server for transport tests.

Reads RTU request frames off a TCP socket, passes them to a slave callable,
writes the slave's response back. No MBAP — the framing on the wire is the
same as serial RTU (slave-addr + PDU + CRC).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Self

# Slave callable: (rtu_frame) -> rtu_response_frame | None.
SlaveCallback = Callable[[bytes], bytes | None]


async def _read_rtu_request_async(
    reader: asyncio.StreamReader,
) -> bytes:
    """Read a complete RTU request frame from a TCP stream (server side)."""
    head = await reader.readexactly(2)  # addr + FC
    fc_byte = head[1]
    if fc_byte in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06):
        # 4 bytes payload + 2 CRC.
        rest = await reader.readexactly(6)
    elif fc_byte in (0x0F, 0x10):
        # 4 bytes (start + qty) + 1 byte byte_count, then bc bytes + 2 CRC.
        meta = await reader.readexactly(5)
        byte_count = meta[4]
        rest = meta + await reader.readexactly(byte_count + 2)
    else:
        # Unsupported FC for our test harness.
        raise ValueError(f"unsupported FC in test server: {fc_byte:#04x}")
    return head + rest


class FakeRtuOverTcpServer:
    """Listener + per-connection request/response loop, sequential per
    connection (matching the real-world Moxa-style gateway shape)."""

    def __init__(self, slave: SlaveCallback) -> None:
        self._slave = slave
        self._server: asyncio.Server | None = None
        self._port = 0
        self._client_writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        sockets = self._server.sockets
        assert sockets, "server has no sockets"
        self._port = sockets[0].getsockname()[1]

    async def drop_clients(self) -> None:
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
        await self.drop_clients()
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
                request = await _read_rtu_request_async(reader)
                response = self._slave(request)
                if response is None:
                    continue  # silent: no reply
                if writer.is_closing():
                    return
                writer.write(response)
                await writer.drain()
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
