"""Tests for `pymod.server.Server` — callback dispatch, exception mapping,
authorization-by-area, multiple connections, lifecycle."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Sequence
from typing import Any

import pytest

from pymod._types import (
    Area,
    Coil,
    Discrete,
    Holding,
    Input,
    WriteCoils,
    WriteHolding,
)
from pymod.client import AsyncClient
from pymod.errors import (
    IllegalDataAddress,
    IllegalDataValue,
    IllegalFunction,
    ModbusExceptionResponse,
    SlaveDeviceFailure,
)
from pymod.protocol.pdu import (
    decode_read_coils,
    decode_read_holding_registers,
    encode_read_coils,
    encode_read_holding_registers,
    encode_write_single_register,
)
from pymod.retry import RetryPolicy
from pymod.server import Server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- basic dispatch ----------


class TestBasicReads:
    async def test_read_holding_registers(self) -> None:
        store = {0: 0x1234, 1: 0xCAFE, 2: 0xBEEF}

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            assert area is Area.HOLDING_REGISTER
            return [store[address + i] for i in range(count)]

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Holding(start=0, count=3, dtype="uint16")])
                assert results[0].ok
                assert results[0].values == [0x1234, 0xCAFE, 0xBEEF]

    async def test_read_input_registers(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            assert area is Area.INPUT_REGISTER
            return [address + i for i in range(count)]

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Input(start=10, count=5, dtype="uint16")])
                assert results[0].ok
                assert results[0].values == [10, 11, 12, 13, 14]

    async def test_read_coils(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            assert area is Area.COIL
            return [bool(i % 2) for i in range(count)]

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Coil(start=0, count=8)])
                assert results[0].ok
                assert results[0].values == [False, True, False, True, False, True, False, True]

    async def test_read_discrete_inputs(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            assert area is Area.DISCRETE_INPUT
            return [True] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Discrete(start=0, count=4)])
                assert results[0].ok
                assert results[0].values == [True, True, True, True]


class TestBasicWrites:
    async def test_write_single_register(self) -> None:
        store: dict[int, int] = {}

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [store.get(address + i, 0) for i in range(count)]

        def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            assert area is Area.HOLDING_REGISTER
            for i, v in enumerate(values):
                store[address + i] = int(v)

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.write(
                    [WriteHolding(start=10, values=[0xCAFE], dtype="uint16")]
                )
                assert results[0].ok
                assert store[10] == 0xCAFE

    async def test_write_multiple_registers(self) -> None:
        store: dict[int, int] = {}

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [store.get(address + i, 0) for i in range(count)]

        def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            for i, v in enumerate(values):
                store[address + i] = int(v)

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.write(
                    [WriteHolding(start=0, values=[0xAA, 0xBB, 0xCC], dtype="uint16")]
                )
                assert results[0].ok
                assert store == {0: 0xAA, 1: 0xBB, 2: 0xCC}

    async def test_write_single_coil(self) -> None:
        coil_store: dict[int, bool] = {}

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [coil_store.get(address + i, False) for i in range(count)]

        def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            assert area is Area.COIL
            for i, v in enumerate(values):
                coil_store[address + i] = bool(v)

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.write([WriteCoils(start=5, values=[True])])
                assert results[0].ok
                assert coil_store == {5: True}

    async def test_write_multiple_coils(self) -> None:
        coil_store: dict[int, bool] = {}

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [coil_store.get(address + i, False) for i in range(count)]

        def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            for i, v in enumerate(values):
                coil_store[address + i] = bool(v)

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.write(
                    [WriteCoils(start=0, values=[True, False, True, True])]
                )
                assert results[0].ok
                assert coil_store == {0: True, 1: False, 2: True, 3: True}


# ---------- authorization by area ----------


class TestAuthorizationByArea:
    async def test_no_on_write_returns_illegal_function_for_writes(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            # Connect raw and send FC06 to verify ILLEGAL_FUNCTION on the wire.
            async with AsyncClient.tcp(
                "127.0.0.1",
                srv.port,
                timeout_s=1.0,
                retry=RetryPolicy(max_attempts=1),
            ) as c:
                response_pdu = await c.execute(encode_write_single_register(0, 1))
                assert response_pdu[0] == 0x86  # FC06 | exception bit
                assert response_pdu[1] == IllegalFunction.code

    async def test_input_register_only_data_unwritable(self) -> None:
        # Server only knows how to read INPUT_REGISTER. Holding reads/writes
        # raise IllegalDataAddress from the callback.
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            if area is Area.INPUT_REGISTER:
                return [42] * count
            raise IllegalDataAddress(f"no data at {area}/{address}")

        def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            raise IllegalDataAddress("no writable data")

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                # Input read works.
                in_results = await c.read([Input(start=0, count=2, dtype="uint16")])
                assert in_results[0].ok
                # Holding read fails.
                hold_results = await c.read([Holding(start=0, count=2, dtype="uint16")])
                assert not hold_results[0].ok
                assert isinstance(hold_results[0].error, IllegalDataAddress)
                # Holding write fails.
                w_results = await c.write([WriteHolding(start=0, values=[1], dtype="uint16")])
                assert not w_results[0].ok
                assert isinstance(w_results[0].error, IllegalDataAddress)


# ---------- callback exception mapping ----------


class TestCallbackExceptions:
    async def test_callback_raises_illegal_data_address(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            raise IllegalDataAddress(f"address {address} out of map")

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Holding(start=99, count=1, dtype="uint16")])
                assert not results[0].ok
                assert isinstance(results[0].error, IllegalDataAddress)

    async def test_callback_raises_arbitrary_exception_maps_to_slave_device_failure(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            raise RuntimeError("database is on fire")

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Holding(start=0, count=1, dtype="uint16")])
                assert not results[0].ok
                assert isinstance(results[0].error, SlaveDeviceFailure)

    async def test_async_callback_supported(self) -> None:
        async def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            await asyncio.sleep(0)
            return [0xABCD] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Holding(start=0, count=3, dtype="uint16")])
                assert results[0].ok
                assert results[0].values == [0xABCD, 0xABCD, 0xABCD]

    async def test_async_write_callback_supported(self) -> None:
        captured: list[tuple[Area, int, list[Any]]] = []

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        async def on_write(area: Area, address: int, values: Sequence[int | bool]) -> None:
            await asyncio.sleep(0)
            captured.append((area, address, list(values)))

        async with Server(host="127.0.0.1", port=0, on_read=on_read, on_write=on_write) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                await c.write([WriteHolding(start=0, values=[1], dtype="uint16")])
        assert captured == [(Area.HOLDING_REGISTER, 0, [1])]


# ---------- bad request handling ----------


class TestBadRequest:
    async def test_unknown_fc_returns_illegal_function(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1",
                srv.port,
                timeout_s=1.0,
                retry=RetryPolicy(max_attempts=1),
            ) as c:
                # FC0x42 is not a standard FC.
                response = await c.execute(b"\x42\x00\x00\x00\x01")
                assert response[0] == 0xC2  # 0x42 | 0x80
                assert response[1] == IllegalFunction.code

    async def test_count_zero_in_request_returns_illegal_data_value(self) -> None:
        # The server's request decoder rejects count=0 with IllegalDataValue.
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1",
                srv.port,
                timeout_s=1.0,
                retry=RetryPolicy(max_attempts=1),
            ) as c:
                # FC03 with count=0.
                response = await c.execute(b"\x03\x00\x00\x00\x00")
                assert response[0] == 0x83
                assert response[1] == IllegalDataValue.code


# ---------- multi-connection ----------


class TestMultipleConnections:
    async def test_two_clients_concurrently(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [address + i for i in range(count)]

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c1, \
                       AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c2:
                r1, r2 = await asyncio.gather(
                    c1.read([Holding(start=0, count=5, dtype="uint16")]),
                    c2.read([Holding(start=100, count=5, dtype="uint16")]),
                )
                assert r1[0].values == [0, 1, 2, 3, 4]
                assert r2[0].values == [100, 101, 102, 103, 104]

    async def test_max_connections_rejects_excess(self) -> None:
        # Hold open two slow clients. With max_connections=2, a third
        # connection's request must fail.
        gate = asyncio.Event()

        async def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            await gate.wait()
            return [0] * count

        async with Server(
            host="127.0.0.1", port=0, on_read=on_read, max_connections=2
        ) as srv:
            c1 = AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=2.0)
            c2 = AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=2.0)
            await c1.connect()
            await c2.connect()
            # Issue requests that will block on the gate.
            t1 = asyncio.create_task(c1.read([Holding(start=0, count=1, dtype="uint16")]))
            t2 = asyncio.create_task(c2.read([Holding(start=0, count=1, dtype="uint16")]))
            # Let those connections register on the server.
            for _ in range(50):
                if srv.active_connections >= 2:
                    break
                await asyncio.sleep(0.01)
            assert srv.active_connections == 2

            # Third client: connection should be accepted at TCP level but
            # immediately closed by the server. The next request fails.
            c3 = AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=0.5)
            with pytest.raises(Exception):
                async with c3:
                    await c3.execute(encode_read_holding_registers(0, 1))

            # Release the gate so the first two complete.
            gate.set()
            await asyncio.gather(t1, t2)
            await c1.close()
            await c2.close()


# ---------- unit id discrimination ----------


class TestUnitId:
    async def test_unit_id_filter_rejects_other_units(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [42] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read, unit_id=7) as srv:
            async with AsyncClient.tcp(
                "127.0.0.1",
                srv.port,
                timeout_s=1.0,
                unit_id=7,
                retry=RetryPolicy(max_attempts=1),
            ) as c:
                # Matching unit id works.
                results = await c.read([Holding(start=0, count=1, dtype="uint16")])
                assert results[0].ok

                # Non-matching unit id returns GATEWAY_TARGET_FAILED_TO_RESPOND (0x0B).
                response = await c.execute(
                    encode_read_holding_registers(0, 1), unit_id=99
                )
                assert response[0] == 0x83
                assert response[1] == 0x0B  # GATEWAY_TARGET_FAILED_TO_RESPOND

    async def test_unit_id_none_accepts_any(self) -> None:
        seen_units: list[int] = []

        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        async with Server(host="127.0.0.1", port=0, on_read=on_read) as srv:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                for uid in (1, 5, 99):
                    results = await c.read(
                        [Holding(start=0, count=1, dtype="uint16")],
                        unit_id=uid,
                    )
                    assert results[0].ok


# ---------- lifecycle ----------


class TestLifecycle:
    async def test_is_running_and_active_connections(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        srv = Server(host="127.0.0.1", port=0, on_read=on_read)
        assert not srv.is_running
        assert srv.active_connections == 0
        await srv.start()
        try:
            assert srv.is_running
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                await c.read([Holding(start=0, count=1, dtype="uint16")])
                # Give the server a moment to register the connection.
                await asyncio.sleep(0.05)
                assert srv.active_connections >= 1
            # After client closes, server should drop the connection eventually.
            for _ in range(50):
                if srv.active_connections == 0:
                    break
                await asyncio.sleep(0.02)
            assert srv.active_connections == 0
        finally:
            await srv.stop()
            assert not srv.is_running

    async def test_start_idempotent(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [0] * count

        srv = Server(host="127.0.0.1", port=0, on_read=on_read)
        await srv.start()
        try:
            await srv.start()  # second start is a no-op.
            assert srv.is_running
        finally:
            await srv.stop()

    async def test_can_restart_after_stop(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return [42] * count

        srv = Server(host="127.0.0.1", port=0, on_read=on_read)
        await srv.start()
        first_port = srv.port
        await srv.stop()
        assert not srv.is_running

        await srv.start()
        try:
            async with AsyncClient.tcp("127.0.0.1", srv.port, timeout_s=1.0) as c:
                results = await c.read([Holding(start=0, count=1, dtype="uint16")])
                assert results[0].values == [42]
        finally:
            await srv.stop()


# ---------- constructor validation ----------


class TestConstructorValidation:
    def test_max_connections_must_be_positive(self) -> None:
        def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
            return []

        with pytest.raises(ValueError):
            Server(host="127.0.0.1", port=0, on_read=on_read, max_connections=0)
