"""Tests for the read planner — coalescing, splitting, decoding, partial failures."""

from __future__ import annotations

import pytest

from pymod._types import Coil, Discrete, Holding, Input
from pymod.errors import IllegalDataAddress, ModbusTimeoutError
from pymod.planner import plan_and_execute_reads
from pymod.protocol.pdu import (
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
)

from ._fake_executor import FakeExecutor, request_summaries


# ---------- single-item reads ----------------------------------------------


class TestSingleItem:
    async def test_uint16_single(self) -> None:
        ex = FakeExecutor(holding={0: 0x1234, 1: 0xCAFE})
        results = await plan_and_execute_reads(
            [Holding(start=0, count=2, dtype="uint16")], ex
        )
        assert len(results) == 1
        assert results[0].ok
        assert results[0].values == [0x1234, 0xCAFE]
        assert request_summaries(ex) == [(FC_READ_HOLDING_REGISTERS, 0, 2)]

    async def test_float32_single(self) -> None:
        # 1.0 IEEE-754 single = 0x3F800000.
        ex = FakeExecutor(holding={0: 0x3F80, 1: 0x0000})
        results = await plan_and_execute_reads(
            [Holding(start=0, count=2, dtype="float32")], ex
        )
        assert results[0].ok
        assert results[0].values == [1.0]

    async def test_input_register_dispatch(self) -> None:
        ex = FakeExecutor(input_regs={10: 0x000A, 11: 0x000B})
        results = await plan_and_execute_reads(
            [Input(start=10, count=2, dtype="uint16")], ex
        )
        assert results[0].ok
        assert results[0].values == [10, 11]
        assert request_summaries(ex)[0][0] == FC_READ_INPUT_REGISTERS

    async def test_coil_read(self) -> None:
        ex = FakeExecutor(coils={i: bool(i % 2) for i in range(8)})
        results = await plan_and_execute_reads(
            [Coil(start=0, count=8)], ex
        )
        assert results[0].ok
        assert results[0].values == [False, True, False, True, False, True, False, True]
        assert request_summaries(ex)[0][0] == FC_READ_COILS

    async def test_discrete_read(self) -> None:
        ex = FakeExecutor(discrete={0: True, 1: False, 2: True})
        results = await plan_and_execute_reads(
            [Discrete(start=0, count=3)], ex
        )
        assert results[0].ok
        assert results[0].values == [True, False, True]
        assert request_summaries(ex)[0][0] == FC_READ_DISCRETE_INPUTS


# ---------- coalescing -----------------------------------------------------


class TestCoalescing:
    async def test_adjacent_holding_ranges_merge(self) -> None:
        # Holding(0..4) + Holding(5..14) should issue ONE read of 15 regs.
        ex = FakeExecutor(holding={i: i for i in range(15)})
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=5, dtype="uint16"),
                Holding(start=5, count=10, dtype="uint16"),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        assert results[0].values == list(range(0, 5))
        assert results[1].values == list(range(5, 15))
        # ONE Modbus read of 15 registers.
        assert request_summaries(ex) == [(FC_READ_HOLDING_REGISTERS, 0, 15)]

    async def test_non_adjacent_holding_ranges_two_reads(self) -> None:
        # Holding(0..4) and Holding(20..29) — not adjacent → two reads.
        ex = FakeExecutor(holding={**{i: i for i in range(5)}, **{i: i for i in range(20, 30)}})
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=5, dtype="uint16"),
                Holding(start=20, count=10, dtype="uint16"),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        summaries = sorted(request_summaries(ex))
        assert summaries == [
            (FC_READ_HOLDING_REGISTERS, 0, 5),
            (FC_READ_HOLDING_REGISTERS, 20, 10),
        ]

    async def test_overlapping_ranges_one_read(self) -> None:
        # Holding(0..9) and Holding(5..14) overlap → one read of 15.
        ex = FakeExecutor(holding={i: i * 100 for i in range(15)})
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=10, dtype="uint16"),
                Holding(start=5, count=10, dtype="uint16"),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        assert results[0].values == [i * 100 for i in range(0, 10)]
        assert results[1].values == [i * 100 for i in range(5, 15)]
        assert request_summaries(ex) == [(FC_READ_HOLDING_REGISTERS, 0, 15)]

    async def test_different_areas_never_coalesce(self) -> None:
        # Holding(0..9) and Input(0..9) and Coil(0..9) — three different
        # FCs, three reads.
        ex = FakeExecutor(
            holding={i: i for i in range(10)},
            input_regs={i: i + 1000 for i in range(10)},
            coils={i: bool(i % 2) for i in range(10)},
        )
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=10),
                Input(start=0, count=10),
                Coil(start=0, count=10),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        fcs = sorted({s[0] for s in request_summaries(ex)})
        assert fcs == sorted([
            FC_READ_HOLDING_REGISTERS,
            FC_READ_INPUT_REGISTERS,
            FC_READ_COILS,
        ])


# ---------- splitting by PDU limit -----------------------------------------


class TestSplitting:
    async def test_oversized_register_read_splits_at_125(self) -> None:
        # 200 registers > 125 limit → 2 reads.
        ex = FakeExecutor(holding={i: i for i in range(200)})
        results = await plan_and_execute_reads(
            [Holding(start=0, count=200, dtype="uint16")], ex
        )
        assert results[0].ok
        assert results[0].values == list(range(200))
        summaries = sorted(request_summaries(ex))
        assert summaries == [
            (FC_READ_HOLDING_REGISTERS, 0, 125),
            (FC_READ_HOLDING_REGISTERS, 125, 75),
        ]

    async def test_oversized_coil_read_splits_at_2000(self) -> None:
        ex = FakeExecutor(coils={i: True for i in range(2500)})
        results = await plan_and_execute_reads(
            [Coil(start=0, count=2500)], ex
        )
        assert results[0].ok
        assert len(results[0].values) == 2500
        summaries = sorted(request_summaries(ex))
        assert summaries == [
            (FC_READ_COILS, 0, 2000),
            (FC_READ_COILS, 2000, 500),
        ]


# ---------- bit reads from registers ---------------------------------------


class TestBitReads:
    async def test_single_bit_read(self) -> None:
        # Read 2 holding regs starting at 0; extract bit 8 of the assembled
        # 32-bit value (LSB-first default).
        # 0x12345678 → bit 8 = 0 (LSB of 0x56 = 0)
        ex = FakeExecutor(holding={0: 0x1234, 1: 0x5678})
        results = await plan_and_execute_reads(
            [Holding(start=0, count=2, dtype="bit", bit_index=8)], ex
        )
        assert results[0].ok
        assert results[0].values == [False]

    async def test_multiple_bits_read(self) -> None:
        ex = FakeExecutor(holding={0: 0x1234, 1: 0x5678})
        results = await plan_and_execute_reads(
            [
                Holding(
                    start=0,
                    count=2,
                    dtype="bits",
                    bit_indices=[0, 3, 9, 28],
                ),
            ],
            ex,
        )
        assert results[0].ok
        assert results[0].values == [False, True, True, True]

    async def test_missing_bit_index_raises(self) -> None:
        ex = FakeExecutor()
        with pytest.raises(ValueError):
            await plan_and_execute_reads(
                [Holding(start=0, count=1, dtype="bit")],  # no bit_index
                ex,
            )

    async def test_bit_read_coalesces_with_uint16_read(self) -> None:
        # User asks for a bit at Holding[0..1] AND uint16 at Holding[2].
        # Adjacent ranges → ONE Modbus read of 3 registers.
        ex = FakeExecutor(holding={0: 0xFFFF, 1: 0x0000, 2: 0x1234})
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=2, dtype="bit", bit_index=0),
                Holding(start=2, count=1, dtype="uint16"),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        assert results[0].values == [False]  # bit 0 of 0xFFFF0000 = 0
        assert results[1].values == [0x1234]
        assert request_summaries(ex) == [(FC_READ_HOLDING_REGISTERS, 0, 3)]


# ---------- partial failure ------------------------------------------------


class TestPartialFailure:
    async def test_one_chunk_fails_other_items_succeed(self) -> None:
        # Three non-adjacent items → three reads. Fail the middle one.
        ex = FakeExecutor(
            holding={**{i: i for i in range(5)}, **{i: i for i in range(40, 45)}}
        )
        ex.fail_once(FC_READ_HOLDING_REGISTERS, 20, ModbusTimeoutError("middle"))

        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=5, dtype="uint16"),
                Holding(start=20, count=5, dtype="uint16"),
                Holding(start=40, count=5, dtype="uint16"),
            ],
            ex,
        )
        assert results[0].ok
        assert not results[1].ok
        assert isinstance(results[1].error, ModbusTimeoutError)
        assert results[2].ok

    async def test_illegal_address_propagates(self) -> None:
        # Slave returns IllegalDataAddress for the (non-existent) range.
        ex = FakeExecutor(holding={0: 0xABCD})  # only address 0
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=1, dtype="uint16"),
                Holding(start=99, count=1, dtype="uint16"),
            ],
            ex,
        )
        assert results[0].ok
        assert not results[1].ok
        assert isinstance(results[1].error, IllegalDataAddress)


# ---------- edge cases -----------------------------------------------------


class TestEdgeCases:
    async def test_empty_items_returns_empty(self) -> None:
        ex = FakeExecutor()
        results = await plan_and_execute_reads([], ex)
        assert results == []
        assert ex.sent_pdus == []

    async def test_zero_count_rejected(self) -> None:
        ex = FakeExecutor()
        with pytest.raises(ValueError):
            await plan_and_execute_reads(
                [Holding(start=0, count=0, dtype="uint16")], ex
            )

    async def test_results_order_matches_input(self) -> None:
        # Even though execution is parallel, results must be parallel to
        # the input list.
        ex = FakeExecutor(
            holding={**{i: i for i in range(5)}, **{i: i for i in range(20, 25)}}
        )
        results = await plan_and_execute_reads(
            [
                Holding(start=20, count=5, dtype="uint16"),  # first item: high addr
                Holding(start=0, count=5, dtype="uint16"),   # second item: low addr
            ],
            ex,
        )
        assert results[0].values == list(range(20, 25))
        assert results[1].values == list(range(0, 5))


# ---------- mixed batch ----------------------------------------------------


class TestMixedBatch:
    async def test_holding_input_coil_discrete_together(self) -> None:
        ex = FakeExecutor(
            holding={0: 0x1234},
            input_regs={0: 0x5678},
            coils={0: True, 1: False},
            discrete={0: False, 1: True},
        )
        results = await plan_and_execute_reads(
            [
                Holding(start=0, count=1, dtype="uint16"),
                Input(start=0, count=1, dtype="uint16"),
                Coil(start=0, count=2),
                Discrete(start=0, count=2),
            ],
            ex,
        )
        assert all(r.ok for r in results)
        assert results[0].values == [0x1234]
        assert results[1].values == [0x5678]
        assert results[2].values == [True, False]
        assert results[3].values == [False, True]
        # Four FCs, four requests.
        assert len(ex.sent_pdus) == 4
