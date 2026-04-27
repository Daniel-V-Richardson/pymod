"""Tests for `pymod.transport.tcp.TcpTransport`."""

from __future__ import annotations

import asyncio
import socket

import pytest

from pymod.errors import ModbusConnectionError, ModbusError, ModbusTimeoutError
from pymod.protocol.pdu import (
    encode_read_holding_registers,
    encode_write_single_register,
)
from pymod.transport.tcp import TcpTransport

from ._fake_server import FakeModbusServer


def _free_port() -> int:
    """Reserve and release an ephemeral port — racy but adequate for
    'connect to a closed port' tests."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_holding_registers_handler(values: list[int]) -> bytes:
    """Build a canned FC03 response containing `values`."""
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([0x03, len(body)]) + body


def _echo_write_single_register(pdu: bytes) -> bytes:
    """Echo a FC06 request as the response (slaves echo the request)."""
    return pdu


# ---------- basic round-trip ----------


class TestRoundTrip:
    async def test_read_holding_registers(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            assert pdu[0] == 0x03  # FC03
            return _read_holding_registers_handler([0x000A, 0x000B, 0x000C])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port) as t:
                response = await t.send_and_receive(
                    encode_read_holding_registers(0, 3),
                    unit_id=1,
                    timeout_s=1.0,
                )
                assert response == _read_holding_registers_handler([0x0A, 0x0B, 0x0C])

    async def test_write_single_register_echo(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _echo_write_single_register(pdu)

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port) as t:
                req = encode_write_single_register(0x0001, 0x1234)
                response = await t.send_and_receive(req, unit_id=1, timeout_s=1.0)
                assert response == req

    async def test_is_connected_lifecycle(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            t = TcpTransport("127.0.0.1", srv.port)
            assert not t.is_connected
            await t.connect()
            assert t.is_connected
            await t.close()
            assert not t.is_connected


# ---------- pipelining ----------


class TestPipelining:
    async def test_many_concurrent_requests_all_complete(self) -> None:
        # Echo handler that includes a tiny per-request randomization in
        # response timing.
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(0)  # yield once to interleave
            return _read_holding_registers_handler(
                [int.from_bytes(pdu[1:3], "big")]  # echo start address as first reg
            )

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port, pipeline=True) as t:
                tasks = [
                    t.send_and_receive(
                        encode_read_holding_registers(i, 1),
                        unit_id=1,
                        timeout_s=2.0,
                    )
                    for i in range(100)
                ]
                results = await asyncio.gather(*tasks)
                for i, r in enumerate(results):
                    # Decode the single register from the response.
                    assert r[0] == 0x03  # FC
                    assert r[1] == 2     # byte count
                    reg = int.from_bytes(r[2:4], "big")
                    assert reg == i, f"request {i} got register {reg}"

    async def test_out_of_order_responses_dispatched_correctly(self) -> None:
        # The slave deliberately delays request with start=0 so request
        # with start=1 responds first. Without TID-based dispatch, the
        # first request's awaiter would receive the second response.
        delays: dict[int, float] = {0: 0.2, 1: 0.0}

        async def handler(pdu: bytes) -> bytes:
            start = int.from_bytes(pdu[1:3], "big")
            await asyncio.sleep(delays.get(start, 0.0))
            return _read_holding_registers_handler([start])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port, pipeline=True) as t:
                a = asyncio.create_task(
                    t.send_and_receive(
                        encode_read_holding_registers(0, 1), unit_id=1, timeout_s=2.0
                    )
                )
                # Ensure request 0 is in flight before request 1 is sent.
                await asyncio.sleep(0.01)
                b = await t.send_and_receive(
                    encode_read_holding_registers(1, 1), unit_id=1, timeout_s=2.0
                )
                assert int.from_bytes(b[2:4], "big") == 1
                resp_a = await a
                assert int.from_bytes(resp_a[2:4], "big") == 0

    async def test_pipeline_disabled_serializes_calls(self) -> None:
        # When pipeline=False, two concurrent calls must NOT overlap on the
        # wire. We assert this by tracking concurrent in-flight count in
        # the handler — it should never exceed 1.
        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def handler(pdu: bytes) -> bytes:
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port, pipeline=False) as t:
                tasks = [
                    t.send_and_receive(
                        encode_read_holding_registers(0, 1),
                        unit_id=1,
                        timeout_s=2.0,
                    )
                    for _ in range(5)
                ]
                await asyncio.gather(*tasks)

        assert max_in_flight == 1, f"expected strict serialization, saw {max_in_flight} concurrent"


# ---------- timeouts and connection errors ----------


class TestTimeouts:
    async def test_request_timeout(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(10)  # never responds within test budget
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port) as t:
                with pytest.raises(ModbusTimeoutError):
                    await t.send_and_receive(
                        encode_read_holding_registers(0, 1),
                        unit_id=1,
                        timeout_s=0.1,
                    )

    async def test_timeout_does_not_leak_pending(self) -> None:
        # After a timeout, the TID slot must be freed; otherwise the
        # 65k-slot map would gradually fill across many timeouts.
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(10)
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port) as t:
                for _ in range(10):
                    with pytest.raises(ModbusTimeoutError):
                        await t.send_and_receive(
                            encode_read_holding_registers(0, 1),
                            unit_id=1,
                            timeout_s=0.05,
                        )
                assert len(t._pending) == 0  # type: ignore[attr-defined]

    async def test_connect_to_closed_port_raises_connection_error(self) -> None:
        # On most OSes connect-to-closed-port returns ConnectionRefusedError
        # immediately; on others it times out. Either path must map to
        # ModbusConnectionError.
        port = _free_port()
        t = TcpTransport("127.0.0.1", port, connect_timeout_s=0.5)
        with pytest.raises(ModbusConnectionError):
            await t.connect()


# ---------- reconnect ----------


class TestReconnect:
    async def test_auto_reconnect_after_connection_drop(self) -> None:
        # Simulate a transient network blip: the listener stays up, but
        # the existing client socket is dropped server-side. The transport
        # should reconnect transparently on the next call.
        async def handler(pdu: bytes) -> bytes:
            return _read_holding_registers_handler([0x42])

        async with FakeModbusServer(handler) as srv:
            async with TcpTransport("127.0.0.1", srv.port) as t:
                r = await t.send_and_receive(
                    encode_read_holding_registers(0, 1), unit_id=1, timeout_s=1.0
                )
                assert int.from_bytes(r[2:4], "big") == 0x42

                await srv.drop_clients()
                # Let the read loop notice the EOF and tear down state.
                await asyncio.sleep(0.1)
                assert not t.is_connected

                r2 = await t.send_and_receive(
                    encode_read_holding_registers(0, 1), unit_id=1, timeout_s=2.0
                )
                assert int.from_bytes(r2[2:4], "big") == 0x42
                assert t.is_connected

    async def test_close_fails_pending_requests(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            await asyncio.sleep(10)
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            t = TcpTransport("127.0.0.1", srv.port)
            await t.connect()
            pending = asyncio.create_task(
                t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=10.0,
                )
            )
            await asyncio.sleep(0.05)
            await t.close()
            with pytest.raises((ModbusConnectionError, ModbusError)):
                await pending


# ---------- closed transport ----------


class TestClosedTransport:
    async def test_send_after_close_raises(self) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _read_holding_registers_handler([0])

        async with FakeModbusServer(handler) as srv:
            t = TcpTransport("127.0.0.1", srv.port)
            await t.connect()
            await t.close()
            with pytest.raises(ModbusError):
                await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=0.5,
                )
