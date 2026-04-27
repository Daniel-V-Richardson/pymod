# Quickstart

Five-minute tour. The library has both a sync and an async API; pick
whichever fits your application.

## TCP — sync client

Use this from Flask, scripts, the REPL, or anything else that doesn't
already run on `asyncio`.

```python
import pymod

client = pymod.Client.tcp(
    "127.0.0.1", 502,
    unit_id=1,
    timeout_s=0.5,
    retry=pymod.RetryPolicy(max_attempts=2),  # 1 retry on top of the first attempt
)
client.connect()
try:
    results = client.read([
        pymod.Holding(start=0, count=10, dtype="int16"),
    ])
    for value in results[0].values:
        print(value)
finally:
    client.close()
```

A single `Client` instance is **safe to share across many threads**
concurrently — all calls are dispatched onto a private asyncio loop
running on a daemon thread.

## TCP — async client

```python
import asyncio
import pymod

async def main():
    async with pymod.AsyncClient.tcp("127.0.0.1", 502, unit_id=1) as c:
        results = await c.read([
            pymod.Holding(start=0, count=5, dtype="float32"),
            pymod.Coil(start=0, count=16),
        ])
        for r in results:
            print(r.values if r.ok else r.error)

asyncio.run(main())
```

## Serial RTU

Same shape, just a different factory:

```python
client = pymod.Client.rtu(
    "/dev/ttyUSB0", baudrate=9600,
    bytesize=8, parity="N", stopbits=1,
    unit_id=1, timeout_s=0.5,
)
```

```{note}
On Windows the port is something like `"COM5"`. The bus is
internally serialised — you can share one `Client` across multiple
threads or tasks and the requests will go on the wire one at a time.
```

## RTU over TCP (Moxa-style gateways)

For brownfield deployments where a serial-to-Ethernet converter sits
between you and the actual RTU slave:

```python
client = pymod.Client.rtu_over_tcp("10.0.0.5", 502, unit_id=1)
```

## Writing values

```python
client.write([
    pymod.WriteHolding(start=0, values=[42], dtype="uint16"),       # FC06
    pymod.WriteHolding(start=10, values=[1.5, 2.5], dtype="float32"),  # FC16
    pymod.WriteCoils(start=0, values=[True, False, True]),           # FC15
])
```

The function code is selected automatically by value count and dtype:

| Item                                              | FC chosen |
|---------------------------------------------------|-----------|
| `WriteHolding` with one `uint16`/`int16` value    | FC06      |
| `WriteHolding` with multiple values or 32/64-bit  | FC16      |
| `WriteCoils` with one bool                        | FC05      |
| `WriteCoils` with multiple bools                  | FC15      |

## Per-call overrides

Every read/write/execute call accepts per-call `unit_id`, `timeout_s`,
and `retry` arguments that override the client defaults — useful when
talking to multiple slaves on a shared RTU bus, or doing one
control-loop write that needs different retry semantics than a discovery
scan.

```python
client.read(items, unit_id=42, timeout_s=2.0, retry=pymod.RetryPolicy(max_attempts=1))
```

## Error handling

Three families of errors, each with distinct semantics:

| Class                       | When                                       | Retry by default? |
|-----------------------------|--------------------------------------------|--------------------|
| `ModbusTimeoutError`        | no response within timeout                 | yes                |
| `ModbusConnectionError`     | TCP closed / serial port gone              | yes                |
| `ModbusExceptionResponse`   | slave returned a NAK (illegal address etc.)| **no**             |

Subclasses of `ModbusExceptionResponse` map to specific Modbus exception
codes: `IllegalFunction`, `IllegalDataAddress`, `IllegalDataValue`,
`SlaveDeviceFailure`, etc.

In a batch read/write, **per-item failures are isolated** — if one chunk
times out, only the items it covered are marked `ok=False`; other items
in the same batch still succeed.

## What's next

- Heterogeneous batches and the planner's coalescing rules:
  [batch reads](batch-reads.md).
- Building a Modbus server for protocol bridging: [server](server.md).
- The `pymod` CLI: [cli](cli.md).
- Full API reference: [api](api.md).
