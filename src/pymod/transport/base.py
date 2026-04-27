"""Abstract transport interface.

A transport accepts a PDU (function-code byte + per-FC payload), wraps it
in the appropriate framing for its medium (MBAP for TCP, slave-addr + CRC
for RTU), sends it, reads the matching response, strips the framing, and
returns the response PDU.

Concurrency safety is the implementer's concern: TCP demuxes pipelined
responses by transaction id; serial-RTU serializes calls behind a lock.
Callers never see TIDs, locks, sockets, or CRCs.
"""

from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    """Common transport interface for TCP, RTU-serial, and RTU-over-TCP."""

    async def connect(self) -> None:
        """Establish the underlying connection (open socket, open serial
        port). May be called explicitly or implicitly by `send_and_receive`.
        """
        ...

    async def close(self) -> None:
        """Tear down the connection and reject further calls."""
        ...

    @property
    def is_connected(self) -> bool: ...

    async def send_and_receive(
        self,
        pdu: bytes,
        *,
        unit_id: int,
        timeout_s: float,
    ) -> bytes:
        """Send `pdu`, return the response PDU.

        Raises `ModbusTimeoutError` if no response arrives within `timeout_s`,
        `ModbusConnectionError` if the connection cannot be established or
        is lost mid-call, and `ModbusProtocolError` if the response is
        malformed.
        """
        ...
