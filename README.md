# bacsys-pymod

[![PyPI version](https://img.shields.io/pypi/v/bacsys-pymod.svg)](https://pypi.org/project/bacsys-pymod/)
[![Python versions](https://img.shields.io/pypi/pyversions/bacsys-pymod.svg)](https://pypi.org/project/bacsys-pymod/)
[![Documentation Status](https://readthedocs.org/projects/bacsys-pymod/badge/?version=latest)](https://bacsys-pymod.readthedocs.io/en/latest/)
[![CI](https://github.com/Daniel-V-Richardson/pymod/actions/workflows/ci.yml/badge.svg)](https://github.com/Daniel-V-Richardson/pymod/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/pypi/l/bacsys-pymod.svg)](https://github.com/Daniel-V-Richardson/pymod/blob/main/LICENSE)

Production-grade **Modbus TCP / RTU driver** for Python. Library + one-shot CLI.

> *Distribution name `bacsys-pymod`, import name `pymod`.*

```python
import pymod

with pymod.Client.tcp("10.0.0.5", 502, unit_id=1) as c:
    results = c.read([
        pymod.Holding(start=0, count=10, dtype="float32"),
        pymod.Coil(start=0, count=16),
        pymod.Holding(start=0, count=2, dtype="bit", bit_index=8),
    ])
    for r in results:
        print(r.values if r.ok else f"failed: {r.error}")
```

## Highlights

- **Modbus TCP** with concurrent connections and request pipelining (TID demultiplexing).
- **Modbus RTU** over a serial port — sequentialized internally, safe to share across callers.
- **Modbus RTU over TCP** for serial-to-Ethernet gateways (Moxa-style).
- **All standard function codes** (FC01/02/03/04/05/06/15/16) plus a registration hook for custom FCs.
- **Heterogeneous batch reads** — pass a list of typed items (Holding, Input, Coil, Discrete) and the planner coalesces adjacent same-area ranges into the minimum number of Modbus requests.
- **Typed values** — `int16`, `uint16`, `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64`, plus bit extraction from registers.
- **All four PLC byte/word orderings** (ABCD / CDAB / BADC / DCBA) configurable per item.
- **Sync facade** for Flask / scripts / REPL — `pymod.Client.tcp(...)` is a normal blocking object backed by an internal asyncio loop.
- **Server mode** — callback-based, no internal datastore. Bring your own data, use it for protocol bridging (e.g. Modbus → BACnet / MQTT).
- **`pymod` CLI** — `read`, `write`, `scan`, `serve`, plus an interactive `--guide` wizard.
- Python 3.11+, pure Python (one runtime dep: `pyserial`), `mypy --strict` clean.

## Install

```bash
pip install bacsys-pymod
```

Once installed:

```python
import pymod
print(pymod.__version__)
```

…and the `pymod` CLI is on PATH.

## Quick examples

### TCP — sync client (Flask, scripts, REPL)

```python
import pymod

client = pymod.Client.tcp("127.0.0.1", 502, unit_id=1, timeout_s=0.5)
client.connect()
try:
    results = client.read([pymod.Holding(start=0, count=10, dtype="int16")])
    print(results[0].values)
finally:
    client.close()
```

### TCP — async client

```python
import asyncio, pymod

async def main():
    async with pymod.AsyncClient.tcp("127.0.0.1", 502, unit_id=1) as c:
        results = await c.read([pymod.Holding(start=0, count=10, dtype="int16")])
        print(results[0].values)

asyncio.run(main())
```

### Serial RTU

```python
client = pymod.Client.rtu("COM5", baudrate=9600, parity="N", unit_id=1)
```

…the rest of the API is identical to the TCP client.

### Modbus TCP server (callback-based)

```python
import asyncio, pymod

async def on_read(area, address, count):
    return store.read(area, address, count)

async def on_write(area, address, values):
    store.write(area, address, values)

async def main():
    async with pymod.Server(host="0.0.0.0", port=502,
                            on_read=on_read, on_write=on_write):
        await asyncio.Future()

asyncio.run(main())
```

The server has no internal datastore — the host application owns the data, the server is just a Modbus protocol terminator. Perfect for bridging Modbus to other industrial protocols.

### CLI

```bash
pymod read  --host 127.0.0.1 --port 502 --area holding --start 0 --count 10 --dtype int16
pymod write --host 127.0.0.1 --port 502 --area holding --start 0 --values 1,2,3 --dtype uint16
pymod scan  --host 127.0.0.1 --port 502
pymod serve --host 0.0.0.0   --port 5020 --holding 0=100,1=200
pymod --guide       # interactive wizard
```

Each subcommand has detailed `--help`.

## Documentation

Full docs at <https://bacsys-pymod.readthedocs.io/>.

## License

MIT. See [LICENSE](LICENSE).
