"""Test double for `pymod.transport.base.Transport`.

`send_and_receive` is driven by a list of canned responses: pop the next
one and either return it (if `bytes`) or raise it (if a `BaseException`).
This makes retry-policy tests deterministic without needing a real socket.
"""

from __future__ import annotations

from collections.abc import Iterable


class FakeTransport:
    def __init__(self, responses: Iterable[bytes | BaseException]) -> None:
        self._responses: list[bytes | BaseException] = list(responses)
        self.call_log: list[tuple[bytes, int, float]] = []
        self._connected = False
        self._closed = False

    async def connect(self) -> None:
        if self._closed:
            raise RuntimeError("closed")
        self._connected = True

    async def close(self) -> None:
        self._connected = False
        self._closed = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_and_receive(
        self,
        pdu: bytes,
        *,
        unit_id: int,
        timeout_s: float,
    ) -> bytes:
        self.call_log.append((pdu, unit_id, timeout_s))
        if not self._responses:
            raise AssertionError("FakeTransport: out of canned responses")
        r = self._responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r
