"""In-process fake of `pyserial.Serial` for SerialTransport tests.

The fake is synchronous (matching pyserial's blocking API). When the
transport calls `write(frame)` the fake invokes a user-supplied slave
callback synchronously, feeds the response into its read buffer, and the
subsequent `read(n)` returns those bytes. There is no actual I/O — the
transport's executor thread does call into this object normally, but the
fake never blocks.

Returning `None` from the slave callback simulates a non-responsive slave:
the read buffer stays empty, `read()` returns fewer bytes than requested,
and the transport raises `ModbusTimeoutError`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

SlaveCallback = Callable[[bytes], bytes | None]


class FakeSerial:
    """Match the slice of `pyserial.Serial` that SerialTransport uses."""

    def __init__(self, slave: SlaveCallback) -> None:
        self._slave = slave
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        self.timeout: float | None = None
        self.write_timeout: float | None = None
        self.write_log: list[bytes] = []

        # Concurrency-tracking bookkeeping.
        self._in_flight = 0
        self.max_in_flight = 0

    def write(self, data: bytes) -> int:
        with self._lock:
            if self._closed:
                raise OSError("port closed")
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            self.write_log.append(bytes(data))
            response = self._slave(bytes(data))
            with self._lock:
                if response is not None:
                    self._buffer.extend(response)
            return len(data)
        finally:
            with self._lock:
                self._in_flight -= 1

    def read(self, size: int) -> bytes:
        with self._lock:
            available = min(size, len(self._buffer))
            data = bytes(self._buffer[:available])
            del self._buffer[:available]
        return data

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True


def make_serial_factory(slave: SlaveCallback) -> Callable[..., FakeSerial]:
    """Return a factory matching SerialTransport's `serial_factory` shape."""
    fake = FakeSerial(slave)

    def factory(**_kwargs: Any) -> FakeSerial:
        return fake

    factory.fake = fake  # type: ignore[attr-defined]
    return factory
