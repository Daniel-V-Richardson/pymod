# bacsys-pymod

Production-grade **Modbus TCP / RTU driver** for Python.

```{warning}
Distribution name on PyPI: `bacsys-pymod` — but the import name is just
`pymod`. So you `pip install bacsys-pymod` and then `import pymod`.
```

## Why another Modbus library

`bacsys-pymod` is built from scratch for industrial deployments where
correctness and operability matter. It's the foundation of an IoT gateway
product, so it's been hardened against the things that actually break in
the field:

- Pipelined TCP requests demuxed by transaction id (no scrambled responses
  when a slave answers out of order).
- RTU buses serialised internally — share one client across many tasks,
  the wire stays clean.
- Auto-reconnect on TCP drops, with a clean failure surface that
  distinguishes timeouts from exception responses from connection loss.
- Heterogeneous batch reads with adjacent-range coalescing (one wire
  request for `Holding(0..4) + Holding(5..14)`).
- All four PLC byte/word orderings (ABCD / CDAB / BADC / DCBA) per item.
- 16-, 32-, and 64-bit signed/unsigned integers and IEEE 754 float32 /
  float64.
- Bit extraction from register blocks (LSB or MSB-first numbering).
- Sync facade for Flask / scripts; async API for high-throughput services.
- Server mode is callback-based — bring your own data store.

## Install

```bash
pip install bacsys-pymod
```

```{note}
Requires Python 3.11 or newer. Pure-Python wheel — works on Linux x86_64,
Windows, and ARM (Allwinner SoC, Raspberry Pi) without recompilation.
```

## 60-second example

```python
import pymod

with pymod.Client.tcp("10.0.0.5", 502, unit_id=1, timeout_s=0.5) as client:
    results = client.read([
        pymod.Holding(start=0,  count=10, dtype="float32"),
        pymod.Holding(start=20, count=5,  dtype="uint16"),
        pymod.Coil(start=0, count=16),
    ])

for r in results:
    if r.ok:
        print(r.values)
    else:
        print(f"failed: {type(r.error).__name__}: {r.error}")
```

The first two `Holding` items above coalesce into a single FC03 request
on the wire (because they're adjacent — `0..9` plus `20..24` would be
two requests). The `Coil` item dispatches to FC01. The planner figures
this out automatically.

## Table of contents

```{toctree}
:maxdepth: 2
:caption: Guides

quickstart
batch-reads
server
cli
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
```

## License

MIT — see `LICENSE` in the source tree.
