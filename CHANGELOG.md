# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-27

### Added

- Initial release.
- Modbus TCP client with auto-reconnect, request pipelining, and per-connection
  transaction-id demultiplexing.
- Modbus RTU client over a serial port (pyserial), with internal bus
  serialisation safe for concurrent callers.
- Modbus RTU over TCP for serial-to-Ethernet gateways (Moxa-style).
- All standard function codes (FC01/02/03/04/05/06/15/16) plus a registration
  hook for custom codes.
- Heterogeneous batch reads with adjacent-range coalescing and PDU-limit
  splitting.
- Typed values: `int16`, `uint16`, `int32`, `uint32`, `float32`, `int64`,
  `uint64`, `float64`.
- Bit extraction from register blocks (LSB-first or MSB-first numbering).
- All four PLC byte/word orderings (ABCD / CDAB / BADC / DCBA) configurable
  per item.
- Sync facade (`pymod.Client`) for non-async callers (Flask, scripts, REPL).
- Callback-based Modbus TCP server for protocol bridging — no internal
  datastore.
- `pymod` CLI with `read` / `write` / `scan` / `serve` subcommands plus an
  interactive `--guide` wizard.
- Sphinx documentation hosted on Read the Docs.
- Strict typing: `mypy --strict` clean across the package.
- 324 unit tests covering protocol, transport, planner, codec, server, CLI,
  and logging.

[Unreleased]: https://github.com/Daniel-V-Richardson/pymod/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Daniel-V-Richardson/pymod/releases/tag/v0.1.0
