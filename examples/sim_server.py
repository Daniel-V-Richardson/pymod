"""Modbus TCP simulator on port 502.

Spins up `pymod.Server` with pre-populated data across all four areas so
external tools (Modscan, QModMaster, ModbusPoll, etc.) have something
interesting to read and write against.

Data layout
-----------
HOLDING REGISTERS (FC03 / FC06 / FC16; read-write):
  0..9    : int16 ladder 100, 200, 300, ... 1000
  10..11  : float32 3.14159  (read with --dtype float32, count=2)
  12..13  : uint32  0x12345678
  20..29  : writeable scratchpad, starts at 0
  others  : not mapped → ILLEGAL_DATA_ADDRESS

INPUT REGISTERS (FC04; read-only by spec):
  0..4    : 25, 50, 75, 100, 999

COILS (FC01 / FC05 / FC15; read-write):
  0..9    : alternating True/False starting True

DISCRETE INPUTS (FC02; read-only by spec):
  0..9    : every 3rd True

Run:
    python sim_server.py

Stop with Ctrl-C.

Notes
-----
* Port 502 is privileged on Linux/macOS — run with sudo or use --port 5020.
* On Windows no special privilege is needed.
* Any unit ID is accepted (the server is configured with unit_id=None).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pymod
from pymod.codec import encode_registers


# ---- bind config ----------------------------------------------------------

HOST = "0.0.0.0"
PORT = 502
UNIT_ID = None  # None = accept any unit id


# ---- initial data ---------------------------------------------------------


def _build_state() -> dict[str, dict[int, object]]:
    holding: dict[int, int] = {}

    # 0..9 ladder
    for i in range(10):
        holding[i] = (i + 1) * 100

    # 10..11 float32 = 3.14159, big-endian word/byte order
    pi_regs = encode_registers([3.14159], "float32")
    holding[10], holding[11] = pi_regs[0], pi_regs[1]

    # 12..13 uint32 = 0x12345678
    u32_regs = encode_registers([0x12345678], "uint32")
    holding[12], holding[13] = u32_regs[0], u32_regs[1]

    # 20..29 scratchpad
    for i in range(20, 30):
        holding[i] = 0

    input_regs = {0: 25, 1: 50, 2: 75, 3: 100, 4: 999}

    coils = {i: bool(i % 2 == 0) for i in range(10)}
    discrete = {i: bool(i % 3 == 0) for i in range(10)}

    return {
        "holding": holding,
        "input": input_regs,
        "coil": coils,
        "discrete": discrete,
    }


# ---- callbacks ------------------------------------------------------------


def make_callbacks(state: dict[str, dict[int, object]]):
    def on_read(area: pymod.Area, address: int, count: int) -> Sequence[int | bool]:
        store_key = {
            pymod.Area.HOLDING_REGISTER: "holding",
            pymod.Area.INPUT_REGISTER: "input",
            pymod.Area.COIL: "coil",
            pymod.Area.DISCRETE_INPUT: "discrete",
        }[area]
        store = state[store_key]
        out: list[int | bool] = []
        for i in range(count):
            addr = address + i
            if addr not in store:
                raise pymod.IllegalDataAddress(
                    f"no value at {area.value}/{addr}"
                )
            out.append(store[addr])  # type: ignore[arg-type]
        return out

    def on_write(area: pymod.Area, address: int, values: Sequence[int | bool]) -> None:
        store_key = {
            pymod.Area.HOLDING_REGISTER: "holding",
            pymod.Area.COIL: "coil",
        }[area]
        store = state[store_key]
        for i, v in enumerate(values):
            store[address + i] = v
        print(f"  WRITE {area.value} addr={address} values={list(values)}")

    return on_read, on_write


# ---- entry point ----------------------------------------------------------


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    state = _build_state()
    on_read, on_write = make_callbacks(state)

    server = pymod.Server(
        host=HOST,
        port=PORT,
        on_read=on_read,
        on_write=on_write,
        unit_id=UNIT_ID,
    )
    async with server:
        bound_port = server.port
        print(f"pymod sim_server: listening on {HOST}:{bound_port}")
        print("  holding 0..9 (int16 ladder), 10..11 (float32 pi), "
              "12..13 (uint32 0x12345678), 20..29 (writeable scratchpad)")
        print("  input   0..4")
        print("  coils   0..9 (alternating)")
        print("  discrete 0..9 (every 3rd True)")
        print("Stop with Ctrl-C.")
        await asyncio.Future()  # run forever


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
