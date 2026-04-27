"""`pymod` one-shot CLI.

Built on top of the public `AsyncClient` and `Server` APIs — no duplicate
logic.

Subcommands:

* ``read``  — read registers/coils, decode per dtype, print values.
* ``write`` — write registers/coils, return success or error.
* ``scan``  — probe a device by issuing a single FC03 read; reports liveness
              even when the device replies with an exception.
* ``serve`` — run a Modbus TCP simulator backed by an in-memory map. Intended
              for development and testing, not production. (Production
              servers should use ``pymod.Server`` with their own callbacks.)

Transport selection (read/write/scan):

* TCP (default):     ``--host HOST --port PORT``
* RTU-over-TCP:      ``--host HOST --port PORT --rtu-over-tcp``
* Serial RTU:        ``--rtu PATH --baudrate RATE``

Argparse is used (no `click` dependency) — keeps the dependency surface
minimal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from typing import Any

from . import (
    Area,
    AsyncClient,
    Coil,
    Discrete,
    Holding,
    Input,
    ReadItem,
    ReadResult,
    RetryPolicy,
    Server,
    WriteCoils,
    WriteHolding,
    WriteItem,
    WriteResult,
)
from .errors import ModbusConnectionError, ModbusError, ModbusExceptionResponse


# ---------- entry point ----------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
            stream=sys.stderr,
        )

    if args.guide:
        return _dispatch_guide()

    if args.subcommand is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


def _dispatch_guide() -> int:
    """Run the interactive wizard, then dispatch to the chosen subcommand."""
    from ._guide import build_namespace

    try:
        result = build_namespace()
    except (KeyboardInterrupt, EOFError):
        print("\naborted.", file=sys.stderr)
        return 130
    if result is None:
        return 0
    op, ns = result
    try:
        return asyncio.run(_run(ns))
    except KeyboardInterrupt:
        return 130


# ---------- argument parsing -----------------------------------------------


_AREA_CHOICES_READ = ("holding", "input", "coil", "discrete")
_AREA_CHOICES_WRITE = ("holding", "coil")
_DTYPE_CHOICES_READ = (
    "int16", "uint16",
    "int32", "uint32", "float32",
    "int64", "uint64", "float64",
    "bit", "bits",
)
_DTYPE_CHOICES_WRITE = (
    "int16", "uint16",
    "int32", "uint32", "float32",
    "int64", "uint64", "float64",
)


_TOP_EPILOG = """\
examples:
  pymod read  --host 127.0.0.1 --port 502 --area holding --start 0 --count 10 --dtype int16
  pymod write --host 127.0.0.1 --port 502 --area holding --start 0 --values 1,2,3 --dtype uint16
  pymod scan  --host 127.0.0.1 --port 502
  pymod serve --host 0.0.0.0   --port 5020 --holding 0=100,1=200

run "pymod COMMAND --help" for details on a specific command.
"""

_READ_DESCRIPTION = """\
Read registers or coils from a Modbus device, decode the response per the
chosen dtype, and print the values.

Counting:
  --count is in *registers* for holding/input, *coils* for coil/discrete.
  For 32-bit types (int32, uint32, float32), one value uses 2 registers.
  So --count 10 --dtype float32 returns 5 floats.

Function code dispatch:
  --area holding   -> FC03 read holding registers
  --area input     -> FC04 read input registers
  --area coil      -> FC01 read coils
  --area discrete  -> FC02 read discrete inputs

Word/byte order (32-bit and bit-extraction reads):
  Defaults are word=big, byte=big (the "ABCD" PLC layout). Common
  alternatives: --word-order little (CDAB), --byte-order little (BADC),
  both little (DCBA).

Bit reads from registers:
  --dtype bit  --bit-index N           returns one bool
  --dtype bits --bit-indices 0,3,7,15  returns a list of bools
  --bit-numbering controls bit 0 = LSB (default) or MSB.
"""

_READ_EPILOG = """\
examples:
  Read 10 holding registers as signed int16:
    pymod read --host 127.0.0.1 --port 5020 --unit-id 1 \\
               --area holding --start 0 --count 10 --dtype int16

  Read 5 floats (10 registers):
    pymod read --host 192.168.1.50 --area holding --start 0 --count 10 \\
               --dtype float32 --word-order big --byte-order big

  Read 16 coils:
    pymod read --host 192.168.1.50 --area coil --start 0 --count 16

  Read bit 8 from a 2-register block:
    pymod read --host 192.168.1.50 --area holding --start 0 --count 2 \\
               --dtype bit --bit-index 8

  RTU over a serial port:
    pymod read --rtu /dev/ttyUSB0 --baudrate 9600 --unit-id 1 \\
               --area holding --start 0 --count 10 --dtype uint16

  Machine-readable output:
    pymod read --host 127.0.0.1 --area holding --start 0 --count 4 --json
"""

_WRITE_DESCRIPTION = """\
Write to holding registers or coils.

Function code is selected automatically by value count and dtype:
  --area holding, single uint16 value          -> FC06 (write single register)
  --area holding, multiple values or 32-bit    -> FC16 (write multiple registers)
  --area coil, single value                    -> FC05 (write single coil)
  --area coil, multiple values                 -> FC15 (write multiple coils)

Value parsing:
  --values is a comma-separated list. Integers may use 0x.. hex.
  Floats accept decimal and scientific notation.
  Bools accept true/false, 1/0, on/off, yes/no (case-insensitive).
"""

_WRITE_EPILOG = """\
examples:
  Write 0xCAFE to holding register 5 (FC06):
    pymod write --host 127.0.0.1 --area holding --start 5 \\
                --values 0xCAFE --dtype uint16

  Write three holding registers (FC16):
    pymod write --host 127.0.0.1 --area holding --start 0 \\
                --values 100,200,300 --dtype uint16

  Write a float32 to address 10 (FC16, two registers):
    pymod write --host 127.0.0.1 --area holding --start 10 \\
                --values 3.14159 --dtype float32

  Write a single coil (FC05):
    pymod write --host 127.0.0.1 --area coil --start 0 --values true

  Write four coils (FC15):
    pymod write --host 127.0.0.1 --area coil --start 0 \\
                --values true,false,true,1
"""

_SCAN_EPILOG = """\
examples:
  pymod scan --host 127.0.0.1 --port 502
  pymod scan --host 127.0.0.1 --port 502 --start 0x100

Reports "alive" for any wire-level response, including exception responses
(which still prove the device is reachable and following Modbus framing).
"""

_SERVE_EPILOG = """\
examples:
  Inline data:
    pymod serve --host 0.0.0.0 --port 5020 \\
                --holding 0=100,1=200,2=300 --coil 0=true

  From a JSON config:
    pymod serve --host 0.0.0.0 --port 5020 --config sim.json

  Read-only (rejects all writes with ILLEGAL_FUNCTION):
    pymod serve --host 0.0.0.0 --port 5020 --holding 0=42 --readonly

config file format (sim.json):
  {
    "holding":  {"0": 100, "1": 200},
    "input":    {"100": 42},
    "coil":     {"5": true},
    "discrete": {"0": true}
  }
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pymod",
        description="Production-grade Modbus driver — one-shot CLI.",
        epilog=_TOP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="enable debug logging on stderr"
    )
    parser.add_argument(
        "--guide", action="store_true",
        help="interactive wizard mode — prompts for each option and runs the result"
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="COMMAND")

    # ---- read ----
    p_read = sub.add_parser(
        "read",
        help="read registers or coils",
        description=_READ_DESCRIPTION,
        epilog=_READ_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_transport_args(p_read)
    _add_call_args(p_read)
    p_read.add_argument("--area", required=True, choices=_AREA_CHOICES_READ,
                        help="address space / function code group")
    p_read.add_argument("--start", type=_int_auto, required=True,
                        help="start address (decimal or 0x.. hex)")
    p_read.add_argument("--count", type=int, required=True,
                        help="number of registers or coils to read")
    p_read.add_argument("--dtype", default="uint16", choices=_DTYPE_CHOICES_READ,
                        help="register decoding (default: uint16). "
                             "32-bit types span 2 registers per value.")
    p_read.add_argument("--word-order", default="big", choices=("big", "little"),
                        help="for 32-bit types: which register holds the high word "
                             "(default: big = first register is high word)")
    p_read.add_argument("--byte-order", default="big", choices=("big", "little"),
                        help="byte order within each register "
                             "(default: big = Modbus spec wire order)")
    p_read.add_argument(
        "--bit-index", type=int, default=None,
        help="required with --dtype bit: which bit to extract (0..count*16-1)"
    )
    p_read.add_argument(
        "--bit-indices", default=None,
        help="required with --dtype bits: comma-separated bit indices to extract"
    )
    p_read.add_argument(
        "--bit-numbering", default="lsb_first", choices=("lsb_first", "msb_first"),
        help="bit 0 placement (default: lsb_first = (value >> bit) & 1)"
    )
    p_read.add_argument("--json", action="store_true",
                        help="output structured JSON instead of human-readable")

    # ---- write ----
    p_write = sub.add_parser(
        "write",
        help="write registers or coils",
        description=_WRITE_DESCRIPTION,
        epilog=_WRITE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_transport_args(p_write)
    _add_call_args(p_write)
    p_write.add_argument("--area", required=True, choices=_AREA_CHOICES_WRITE,
                         help="holding (FC06/16) or coil (FC05/15)")
    p_write.add_argument("--start", type=_int_auto, required=True,
                         help="start address (decimal or 0x.. hex)")
    p_write.add_argument(
        "--values", required=True,
        help="comma-separated values. Ints accept 0x.. hex. "
             "Bools accept true/false/on/off/yes/no/1/0."
    )
    p_write.add_argument("--dtype", default="uint16", choices=_DTYPE_CHOICES_WRITE,
                         help="value type (default: uint16). Ignored for --area coil.")
    p_write.add_argument("--word-order", default="big", choices=("big", "little"))
    p_write.add_argument("--byte-order", default="big", choices=("big", "little"))
    p_write.add_argument("--json", action="store_true")

    # ---- scan ----
    p_scan = sub.add_parser(
        "scan",
        help="probe device responsiveness",
        description=(
            "Probe a device by issuing one FC03 read at the given address. "
            "Reports the device as responsive even if it replies with an "
            "exception (because the wire round-trip succeeded)."
        ),
        epilog=_SCAN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_transport_args(p_scan)
    _add_call_args(p_scan)
    p_scan.add_argument("--start", type=_int_auto, default=0,
                        help="address to probe (default: 0)")
    p_scan.add_argument("--json", action="store_true")

    # ---- serve ----
    p_serve = sub.add_parser(
        "serve",
        help="run a Modbus TCP simulator (development only)",
        description=(
            "Run a Modbus TCP server backed by an in-memory map for "
            "development / testing. NOT for production — production "
            "deployments should use pymod.Server with their own callbacks."
        ),
        epilog=_SERVE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_serve.add_argument("--host", default="0.0.0.0", help="bind address")
    p_serve.add_argument("--port", type=int, default=502)
    p_serve.add_argument("--unit-id", type=int, default=None,
                         help="if set, only this unit id is accepted")
    p_serve.add_argument("--max-connections", type=int, default=32)
    p_serve.add_argument("--readonly", action="store_true",
                         help="reject all write FCs with ILLEGAL_FUNCTION")
    p_serve.add_argument("--config", help="JSON config file with initial register data")
    p_serve.add_argument(
        "--holding", default="",
        help="comma-separated addr=value (e.g. 0=0x1234,1=42)"
    )
    p_serve.add_argument(
        "--input", default="",
        help="comma-separated addr=value for input registers"
    )
    p_serve.add_argument(
        "--coil", default="",
        help="comma-separated addr=bool (e.g. 5=true,6=false)"
    )
    p_serve.add_argument(
        "--discrete", default="",
        help="comma-separated addr=bool for discrete inputs"
    )

    return parser


def _add_transport_args(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("transport")
    g.add_argument("--host", help="TCP host (or RTU-over-TCP host)")
    g.add_argument("--port", type=int, default=502, help="TCP port (default 502)")
    g.add_argument("--rtu", metavar="PATH",
                   help="serial port path (e.g. /dev/ttyUSB0 or COM3)")
    g.add_argument("--baudrate", type=int, default=9600)
    g.add_argument("--bytesize", type=int, default=8)
    g.add_argument("--parity", default="N", choices=("N", "E", "O"))
    g.add_argument("--stopbits", type=float, default=1)
    g.add_argument("--rtu-over-tcp", action="store_true",
                   help="use RTU framing over a TCP socket (Moxa-style gateway)")


def _add_call_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=0.5, dest="timeout_s")
    parser.add_argument("--retries", type=int, default=1, help="retry attempts on transport failure")


def _int_auto(s: str) -> int:
    """argparse type for ints accepting 0x.. hex prefix."""
    return int(s, 0)


# ---------- subcommand dispatch --------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    if args.subcommand == "read":
        return await _run_read(args)
    if args.subcommand == "write":
        return await _run_write(args)
    if args.subcommand == "scan":
        return await _run_scan(args)
    if args.subcommand == "serve":
        return await _run_serve(args)
    return 2


def _make_client(args: argparse.Namespace) -> AsyncClient:
    retry = RetryPolicy(max_attempts=max(1, args.retries + 1))
    if args.rtu:
        return AsyncClient.rtu(
            args.rtu,
            args.baudrate,
            bytesize=args.bytesize,
            parity=args.parity,
            stopbits=args.stopbits,
            unit_id=args.unit_id,
            timeout_s=args.timeout_s,
            retry=retry,
        )
    if not args.host:
        raise SystemExit("either --host or --rtu must be specified")
    if args.rtu_over_tcp:
        return AsyncClient.rtu_over_tcp(
            args.host,
            args.port,
            unit_id=args.unit_id,
            timeout_s=args.timeout_s,
            retry=retry,
        )
    return AsyncClient.tcp(
        args.host,
        args.port,
        unit_id=args.unit_id,
        timeout_s=args.timeout_s,
        retry=retry,
    )


# ---------- read -----------------------------------------------------------


async def _run_read(args: argparse.Namespace) -> int:
    item = _build_read_item(args)
    client = _make_client(args)
    try:
        async with client:
            results = await client.read([item])
    except ModbusConnectionError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 2)
    except ModbusError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 1)
    return _emit_read_result(results[0], args.json)


def _build_read_item(args: argparse.Namespace) -> ReadItem:
    if args.area == "coil":
        return Coil(start=args.start, count=args.count)
    if args.area == "discrete":
        return Discrete(start=args.start, count=args.count)

    cls: type[Holding] | type[Input] = Holding if args.area == "holding" else Input
    bit_index: int | None = None
    bit_indices: list[int] | None = None
    if args.dtype == "bit":
        if args.bit_index is None:
            raise SystemExit("--bit-index required when --dtype=bit")
        bit_index = args.bit_index
    elif args.dtype == "bits":
        if not args.bit_indices:
            raise SystemExit("--bit-indices required when --dtype=bits")
        try:
            bit_indices = [int(s.strip(), 0) for s in args.bit_indices.split(",")]
        except ValueError as e:
            raise SystemExit(f"--bit-indices: {e}") from e

    return cls(
        start=args.start,
        count=args.count,
        dtype=args.dtype,
        word_order=args.word_order,
        byte_order=args.byte_order,
        bit_index=bit_index,
        bit_indices=bit_indices,
        bit_numbering=args.bit_numbering,
    )


def _emit_read_result(result: ReadResult, json_mode: bool) -> int:
    if json_mode:
        if result.ok:
            print(json.dumps({"ok": True, "values": _serialize(result.values)}))
            return 0
        print(json.dumps({
            "ok": False,
            "error": _error_payload(result.error),
        }))
        return 1
    if not result.ok:
        err = result.error
        print(
            f"error: {type(err).__name__}: {err}" if err else "error: unknown",
            file=sys.stderr,
        )
        return 1
    dtype = getattr(result.item, "dtype", None)
    for i, v in enumerate(result.values):
        print(_format_value(i, v, dtype))
    return 0


def _format_value(index: int, value: Any, dtype: str | None) -> str:
    if isinstance(value, bool):
        return f"[{index}]: {value}"
    if isinstance(value, int):
        # Mask hex display to the dtype width so two's complement renders
        # correctly: int16 -1 → 0xFFFF, int32 -1 → 0xFFFFFFFF, int64 -1 →
        # 0xFFFFFFFFFFFFFFFF.
        if dtype in ("int64", "uint64"):
            mask = 0xFFFF_FFFF_FFFF_FFFF
            width = 16
        elif dtype in ("int32", "uint32"):
            mask = 0xFFFFFFFF
            width = 8
        else:
            mask = 0xFFFF
            width = 4
        return f"[{index}]: 0x{value & mask:0{width}X} ({value})"
    return f"[{index}]: {value}"


def _serialize(values: Sequence[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, bool):
            out.append(bool(v))
        elif isinstance(v, int):
            out.append(int(v))
        else:
            out.append(float(v))
    return out


# ---------- write ----------------------------------------------------------


async def _run_write(args: argparse.Namespace) -> int:
    item = _build_write_item(args)
    client = _make_client(args)
    try:
        async with client:
            results = await client.write([item])
    except ModbusConnectionError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 2)
    except ModbusError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 1)
    return _emit_write_result(results[0], args.json)


def _build_write_item(args: argparse.Namespace) -> WriteItem:
    raw_values = [s.strip() for s in args.values.split(",") if s.strip()]
    if not raw_values:
        raise SystemExit("--values must be a non-empty comma-separated list")
    if args.area == "coil":
        bools = [_parse_bool(s) for s in raw_values]
        return WriteCoils(start=args.start, values=bools)
    # holding
    if args.dtype == "float32":
        try:
            float_values = [float(s) for s in raw_values]
        except ValueError as e:
            raise SystemExit(f"--values: {e}") from e
        return WriteHolding(
            start=args.start,
            values=float_values,
            dtype=args.dtype,
            word_order=args.word_order,
            byte_order=args.byte_order,
        )
    try:
        int_values = [int(s, 0) for s in raw_values]
    except ValueError as e:
        raise SystemExit(f"--values: {e}") from e
    return WriteHolding(
        start=args.start,
        values=int_values,
        dtype=args.dtype,
        word_order=args.word_order,
        byte_order=args.byte_order,
    )


def _parse_bool(s: str) -> bool:
    s = s.lower()
    if s in ("true", "1", "on", "yes"):
        return True
    if s in ("false", "0", "off", "no"):
        return False
    raise SystemExit(f"--values: invalid boolean {s!r}")


def _emit_write_result(result: WriteResult, json_mode: bool) -> int:
    if json_mode:
        payload: dict[str, Any] = {"ok": result.ok}
        if not result.ok:
            payload["error"] = _error_payload(result.error)
        print(json.dumps(payload))
        return 0 if result.ok else 1
    if result.ok:
        print("ok")
        return 0
    err = result.error
    print(
        f"error: {type(err).__name__}: {err}" if err else "error: unknown",
        file=sys.stderr,
    )
    return 1


# ---------- scan -----------------------------------------------------------


async def _run_scan(args: argparse.Namespace) -> int:
    from .protocol.pdu import (
        FC_READ_HOLDING_REGISTERS,
        detect_exception,
        encode_read_holding_registers,
    )

    client = _make_client(args)
    try:
        async with client:
            response_pdu = await client.execute(
                encode_read_holding_registers(args.start, 1)
            )
    except ModbusConnectionError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 2)
    except ModbusError as e:
        return _emit_failure(args.json, type(e).__name__, str(e), 1)

    # `execute()` returns raw bytes; check for an exception response inline.
    exc = detect_exception(response_pdu, FC_READ_HOLDING_REGISTERS)
    if exc is not None:
        if args.json:
            print(json.dumps({
                "ok": True,
                "responsive": True,
                "exception": type(exc).__name__,
            }))
        else:
            print(f"alive (replied with {type(exc).__name__})")
        return 0

    if args.json:
        print(json.dumps({"ok": True, "responsive": True}))
    else:
        print("alive")
    return 0


# ---------- serve ----------------------------------------------------------


class _SimState:
    """In-memory backing store for `pymod serve`.

    Holds four address→value dicts keyed by `Area`. Reads of unmapped
    addresses raise `IllegalDataAddress`; writes silently auto-extend.
    """

    def __init__(self) -> None:
        self.holding: dict[int, int] = {}
        self.input: dict[int, int] = {}
        self.coil: dict[int, bool] = {}
        self.discrete: dict[int, bool] = {}

    def _store(self, area: Area) -> dict[int, Any]:
        if area is Area.HOLDING_REGISTER:
            return self.holding
        if area is Area.INPUT_REGISTER:
            return self.input
        if area is Area.COIL:
            return self.coil
        return self.discrete

    def read(
        self,
        area: Area,
        address: int,
        count: int,
    ) -> list[int | bool]:
        from .errors import IllegalDataAddress

        store = self._store(area)
        out: list[int | bool] = []
        for i in range(count):
            addr = address + i
            if addr not in store:
                raise IllegalDataAddress(f"no value mapped at {area.value}/{addr}")
            out.append(store[addr])
        return out

    def write(
        self,
        area: Area,
        address: int,
        values: Sequence[int | bool],
    ) -> None:
        store = self._store(area)
        for i, v in enumerate(values):
            store[address + i] = v


def _parse_int_map(spec: str) -> dict[int, int]:
    """Parse 'addr=val,addr=val' into a dict of ints. addr/val accept hex."""
    if not spec.strip():
        return {}
    out: dict[int, int] = {}
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise SystemExit(f"invalid spec {piece!r}: expected ADDR=VALUE")
        addr_s, val_s = piece.split("=", 1)
        out[int(addr_s.strip(), 0)] = int(val_s.strip(), 0)
    return out


def _parse_bool_map(spec: str) -> dict[int, bool]:
    """Parse 'addr=bool,addr=bool' into a dict of bools."""
    if not spec.strip():
        return {}
    out: dict[int, bool] = {}
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise SystemExit(f"invalid spec {piece!r}: expected ADDR=BOOL")
        addr_s, val_s = piece.split("=", 1)
        out[int(addr_s.strip(), 0)] = _parse_bool(val_s.strip())
    return out


def _load_config_into_state(state: _SimState, path: str) -> None:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a JSON object, got {type(data).__name__}")
    for area_key, store in (
        ("holding", state.holding),
        ("input", state.input),
    ):
        section = data.get(area_key, {})
        if not isinstance(section, dict):
            raise SystemExit(f"config['{area_key}'] must be an object")
        for k, v in section.items():
            if not isinstance(v, int):
                raise SystemExit(f"config['{area_key}'][{k!r}] must be int")
            store[int(k, 0) if isinstance(k, str) else k] = v
    for area_key, bool_store in (
        ("coil", state.coil),
        ("discrete", state.discrete),
    ):
        section = data.get(area_key, {})
        if not isinstance(section, dict):
            raise SystemExit(f"config['{area_key}'] must be an object")
        for k, v in section.items():
            if not isinstance(v, bool):
                raise SystemExit(f"config['{area_key}'][{k!r}] must be bool")
            bool_store[int(k, 0) if isinstance(k, str) else k] = v


def _build_sim_state(args: argparse.Namespace) -> _SimState:
    state = _SimState()
    if args.config:
        _load_config_into_state(state, args.config)
    state.holding.update(_parse_int_map(args.holding))
    state.input.update(_parse_int_map(args.input))
    state.coil.update(_parse_bool_map(args.coil))
    state.discrete.update(_parse_bool_map(args.discrete))
    return state


async def _run_serve(args: argparse.Namespace) -> int:
    state = _build_sim_state(args)

    def on_read(area: Area, address: int, count: int) -> Sequence[int | bool]:
        return state.read(area, address, count)

    on_write = None if args.readonly else state.write

    server = Server(
        host=args.host,
        port=args.port,
        on_read=on_read,
        on_write=on_write,
        unit_id=args.unit_id,
        max_connections=args.max_connections,
    )
    async with server:
        bound_port = server.port
        readonly = " (read-only)" if args.readonly else ""
        print(
            f"pymod serve: listening on {args.host}:{bound_port}{readonly}",
            file=sys.stderr,
        )
        print(
            f"  holding={len(state.holding)} input={len(state.input)} "
            f"coil={len(state.coil)} discrete={len(state.discrete)}",
            file=sys.stderr,
        )
        # Block forever; Ctrl-C is caught by main().
        await asyncio.Future()
    return 0


# ---------- shared helpers -------------------------------------------------


def _error_payload(error: ModbusError | None) -> dict[str, str]:
    if error is None:
        return {"type": "Unknown", "message": ""}
    return {"type": type(error).__name__, "message": str(error)}


def _emit_failure(
    json_mode: bool,
    err_type: str,
    message: str,
    exit_code: int,
) -> int:
    if json_mode:
        print(json.dumps({
            "ok": False,
            "error": {"type": err_type, "message": message},
        }))
    else:
        print(f"error: {err_type}: {message}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
