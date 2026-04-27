"""Tests for `pymod.cli` — argument parsing, output formatting, exit codes.

The CLI's `main()` runs `asyncio.run()` internally, so tests are
synchronous. The fake server runs on a separate thread (via
`ThreadedFakeServer`) to avoid loop conflicts.
"""

from __future__ import annotations

import json
import socket
import struct

import pytest

from pymod.cli import main

from ..client._threaded_server import ThreadedFakeServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _holding_response(values: list[int]) -> bytes:
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([0x03, len(body)]) + body


def _coil_response(values: list[bool]) -> bytes:
    byte_count = (len(values) + 7) // 8
    buf = bytearray(byte_count)
    for i, v in enumerate(values):
        if v:
            buf[i // 8] |= 1 << (i % 8)
    return bytes([0x01, byte_count]) + bytes(buf)


# ---------- read --------------------------------------------------------


class TestRead:
    def test_holding_uint16_human_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0x1234, 0xCAFE])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "2",
                "--dtype", "uint16",
                "--timeout", "1.0",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[0]: 0x1234 (4660)" in out
        assert "[1]: 0xCAFE (51966)" in out

    def test_holding_float32_decoding(self, capsys: pytest.CaptureFixture[str]) -> None:
        # 1.0 IEEE-754 single = 0x3F800000.
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0x3F80, 0x0000])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "2",
                "--dtype", "float32",
                "--timeout", "1.0",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "1.0" in out

    def test_coil_read(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _coil_response([True, False, True, True])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "coil",
                "--start", "0",
                "--count", "4",
                "--timeout", "1.0",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[0]: True" in out
        assert "[1]: False" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([42, 100])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "2",
                "--dtype", "uint16",
                "--json",
                "--timeout", "1.0",
            ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload == {"ok": True, "values": [42, 100]}

    def test_exception_response_exit_code_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def handler(pdu: bytes) -> bytes:
            return bytes([0x83, 0x02])  # IllegalDataAddress

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "1",
                "--dtype", "uint16",
                "--timeout", "1.0",
                "--retries", "0",
            ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "IllegalDataAddress" in err

    def test_exception_response_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return bytes([0x83, 0x02])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "1",
                "--dtype", "uint16",
                "--json",
                "--timeout", "1.0",
                "--retries", "0",
            ])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is False
        assert payload["error"]["type"] == "IllegalDataAddress"

    def test_connection_error_exit_code_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        port = _free_port()
        rc = main([
            "read",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--area", "holding",
            "--start", "0",
            "--count", "1",
            "--dtype", "uint16",
            "--timeout", "0.3",
            "--retries", "0",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "ModbusConnectionError" in err

    def test_bit_dtype_requires_bit_index(self) -> None:
        with pytest.raises(SystemExit):
            main([
                "read",
                "--host", "127.0.0.1",
                "--port", "502",
                "--area", "holding",
                "--start", "0",
                "--count", "1",
                "--dtype", "bit",
            ])

    def test_bit_dtype_with_bit_index(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0xABCD])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--count", "1",
                "--dtype", "bit",
                "--bit-index", "0",
                "--timeout", "1.0",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        # Bit 0 of 0xABCD = 1
        assert "[0]: True" in out


# ---------- write -------------------------------------------------------


class TestWrite:
    def test_write_single_register(self, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[bytes] = []

        async def handler(pdu: bytes) -> bytes:
            captured.append(pdu)
            return pdu  # FC06 echoes

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "write",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "5",
                "--values", "0xCAFE",
                "--dtype", "uint16",
                "--timeout", "1.0",
            ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "ok"
        assert captured[0][0] == 0x06  # FC06

    def test_write_multiple_registers(self, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[bytes] = []

        async def handler(pdu: bytes) -> bytes:
            captured.append(pdu)
            fc = pdu[0]
            if fc == 0x10:
                start, count = struct.unpack(">HH", pdu[1:5])
                return bytes([fc]) + struct.pack(">HH", start, count)
            return pdu

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "write",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--values", "1,2,3",
                "--dtype", "uint16",
                "--timeout", "1.0",
            ])
        assert rc == 0
        assert captured[0][0] == 0x10  # FC16

    def test_write_coils(self, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[bytes] = []

        async def handler(pdu: bytes) -> bytes:
            captured.append(pdu)
            fc = pdu[0]
            if fc == 0x0F:
                start, count = struct.unpack(">HH", pdu[1:5])
                return bytes([fc]) + struct.pack(">HH", start, count)
            return pdu

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "write",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "coil",
                "--start", "0",
                "--values", "true,false,1,0",
                "--timeout", "1.0",
            ])
        assert rc == 0
        assert captured[0][0] == 0x0F  # FC15

    def test_write_float32(self, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[bytes] = []

        async def handler(pdu: bytes) -> bytes:
            captured.append(pdu)
            fc = pdu[0]
            if fc == 0x10:
                start, count = struct.unpack(">HH", pdu[1:5])
                return bytes([fc]) + struct.pack(">HH", start, count)
            return pdu

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "write",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--values", "1.5",
                "--dtype", "float32",
                "--timeout", "1.0",
            ])
        assert rc == 0
        # 1.5 as float32 = 0x3FC00000 → registers [0x3FC0, 0x0000].
        # Body of FC16 request: start(2) count(2) bc(1) regs(2*N).
        assert captured[0][6:8] == b"\x3f\xc0"
        assert captured[0][8:10] == b"\x00\x00"

    def test_write_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return pdu

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "write",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0",
                "--values", "42",
                "--dtype", "uint16",
                "--json",
                "--timeout", "1.0",
            ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload == {"ok": True}

    def test_write_invalid_bool_rejected(self) -> None:
        with pytest.raises(SystemExit):
            main([
                "write",
                "--host", "127.0.0.1",
                "--port", "502",
                "--area", "coil",
                "--start", "0",
                "--values", "maybe",
            ])

    def test_write_value_out_of_range_propagates(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def handler(pdu: bytes) -> bytes:
            return pdu

        with ThreadedFakeServer(handler) as srv:
            with pytest.raises(ValueError):
                main([
                    "write",
                    "--host", "127.0.0.1",
                    "--port", str(srv.port),
                    "--area", "holding",
                    "--start", "0",
                    "--values", "0x10000",
                    "--dtype", "uint16",
                    "--timeout", "1.0",
                ])


# ---------- scan --------------------------------------------------------


class TestScan:
    def test_scan_alive_device(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "scan",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--timeout", "1.0",
            ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "alive"

    def test_scan_responds_with_exception_still_alive(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def handler(pdu: bytes) -> bytes:
            return bytes([0x83, 0x02])  # IllegalDataAddress

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "scan",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--timeout", "1.0",
                "--retries", "0",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alive" in out
        assert "IllegalDataAddress" in out

    def test_scan_dead_port(self, capsys: pytest.CaptureFixture[str]) -> None:
        port = _free_port()
        rc = main([
            "scan",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--timeout", "0.3",
            "--retries", "0",
        ])
        assert rc == 2

    def test_scan_json_alive(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "scan",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--json",
                "--timeout", "1.0",
            ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is True
        assert payload["responsive"] is True


# ---------- arg validation ----------------------------------------------


class TestArgValidation:
    def test_no_subcommand_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main([])
        assert rc == 2

    def test_missing_required_args_argparse_exit(self) -> None:
        with pytest.raises(SystemExit):
            main(["read", "--area", "holding"])  # missing --start, --count

    def test_no_host_or_rtu_raises(self) -> None:
        with pytest.raises(SystemExit):
            main([
                "read",
                "--area", "holding",
                "--start", "0",
                "--count", "1",
            ])

    def test_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_hex_start_address(self, capsys: pytest.CaptureFixture[str]) -> None:
        # --start accepts hex via int(s, 0).
        async def handler(pdu: bytes) -> bytes:
            start = int.from_bytes(pdu[1:3], "big")
            assert start == 0x100
            return _holding_response([0xDEAD])

        with ThreadedFakeServer(handler) as srv:
            rc = main([
                "read",
                "--host", "127.0.0.1",
                "--port", str(srv.port),
                "--area", "holding",
                "--start", "0x100",
                "--count", "1",
                "--dtype", "uint16",
                "--timeout", "1.0",
            ])
        assert rc == 0
