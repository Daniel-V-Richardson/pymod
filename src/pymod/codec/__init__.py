"""Data-mapping codec.

Pure functions over register blocks (sequences of 16-bit ints). No
protocol awareness, no I/O.

* `values` — int16/uint16/int32/uint32/float32 with word/byte order.
* `bits`   — single-bit and multi-bit extraction with word/byte/bit order.
"""

from __future__ import annotations

from ._common import ByteOrder, WordOrder, bytes_to_registers, registers_to_bytes
from .bits import BitNumbering, extract_bit, extract_bits
from .values import NumericDType, decode_registers, encode_registers, regs_per_item

__all__ = [
    "BitNumbering",
    "ByteOrder",
    "NumericDType",
    "WordOrder",
    "bytes_to_registers",
    "decode_registers",
    "encode_registers",
    "extract_bit",
    "extract_bits",
    "registers_to_bytes",
    "regs_per_item",
]
