"""Interactive `--guide` wizard for the pymod CLI.

Asks the user a series of questions, builds an `argparse.Namespace`
matching the equivalent one-shot subcommand, prints the equivalent CLI
command (so users learn the flags), then dispatches to the existing
`_run_read` / `_run_write` / `_run_scan` handlers.

The wizard is opt-in via `pymod --guide` — the rest of the CLI remains
strictly one-shot.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Any

# Choices presented in numbered menus. Index-based selection.
_OPS: list[tuple[str, str]] = [
    ("read", "read registers or coils"),
    ("write", "write registers or coils"),
    ("scan", "probe device responsiveness"),
]
_TRANSPORTS: list[tuple[str, str]] = [
    ("tcp", "Modbus TCP"),
    ("rtu", "Modbus RTU over a serial port"),
    ("rtu-over-tcp", "RTU framing over TCP (Moxa-style gateway)"),
]
_READ_AREAS: list[tuple[str, str]] = [
    ("holding", "holding registers (FC03)"),
    ("input", "input registers (FC04)"),
    ("coil", "coils (FC01)"),
    ("discrete", "discrete inputs (FC02)"),
]
_WRITE_AREAS: list[tuple[str, str]] = [
    ("holding", "holding registers (FC06/16)"),
    ("coil", "coils (FC05/15)"),
]
_READ_DTYPES: list[tuple[str, str]] = [
    ("uint16", "unsigned 16-bit"),
    ("int16", "signed 16-bit"),
    ("uint32", "unsigned 32-bit (2 registers per value)"),
    ("int32", "signed 32-bit (2 registers per value)"),
    ("float32", "IEEE 754 single (2 registers per value)"),
    ("uint64", "unsigned 64-bit (4 registers per value)"),
    ("int64", "signed 64-bit (4 registers per value)"),
    ("float64", "IEEE 754 double (4 registers per value)"),
    ("bit", "extract a single bit from the register block"),
    ("bits", "extract multiple bits from the register block"),
]
_WRITE_DTYPES: list[tuple[str, str]] = [
    ("uint16", "unsigned 16-bit"),
    ("int16", "signed 16-bit"),
    ("uint32", "unsigned 32-bit (2 registers per value)"),
    ("int32", "signed 32-bit (2 registers per value)"),
    ("float32", "IEEE 754 single (2 registers per value)"),
    ("uint64", "unsigned 64-bit (4 registers per value)"),
    ("int64", "signed 64-bit (4 registers per value)"),
    ("float64", "IEEE 754 double (4 registers per value)"),
]


# ---------- prompt helpers -------------------------------------------------


def _print_lines(out: Callable[[str], None], *lines: str) -> None:
    for line in lines:
        out(line)


def _ask(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    prompt: str,
    *,
    default: str | None = None,
) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = inp(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        out("  (a value is required)")


def _ask_int(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    prompt: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
) -> int:
    default_s = str(default) if default is not None else None
    while True:
        raw = _ask(inp, out, prompt, default=default_s)
        try:
            value = int(raw, 0)
        except ValueError:
            out(f"  not a valid integer: {raw!r}")
            continue
        if minimum is not None and value < minimum:
            out(f"  must be >= {minimum}")
            continue
        return value


def _ask_float(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    prompt: str,
    *,
    default: float | None = None,
) -> float:
    default_s = str(default) if default is not None else None
    while True:
        raw = _ask(inp, out, prompt, default=default_s)
        try:
            return float(raw)
        except ValueError:
            out(f"  not a valid number: {raw!r}")


def _ask_yes_no(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    prompt: str,
    *,
    default: bool = False,
) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = inp(f"{prompt} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        out("  please answer y or n")


def _ask_choice(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    prompt: str,
    choices: Sequence[tuple[str, str]],
    *,
    default_index: int = 0,
) -> str:
    out(prompt)
    for i, (value, desc) in enumerate(choices, 1):
        marker = " (default)" if (i - 1) == default_index else ""
        out(f"  {i}) {value:<14} {desc}{marker}")
    while True:
        raw = inp(f"> [{default_index + 1}]: ").strip()
        if not raw:
            return choices[default_index][0]
        # Accept either the number or the value name itself.
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx][0]
        for value, _ in choices:
            if raw == value:
                return value
        out(f"  invalid selection: {raw!r}")


# ---------- transport sub-wizard -------------------------------------------


def _ask_transport(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    ns: argparse.Namespace,
) -> None:
    """Mutate `ns` in place with transport-related fields."""
    kind = _ask_choice(inp, out, "Transport?", _TRANSPORTS, default_index=0)
    ns.host = None
    ns.port = 502
    ns.rtu = None
    ns.baudrate = 9600
    ns.bytesize = 8
    ns.parity = "N"
    ns.stopbits = 1
    ns.rtu_over_tcp = False

    if kind == "tcp":
        ns.host = _ask(inp, out, "Host", default="127.0.0.1")
        ns.port = _ask_int(inp, out, "Port", default=502, minimum=1)
    elif kind == "rtu":
        ns.rtu = _ask(inp, out, "Serial port (e.g. COM3 or /dev/ttyUSB0)")
        ns.baudrate = _ask_int(inp, out, "Baudrate", default=9600, minimum=300)
        ns.parity = _ask_choice(
            inp, out, "Parity?",
            [("N", "none"), ("E", "even"), ("O", "odd")],
            default_index=0,
        )
    else:  # rtu-over-tcp
        ns.host = _ask(inp, out, "Host", default="127.0.0.1")
        ns.port = _ask_int(inp, out, "Port", default=502, minimum=1)
        ns.rtu_over_tcp = True


def _ask_call_args(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    ns: argparse.Namespace,
) -> None:
    ns.unit_id = _ask_int(inp, out, "Unit ID (slave ID)", default=1, minimum=0)
    ns.timeout_s = _ask_float(inp, out, "Timeout (seconds)", default=0.5)
    ns.retries = _ask_int(inp, out, "Retries on transport failure", default=1, minimum=0)


# ---------- per-op wizards -------------------------------------------------


def _ask_read(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    ns: argparse.Namespace,
) -> None:
    ns.area = _ask_choice(inp, out, "Area?", _READ_AREAS, default_index=0)
    ns.start = _ask_int(inp, out, "Start address (decimal or 0x.. hex)", minimum=0)
    if ns.area in ("holding", "input"):
        ns.count = _ask_int(inp, out, "Number of registers", minimum=1)
        ns.dtype = _ask_choice(inp, out, "Data type?", _READ_DTYPES, default_index=0)
        if ns.dtype in ("int32", "uint32", "float32", "int64", "uint64", "float64"):
            ns.word_order = _ask_choice(
                inp, out, "Word order?",
                [("big", "first register holds the high word (default ABCD)"),
                 ("little", "first register holds the low word (CDAB)")],
                default_index=0,
            )
            ns.byte_order = _ask_choice(
                inp, out, "Byte order?",
                [("big", "Modbus default — high byte first"),
                 ("little", "swap bytes within each register")],
                default_index=0,
            )
        else:
            ns.word_order = "big"
            ns.byte_order = "big"
        ns.bit_index = None
        ns.bit_indices = None
        ns.bit_numbering = "lsb_first"
        if ns.dtype == "bit":
            ns.bit_index = _ask_int(
                inp, out,
                f"Bit index (0..{ns.count * 16 - 1})",
                minimum=0,
            )
            ns.bit_numbering = _ask_choice(
                inp, out, "Bit numbering?",
                [("lsb_first", "bit 0 = LSB (default)"),
                 ("msb_first", "bit 0 = MSB")],
                default_index=0,
            )
        elif ns.dtype == "bits":
            indices_raw = _ask(
                inp, out, "Bit indices (comma-separated, e.g. 0,3,7,15)"
            )
            ns.bit_indices = indices_raw
            ns.bit_numbering = _ask_choice(
                inp, out, "Bit numbering?",
                [("lsb_first", "bit 0 = LSB (default)"),
                 ("msb_first", "bit 0 = MSB")],
                default_index=0,
            )
    else:
        ns.count = _ask_int(inp, out, "Number of coils/inputs", minimum=1)
        ns.dtype = "uint16"
        ns.word_order = "big"
        ns.byte_order = "big"
        ns.bit_index = None
        ns.bit_indices = None
        ns.bit_numbering = "lsb_first"
    ns.json = _ask_yes_no(inp, out, "JSON output?", default=False)


def _ask_write(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    ns: argparse.Namespace,
) -> None:
    ns.area = _ask_choice(inp, out, "Area?", _WRITE_AREAS, default_index=0)
    ns.start = _ask_int(inp, out, "Start address (decimal or 0x.. hex)", minimum=0)
    if ns.area == "coil":
        ns.values = _ask(
            inp, out,
            "Values (comma-separated booleans, e.g. true,false,1,0)"
        )
        ns.dtype = "uint16"  # ignored for coils
    else:
        ns.dtype = _ask_choice(inp, out, "Data type?", _WRITE_DTYPES, default_index=0)
        ns.values = _ask(
            inp, out,
            "Values (comma-separated; ints accept 0x.. hex)"
        )
        if ns.dtype in ("int32", "uint32", "float32", "int64", "uint64", "float64"):
            ns.word_order = _ask_choice(
                inp, out, "Word order?",
                [("big", "first register holds the high word (default)"),
                 ("little", "CDAB layout")],
                default_index=0,
            )
            ns.byte_order = _ask_choice(
                inp, out, "Byte order?",
                [("big", "Modbus default"),
                 ("little", "BADC swap")],
                default_index=0,
            )
        else:
            ns.word_order = "big"
            ns.byte_order = "big"
    if not hasattr(ns, "word_order"):
        ns.word_order = "big"
    if not hasattr(ns, "byte_order"):
        ns.byte_order = "big"
    ns.json = _ask_yes_no(inp, out, "JSON output?", default=False)


def _ask_scan(
    inp: Callable[[str], str],
    out: Callable[[str], None],
    ns: argparse.Namespace,
) -> None:
    ns.start = _ask_int(inp, out, "Address to probe", default=0, minimum=0)
    ns.json = _ask_yes_no(inp, out, "JSON output?", default=False)


# ---------- command-line preview ------------------------------------------


def _quote(s: str) -> str:
    """Quote a token for shell display if it contains spaces."""
    if any(c in s for c in (" ", "\t")):
        return f'"{s}"'
    return s


def _equivalent_command(op: str, ns: argparse.Namespace) -> str:
    parts = ["pymod", op]
    if ns.rtu:
        parts += ["--rtu", _quote(ns.rtu)]
        if ns.baudrate != 9600:
            parts += ["--baudrate", str(ns.baudrate)]
        if ns.parity != "N":
            parts += ["--parity", ns.parity]
    else:
        parts += ["--host", _quote(ns.host or "")]
        if ns.port != 502:
            parts += ["--port", str(ns.port)]
        if ns.rtu_over_tcp:
            parts.append("--rtu-over-tcp")
    if ns.unit_id != 1:
        parts += ["--unit-id", str(ns.unit_id)]
    if ns.timeout_s != 0.5:
        parts += ["--timeout", str(ns.timeout_s)]
    if ns.retries != 1:
        parts += ["--retries", str(ns.retries)]

    if op in ("read", "write"):
        parts += ["--area", ns.area, "--start", str(ns.start)]
    if op == "read":
        parts += ["--count", str(ns.count)]
        if ns.dtype != "uint16":
            parts += ["--dtype", ns.dtype]
        if ns.word_order != "big":
            parts += ["--word-order", ns.word_order]
        if ns.byte_order != "big":
            parts += ["--byte-order", ns.byte_order]
        if ns.bit_index is not None:
            parts += ["--bit-index", str(ns.bit_index)]
        if ns.bit_indices is not None:
            parts += ["--bit-indices", _quote(str(ns.bit_indices))]
        if ns.bit_numbering != "lsb_first":
            parts += ["--bit-numbering", ns.bit_numbering]
    elif op == "write":
        parts += ["--values", _quote(ns.values)]
        if ns.dtype != "uint16":
            parts += ["--dtype", ns.dtype]
        if ns.word_order != "big":
            parts += ["--word-order", ns.word_order]
        if ns.byte_order != "big":
            parts += ["--byte-order", ns.byte_order]
    elif op == "scan":
        if ns.start != 0:
            parts += ["--start", str(ns.start)]
    if ns.json:
        parts.append("--json")
    return " ".join(parts)


# ---------- entry point ----------------------------------------------------


def build_namespace(
    inp: Callable[[str], str] | None = None,
    out: Callable[[str], None] | None = None,
) -> tuple[str, argparse.Namespace] | None:
    """Run the wizard and return ``(op, namespace)``.

    Returns ``None`` if the user declines to execute at the confirmation
    step. Raises ``KeyboardInterrupt`` / ``EOFError`` if the user aborts
    mid-wizard; the CLI's `main()` catches those and exits cleanly.

    `inp` and `out` are late-bound (default to builtin ``input`` /
    ``print`` only when the function actually runs) so tests that
    monkey-patch ``builtins.input`` work as expected.
    """
    if inp is None:
        inp = input
    if out is None:
        out = print

    out("pymod guide — interactive Modbus operation")
    out("(Ctrl-C to abort)")
    out("")

    op = _ask_choice(inp, out, "What would you like to do?", _OPS, default_index=0)
    out("")
    ns = argparse.Namespace(subcommand=op, verbose=False)
    _ask_transport(inp, out, ns)
    out("")
    _ask_call_args(inp, out, ns)
    out("")
    if op == "read":
        _ask_read(inp, out, ns)
    elif op == "write":
        _ask_write(inp, out, ns)
    elif op == "scan":
        _ask_scan(inp, out, ns)

    out("")
    out("Equivalent command:")
    out(f"  {_equivalent_command(op, ns)}")
    out("")
    if not _ask_yes_no(inp, out, "Execute?", default=True):
        out("aborted.")
        return None
    return op, ns
