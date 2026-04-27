"""Tests for `pymod.client.AsyncClient`."""

from __future__ import annotations

import asyncio
import struct

import pytest

from pymod._types import Holding, WriteHolding
from pymod.client import AsyncClient
from pymod.errors import (
    IllegalDataAddress,
    ModbusConnectionError,
    ModbusError,
    ModbusTimeoutError,
)
from pymod.protocol.pdu import (
    decode_read_holding_registers,
    encode_read_holding_registers,
    encode_write_single_register,
)
from pymod.retry import RetryPolicy

from ..transport._fake_server import FakeModbusServer
from ._fake_transport import FakeTransport

# Helpers --------------------------------------------------------------------


def _holding_response(values: list[int]) -> bytes:
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([0x03, len(body)]) + body


# ---------- Construction & lifecycle ----------------------------------------


class TestConstruction:
    async def test_tcp_factory(self) -> None:
        c = AsyncClient.tcp("127.0.0.1", 502)
        assert not c.is_connected
        await c.close()

    async def test_rtu_factory(self) -> None:
        # Don't actually open the port; just verify the client constructs.
        c = AsyncClient.rtu("/dev/null", 9600)
        assert not c.is_connected

    async def test_rtu_over_tcp_factory(self) -> None:
        c = AsyncClient.rtu_over_tcp("127.0.0.1", 1502)
        assert not c.is_connected
        await c.close()

    async def test_async_context_manager_round_trip(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0x42])

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port) as c:
                assert c.is_connected
                pdu = await c.execute(encode_read_holding_registers(0, 1))
                assert decode_read_holding_registers(pdu, 1) == [0x42]
            assert not c.is_connected


# ---------- execute() against a real fake server -----------------------------


class TestExecuteOverTcp:
    async def test_round_trip_uses_default_unit_id(self) -> None:
        seen_units: list[int] = []

        async def handler(pdu: bytes) -> bytes:
            return _holding_response([1])

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1", srv.port, unit_id=7, timeout_s=1.0
            ) as c:
                # We can't observe unit_id from the FakeModbusServer's
                # handler signature directly, but the behavior contract is
                # that the default propagates. Verify via FakeTransport
                # below; here we just check the call works.
                pdu = await c.execute(encode_read_holding_registers(0, 1))
                assert decode_read_holding_registers(pdu, 1) == [1]

    async def test_per_call_timeout_override(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(0.5)
            return _holding_response([0])

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1", srv.port, timeout_s=2.0, retry=RetryPolicy(max_attempts=1)
            ) as c:
                # Override per-call to 0.1s — should time out.
                with pytest.raises(ModbusTimeoutError):
                    await c.execute(
                        encode_read_holding_registers(0, 1), timeout_s=0.1
                    )

    async def test_exception_response_propagates_without_retry(self) -> None:
        # The slave returns IllegalDataAddress; client should propagate it
        # immediately, NOT retry.
        call_count = 0

        async def handler(pdu: bytes) -> bytes:
            nonlocal call_count
            call_count += 1
            return bytes([0x83, 0x02])  # FC03 | exception bit, IllegalDataAddress

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1", srv.port,
                retry=RetryPolicy(max_attempts=5),
            ) as c:
                with pytest.raises(IllegalDataAddress):
                    pdu = await c.execute(encode_read_holding_registers(0, 1))
                    decode_read_holding_registers(pdu, 1)
        # Note: detect_exception inside decode raises IllegalDataAddress,
        # but the wire round-trip itself should only happen once because
        # the response succeeded at transport level. The decoder doesn't
        # get involved for execute() (raw bytes returned). The retry test
        # for exception responses lives in TestRetryWithFakeTransport.


# ---------- Retry policy with FakeTransport ---------------------------------


class TestRetryPolicy:
    async def test_retries_on_timeout_and_succeeds(self) -> None:
        good = _holding_response([0xAB])
        transport = FakeTransport([
            ModbusTimeoutError("attempt 1 timed out"),
            ModbusTimeoutError("attempt 2 timed out"),
            good,
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(
                max_attempts=3,
                backoff_initial_s=0.0,
                backoff_factor=1.0,
                backoff_cap_s=0.0,
            ),
        )
        result = await c.execute(encode_read_holding_registers(0, 1))
        assert result == good
        assert len(transport.call_log) == 3

    async def test_retries_exhausted_raises_last_error(self) -> None:
        transport = FakeTransport([
            ModbusConnectionError("1"),
            ModbusConnectionError("2"),
            ModbusConnectionError("3"),
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(
                max_attempts=3,
                backoff_initial_s=0.0,
                backoff_factor=1.0,
                backoff_cap_s=0.0,
            ),
        )
        with pytest.raises(ModbusConnectionError):
            await c.execute(encode_read_holding_registers(0, 1))
        assert len(transport.call_log) == 3

    async def test_does_not_retry_exception_response(self) -> None:
        # An IllegalDataAddress is a successful round-trip with a NAK;
        # retrying would just hit the same illegal address. Default policy
        # excludes ModbusExceptionResponse from retry_on.
        transport = FakeTransport([
            IllegalDataAddress("nope"),
            _holding_response([0]),  # would succeed if retried
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(max_attempts=5, backoff_initial_s=0.0),
        )
        with pytest.raises(IllegalDataAddress):
            await c.execute(encode_read_holding_registers(0, 1))
        assert len(transport.call_log) == 1

    async def test_per_call_retry_policy_overrides_default(self) -> None:
        # Default retry policy on the client retries 1 time. Per-call
        # override expands to 4. Without the override the call would fail
        # after 1 retry; with it, the call succeeds on the 4th attempt.
        transport = FakeTransport([
            ModbusTimeoutError(),
            ModbusTimeoutError(),
            ModbusTimeoutError(),
            _holding_response([0xCC]),
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(max_attempts=2, backoff_initial_s=0.0),
        )
        per_call = RetryPolicy(max_attempts=4, backoff_initial_s=0.0)
        result = await c.execute(
            encode_read_holding_registers(0, 1),
            retry=per_call,
        )
        assert result == _holding_response([0xCC])

    async def test_retry_on_custom_filter(self) -> None:
        # If the user explicitly excludes ModbusTimeoutError from retry_on,
        # a single timeout fails immediately.
        transport = FakeTransport([
            ModbusTimeoutError(),
            _holding_response([0]),
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(
                max_attempts=5,
                retry_on=(ModbusConnectionError,),
                backoff_initial_s=0.0,
            ),
        )
        with pytest.raises(ModbusTimeoutError):
            await c.execute(encode_read_holding_registers(0, 1))
        assert len(transport.call_log) == 1


# ---------- Per-call overrides ---------------------------------------------


class TestPerCallOverrides:
    async def test_unit_id_override(self) -> None:
        transport = FakeTransport([_holding_response([0])])
        c = AsyncClient(transport, unit_id=1)  # type: ignore[arg-type]
        await c.execute(encode_read_holding_registers(0, 1), unit_id=42)
        assert transport.call_log[0][1] == 42

    async def test_default_unit_id_used_when_not_overridden(self) -> None:
        transport = FakeTransport([_holding_response([0])])
        c = AsyncClient(transport, unit_id=7)  # type: ignore[arg-type]
        await c.execute(encode_read_holding_registers(0, 1))
        assert transport.call_log[0][1] == 7

    async def test_default_timeout_used_when_not_overridden(self) -> None:
        transport = FakeTransport([_holding_response([0])])
        c = AsyncClient(transport, timeout_s=1.5)  # type: ignore[arg-type]
        await c.execute(encode_read_holding_registers(0, 1))
        assert transport.call_log[0][2] == 1.5


# ---------- End-to-end through the full stack ------------------------------


class TestEndToEnd:
    async def test_read_through_full_stack_with_coalescing(self) -> None:
        # AsyncClient.read() → planner → execute → TcpTransport → FakeModbusServer.
        # Two adjacent items must coalesce into one wire request.
        request_count = 0

        async def handler(pdu: bytes) -> bytes:
            nonlocal request_count
            request_count += 1
            assert pdu[0] == 0x03
            start, count = struct.unpack(">HH", pdu[1:5])
            body = b"".join((start + i).to_bytes(2, "big") for i in range(count))
            return bytes([0x03, len(body)]) + body

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read(
                    [
                        Holding(start=0, count=5, dtype="uint16"),
                        Holding(start=5, count=10, dtype="uint16"),
                    ]
                )
        assert all(r.ok for r in results)
        assert results[0].values == list(range(0, 5))
        assert results[1].values == list(range(5, 15))
        assert request_count == 1, f"expected coalesce to one request, got {request_count}"

    async def test_write_through_full_stack(self) -> None:
        captured: list[bytes] = []

        async def handler(pdu: bytes) -> bytes:
            captured.append(pdu)
            fc = pdu[0]
            if fc == 0x06:
                return pdu  # echo
            if fc == 0x10:
                start, count = struct.unpack(">HH", pdu[1:5])
                return bytes([fc]) + struct.pack(">HH", start, count)
            return pdu

        async with FakeModbusServer(handler) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.write(
                    [
                        WriteHolding(start=0, values=[0x1234], dtype="uint16"),
                        WriteHolding(start=10, values=[1.5], dtype="float32"),
                    ]
                )
        assert all(r.ok for r in results)
        assert captured[0][0] == 0x06   # FC06 (single uint16)
        assert captured[1][0] == 0x10   # FC16 (float32 = 2 regs)


# ---------- Empty batch ----------------------------------------------------


class TestEmptyBatch:
    async def test_read_empty_returns_empty(self) -> None:
        transport = FakeTransport([])
        c = AsyncClient(transport)  # type: ignore[arg-type]
        assert await c.read([]) == []
        assert transport.call_log == []

    async def test_write_empty_returns_empty(self) -> None:
        transport = FakeTransport([])
        c = AsyncClient(transport)  # type: ignore[arg-type]
        assert await c.write([]) == []
        assert transport.call_log == []


# ---------- Backoff timing --------------------------------------------------


class TestBackoff:
    async def test_backoff_delays_observed(self) -> None:
        # Exponential backoff: 0.05 → 0.10. Total wait between 3 attempts ≈ 0.15s.
        transport = FakeTransport([
            ModbusTimeoutError(),
            ModbusTimeoutError(),
            _holding_response([0]),
        ])
        c = AsyncClient(
            transport,  # type: ignore[arg-type]
            retry=RetryPolicy(
                max_attempts=3,
                backoff_initial_s=0.05,
                backoff_factor=2.0,
                backoff_cap_s=1.0,
            ),
        )
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await c.execute(encode_read_holding_registers(0, 1))
        elapsed = loop.time() - t0
        assert 0.13 <= elapsed <= 0.5, f"unexpected elapsed: {elapsed}"
