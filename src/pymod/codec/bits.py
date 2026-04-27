"""Extract bits from a register block.

Algorithm (per ADR 04):

1. Each register is two bytes, MSB-first per the Modbus spec.
2. Apply `byte_order` per-register, then `word_order` across registers,
   producing one big-endian byte string of `count * 16` bits — equivalently
   one large unsigned integer.
3. Index into that integer per `bit_numbering`. The default is
   `lsb_first`: bit 0 is the LSB, equivalent to `(value >> bit_index) & 1`.
   `msb_first` flips so bit 0 is the most-significant bit of the assembled
   integer (vendors that label bits from the top).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ._common import ByteOrder, WordOrder, registers_to_bytes

BitNumbering = Literal["lsb_first", "msb_first"]


def _resolve_pos(bit_index: int, total_bits: int, bit_numbering: BitNumbering) -> int:
    if not 0 <= bit_index < total_bits:
        raise ValueError(
            f"bit_index {bit_index} out of range for {total_bits}-bit window"
        )
    if bit_numbering == "lsb_first":
        return bit_index
    return total_bits - 1 - bit_index


def _registers_to_int(
    registers: Sequence[int],
    word_order: WordOrder,
    byte_order: ByteOrder,
) -> int:
    raw = registers_to_bytes(registers, word_order, byte_order)
    return int.from_bytes(raw, "big", signed=False)


def extract_bit(
    registers: Sequence[int],
    bit_index: int,
    *,
    word_order: WordOrder = "big",
    byte_order: ByteOrder = "big",
    bit_numbering: BitNumbering = "lsb_first",
) -> bool:
    """Return a single bit from a register block."""
    if len(registers) == 0:
        raise ValueError("register block is empty")
    total_bits = len(registers) * 16
    pos = _resolve_pos(bit_index, total_bits, bit_numbering)
    value = _registers_to_int(registers, word_order, byte_order)
    return bool((value >> pos) & 1)


def extract_bits(
    registers: Sequence[int],
    bit_indices: Sequence[int],
    *,
    word_order: WordOrder = "big",
    byte_order: ByteOrder = "big",
    bit_numbering: BitNumbering = "lsb_first",
) -> list[bool]:
    """Return a list of bits at the given indices from a register block.

    Returned list is parallel to `bit_indices`.
    """
    if len(registers) == 0:
        raise ValueError("register block is empty")
    total_bits = len(registers) * 16
    value = _registers_to_int(registers, word_order, byte_order)
    return [
        bool((value >> _resolve_pos(idx, total_bits, bit_numbering)) & 1)
        for idx in bit_indices
    ]
