"""Shared register-to-bytes helpers used by both `values` and `bits`.

Modbus places each register on the wire as two bytes, **high byte first**
per the spec. The user-facing `word_order` / `byte_order` parameters
describe how a multi-register typed value is laid out on top of that wire
representation:

* `word_order="big"` — first register holds the most significant word.
* `word_order="little"` — first register holds the least significant word.
* `byte_order="big"` — within each register, high byte first (Modbus default).
* `byte_order="little"` — within each register, low byte first (vendor swap).

Together these produce the four conventional layouts: ABCD / CDAB / BADC /
DCBA. The functions here normalize a register block to a flat byte string
(big-endian semantics) regardless of the input layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

ByteOrder = Literal["big", "little"]
WordOrder = Literal["big", "little"]


def registers_to_bytes(
    registers: Sequence[int],
    word_order: WordOrder,
    byte_order: ByteOrder,
) -> bytes:
    """Flatten a register block into a big-endian byte string.

    Each register must be 0..0xFFFF.
    """
    for r in registers:
        if not 0 <= r <= 0xFFFF:
            raise ValueError(f"register value out of range: {r}")
    seq: Sequence[int] = list(reversed(registers)) if word_order == "little" else registers
    if byte_order == "big":
        return b"".join(r.to_bytes(2, "big") for r in seq)
    return b"".join(r.to_bytes(2, "little") for r in seq)


def bytes_to_registers(
    raw: bytes,
    word_order: WordOrder,
    byte_order: ByteOrder,
) -> list[int]:
    """Inverse of `registers_to_bytes`. `raw` length must be a multiple of 2."""
    if len(raw) % 2 != 0:
        raise ValueError(f"byte count must be even, got {len(raw)}")
    chunks = [raw[i : i + 2] for i in range(0, len(raw), 2)]
    if byte_order == "big":
        regs = [int.from_bytes(c, "big") for c in chunks]
    else:
        regs = [int.from_bytes(c, "little") for c in chunks]
    if word_order == "little":
        regs.reverse()
    return regs
