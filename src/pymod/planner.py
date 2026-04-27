"""Request planner.

Takes a list of typed `ReadItem` / `WriteItem` and produces the minimal
sequence of Modbus requests needed to satisfy them, then re-assembles
results into per-item form.

Read-side rules:
  * Items are grouped by area (Holding/Input/Coil/Discrete). Different
    areas use different function codes and never coalesce together.
  * Within an area, overlapping or adjacent ranges merge into one
    contiguous read. Non-adjacent ranges produce separate reads.
  * Anything exceeding the per-FC PDU limit (125 registers / 2000 coils)
    is split across multiple sequential reads.
  * Item failures are recorded per-item: a chunk timing out only marks the
    items it covered as failed; other items in the same batch succeed
    independently.

Write-side rules:
  * Writes are not coalesced across items — the user supplied specific
    values for specific addresses, and merging would obscure intent.
  * FC06 vs FC16 (and FC05 vs FC15) is selected by value count.
  * A single oversized write is split (rare; only if values exceed the
    per-FC limit).
  * Encoding errors (out-of-range values, missing required fields) raise
    immediately rather than being reported per-item — they are
    programming errors, not wire failures.

This module owns NO transport and NO retry logic. The caller passes a
`PduExecutor` that already has unit-id, timeout, and retry policy baked
in via closure. Mockable for tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ._types import (
    Area,
    Coil,
    Discrete,
    Holding,
    Input,
    ReadItem,
    ReadResult,
    WriteCoils,
    WriteHolding,
    WriteItem,
    WriteResult,
)
from .codec import (
    decode_registers,
    encode_registers,
    extract_bit,
    extract_bits,
)
from .errors import ModbusError
from .protocol.pdu import (
    MAX_READ_COILS,
    MAX_READ_DISCRETE_INPUTS,
    MAX_READ_REGISTERS,
    MAX_WRITE_COILS,
    MAX_WRITE_REGISTERS,
    decode_read_coils,
    decode_read_discrete_inputs,
    decode_read_holding_registers,
    decode_read_input_registers,
    decode_write_multiple_coils,
    decode_write_multiple_registers,
    decode_write_single_coil,
    decode_write_single_register,
    encode_read_coils,
    encode_read_discrete_inputs,
    encode_read_holding_registers,
    encode_read_input_registers,
    encode_write_multiple_coils,
    encode_write_multiple_registers,
    encode_write_single_coil,
    encode_write_single_register,
)

PduExecutor = Callable[[bytes], Awaitable[bytes]]
"""Send a request PDU, return the response PDU. Transport-agnostic."""


# ---------- read-side internal types ---------------------------------------


@dataclass(frozen=True)
class _ReadChunk:
    """One Modbus read on the wire. Units are registers (FC03/04) or
    bits/coils (FC01/02) — depends on `area`."""

    area: Area
    start: int
    count: int


@dataclass
class _ReadChunkResult:
    chunk: _ReadChunk
    values: list[int] | list[bool] | None
    error: ModbusError | None


_READ_LIMITS: dict[Area, int] = {
    Area.HOLDING_REGISTER: MAX_READ_REGISTERS,
    Area.INPUT_REGISTER: MAX_READ_REGISTERS,
    Area.COIL: MAX_READ_COILS,
    Area.DISCRETE_INPUT: MAX_READ_DISCRETE_INPUTS,
}


def _item_area(item: ReadItem) -> Area:
    if isinstance(item, Holding):
        return Area.HOLDING_REGISTER
    if isinstance(item, Input):
        return Area.INPUT_REGISTER
    if isinstance(item, Coil):
        return Area.COIL
    if isinstance(item, Discrete):
        return Area.DISCRETE_INPUT
    raise TypeError(f"unknown ReadItem type: {type(item).__name__}")


def _validate_read_item(item: ReadItem) -> None:
    if item.count <= 0:
        raise ValueError(f"item count must be > 0, got {item.count}")
    if isinstance(item, (Holding, Input)):
        if item.dtype == "bit" and item.bit_index is None:
            raise ValueError("dtype='bit' requires bit_index")
        if item.dtype == "bits" and item.bit_indices is None:
            raise ValueError("dtype='bits' requires bit_indices")


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent ``(start, end_exclusive)`` pairs."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged: list[list[int]] = [list(sorted_ranges[0])]
    for s, e in sorted_ranges[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _plan_read_chunks(items: Sequence[ReadItem]) -> list[_ReadChunk]:
    """Group items by area, coalesce ranges, split by per-FC PDU limit."""
    by_area: dict[Area, list[tuple[int, int]]] = {}
    for item in items:
        area = _item_area(item)
        by_area.setdefault(area, []).append((item.start, item.start + item.count))

    chunks: list[_ReadChunk] = []
    for area, ranges in by_area.items():
        merged = _merge_ranges(ranges)
        max_units = _READ_LIMITS[area]
        for s, e in merged:
            cur = s
            while cur < e:
                chunk_end = min(cur + max_units, e)
                chunks.append(_ReadChunk(area=area, start=cur, count=chunk_end - cur))
                cur = chunk_end
    return chunks


async def _execute_read_chunk(
    chunk: _ReadChunk,
    execute: PduExecutor,
) -> _ReadChunkResult:
    try:
        if chunk.area is Area.HOLDING_REGISTER:
            req = encode_read_holding_registers(chunk.start, chunk.count)
            resp = await execute(req)
            values: list[int] | list[bool] = decode_read_holding_registers(resp, chunk.count)
        elif chunk.area is Area.INPUT_REGISTER:
            req = encode_read_input_registers(chunk.start, chunk.count)
            resp = await execute(req)
            values = decode_read_input_registers(resp, chunk.count)
        elif chunk.area is Area.COIL:
            req = encode_read_coils(chunk.start, chunk.count)
            resp = await execute(req)
            values = decode_read_coils(resp, chunk.count)
        elif chunk.area is Area.DISCRETE_INPUT:
            req = encode_read_discrete_inputs(chunk.start, chunk.count)
            resp = await execute(req)
            values = decode_read_discrete_inputs(resp, chunk.count)
        else:
            raise AssertionError(f"unhandled area: {chunk.area}")
        return _ReadChunkResult(chunk=chunk, values=values, error=None)
    except ModbusError as e:
        return _ReadChunkResult(chunk=chunk, values=None, error=e)


def _decode_read_item(
    item: ReadItem,
    chunk_results: list[_ReadChunkResult],
) -> ReadResult:
    item_area = _item_area(item)
    item_end = item.start + item.count

    covering = [
        cr
        for cr in chunk_results
        if cr.chunk.area is item_area
        and cr.chunk.start < item_end
        and cr.chunk.start + cr.chunk.count > item.start
    ]
    covering.sort(key=lambda cr: cr.chunk.start)

    if not covering:
        # Should be unreachable — every item produced at least one chunk.
        return ReadResult(
            item=item,
            values=[],
            ok=False,
            error=ModbusError("internal planner error: no chunk covers item"),
        )

    for cr in covering:
        if cr.error is not None:
            return ReadResult(item=item, values=[], ok=False, error=cr.error)

    base_start = covering[0].chunk.start
    flat: list[Any] = []
    for cr in covering:
        assert cr.values is not None
        flat.extend(cr.values)

    rel_start = item.start - base_start
    rel_end = rel_start + item.count
    item_values = flat[rel_start:rel_end]

    if isinstance(item, (Coil, Discrete)):
        return ReadResult(item=item, values=list(item_values), ok=True)

    # Holding or Input.
    return _decode_register_item(item, item_values)


def _decode_register_item(
    item: Holding | Input,
    raw_registers: list[Any],
) -> ReadResult:
    dtype = item.dtype
    try:
        if dtype == "bit":
            assert item.bit_index is not None  # validated up-front
            decoded: list[Any] = [
                extract_bit(
                    raw_registers,
                    item.bit_index,
                    word_order=item.word_order,
                    byte_order=item.byte_order,
                    bit_numbering=item.bit_numbering,
                )
            ]
        elif dtype == "bits":
            assert item.bit_indices is not None
            decoded = list(
                extract_bits(
                    raw_registers,
                    item.bit_indices,
                    word_order=item.word_order,
                    byte_order=item.byte_order,
                    bit_numbering=item.bit_numbering,
                )
            )
        else:
            decoded = list(
                decode_registers(
                    raw_registers,
                    dtype,
                    word_order=item.word_order,
                    byte_order=item.byte_order,
                )
            )
    except ValueError as e:
        # Decoding failed — surface as item-level failure rather than
        # taking down the batch.
        return ReadResult(
            item=item,
            values=[],
            ok=False,
            error=ModbusError(f"decode failed: {e}"),
        )
    return ReadResult(item=item, values=decoded, ok=True)


async def plan_and_execute_reads(
    items: Sequence[ReadItem],
    execute: PduExecutor,
) -> list[ReadResult]:
    """Coalesce items into minimal Modbus reads, execute, assemble per-item
    results. Returns a list parallel to `items`."""
    if not items:
        return []
    for item in items:
        _validate_read_item(item)

    chunks = _plan_read_chunks(items)
    chunk_results = await asyncio.gather(
        *(_execute_read_chunk(c, execute) for c in chunks)
    )
    return [_decode_read_item(item, list(chunk_results)) for item in items]


# ---------- write-side internal types --------------------------------------


@dataclass(frozen=True)
class _WriteChunk:
    item_idx: int
    area: Area
    start: int
    register_values: list[int] | None  # for Holding writes
    coil_values: list[bool] | None  # for Coil writes


@dataclass
class _WriteChunkResult:
    chunk: _WriteChunk
    error: ModbusError | None


def _validate_write_item(item: WriteItem) -> None:
    if isinstance(item, WriteHolding):
        if len(item.values) == 0:
            raise ValueError("WriteHolding values must be non-empty")
    elif isinstance(item, WriteCoils):
        if len(item.values) == 0:
            raise ValueError("WriteCoils values must be non-empty")
    else:
        raise TypeError(f"unknown WriteItem type: {type(item).__name__}")


def _plan_writes(items: Sequence[WriteItem]) -> list[_WriteChunk]:
    chunks: list[_WriteChunk] = []
    for idx, item in enumerate(items):
        _validate_write_item(item)
        if isinstance(item, WriteHolding):
            regs = encode_registers(
                item.values,
                item.dtype,
                word_order=item.word_order,
                byte_order=item.byte_order,
            )
            cur = 0
            addr = item.start
            while cur < len(regs):
                size = min(MAX_WRITE_REGISTERS, len(regs) - cur)
                chunks.append(
                    _WriteChunk(
                        item_idx=idx,
                        area=Area.HOLDING_REGISTER,
                        start=addr,
                        register_values=regs[cur : cur + size],
                        coil_values=None,
                    )
                )
                cur += size
                addr += size
        elif isinstance(item, WriteCoils):
            values = list(item.values)
            cur = 0
            addr = item.start
            while cur < len(values):
                size = min(MAX_WRITE_COILS, len(values) - cur)
                chunks.append(
                    _WriteChunk(
                        item_idx=idx,
                        area=Area.COIL,
                        start=addr,
                        register_values=None,
                        coil_values=values[cur : cur + size],
                    )
                )
                cur += size
                addr += size
    return chunks


async def _execute_write_chunk(
    chunk: _WriteChunk,
    execute: PduExecutor,
) -> _WriteChunkResult:
    try:
        if chunk.area is Area.HOLDING_REGISTER:
            assert chunk.register_values is not None
            if len(chunk.register_values) == 1:
                req = encode_write_single_register(chunk.start, chunk.register_values[0])
                resp = await execute(req)
                decode_write_single_register(resp)
            else:
                req = encode_write_multiple_registers(chunk.start, chunk.register_values)
                resp = await execute(req)
                decode_write_multiple_registers(resp)
        elif chunk.area is Area.COIL:
            assert chunk.coil_values is not None
            if len(chunk.coil_values) == 1:
                req = encode_write_single_coil(chunk.start, chunk.coil_values[0])
                resp = await execute(req)
                decode_write_single_coil(resp)
            else:
                req = encode_write_multiple_coils(chunk.start, chunk.coil_values)
                resp = await execute(req)
                decode_write_multiple_coils(resp)
        else:
            raise AssertionError(f"unhandled area: {chunk.area}")
        return _WriteChunkResult(chunk=chunk, error=None)
    except ModbusError as e:
        return _WriteChunkResult(chunk=chunk, error=e)


async def plan_and_execute_writes(
    items: Sequence[WriteItem],
    execute: PduExecutor,
) -> list[WriteResult]:
    """Encode writes, choose FC05/06/15/16 per item, execute, assemble per-
    item results. Returns a list parallel to `items`."""
    if not items:
        return []

    chunks = _plan_writes(items)  # may raise on encoding errors

    chunk_results = await asyncio.gather(
        *(_execute_write_chunk(c, execute) for c in chunks)
    )

    results: list[WriteResult] = [
        WriteResult(item=item, ok=True, error=None) for item in items
    ]
    # Apply errors. First error per item wins; subsequent successes don't
    # un-fail a partially-failed item.
    for cr in chunk_results:
        if cr.error is None:
            continue
        idx = cr.chunk.item_idx
        if results[idx].ok:
            results[idx] = WriteResult(item=items[idx], ok=False, error=cr.error)
    return results
