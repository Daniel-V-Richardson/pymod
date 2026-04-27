"""Tests for the synchronous `pymod.client.Client` facade."""

from __future__ import annotations

import asyncio

import pytest

from pymod.client import Client
from pymod.errors import ModbusTimeoutError
from pymod.protocol.pdu import (
    decode_read_holding_registers,
    encode_read_holding_registers,
)
from pymod.retry import RetryPolicy

from ._threaded_server import ThreadedFakeServer


def _holding_response(values: list[int]) -> bytes:
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([0x03, len(body)]) + body


class TestSyncClientLifecycle:
    def test_round_trip(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0xAB, 0xCD])

        with ThreadedFakeServer(handler) as srv:
            client = Client.tcp("127.0.0.1", srv.port, timeout_s=1.0)
            try:
                pdu = client.execute(encode_read_holding_registers(0, 2))
                assert decode_read_holding_registers(pdu, 2) == [0xAB, 0xCD]
            finally:
                client.close()

    def test_context_manager(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0x01])

        with ThreadedFakeServer(handler) as srv:
            with Client.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                assert c.is_connected
                pdu = c.execute(encode_read_holding_registers(0, 1))
                assert decode_read_holding_registers(pdu, 1) == [0x01]

    def test_close_idempotent(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0])

        with ThreadedFakeServer(handler) as srv:
            c = Client.tcp("127.0.0.1", srv.port, timeout_s=1.0)
            c.connect()
            c.close()
            c.close()  # second close: must not raise.


class TestSyncRetry:
    def test_timeout_propagates_after_retries_exhausted(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(10)
            return _holding_response([0])

        with ThreadedFakeServer(handler) as srv:
            c = Client.tcp(
                "127.0.0.1",
                srv.port,
                timeout_s=0.05,
                retry=RetryPolicy(max_attempts=2, backoff_initial_s=0.0),
            )
            try:
                with pytest.raises(ModbusTimeoutError):
                    c.execute(encode_read_holding_registers(0, 1))
            finally:
                c.close()


class TestSyncBatchEmpty:
    def test_read_empty_returns_empty(self) -> None:
        # No connection actually made.
        c = Client.tcp("127.0.0.1", 502)
        try:
            assert c.read([]) == []
        finally:
            c.close()

    def test_write_empty_returns_empty(self) -> None:
        c = Client.tcp("127.0.0.1", 502)
        try:
            assert c.write([]) == []
        finally:
            c.close()
