"""Client API — async source of truth and a sync facade.

`AsyncClient` owns a `Transport` and a `RetryPolicy`. Its primary public
method is `execute(pdu, ...)` — it sends a request PDU, applies the retry
policy, and returns the response PDU. The transport handles framing, the
client handles retries and per-call overrides.

`Client` is a synchronous facade running an `AsyncClient` on a private
event loop in a daemon thread. CLI users and ad-hoc scripts call sync
methods that block until the underlying coroutine completes.

Both `AsyncClient` and `Client` are safe to share across many concurrent
callers — the underlying transport serializes RTU buses internally and
demultiplexes pipelined TCP responses.

The typed batch `read([Holding(...), Coil(...)])` and `write([...])` APIs
are stubbed here; the planner that powers them is delivered in M4. Until
then, use `execute()` with raw PDUs encoded by `pymod.protocol.pdu`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from concurrent.futures import Future as ThreadFuture
from typing import Any, Self, TypeVar

from ._types import ReadItem, ReadResult, WriteItem, WriteResult
from .errors import ModbusError
from .planner import plan_and_execute_reads, plan_and_execute_writes
from .retry import RetryPolicy
from .transport import RtuOverTcpTransport, SerialTransport, TcpTransport
from .transport.base import Transport

_logger = logging.getLogger("pymod.client")

DEFAULT_TIMEOUT_S = 0.5
_DEFAULT_RETRY = RetryPolicy()

_T = TypeVar("_T")


# ---------- AsyncClient ----------------------------------------------------


class AsyncClient:
    """Async Modbus client. Construct via the `tcp`, `rtu`, or
    `rtu_over_tcp` factories."""

    def __init__(
        self,
        transport: Transport,
        *,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._transport = transport
        self._default_unit_id = unit_id
        self._default_timeout_s = timeout_s
        self._default_retry = retry if retry is not None else _DEFAULT_RETRY

    # ---------- Factories ----------

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int = 502,
        *,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        pipeline: bool = True,
        connect_timeout_s: float = 3.0,
    ) -> Self:
        """Modbus TCP client. One auto-reconnecting connection per
        (host, port). Pipelining can be disabled per-PLC."""
        transport: Transport = TcpTransport(
            host,
            port,
            pipeline=pipeline,
            connect_timeout_s=connect_timeout_s,
        )
        return cls(transport, unit_id=unit_id, timeout_s=timeout_s, retry=retry)

    @classmethod
    def rtu(
        cls,
        port: str,
        baudrate: int = 9600,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        inter_frame_delay_s: float | None = None,
    ) -> Self:
        """Modbus RTU client over a serial port. Internal lock serializes
        the bus across callers."""
        transport: Transport = SerialTransport(
            port,
            baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            inter_frame_delay_s=inter_frame_delay_s,
        )
        return cls(transport, unit_id=unit_id, timeout_s=timeout_s, retry=retry)

    @classmethod
    def rtu_over_tcp(
        cls,
        host: str,
        port: int,
        *,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        connect_timeout_s: float = 3.0,
    ) -> Self:
        """RTU framing over a TCP socket — for serial-to-Ethernet gateways."""
        transport: Transport = RtuOverTcpTransport(
            host, port, connect_timeout_s=connect_timeout_s
        )
        return cls(transport, unit_id=unit_id, timeout_s=timeout_s, retry=retry)

    # ---------- Lifecycle ----------

    async def connect(self) -> None:
        await self._transport.connect()

    async def close(self) -> None:
        await self._transport.close()

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ---------- Low-level execute ----------

    async def execute(
        self,
        pdu: bytes,
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> bytes:
        """Send a request PDU, return the response PDU.

        The retry policy is applied to errors listed in `policy.retry_on`
        (default: `ModbusTimeoutError`, `ModbusConnectionError`). Other
        errors — notably `ModbusExceptionResponse` subclasses — are NOT
        retried, on the assumption that retrying an illegal address yields
        the same illegal address.

        Per-call overrides default to the values supplied at client
        construction time.
        """
        effective_unit_id = unit_id if unit_id is not None else self._default_unit_id
        effective_timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        policy = retry if retry is not None else self._default_retry

        last_exc: BaseException | None = None
        for attempt in range(1, policy.max_attempts + 1):
            if attempt > 1:
                await asyncio.sleep(policy.delay_for(attempt - 1))
            try:
                return await self._transport.send_and_receive(
                    pdu,
                    unit_id=effective_unit_id,
                    timeout_s=effective_timeout,
                )
            except policy.retry_on as e:
                last_exc = e
                if attempt < policy.max_attempts:
                    _logger.debug(
                        "attempt %d/%d failed (%s: %s); retrying",
                        attempt,
                        policy.max_attempts,
                        type(e).__name__,
                        e,
                    )
                    continue
                raise
        # Unreachable: the loop either returns or raises.
        assert last_exc is not None
        raise last_exc

    # ---------- Typed batch reads/writes ----------

    async def read(
        self,
        items: Sequence[ReadItem],
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> list[ReadResult]:
        """Issue a heterogeneous batch read.

        Adjacent same-area ranges are coalesced into the minimum number of
        Modbus reads; per-FC PDU limits are honored by automatic splitting.
        Returns a list parallel to `items` — one item failing does not
        abort the batch.
        """
        executor = self._make_pdu_executor(unit_id, timeout_s, retry)
        return await plan_and_execute_reads(items, executor)

    async def write(
        self,
        items: Sequence[WriteItem],
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> list[WriteResult]:
        """Issue a batch write. FC05/06 vs FC15/16 selected per item by
        value count. Per-item results parallel `items`."""
        executor = self._make_pdu_executor(unit_id, timeout_s, retry)
        return await plan_and_execute_writes(items, executor)

    def _make_pdu_executor(
        self,
        unit_id: int | None,
        timeout_s: float | None,
        retry: RetryPolicy | None,
    ) -> "Callable[[bytes], Awaitable[bytes]]":
        eu = unit_id if unit_id is not None else self._default_unit_id
        et = timeout_s if timeout_s is not None else self._default_timeout_s
        er = retry if retry is not None else self._default_retry

        async def _exec(pdu: bytes) -> bytes:
            return await self.execute(pdu, unit_id=eu, timeout_s=et, retry=er)

        return _exec


# ---------- Sync facade ----------------------------------------------------


class _BackgroundLoop:
    """Owns an asyncio event loop running on a daemon thread.

    Coroutines submitted via `run_coroutine` execute on the loop's thread
    and the calling (synchronous) thread blocks on the resulting future.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="pymod-sync-loop",
        )
        self._thread.start()
        self._started.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def run_coroutine(self, coro: Coroutine[Any, Any, _T]) -> ThreadFuture[_T]:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()


class Client:
    """Synchronous facade over `AsyncClient`.

    Owns a private daemon-thread event loop. Each sync call schedules the
    underlying coroutine on that loop and blocks the caller until it
    completes. The event loop is stopped on `close()`.
    """

    def __init__(self, async_client: AsyncClient, loop: _BackgroundLoop) -> None:
        self._async = async_client
        self._loop = loop

    # ---------- Factories ----------

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int = 502,
        *,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        pipeline: bool = True,
        connect_timeout_s: float = 3.0,
    ) -> Self:
        loop = _BackgroundLoop()
        async_client = AsyncClient.tcp(
            host,
            port,
            unit_id=unit_id,
            timeout_s=timeout_s,
            retry=retry,
            pipeline=pipeline,
            connect_timeout_s=connect_timeout_s,
        )
        return cls(async_client, loop)

    @classmethod
    def rtu(
        cls,
        port: str,
        baudrate: int = 9600,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        inter_frame_delay_s: float | None = None,
    ) -> Self:
        loop = _BackgroundLoop()
        async_client = AsyncClient.rtu(
            port,
            baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            unit_id=unit_id,
            timeout_s=timeout_s,
            retry=retry,
            inter_frame_delay_s=inter_frame_delay_s,
        )
        return cls(async_client, loop)

    @classmethod
    def rtu_over_tcp(
        cls,
        host: str,
        port: int,
        *,
        unit_id: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        connect_timeout_s: float = 3.0,
    ) -> Self:
        loop = _BackgroundLoop()
        async_client = AsyncClient.rtu_over_tcp(
            host,
            port,
            unit_id=unit_id,
            timeout_s=timeout_s,
            retry=retry,
            connect_timeout_s=connect_timeout_s,
        )
        return cls(async_client, loop)

    # ---------- Lifecycle ----------

    def connect(self) -> None:
        self._loop.run_coroutine(self._async.connect()).result()

    def close(self) -> None:
        if self._loop.is_alive:
            try:
                self._loop.run_coroutine(self._async.close()).result(timeout=5.0)
            except Exception:
                pass
            self._loop.stop()

    @property
    def is_connected(self) -> bool:
        return self._async.is_connected

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- execute ----------

    def execute(
        self,
        pdu: bytes,
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> bytes:
        return self._loop.run_coroutine(
            self._async.execute(
                pdu, unit_id=unit_id, timeout_s=timeout_s, retry=retry
            )
        ).result()

    # ---------- Typed reads/writes (M4) ----------

    def read(
        self,
        items: Sequence[ReadItem],
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> list[ReadResult]:
        return self._loop.run_coroutine(
            self._async.read(items, unit_id=unit_id, timeout_s=timeout_s, retry=retry)
        ).result()

    def write(
        self,
        items: Sequence[WriteItem],
        *,
        unit_id: int | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> list[WriteResult]:
        return self._loop.run_coroutine(
            self._async.write(items, unit_id=unit_id, timeout_s=timeout_s, retry=retry)
        ).result()
