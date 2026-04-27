"""Tests for `pymod.transport.rtu_over_tcp.RtuOverTcpTransport`."""

from __future__ import annotations

import asyncio
import socket

import pytest

from pymod.errors import (
    IllegalDataAddress,
    ModbusConnectionError,
    ModbusError,
    ModbusTimeoutError,
)
from pymod.protocol.pdu import (
    decode_read_holding_registers,
    encode_read_holding_registers,
    encode_write_multiple_registers,
    encode_write_single_register,
)
from pymod.transport.rtu_over_tcp import RtuOverTcpTransport

from ._fake_rtu_tcp_server import FakeRtuOverTcpServer
from ._rtu_slave import RtuSlave


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- basic round-trip ----------


class TestRoundTrip:
    async def test_read_holding_registers(self) -> None:
        slave = RtuSlave(holding={0: 0x1111, 1: 0x2222, 2: 0x3333})
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                response_pdu = await t.send_and_receive(
                    encode_read_holding_registers(0, 3),
                    unit_id=1,
                    timeout_s=1.0,
                )
                assert decode_read_holding_registers(response_pdu, 3) == [
                    0x1111, 0x2222, 0x3333
                ]

    async def test_write_single_register(self) -> None:
        slave = RtuSlave()
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                req = encode_write_single_register(0x0010, 0xCAFE)
                resp = await t.send_and_receive(req, unit_id=1, timeout_s=1.0)
                assert resp == req
                assert slave.holding[0x0010] == 0xCAFE

    async def test_write_multiple_registers(self) -> None:
        slave = RtuSlave()
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                req = encode_write_multiple_registers(0x20, [0xAAAA, 0xBBBB, 0xCCCC])
                resp = await t.send_and_receive(req, unit_id=1, timeout_s=1.0)
                # FC16 response is start + count.
                assert resp[0] == 0x10
                assert slave.holding[0x20] == 0xAAAA
                assert slave.holding[0x21] == 0xBBBB
                assert slave.holding[0x22] == 0xCCCC

    async def test_lifecycle(self) -> None:
        async with FakeRtuOverTcpServer(RtuSlave()) as srv:
            t = RtuOverTcpTransport("127.0.0.1", srv.port)
            assert not t.is_connected
            await t.connect()
            assert t.is_connected
            await t.close()
            assert not t.is_connected


# ---------- exception responses ----------


class TestExceptionResponses:
    async def test_illegal_data_address(self) -> None:
        slave = RtuSlave(holding={0: 0xABCD})
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                response_pdu = await t.send_and_receive(
                    encode_read_holding_registers(99, 1),
                    unit_id=1,
                    timeout_s=1.0,
                )
                with pytest.raises(IllegalDataAddress):
                    decode_read_holding_registers(response_pdu, 1)


# ---------- bus serialization ----------


class TestSequential:
    async def test_concurrent_callers_are_serialized(self) -> None:
        # RTU-over-TCP has no transaction id, so requests must serialize.
        # If we sent them concurrently we'd be reading the wrong response.
        # The internal lock should prevent that — this test asserts results
        # match expectations regardless of caller-side concurrency.
        regs = {i: i * 11 for i in range(20)}
        slave = RtuSlave(holding=regs)
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                tasks = [
                    t.send_and_receive(
                        encode_read_holding_registers(i, 1),
                        unit_id=1,
                        timeout_s=1.0,
                    )
                    for i in range(20)
                ]
                results = await asyncio.gather(*tasks)
                for i, r in enumerate(results):
                    assert decode_read_holding_registers(r, 1) == [i * 11]


# ---------- connection errors ----------


class TestConnectionErrors:
    async def test_connect_to_closed_port_raises(self) -> None:
        port = _free_port()
        t = RtuOverTcpTransport("127.0.0.1", port, connect_timeout_s=0.5)
        with pytest.raises(ModbusConnectionError):
            await t.connect()

    async def test_timeout_when_slave_silent(self) -> None:
        slave = RtuSlave(drop_next=1)
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                with pytest.raises(ModbusTimeoutError):
                    await t.send_and_receive(
                        encode_read_holding_registers(0, 1),
                        unit_id=1,
                        timeout_s=0.1,
                    )

    async def test_recovers_after_timeout(self) -> None:
        # After a timeout the transport closes the socket and reconnects on
        # the next call. The slave only drops the first request.
        slave = RtuSlave(holding={0: 0x42}, drop_next=1)
        async with FakeRtuOverTcpServer(slave) as srv:
            async with RtuOverTcpTransport("127.0.0.1", srv.port) as t:
                with pytest.raises(ModbusTimeoutError):
                    await t.send_and_receive(
                        encode_read_holding_registers(0, 1),
                        unit_id=1,
                        timeout_s=0.1,
                    )
                response_pdu = await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=1.0,
                )
                assert decode_read_holding_registers(response_pdu, 1) == [0x42]


# ---------- closed transport ----------


class TestClosedTransport:
    async def test_send_after_close_raises(self) -> None:
        async with FakeRtuOverTcpServer(RtuSlave()) as srv:
            t = RtuOverTcpTransport("127.0.0.1", srv.port)
            await t.connect()
            await t.close()
            with pytest.raises(ModbusError):
                await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=0.5,
                )
