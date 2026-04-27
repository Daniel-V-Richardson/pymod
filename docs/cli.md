# `pymod` CLI

A one-shot command-line tool for Modbus reads, writes, scans, and a
development-only simulator. Built directly on the public client/server
APIs — no duplicate logic.

## Subcommands

| Command       | What it does                                            |
|---------------|---------------------------------------------------------|
| `pymod read`  | Read registers or coils, decode per dtype, print values |
| `pymod write` | Write registers or coils                                |
| `pymod scan`  | Probe device responsiveness                             |
| `pymod serve` | Run a Modbus TCP simulator (development only)           |
| `pymod --guide` | Interactive wizard — prompts for each option          |

Run `pymod COMMAND --help` for full per-command flags and examples.

## Read

```bash
pymod read --host 127.0.0.1 --port 502 --unit-id 1 \
           --area holding --start 0 --count 10 --dtype int16
```

Sample output:

```
[0]: 0x0064 (100)
[1]: 0x00C8 (200)
...
```

Add `--json` to get machine-readable output:

```bash
pymod read --host ... --json
# {"ok": true, "values": [100, 200, 300, ...]}
```

For 32-bit / 64-bit / float values, `--count` is in **registers**, so a
single `float32` needs `--count 2`:

```bash
pymod read --host 10.0.0.5 --area holding --start 10 --count 2 --dtype float32
pymod read --host 10.0.0.5 --area holding --start 12 --count 2 --dtype uint32
pymod read --host 10.0.0.5 --area holding --start 14 --count 4 --dtype float64
```

PLC byte/word ordering:

```bash
pymod read --host ... --dtype float32 --word-order little --byte-order big   # CDAB
```

Bit reads:

```bash
pymod read --host ... --area holding --start 0 --count 2 --dtype bit  --bit-index 8
pymod read --host ... --area holding --start 0 --count 2 --dtype bits --bit-indices 0,3,7,15
```

## Write

```bash
pymod write --host 127.0.0.1 --area holding --start 0 --values 0xCAFE --dtype uint16
pymod write --host 127.0.0.1 --area holding --start 10 --values 1.5 --dtype float32
pymod write --host 127.0.0.1 --area coil --start 0 --values true,false,true,1
```

Function code is selected automatically (FC06 for one uint16, FC16 for
multi or 32/64-bit, FC05 for one coil, FC15 for multi).

## Scan

```bash
pymod scan --host 127.0.0.1 --port 502
# alive
```

Reports the device as alive even if it replies with an exception
(because the wire round-trip succeeded — the device is reachable, it
just didn't like the address).

## Serve (development simulator)

```bash
pymod serve --host 0.0.0.0 --port 5020 \
            --holding 0=100,1=200,2=0xCAFE \
            --input 100=42 \
            --coil 0=true \
            --discrete 0=true
```

Or from a JSON config:

```bash
pymod serve --port 5020 --config sim.json
```

```json
{
  "holding":  {"0": 100, "1": 200},
  "input":    {"100": 42},
  "coil":     {"5": true},
  "discrete": {"0": true}
}
```

```{warning}
`pymod serve` is for development and testing only. Production
deployments should use `pymod.Server` directly with their own callbacks
into the host application's data store.
```

## Transport selection

Each subcommand accepts the same transport flags:

```bash
# TCP (default)
pymod read --host 127.0.0.1 --port 502 ...

# RTU over a serial port
pymod read --rtu /dev/ttyUSB0 --baudrate 9600 --parity N ...

# RTU over a TCP socket (Moxa-style gateway)
pymod read --host 10.0.0.5 --port 502 --rtu-over-tcp ...
```

## Interactive guide

If you don't remember the flags, run `pymod --guide` and answer the
prompts. It builds the equivalent one-shot command, prints it for you to
learn from, and runs it:

```
$ pymod --guide
What would you like to do?
  1) read
  2) write
  3) scan
> [1]: 1

Transport?
  1) tcp
  2) rtu
  3) rtu-over-tcp
> [1]: 1

Host [127.0.0.1]: 10.0.0.5
Port [502]:
...
Equivalent command:
  pymod read --host 10.0.0.5 --area holding --start 0 --count 10 --dtype int16

Execute? [Y/n]:
```

## Exit codes

| Code | Meaning                                  |
|------|------------------------------------------|
| 0    | success                                  |
| 1    | Modbus exception response (slave NAK)    |
| 2    | connection error or argument error       |
| 130  | interrupted (Ctrl-C)                     |

Non-zero exits are scriptable — your shell pipeline or systemd unit can
distinguish "device replied with an exception" from "couldn't reach it".
