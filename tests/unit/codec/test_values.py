"""Tests for `pymod.codec.values`.

The four word/byte-order combinations correspond to the conventional PLC
labels ABCD / CDAB / BADC / DCBA. Tests verify each pattern against
hand-computed register layouts so a bug in any one direction is caught.
"""

from __future__ import annotations

import math
import struct

import pytest

from pymod.codec.values import (
    decode_registers,
    encode_registers,
    regs_per_item,
)


# ---------- regs_per_item ---------------------------------------------------


class TestRegsPerItem:
    def test_16_bit_types(self) -> None:
        assert regs_per_item("uint16") == 1
        assert regs_per_item("int16") == 1

    def test_32_bit_types(self) -> None:
        assert regs_per_item("uint32") == 2
        assert regs_per_item("int32") == 2
        assert regs_per_item("float32") == 2

    def test_unknown_dtype(self) -> None:
        with pytest.raises(ValueError):
            regs_per_item("string")  # type: ignore[arg-type]


# ---------- uint16 / int16 -------------------------------------------------


class TestUint16:
    def test_decode_default_order(self) -> None:
        assert decode_registers([0x1234, 0xFFFF, 0x0000], "uint16") == [0x1234, 0xFFFF, 0x0000]

    def test_byte_swap(self) -> None:
        # Same wire register 0x1234, byte-swapped interpretation = 0x3412.
        assert decode_registers([0x1234], "uint16", byte_order="little") == [0x3412]

    def test_round_trip_all_orders(self) -> None:
        values = [0, 1, 0x00FF, 0xFF00, 0xFFFF, 12345]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(values, "uint16", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                got = decode_registers(regs, "uint16", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                assert got == values, f"failed at word={word_order} byte={byte_order}"

    def test_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([0x10000], "uint16")
        with pytest.raises(ValueError):
            encode_registers([-1], "uint16")


class TestInt16:
    def test_negative_round_trip(self) -> None:
        values = [-1, -32768, 32767, 0]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(values, "int16", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                got = decode_registers(regs, "int16", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                assert got == values

    def test_minus_one_decodes_to_minus_one_default(self) -> None:
        # 0xFFFF interpreted as int16 = -1.
        assert decode_registers([0xFFFF], "int16") == [-1]

    def test_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([32768], "int16")
        with pytest.raises(ValueError):
            encode_registers([-32769], "int16")


# ---------- uint32 / int32 — the four PLC orderings -----------------------


# uint32 0x12345678 broken into bytes: 12 34 56 78.
# Per layout, the registers received over the wire (each high-byte-first):
#   ABCD (word=big, byte=big):     [0x1234, 0x5678]
#   CDAB (word=little, byte=big):  [0x5678, 0x1234]
#   BADC (word=big, byte=little):  [0x3412, 0x7856]
#   DCBA (word=little, byte=little):[0x7856, 0x3412]


class TestUint32Layouts:
    VALUE = 0x12345678

    def test_abcd(self) -> None:
        assert decode_registers([0x1234, 0x5678], "uint32") == [self.VALUE]

    def test_cdab(self) -> None:
        assert decode_registers(
            [0x5678, 0x1234], "uint32", word_order="little"
        ) == [self.VALUE]

    def test_badc(self) -> None:
        assert decode_registers(
            [0x3412, 0x7856], "uint32", byte_order="little"
        ) == [self.VALUE]

    def test_dcba(self) -> None:
        assert decode_registers(
            [0x7856, 0x3412], "uint32", word_order="little", byte_order="little"
        ) == [self.VALUE]

    def test_round_trip_all_layouts(self) -> None:
        values = [0, 1, 0x12345678, 0xFFFFFFFF]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(values, "uint32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                got = decode_registers(regs, "uint32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                assert got == values

    def test_multi_value_decoding(self) -> None:
        # Two uint32 values, ABCD: [V1_hi, V1_lo, V2_hi, V2_lo].
        regs = [0x0000, 0x000A, 0x0000, 0x0014]  # V1=10, V2=20
        assert decode_registers(regs, "uint32") == [10, 20]


class TestInt32:
    def test_negative_one_default(self) -> None:
        # int32 = -1 → 0xFFFFFFFF → [0xFFFF, 0xFFFF].
        assert decode_registers([0xFFFF, 0xFFFF], "int32") == [-1]

    def test_round_trip_negative_extremes(self) -> None:
        values = [-(2**31), 2**31 - 1, 0, -1]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(values, "int32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                got = decode_registers(regs, "int32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                assert got == values

    def test_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([2**31], "int32")
        with pytest.raises(ValueError):
            encode_registers([-(2**31) - 1], "int32")


# ---------- float32 --------------------------------------------------------


class TestFloat32:
    def test_pi_round_trip_all_layouts(self) -> None:
        values = [3.14159265, -2.5, 0.0, 1.0, 1.5e10]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(values, "float32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                got = decode_registers(regs, "float32", word_order=word_order, byte_order=byte_order)  # type: ignore[arg-type]
                # float32 has ~7 significant digits.
                for a, b in zip(got, values):
                    assert isinstance(a, float)
                    assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)

    def test_known_ieee_value(self) -> None:
        # 1.0 in IEEE 754 single = 0x3F800000.
        assert decode_registers([0x3F80, 0x0000], "float32") == [1.0]

    def test_inf_and_nan_round_trip(self) -> None:
        # NaN comparison requires `math.isnan`; encode then decode.
        for v in (float("inf"), float("-inf")):
            regs = encode_registers([v], "float32")
            assert decode_registers(regs, "float32") == [v]
        nan_regs = encode_registers([float("nan")], "float32")
        decoded = decode_registers(nan_regs, "float32")
        assert math.isnan(decoded[0])  # type: ignore[arg-type]

    def test_negative_zero(self) -> None:
        # -0.0 has bit pattern 0x80000000.
        regs = encode_registers([-0.0], "float32")
        assert regs == [0x8000, 0x0000]
        # struct.unpack of -0.0 returns -0.0.
        decoded = decode_registers(regs, "float32")
        assert math.copysign(1, decoded[0]) == -1.0  # type: ignore[arg-type]

    def test_int_input_accepted(self) -> None:
        # Integers should convert to float for float32 encoding.
        regs = encode_registers([1, 2], "float32")
        decoded = decode_registers(regs, "float32")
        assert decoded == [1.0, 2.0]


# ---------- error handling -------------------------------------------------


class TestErrors:
    def test_decode_register_count_must_be_multiple_of_two_for_32bit(self) -> None:
        with pytest.raises(ValueError):
            decode_registers([0x1234, 0x5678, 0x9ABC], "uint32")

    def test_register_value_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            decode_registers([0x10000], "uint16")
        with pytest.raises(ValueError):
            decode_registers([-1], "uint16")

    def test_encode_unknown_dtype(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([0], "uint128")  # type: ignore[arg-type]

    def test_encode_int_for_float_dtype_with_non_integer_float_rejected(self) -> None:
        # float32 happily takes any float, including non-integer; but
        # integer dtypes must reject non-integer floats.
        with pytest.raises(ValueError):
            encode_registers([3.14], "uint16")

    def test_encode_nan_for_int_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([float("nan")], "uint16")


# ---------- 64-bit types ---------------------------------------------------


class TestUint64:
    # 0x123456789ABCDEF0 broken into bytes: 12 34 56 78 9A BC DE F0
    # Per Modbus 16-bit-big-endian register packing:
    #   ABCDEFGH (word=big, byte=big):     [0x1234, 0x5678, 0x9ABC, 0xDEF0]
    #   GHEFCDAB (word=little, byte=big):  [0xDEF0, 0x9ABC, 0x5678, 0x1234]
    VALUE = 0x123456789ABCDEF0

    def test_default_layout(self) -> None:
        assert decode_registers(
            [0x1234, 0x5678, 0x9ABC, 0xDEF0], "uint64"
        ) == [self.VALUE]

    def test_word_little(self) -> None:
        assert decode_registers(
            [0xDEF0, 0x9ABC, 0x5678, 0x1234], "uint64", word_order="little"
        ) == [self.VALUE]

    def test_round_trip_all_layouts(self) -> None:
        values = [0, 1, self.VALUE, 0xFFFFFFFFFFFFFFFF]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(
                    values, "uint64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                got = decode_registers(
                    regs, "uint64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                assert got == values, f"failed at word={word_order} byte={byte_order}"

    def test_one_value_uses_four_registers(self) -> None:
        regs = encode_registers([1], "uint64")
        assert len(regs) == 4

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([2**64], "uint64")
        with pytest.raises(ValueError):
            encode_registers([-1], "uint64")


class TestInt64:
    def test_negative_one_default(self) -> None:
        # int64 = -1 → 0xFFFFFFFFFFFFFFFF → 4 × 0xFFFF
        assert decode_registers([0xFFFF] * 4, "int64") == [-1]

    def test_round_trip_extremes(self) -> None:
        values = [-(2**63), 2**63 - 1, 0, -1, 42]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(
                    values, "int64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                got = decode_registers(
                    regs, "int64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                assert got == values

    def test_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_registers([2**63], "int64")
        with pytest.raises(ValueError):
            encode_registers([-(2**63) - 1], "int64")


class TestFloat64:
    def test_pi_round_trip(self) -> None:
        values = [3.141592653589793, -2.718281828, 0.0, 1.5e100, -1.5e-100]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                regs = encode_registers(
                    values, "float64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                got = decode_registers(
                    regs, "float64",
                    word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                )
                # float64 is exact for these literals — direct equality.
                assert got == values

    def test_known_ieee_value(self) -> None:
        # 1.0 IEEE 754 double = 0x3FF0000000000000.
        # As 4 big-endian 16-bit registers: 0x3FF0, 0x0000, 0x0000, 0x0000
        assert decode_registers(
            [0x3FF0, 0x0000, 0x0000, 0x0000], "float64"
        ) == [1.0]

    def test_inf_and_nan(self) -> None:
        for v in (float("inf"), float("-inf")):
            regs = encode_registers([v], "float64")
            assert decode_registers(regs, "float64") == [v]
        nan_regs = encode_registers([float("nan")], "float64")
        decoded = decode_registers(nan_regs, "float64")
        assert math.isnan(decoded[0])  # type: ignore[arg-type]

    def test_one_value_uses_four_registers(self) -> None:
        regs = encode_registers([1.0], "float64")
        assert len(regs) == 4


# ---------- known struct equivalence ---------------------------------------


class TestStructEquivalence:
    """The default (word=big, byte=big) layout must agree byte-for-byte
    with `struct` interpretations — this catches any subtle ordering bug
    against a known reference."""

    def test_uint32_matches_struct(self) -> None:
        for v in (0, 1, 0xCAFEBABE, 0xFFFFFFFF):
            regs = encode_registers([v], "uint32")
            raw = struct.pack(">I", v)
            assert regs[0] == int.from_bytes(raw[:2], "big")
            assert regs[1] == int.from_bytes(raw[2:], "big")

    def test_float32_matches_struct(self) -> None:
        for v in (1.0, -3.14, 1e-6, 1e6):
            regs = encode_registers([v], "float32")
            raw = struct.pack(">f", v)
            assert regs[0] == int.from_bytes(raw[:2], "big")
            assert regs[1] == int.from_bytes(raw[2:], "big")
