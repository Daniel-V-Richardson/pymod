"""Helper for running a FakeModbusServer on a dedicated thread+loop.

Sync `Client` tests cannot share an event loop with the server: the sync
client's `execute()` blocks the calling thread until a response arrives,
which prevents that thread's loop from accepting the very connection it's
waiting on. Running the server on a separate thread sidesteps the deadlock.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

from ..transport._fake_server import FakeModbusServer

PduHandler = Callable[[bytes], Awaitable[bytes]]


class ThreadedFakeServer:
    """Run a `FakeModbusServer` on a dedicated thread + event loop."""

    def __init__(self, handler: PduHandler) -> None:
        self._handler = handler
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._server: FakeModbusServer | None = None
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="pymod-test-server",
        )
        self._thread.start()
        self._started.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._server = FakeModbusServer(self._handler)
        self._loop.run_until_complete(self._server.start())
        self._started.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.port

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        assert self._server is not None
        fut = asyncio.run_coroutine_threadsafe(self._server.stop(), self._loop)
        try:
            fut.result(timeout=5.0)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)

    def __enter__(self) -> "ThreadedFakeServer":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
