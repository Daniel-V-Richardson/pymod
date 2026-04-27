"""Poll a Modbus TCP device every second and print the results.

Reads holding registers, input registers, coils, and discrete inputs
in ONE heterogeneous batch. Each area uses a different function code,
so the planner issues four wire requests — pipelined concurrently on
the single TCP connection.

Run:
    python poll_loop_tcp.py

Stop with Ctrl-C.
"""

from __future__ import annotations

import time
from datetime import datetime

import pymod

# ---- target ---------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 5020
UNIT_ID = 1
TIMEOUT_S = 0.5
INTERVAL_S = 1.0
RETRY_POLICY = pymod.RetryPolicy(max_attempts=1)  # 1 attempt total = no retries

# ---- what to read ---------------------------------------------------------

HOLDING_REGS = 10
INPUT_REGS = 5
COILS = 5
DISCRETE_INPUTS = 5

ITEMS = [
    pymod.Holding(start=0, count=HOLDING_REGS, dtype="int16"),
    pymod.Input(start=0, count=INPUT_REGS, dtype="int16"),
    pymod.Coil(start=0, count=COILS),
    pymod.Discrete(start=0, count=DISCRETE_INPUTS),
]


def _format_result(label: str, result: pymod.ReadResult) -> str:
    if result.ok:
        return f"  {label:18} {list(result.values)}"
    err = result.error
    return f"  {label:18} ERROR: {type(err).__name__}: {err}"


def main() -> None:
    client = pymod.Client.tcp(
        HOST,
        PORT,
        unit_id=UNIT_ID,
        timeout_s=TIMEOUT_S,
        retry=RETRY_POLICY,
    )
    with client:
        print(
            f"polling {HOST}:{PORT} (unit {UNIT_ID}) every {INTERVAL_S}s "
            f"— Ctrl-C to stop"
        )
        try:
            while True:
                results = client.read(ITEMS)
                holding, inputs, coils, discrete = results

                print(f"\n== {datetime.now().strftime('%H:%M:%S')} ==")
                print(_format_result(f"holding[0..{HOLDING_REGS - 1}] int16:", holding))
                print(_format_result(f"input[0..{INPUT_REGS - 1}] int16:", inputs))
                print(_format_result(f"coils[0..{COILS - 1}]:", coils))
                print(_format_result(f"discrete[0..{DISCRETE_INPUTS - 1}]:", discrete))

                time.sleep(INTERVAL_S)
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
