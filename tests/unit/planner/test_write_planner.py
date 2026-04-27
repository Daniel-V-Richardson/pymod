"""Tests for the write planner — FC selection, encoding, splitting, partial failures."""

from __future__ import annotations

import pytest

from pymod._types import WriteCoils, WriteHolding
from pymod.errors import IllegalDataAddress, ModbusTimeoutError
from pymod.planner import plan_and_execute_writes
from pymod.protocol.pdu import (
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE_REGISTERS,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE_REGISTER,
)

from ._fake_executor import FakeExecutor, request_summaries


# ---------- FC selection ---------------------------------------------------


class TestFcSelection:
    async def test_single_uint16_uses_fc06(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteHolding(start=0, values=[0x1234], dtype="uint16")], ex
        )
        assert results[0].ok
        assert ex.holding == {0: 0x1234}
        assert request_summaries(ex) == [(FC_WRITE_SINGLE_REGISTER, 0, 1)]

    async def test_multiple_uint16_uses_fc16(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteHolding(start=0, values=[0x1111, 0x2222, 0x3333], dtype="uint16")],
            ex,
        )
        assert results[0].ok
        assert ex.holding == {0: 0x1111, 1: 0x2222, 2: 0x3333}
        assert request_summaries(ex) == [(FC_WRITE_MULTIPLE_REGISTERS, 0, 3)]

    async def test_single_float32_uses_fc16_because_two_registers(self) -> None:
        # float32 is 2 registers — even a single value can't use FC06.
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteHolding(start=0, values=[1.0], dtype="float32")], ex
        )
        assert results[0].ok
        assert ex.holding[0] == 0x3F80  # IEEE 754 1.0 high word
        assert ex.holding[1] == 0x0000
        assert request_summaries(ex) == [(FC_WRITE_MULTIPLE_REGISTERS, 0, 2)]

    async def test_single_int32_uses_fc16(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteHolding(start=10, values=[-1], dtype="int32")], ex
        )
        assert results[0].ok
        assert ex.holding[10] == 0xFFFF
        assert ex.holding[11] == 0xFFFF
        assert request_summaries(ex) == [(FC_WRITE_MULTIPLE_REGISTERS, 10, 2)]

    async def test_single_coil_uses_fc05(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteCoils(start=0, values=[True])], ex
        )
        assert results[0].ok
        assert ex.coils == {0: True}
        assert request_summaries(ex) == [(FC_WRITE_SINGLE_COIL, 0, 1)]

    async def test_multiple_coils_use_fc15(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_writes(
            [WriteCoils(start=10, values=[True, False, True, True])], ex
        )
        assert results[0].ok
        assert ex.coils == {10: True, 11: False, 12: True, 13: True}
        assert request_summaries(ex) == [(FC_WRITE_MULTIPLE_COILS, 10, 4)]


# ---------- splitting -------------------------------------------------------


class TestSplitting:
    async def test_oversized_register_write_splits(self) -> None:
        # 200 uint16 values > 123 limit → split into two FC16 writes.
        ex = FakeExecutor()
        values = list(range(200))
        results = await plan_and_execute_writes(
            [WriteHolding(start=0, values=values, dtype="uint16")], ex
        )
        assert results[0].ok
        # First chunk = 123, second = 77.
        summaries = sorted(request_summaries(ex))
        assert summaries == [
            (FC_WRITE_MULTIPLE_REGISTERS, 0, 123),
            (FC_WRITE_MULTIPLE_REGISTERS, 123, 77),
        ]
        # All 200 values landed.
        for i, v in enumerate(values):
            assert ex.holding[i] == v

    async def test_oversized_coil_write_splits(self) -> None:
        # 2500 coils > 1968 → 1968 + 532.
        ex = FakeExecutor()
        values = [bool(i % 2) for i in range(2500)]
        results = await plan_and_execute_writes(
            [WriteCoils(start=0, values=values)], ex
        )
        assert results[0].ok
        summaries = sorted(request_summaries(ex))
        assert summaries == [
            (FC_WRITE_MULTIPLE_COILS, 0, 1968),
            (FC_WRITE_MULTIPLE_COILS, 1968, 532),
        ]


# ---------- encoding errors -------------------------------------------------


class TestEncodingErrors:
    async def test_value_out_of_range_raises_immediately(self) -> None:
        ex = FakeExecutor()
        with pytest.raises(ValueError):
            await plan_and_execute_writes(
                [WriteHolding(start=0, values=[0x10000], dtype="uint16")], ex
            )
        # No PDU sent — encoding failed at planning.
        assert ex.sent_pdus == []

    async def test_empty_values_rejected(self) -> None:
        ex = FakeExecutor()
        with pytest.raises(ValueError):
            await plan_and_execute_writes(
                [WriteHolding(start=0, values=[], dtype="uint16")], ex
            )

    async def test_negative_int32_in_uint32_rejected(self) -> None:
        ex = FakeExecutor()
        with pytest.raises(ValueError):
            await plan_and_execute_writes(
                [WriteHolding(start=0, values=[-1], dtype="uint32")], ex
            )


# ---------- partial failure ------------------------------------------------


class TestPartialFailure:
    async def test_one_item_fails_others_succeed(self) -> None:
        ex = FakeExecutor()
        ex.fail_once(FC_WRITE_SINGLE_REGISTER, 5, ModbusTimeoutError("oops"))

        results = await plan_and_execute_writes(
            [
                WriteHolding(start=0, values=[0x1111], dtype="uint16"),
                WriteHolding(start=5, values=[0x2222], dtype="uint16"),
                WriteHolding(start=10, values=[0x3333], dtype="uint16"),
            ],
            ex,
        )
        assert results[0].ok
        assert not results[1].ok
        assert isinstance(results[1].error, ModbusTimeoutError)
        assert results[2].ok
        # Items 0 and 2 still landed; item 1 did not.
        assert ex.holding[0] == 0x1111
        assert 5 not in ex.holding
        assert ex.holding[10] == 0x3333

    async def test_split_chunk_failure_marks_whole_item_failed(self) -> None:
        # 200 values, 2 chunks. Fail the second chunk. Whole item = failed.
        ex = FakeExecutor()
        ex.fail_once(
            FC_WRITE_MULTIPLE_REGISTERS, 123, IllegalDataAddress("bad addr")
        )
        results = await plan_and_execute_writes(
            [WriteHolding(start=0, values=list(range(200)), dtype="uint16")], ex
        )
        assert not results[0].ok
        assert isinstance(results[0].error, IllegalDataAddress)


# ---------- edge cases -----------------------------------------------------


class TestEdgeCases:
    async def test_empty_items_returns_empty(self) -> None:
        ex = FakeExecutor()
        assert await plan_and_execute_writes([], ex) == []
        assert ex.sent_pdus == []

    async def test_results_parallel_to_input(self) -> None:
        ex = FakeExecutor()
        items = [
            WriteHolding(start=10, values=[0xAAAA], dtype="uint16"),
            WriteHolding(start=0, values=[0xBBBB], dtype="uint16"),
        ]
        results = await plan_and_execute_writes(items, ex)
        assert results[0].item is items[0]
        assert results[1].item is items[1]
