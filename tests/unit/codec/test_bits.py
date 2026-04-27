"""Tests for `pymod.codec.bits`."""

from __future__ import annotations

import pytest

from pymod.codec.bits import extract_bit, extract_bits


# Reference value for multi-register tests.
# 0x12345678 in binary:
#   0001_0010_0011_0100_0101_0110_0111_1000
# LSB-first bit positions:
#   bit 0  = 0  (LSB of 0x78)
#   bit 1  = 0
#   bit 2  = 0
#   bit 3  = 1  (0x78 = 0111_1000, bit 3 = 1)
#   bit 4  = 1
#   bit 5  = 1
#   bit 6  = 1
#   bit 7  = 0
#   bit 8  = 0  (LSB of 0x56 = 0101_0110)
#   bit 9  = 1
#   bit 10 = 1
#   bit 11 = 0
#   bit 12 = 1
#   bit 13 = 0
#   bit 14 = 1
#   bit 15 = 0
#   bit 16 = 0  (LSB of 0x34 = 0011_0100)
#   bit 17 = 0
#   bit 18 = 1
#   bit 19 = 0
#   bit 20 = 1
#   bit 21 = 1
#   bit 22 = 0
#   bit 23 = 0
#   bit 24 = 0  (LSB of 0x12 = 0001_0010)
#   bit 25 = 1
#   bit 26 = 0
#   bit 27 = 0
#   bit 28 = 1
#   bit 29 = 0
#   bit 30 = 0
#   bit 31 = 0


# ---------- single-register window ------------------------------------------


class TestSingleRegister:
    def test_lsb_first_default(self) -> None:
        # 0xABCD = 1010_1011_1100_1101
        # bit 0 (LSB) = 1, bit 1 = 0, bit 2 = 1, bit 3 = 1
        # bit 14 = 0, bit 15 (MSB) = 1
        regs = [0xABCD]
        assert extract_bit(regs, 0) is True
        assert extract_bit(regs, 1) is False
        assert extract_bit(regs, 2) is True
        assert extract_bit(regs, 3) is True
        assert extract_bit(regs, 14) is False
        assert extract_bit(regs, 15) is True

    def test_msb_first(self) -> None:
        # MSB-first numbering: bit 0 is the top bit.
        regs = [0xABCD]
        assert extract_bit(regs, 0, bit_numbering="msb_first") is True   # MSB of 0xABCD = 1
        assert extract_bit(regs, 1, bit_numbering="msb_first") is False  # next: 0
        assert extract_bit(regs, 15, bit_numbering="msb_first") is True  # LSB of 0xABCD = 1

    def test_byte_swap_changes_bit_layout(self) -> None:
        # 0x1234 byte-swapped → 0x3412 = 0011_0100_0001_0010
        # bit 0 (LSB) under byte_order=little = 0 (since 0x12 LSB is 0)
        regs = [0x1234]
        # Default order (byte=big): 0x1234 LSB = 0
        assert extract_bit(regs, 0) is False
        # Byte-swapped (byte=little): 0x3412 LSB = 0
        assert extract_bit(regs, 0, byte_order="little") is False
        # bit 1: 0x1234 = ...0100, bit 1 = 0; 0x3412 = ...0010, bit 1 = 1
        assert extract_bit(regs, 1) is False
        assert extract_bit(regs, 1, byte_order="little") is True


# ---------- multi-register window -------------------------------------------


class TestMultiRegister:
    REGS_ABCD = [0x1234, 0x5678]  # 0x12345678 in default (word=big, byte=big)

    def test_lsb_first_known_bits(self) -> None:
        # Per the table at top of file.
        assert extract_bit(self.REGS_ABCD, 0) is False
        assert extract_bit(self.REGS_ABCD, 3) is True
        assert extract_bit(self.REGS_ABCD, 9) is True
        assert extract_bit(self.REGS_ABCD, 28) is True
        assert extract_bit(self.REGS_ABCD, 31) is False

    def test_msb_first(self) -> None:
        # bit 0 (MSB-first) = bit 31 (LSB-first) = 0
        # bit 3 (MSB-first) = bit 28 (LSB-first) = 1
        # bit 31 (MSB-first) = bit 0 (LSB-first) = 0
        assert extract_bit(self.REGS_ABCD, 0, bit_numbering="msb_first") is False
        assert extract_bit(self.REGS_ABCD, 3, bit_numbering="msb_first") is True
        assert extract_bit(self.REGS_ABCD, 31, bit_numbering="msb_first") is False

    def test_word_order_little_recovers_value(self) -> None:
        # CDAB layout produces same logical 0x12345678; same bits.
        regs_cdab = [0x5678, 0x1234]
        for i in (0, 3, 9, 28):
            assert extract_bit(regs_cdab, i, word_order="little") == extract_bit(self.REGS_ABCD, i)

    def test_byte_order_little_recovers_value(self) -> None:
        # BADC layout produces same logical 0x12345678 after swap.
        regs_badc = [0x3412, 0x7856]
        for i in (0, 3, 9, 28):
            assert extract_bit(regs_badc, i, byte_order="little") == extract_bit(self.REGS_ABCD, i)

    def test_dcba_layout(self) -> None:
        regs_dcba = [0x7856, 0x3412]
        for i in (0, 3, 9, 28):
            assert (
                extract_bit(regs_dcba, i, word_order="little", byte_order="little")
                == extract_bit(self.REGS_ABCD, i)
            )


# ---------- extract_bits (multiple) ----------------------------------------


class TestExtractBits:
    def test_returns_parallel_list(self) -> None:
        regs = [0x1234, 0x5678]
        result = extract_bits(regs, [0, 3, 9, 28, 31])
        assert result == [False, True, True, True, False]

    def test_empty_indices_returns_empty(self) -> None:
        assert extract_bits([0xFFFF], []) == []

    def test_msb_first(self) -> None:
        regs = [0x1234, 0x5678]
        # MSB-first bit 0 = LSB-first bit 31, etc.
        result = extract_bits(regs, [0, 3, 31], bit_numbering="msb_first")
        assert result == [False, True, False]


# ---------- error handling -------------------------------------------------


class TestErrors:
    def test_bit_index_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            extract_bit([0xFFFF], 16)
        with pytest.raises(ValueError):
            extract_bit([0xFFFF, 0x0000], 32)
        with pytest.raises(ValueError):
            extract_bit([0xFFFF], -1)

    def test_empty_register_block(self) -> None:
        with pytest.raises(ValueError):
            extract_bit([], 0)
        with pytest.raises(ValueError):
            extract_bits([], [0])

    def test_register_value_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            extract_bit([0x10000], 0)


# ---------- consistency between extract_bit and extract_bits --------------


class TestConsistency:
    def test_single_and_multi_agree(self) -> None:
        regs = [0xDEAD, 0xBEEF]
        for word_order in ("big", "little"):
            for byte_order in ("big", "little"):
                for bit_numbering in ("lsb_first", "msb_first"):
                    indices = list(range(32))
                    multi = extract_bits(
                        regs, indices,
                        word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                        bit_numbering=bit_numbering,  # type: ignore[arg-type]
                    )
                    for i in indices:
                        single = extract_bit(
                            regs, i,
                            word_order=word_order, byte_order=byte_order,  # type: ignore[arg-type]
                            bit_numbering=bit_numbering,  # type: ignore[arg-type]
                        )
                        assert multi[i] == single
