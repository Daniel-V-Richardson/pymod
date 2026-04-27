# Batch reads

The signature feature of `bacsys-pymod` is the heterogeneous batch read:
you describe what you want as a list of typed items, the planner figures
out the minimum number of Modbus requests, executes them, and re-assembles
the results back per-item.

## The request planner

Three rules govern how items become wire requests:

1. **Group by area.** `Holding` and `Input` use different function codes
   (FC03 vs FC04) and never coalesce. Same for `Coil` (FC01) and
   `Discrete` (FC02).
2. **Coalesce contiguous ranges.** Within an area, overlapping or
   adjacent ranges merge into one request.
3. **Split at PDU limits.** Anything over 125 registers (or 2000 coils)
   is split into multiple requests automatically.

Worked example:

```python
results = await client.read([
    pymod.Holding(start=0,   count=5),     # registers 0..4
    pymod.Holding(start=5,   count=10),    # registers 5..14   — adjacent, MERGES
    pymod.Holding(start=200, count=5),     # registers 200..204 — separate
    pymod.Coil(start=0, count=16),         # different FC
])
```

This produces **three** wire requests:

| Wire request                       | Reason                              |
|------------------------------------|-------------------------------------|
| FC03 read holding 0..14 (15 regs)  | First two items merged              |
| FC03 read holding 200..204         | Non-adjacent, gets its own request  |
| FC01 read 16 coils at 0            | Different FC, can't merge with FC03 |

The four `ReadResult`s come back parallel to the input list, and each
slice of the response data is decoded per the item's dtype.

## Item types

```python
pymod.Holding(start, count, dtype="uint16",
              word_order="big", byte_order="big",
              bit_index=None, bit_indices=None,
              bit_numbering="lsb_first")

pymod.Input(...)         # same shape as Holding, FC04 instead of FC03
pymod.Coil(start, count) # always returns list[bool], FC01
pymod.Discrete(start, count) # always returns list[bool], FC02
```

```{note}
**`count` is in *registers* for `Holding`/`Input`** (not in items).
For 32-bit dtypes (`int32`, `uint32`, `float32`), one value uses 2
registers, so `count=10` returns 5 values. For 64-bit dtypes, one value
is 4 registers.
```

## Supported dtypes

| dtype     | Registers | Notes                                      |
|-----------|-----------|--------------------------------------------|
| `uint16`  | 1         | unsigned 16-bit                            |
| `int16`   | 1         | signed 16-bit                              |
| `uint32`  | 2         | unsigned 32-bit                            |
| `int32`   | 2         | signed 32-bit                              |
| `float32` | 2         | IEEE 754 single                            |
| `uint64`  | 4         | unsigned 64-bit                            |
| `int64`   | 4         | signed 64-bit                              |
| `float64` | 4         | IEEE 754 double                            |
| `bit`     | N         | extract one bit from the register block    |
| `bits`    | N         | extract multiple bits                      |

## Word and byte order

For multi-register types, the four common PLC layouts are all supported:

| `word_order` | `byte_order` | Conventional name |
|--------------|--------------|-------------------|
| `big`        | `big`        | `ABCD` (Modbus default) |
| `little`     | `big`        | `CDAB` |
| `big`        | `little`     | `BADC` |
| `little`     | `little`     | `DCBA` |

```python
pymod.Holding(start=0, count=2, dtype="float32",
              word_order="little", byte_order="big")  # CDAB
```

## Bit reads

For packed flag registers, you can extract bits without a separate
function-code path — `dtype="bit"` reads N registers as a flat bit string
and pulls out the bit you asked for:

```python
results = await client.read([
    # Read 2 registers (32 bits), return one bool from bit position 8.
    pymod.Holding(start=0, count=2, dtype="bit", bit_index=8),

    # Same idea, but pull a list.
    pymod.Holding(start=0, count=2, dtype="bits", bit_indices=[0, 3, 7, 15]),
])
```

The default `bit_numbering="lsb_first"` means bit 0 is the LSB of the
post-swap integer (matches `(value >> bit_index) & 1`). Set
`bit_numbering="msb_first"` for vendors that label from the top.

```{tip}
A bit read on registers `0..1` and a uint16 read on register `2` will
**still coalesce** into a single FC03 request of 3 registers — bit
extraction is just decoding logic, not a separate wire path.
```

## Per-item failure isolation

```python
results = await client.read([
    pymod.Holding(start=0, count=5, dtype="uint16"),    # OK
    pymod.Holding(start=999, count=5, dtype="uint16"),  # IllegalDataAddress
    pymod.Holding(start=20, count=5, dtype="uint16"),   # OK
])

assert results[0].ok                                      # True
assert not results[1].ok
assert isinstance(results[1].error, pymod.IllegalDataAddress)
assert results[2].ok                                      # True — independent
```

A failed chunk only marks the items it covered as failed. Other items
in the batch are unaffected.
