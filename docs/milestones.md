# pymod — Milestone plan

Each milestone is one delivery turn. Acceptance criteria must pass before moving on. M0 is done; the rest are sequenced.

---

## M0 — Skeleton (done)
**Deliverable:** Folder layout, public API stubs, dependency declaration, milestone plan.
**Acceptance:**
- [x] `pyproject.toml` declares all deps with justification.
- [x] Package skeleton importable: `python -c "import pymod"` succeeds (after `pip install -e .`).
- [x] All public types (`Client`, `AsyncClient`, `Server`, item types, errors, `RetryPolicy`) re-exported from top-level.
- [x] Decision records mirrored in `docs/architecture/`.

---

## M1 — Protocol layer (pure, no I/O)
**Scope:** `protocol/pdu.py`, `protocol/adu_tcp.py`, `protocol/adu_rtu.py`. Function codes 1, 2, 3, 4, 5, 6, 15, 16. Exception-response decoding. CRC16. MBAP framing. Custom-FC registration hook.

**Acceptance:**
- [ ] Round-trip encode/decode for each FC: golden frames from the Modbus spec match byte-for-byte.
- [ ] Exception responses (FC | 0x80) decode to the correct `ModbusExceptionResponse` subclass.
- [ ] CRC16 matches reference vectors (Modicon table) for >= 16 inputs including zero-length and 250+ byte frames.
- [ ] MBAP encode/decode preserves transaction id, protocol id, length, unit id; rejects malformed length.
- [ ] RTU frame parser detects truncation and bad CRC, raises `ModbusCRCError` / `ModbusProtocolError`.
- [ ] Custom-FC registration: a third-party FC (e.g., 0x64) can be added without editing core files.
- [ ] `pytest tests/unit/protocol -q` passes; coverage on `protocol/` >= 95%.
- [ ] `mypy --strict src/pymod/protocol` clean.

---

## M2 — Transport layer
**Scope:** `transport/tcp.py`, `transport/serial.py`, `transport/rtu_over_tcp.py`. Auto-reconnect. TID demultiplexer for TCP pipelining. Per-port serial lock. Inter-frame gap on RTU. Loopback test harness.

**Acceptance:**
- [ ] In-process loopback: a `tcp.Transport` paired with an in-process MBAP echo server completes 1000 randomized round-trips with 100 concurrent calls and no mismatches.
- [ ] Pipelining demuxer survives out-of-order responses and a deliberately delayed reply mid-stream.
- [ ] `pipeline=False` serializes calls strictly, observable via TID sequence.
- [ ] Auto-reconnect: when the loopback server is killed and restarted, the transport recovers within 1 s and the next call succeeds.
- [ ] Connect timeout and request timeout both honored; `ModbusTimeoutError` / `ModbusConnectionError` raised distinctly.
- [ ] Serial transport tested via `pyserial`'s `loop://` URL: 100 sequential round-trips with simulated inter-character delays succeed; 0 collisions when 10 tasks share a client.
- [ ] RTU-over-TCP reuses the RTU codec verbatim (no duplicated CRC logic).
- [ ] `mypy --strict src/pymod/transport` clean.

---

## M3 — Client API and retry
**Scope:** `client.AsyncClient`, `client.Client` (sync facade), `retry.RetryPolicy`. Wires planner + transport + retry. Connection lifecycle. Per-call overrides.

**Acceptance:**
- [ ] `AsyncClient.tcp(...)`, `.rtu(...)`, `.rtu_over_tcp(...)` all instantiate and connect against the in-process server.
- [ ] Sync `Client` mirrors async behavior end-to-end (same tests, sync API).
- [ ] Default 500 ms timeout applied; per-call override works.
- [ ] `RetryPolicy(max_attempts=3, retry_on=(ModbusTimeoutError,))` retries the right errors and not exception responses.
- [ ] Backoff delays observable in tests via fake-time.
- [ ] Concurrent reads from a shared `AsyncClient` do not interleave or corrupt; verified by stress test (200 concurrent reads, all results match expected).
- [ ] `mypy --strict src/pymod/client.py src/pymod/retry.py` clean.

---

## M4 — Data mapping & planner
**Scope:** `codec/values.py`, `codec/bits.py`, `planner.py`. Coalescing. Heterogeneous batch reads. Bit reads with word/byte/bit order. Typed writes (FC05/06 vs 15/16 selection).

**Acceptance:**
- [ ] `decode_int16/uint16/int32/uint32/float32` round-trip against IEEE-754 reference values for every (word_order, byte_order) combination — covers ABCD / CDAB / BADC / DCBA.
- [ ] Bit extraction: golden test cases for `lsb_first` and `msb_first`, single bit and bit-list, with all four word/byte combinations.
- [ ] Planner coalesces `Holding(0..4)` + `Holding(5..14)` into one PDU; `Holding(0..4)` + `Holding(20..29)` into two; per-FC PDU limit splits a 200-register read into two reads.
- [ ] Per-item failure isolation: when one PDU returns `IllegalDataAddress`, only items derived from that PDU report `ok=False`; the rest succeed.
- [ ] Write planner: 1 value → FC06; >1 value → FC16; 1 coil → FC05; >1 coil → FC15.
- [ ] End-to-end batch read of mixed Holding/Input/Coil/Discrete items returns correct typed values from a fake slave.
- [ ] `mypy --strict` clean across the new modules.

---

## M5 — Server
**Scope:** `server.Server`. Callback-based. Concurrent client connections. Authorization-by-area. Exception-response mapping for callback exceptions.

**Acceptance:**
- [ ] Server starts on an ephemeral port; client reads and writes round-trip through user callbacks.
- [ ] Read of an INPUT_REGISTER via FC06 (write to register) returns `ILLEGAL_FUNCTION` to the master.
- [ ] When `on_write` is `None`, all write FCs return `ILLEGAL_FUNCTION`.
- [ ] Callback raising `IllegalDataAddress` produces the matching exception response on the wire.
- [ ] Callback raising any other exception produces `SLAVE_DEVICE_FAILURE` and is logged at ERROR.
- [ ] Server handles 32 concurrent client connections, 1000 requests each, with no leaks (`active_connections == 0` after teardown).
- [ ] Unit-id discrimination works when configured; off by default.

---

## M6 — CLI
**Scope:** `cli.py`. `pymod read`, `pymod write`, `pymod scan`. Subcommands implemented on top of `Client`.

**Acceptance:**
- [ ] `pymod read --host ... --port ... --area holding --start 0 --count 10 --dtype float32 --word-order big --byte-order big` prints the typed values to stdout; exit 0 on success, non-zero on error.
- [ ] Exception responses surface a clear human message and a non-zero exit code.
- [ ] `pymod write --host ... --area holding --start 0 --values 1,2,3 --dtype uint16` works.
- [ ] `--json` flag emits structured output for scripting.
- [ ] Help text covers every flag.
- [ ] Argparse only — no `click` dependency.

---

## M7 — Polish, integration tests, packaging
**Scope:** Logging helpers, `pymod.wire` hex tracer, structured logger optional path, `pymod serve` CLI for dev/sim, version bump to 0.1.0.

**Acceptance:**
- [x] Logging gating verified: `logger.isEnabledFor` short-circuits hot path. (test_logging::TestHotPathGated)
- [x] `pymod.wire` produces hex dumps when enabled, silent otherwise. (test_logging)
- [x] `pymod.logging.configure(...)` and `JsonFormatter` for opt-in structured output.
- [x] `pymod serve` subcommand for development / simulation.
- [x] Version bumped to 0.1.0.
- [x] Coverage at 89% (target was >=90%; close, gaps are RTU/serial error branches that need hardware).
- [ ] Integration suite against user's simulator — needs simulator pointer.
- [ ] `pip install` smoke test on aarch64 — needs ARM env.

---

## Out-of-scope (won't do unless asked later)

- Modbus ASCII.
- Internal datastore for the server (host owns data).
- IoT-side bridge (MQTT/OPC-UA/REST).
- Bit-level *writes* to holding registers (read-modify-write — racy without device support).
- TUI / interactive REPL.
- TCP connection pooling.
