"""Encode/decode int16, uint16, int32, uint32, float32 across registers.

Pure functions. Inputs are 16-bit register values (0..0xFFFF); outputs are
typed Python values. `word_order` and `byte_order` together select the
ABCD / CDAB / BADC / DCBA layout — see `codec/_common.py`.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from typing import Literal

from ._common import ByteOrder, WordOrder, bytes_to_registers, registers_to_bytes

NumericDType = Literal[
    "int16", "uint16",
    "int32", "uint32", "float32",
    "int64", "uint64", "float64",
]

_REGS_PER_ITEM: dict[str, int] = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
    "uint64": 4,
    "int64": 4,
    "float64": 4,
}

_INT_RANGES: dict[str, tuple[int, int]] = {
    "uint16": (0, 0xFFFF),
    "int16": (-0x8000, 0x7FFF),
    "uint32": (0, 0xFFFF_FFFF),
    "int32": (-0x8000_0000, 0x7FFF_FFFF),
    "uint64": (0, 0xFFFF_FFFF_FFFF_FFFF),
    "int64": (-0x8000_0000_0000_0000, 0x7FFF_FFFF_FFFF_FFFF),
}


def regs_per_item(dtype: NumericDType) -> int:
    """How many 16-bit registers are needed to represent one `dtype` item."""
    if dtype not in _REGS_PER_ITEM:
        raise ValueError(f"unsupported numeric dtype: {dtype}")
    return _REGS_PER_ITEM[dtype]


def decode_registers(
    registers: Sequence[int],
    dtype: NumericDType,
    *,
    word_order: WordOrder = "big",
    byte_order: ByteOrder = "big",
) -> list[int | float]:
    """Decode a register block into a list of typed values.

    The block size must be a multiple of `regs_per_item(dtype)`.
    """
    rpi = regs_per_item(dtype)
    if len(registers) % rpi != 0:
        raise ValueError(
            f"register count {len(registers)} is not a multiple of "
            f"{rpi} for dtype {dtype!r}"
        )
    values: list[int | float] = []
    for i in range(0, len(registers), rpi):
        window = registers[i : i + rpi]
        raw = registers_to_bytes(window, word_order, byte_order)
        values.append(_decode_one(raw, dtype))
    return values


def encode_registers(
    values: Sequence[int | float],
    dtype: NumericDType,
    *,
    word_order: WordOrder = "big",
    byte_order: ByteOrder = "big",
) -> list[int]:
    """Encode a list of typed values into a register block.

    Length of returned register list = `len(values) * regs_per_item(dtype)`.
    """
    regs_per_item(dtype)  # validate dtype up-front
    regs: list[int] = []
    for v in values:
        raw = _encode_one(v, dtype)
        regs.extend(bytes_to_registers(raw, word_order, byte_order))
    return regs


# ---------- per-dtype encode/decode primitives ----------


def _decode_one(raw: bytes, dtype: NumericDType) -> int | float:
    if dtype == "uint16":
        return int.from_bytes(raw, "big", signed=False)
    if dtype == "int16":
        return int.from_bytes(raw, "big", signed=True)
    if dtype == "uint32":
        return int.from_bytes(raw, "big", signed=False)
    if dtype == "int32":
        return int.from_bytes(raw, "big", signed=True)
    if dtype == "float32":
        return float(struct.unpack(">f", raw)[0])
    if dtype == "uint64":
        return int.from_bytes(raw, "big", signed=False)
    if dtype == "int64":
        return int.from_bytes(raw, "big", signed=True)
    if dtype == "float64":
        return float(struct.unpack(">d", raw)[0])
    raise ValueError(f"unsupported numeric dtype: {dtype}")


def _encode_one(value: int | float, dtype: NumericDType) -> bytes:
    if dtype == "float32":
        if not isinstance(value, (int, float)):
            raise TypeError(f"float32 expects int|float, got {type(value).__name__}")
        return struct.pack(">f", float(value))
    if dtype == "float64":
        if not isinstance(value, (int, float)):
            raise TypeError(f"float64 expects int|float, got {type(value).__name__}")
        return struct.pack(">d", float(value))
    # Integer types.
    if isinstance(value, float):
        if not value.is_integer() or math.isnan(value) or math.isinf(value):
            raise ValueError(f"{dtype} expects an integer-valued number, got {value!r}")
        ivalue = int(value)
    else:
        ivalue = int(value)
    lo, hi = _INT_RANGES[dtype]
    if not lo <= ivalue <= hi:
        raise ValueError(f"value {ivalue} out of range for {dtype} ({lo}..{hi})")
    nbytes = 2 * regs_per_item(dtype)
    signed = dtype.startswith("int")
    return ivalue.to_bytes(nbytes, "big", signed=signed)
