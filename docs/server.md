# Server (callback-based)

`pymod.Server` is a Modbus TCP protocol terminator. It owns no data —
the host application supplies callbacks for reads and writes, and the
server translates Modbus framing to and from those calls.

This shape is deliberate. The primary production use case is **protocol
bridging**: you already have a data store populated from another
protocol (BACnet, MQTT, OPC-UA, etc.), and you want to expose part of
it via Modbus to a SCADA master. Forcing a duplicate, in-driver
datastore would just create cache-coherency problems.

## Minimal example

```python
import asyncio
import pymod

# Your data store. Whatever shape works for your application.
store = {
    pymod.Area.HOLDING_REGISTER: {0: 100, 1: 200, 2: 300},
    pymod.Area.INPUT_REGISTER:   {},
    pymod.Area.COIL:             {0: True, 1: False},
    pymod.Area.DISCRETE_INPUT:   {},
}

async def on_read(area, address, count):
    return [store[area][address + i] for i in range(count)]

async def on_write(area, address, values):
    for i, v in enumerate(values):
        store[area][address + i] = v

async def main():
    server = pymod.Server(
        host="0.0.0.0",
        port=502,
        on_read=on_read,
        on_write=on_write,
    )
    async with server:
        await asyncio.Future()  # run forever

asyncio.run(main())
```

## Callbacks

Both callbacks may be **sync or async** — the server detects awaitables
and awaits them when needed.

```python
def on_read(area, address, count):
    return [...]                  # sync return

async def on_read(area, address, count):
    return await db.fetch(...)    # async also fine
```

## Authorization is implicit in the area

There's no per-register ACL — the function code determines what's
allowed:

| Area              | Read FCs | Write FCs    | Default         |
|-------------------|----------|--------------|-----------------|
| `INPUT_REGISTER`  | FC04     | (none)       | read-only       |
| `DISCRETE_INPUT`  | FC02     | (none)       | read-only       |
| `HOLDING_REGISTER`| FC03     | FC06, FC16   | read-write      |
| `COIL`            | FC01     | FC05, FC15   | read-write      |

If you only expose data through `INPUT_REGISTER` and `DISCRETE_INPUT`,
no write function code can ever reach it — that's how you make data
read-only.

If you want to disable writes entirely, **omit `on_write`** (or set it
to `None`) and every write FC returns `ILLEGAL_FUNCTION` automatically:

```python
server = pymod.Server(host="0.0.0.0", port=502, on_read=on_read)
# on_write defaults to None → all FC05/06/15/16 → ILLEGAL_FUNCTION
```

## Surfacing exception responses from your callbacks

Raise a `ModbusExceptionResponse` subclass to map the callback failure
to a specific Modbus exception code on the wire:

```python
async def on_read(area, address, count):
    if address < 0 or address + count > MAX_ADDR:
        raise pymod.IllegalDataAddress(f"{area} out of range at {address}")
    if some_safety_lockout_active:
        raise pymod.SlaveDeviceBusy("device locked out for maintenance")
    return ...
```

Any **other** exception from a callback is logged at ERROR on
`pymod.server` and surfaces as `SLAVE_DEVICE_FAILURE` (0x04) — so a
buggy callback never takes the whole server down or produces malformed
wire frames.

## Tying writes back to the source protocol

The killer feature for bridging: in `on_write`, forward the value to
whatever protocol owns it upstream:

```python
async def on_write(area, address, values):
    for i, v in enumerate(values):
        addr = address + i
        # Modbus write → BACnet object write.
        bacnet_object = bacnet_map.get((area, addr))
        if bacnet_object is None:
            raise pymod.IllegalDataAddress(f"no BACnet binding for {area}/{addr}")
        await bacnet_object.write(v)
```

The BACnet write completes (or fails) before the server replies on the
Modbus wire — the SCADA master sees the actual outcome.

## Lifecycle

```python
# Async context manager (preferred)
async with pymod.Server(...) as srv:
    print(f"listening on {srv.port}")  # ephemeral port if 0 was used
    await asyncio.Future()  # run forever

# Or explicit:
srv = pymod.Server(...)
await srv.start()
try:
    ...
finally:
    await srv.stop()
```

`is_running` and `active_connections` are queryable at any time. `port`
returns the bound port (useful when you passed `port=0` to bind to an
ephemeral one).

## Using the server from a sync application

If your host application (Flask, etc.) is sync, run the server on its
own daemon thread:

```python
import threading, asyncio, pymod

def _serve_in_background():
    asyncio.run(_amain())

async def _amain():
    async with pymod.Server(host="0.0.0.0", port=502,
                            on_read=on_read, on_write=on_write):
        await asyncio.Future()

threading.Thread(target=_serve_in_background, daemon=True).start()
```

The callbacks run on the server's loop thread — if they touch shared
state from your sync application, use thread-safe access (locks, a
thread-safe queue, SQLAlchemy session-per-thread, etc.).
