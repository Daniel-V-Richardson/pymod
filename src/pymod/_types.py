"""Public typed read/write items and result objects.

These dataclasses are the primary user-facing API for `Client.read()` and
`Client.write()`. They are designed to be constructable from plain dicts as
well so users can drive the client from YAML/JSON config files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Sequence, Union

from .errors import ModbusError

ByteOrder = Literal["big", "little"]
WordOrder = Literal["big", "little"]
BitNumbering = Literal["lsb_first", "msb_first"]
RegisterDType = Literal[
    "int16", "uint16",
    "int32", "uint32", "float32",
    "int64", "uint64", "float64",
    "bit", "bits",
]


class Area(str, Enum):
    """Modbus address space. Determines which function code is used."""

    COIL = "coil"                          # FC01 read, FC05/15 write
    DISCRETE_INPUT = "discrete_input"      # FC02 read (read-only by spec)
    HOLDING_REGISTER = "holding_register"  # FC03 read, FC06/16 write
    INPUT_REGISTER = "input_register"      # FC04 read (read-only by spec)


# ---------- Read items -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Holding:
    """Read from holding registers (FC03), with typed decoding."""

    start: int
    count: int
    dtype: RegisterDType = "uint16"
    word_order: WordOrder = "big"
    byte_order: ByteOrder = "big"
    bit_index: int | None = None
    bit_indices: Sequence[int] | None = None
    bit_numbering: BitNumbering = "lsb_first"


@dataclass(frozen=True, slots=True)
class Input:
    """Read from input registers (FC04), with typed decoding."""

    start: int
    count: int
    dtype: RegisterDType = "uint16"
    word_order: WordOrder = "big"
    byte_order: ByteOrder = "big"
    bit_index: int | None = None
    bit_indices: Sequence[int] | None = None
    bit_numbering: BitNumbering = "lsb_first"


@dataclass(frozen=True, slots=True)
class Coil:
    """Read coils (FC01)."""

    start: int
    count: int


@dataclass(frozen=True, slots=True)
class Discrete:
    """Read discrete inputs (FC02)."""

    start: int
    count: int


ReadItem = Union[Holding, Input, Coil, Discrete]


# ---------- Write items ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WriteHolding:
    """Write holding registers. Planner picks FC06 (single) vs FC16 (multiple)."""

    start: int
    values: Sequence[int | float]
    dtype: Literal["int16", "uint16", "int32", "uint32", "float32"] = "uint16"
    word_order: WordOrder = "big"
    byte_order: ByteOrder = "big"


@dataclass(frozen=True, slots=True)
class WriteCoils:
    """Write coils. Planner picks FC05 (single) vs FC15 (multiple)."""

    start: int
    values: Sequence[bool]


WriteItem = Union[WriteHolding, WriteCoils]


# ---------- Results ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadResult:
    """Per-item read result. Parallel to the input list passed to `read()`."""

    item: ReadItem
    values: Sequence[int | float | bool] = field(default_factory=list)
    ok: bool = True
    error: ModbusError | None = None


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Per-item write result. Parallel to the input list passed to `write()`."""

    item: WriteItem
    ok: bool = True
    error: ModbusError | None = None
