"""Tests for `pymod._guide` — the `--guide` interactive wizard."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest

from pymod._guide import (
    _equivalent_command,
    build_namespace,
)
from pymod.cli import main

from ..client._threaded_server import ThreadedFakeServer


def _make_io(answers: list[str]) -> tuple[Iterator[str], list[str]]:
    out_buffer: list[str] = []
    answers_iter = iter(answers)

    def fake_input(prompt: str = "") -> str:
        out_buffer.append(prompt)
        return next(answers_iter)

    def fake_print(line: str) -> None:
        out_buffer.append(line)

    return fake_input, fake_print, out_buffer  # type: ignore[return-value]


# ---------- namespace building ---------------------------------------------


class TestBuildNamespace:
    def test_full_read_flow_tcp_uint16(self) -> None:
        answers = [
            "1",            # operation: read
            "1",            # transport: TCP
            "10.0.0.5",     # host
            "5020",         # port
            "1",            # unit_id
            "0.5",          # timeout
            "1",            # retries
            "1",            # area: holding
            "0",            # start
            "10",           # count
            "1",            # dtype: uint16
            "n",            # json
            "y",            # execute
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        assert op == "read"
        assert ns.host == "10.0.0.5"
        assert ns.port == 5020
        assert ns.area == "holding"
        assert ns.start == 0
        assert ns.count == 10
        assert ns.dtype == "uint16"

    def test_read_float32_prompts_word_byte_order(self) -> None:
        answers = [
            "1", "1", "127.0.0.1", "502", "1", "0.5", "1",
            "holding",       # accept name instead of number
            "0", "10",
            "5",             # dtype: float32
            "1",             # word_order: big
            "1",             # byte_order: big
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        assert ns.dtype == "float32"
        assert ns.word_order == "big"
        assert ns.byte_order == "big"

    def test_read_bit_prompts_bit_index(self) -> None:
        answers = [
            "1", "1", "127.0.0.1", "502", "1", "0.5", "1",
            "1",            # area: holding
            "0", "2",       # start, count
            "bit",          # dtype: bit (use name for stability)
            "8",            # bit_index
            "1",            # bit_numbering: lsb_first
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        _, ns = result
        assert ns.dtype == "bit"
        assert ns.bit_index == 8

    def test_write_holding_uint16(self) -> None:
        answers = [
            "2",            # operation: write
            "1",            # transport: TCP
            "127.0.0.1", "502", "1", "0.5", "1",
            "1",            # area: holding
            "5",            # start
            "1",            # dtype: uint16
            "100,200,300",  # values
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        assert op == "write"
        assert ns.area == "holding"
        assert ns.values == "100,200,300"
        assert ns.dtype == "uint16"

    def test_write_coil_skips_dtype(self) -> None:
        answers = [
            "2",            # operation: write
            "1",            # transport: TCP
            "127.0.0.1", "502", "1", "0.5", "1",
            "2",            # area: coil
            "0",            # start
            "true,false",   # values
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        _, ns = result
        assert ns.area == "coil"
        assert ns.values == "true,false"

    def test_scan(self) -> None:
        answers = [
            "3",            # operation: scan
            "1",            # transport: TCP
            "127.0.0.1", "502", "1", "0.5", "1",
            "0",            # start
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        assert op == "scan"
        assert ns.start == 0

    def test_rtu_serial_prompts_baudrate_parity(self) -> None:
        answers = [
            "1",            # operation: read
            "2",            # transport: rtu
            "/dev/ttyUSB0", # serial port
            "9600",         # baudrate
            "1",            # parity: N
            "1", "0.5", "1",
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        _, ns = result
        assert ns.rtu == "/dev/ttyUSB0"
        assert ns.baudrate == 9600
        assert ns.host is None

    def test_rtu_over_tcp(self) -> None:
        answers = [
            "1",            # read
            "3",            # transport: rtu-over-tcp
            "10.0.0.5", "502",
            "1", "0.5", "1",
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        _, ns = result
        assert ns.rtu_over_tcp is True
        assert ns.host == "10.0.0.5"

    def test_decline_to_execute_returns_none(self) -> None:
        answers = [
            "1", "1", "127.0.0.1", "502", "1", "0.5", "1",
            "1", "0", "10", "1", "n",
            "n",            # do NOT execute
        ]
        result = _run_wizard(answers)
        assert result is None

    def test_invalid_choice_reprompts(self) -> None:
        answers = [
            "99",           # invalid op
            "1",            # valid: read
            "1", "127.0.0.1", "502", "1", "0.5", "1",
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, _ = result
        assert op == "read"

    def test_default_for_blank_input(self) -> None:
        # Blank port, unit_id, timeout, retries → defaults.
        answers = [
            "1", "1", "127.0.0.1",
            "",             # port → default 502
            "",             # unit_id → 1
            "",             # timeout → 0.5
            "",             # retries → 1
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        _, ns = result
        assert ns.port == 502
        assert ns.unit_id == 1
        assert ns.timeout_s == 0.5
        assert ns.retries == 1


# ---------- equivalent-command preview --------------------------------------


class TestEquivalentCommand:
    def test_minimal_read(self) -> None:
        answers = [
            "1", "1", "127.0.0.1", "502", "1", "0.5", "1",
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        cmd = _equivalent_command(op, ns)
        assert cmd.startswith("pymod read --host 127.0.0.1")
        assert "--area holding" in cmd
        assert "--start 0" in cmd
        assert "--count 10" in cmd
        # Defaults are NOT printed.
        assert "--port" not in cmd
        assert "--unit-id" not in cmd
        assert "--dtype" not in cmd

    def test_includes_non_defaults(self) -> None:
        answers = [
            "1", "1", "10.0.0.5", "5020", "7", "2.0", "3",
            "1", "100", "10", "2",       # int16
            "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        cmd = _equivalent_command(op, ns)
        assert "--port 5020" in cmd
        assert "--unit-id 7" in cmd
        assert "--timeout 2.0" in cmd
        assert "--retries 3" in cmd
        assert "--dtype int16" in cmd

    def test_rtu_command(self) -> None:
        answers = [
            "1", "2", "/dev/ttyUSB0", "19200", "1",
            "1", "0.5", "1",
            "1", "0", "10", "1", "n", "y",
        ]
        result = _run_wizard(answers)
        assert result is not None
        op, ns = result
        cmd = _equivalent_command(op, ns)
        assert "--rtu /dev/ttyUSB0" in cmd
        assert "--baudrate 19200" in cmd
        assert "--host" not in cmd


# ---------- end-to-end via main() ------------------------------------------


def _holding_response(values: list[int]) -> bytes:
    body = b"".join(v.to_bytes(2, "big") for v in values)
    return bytes([0x03, len(body)]) + body


class TestMainGuide:
    def test_main_guide_runs_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Server returns a single int16 = -1.
        async def handler(pdu: bytes) -> bytes:
            return _holding_response([0xFFFF])

        with ThreadedFakeServer(handler) as srv:
            answers = [
                "1",            # read
                "1",            # tcp
                "127.0.0.1",    # host
                str(srv.port),  # port
                "1",            # unit_id
                "1.0",          # timeout
                "0",            # retries
                "1",            # area: holding
                "0",            # start
                "1",            # count
                "2",            # dtype: int16
                "n",            # json
                "y",            # execute
            ]
            it = iter(answers)
            monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
            rc = main(["--guide"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[0]: 0xFFFF (-1)" in out

    def test_main_guide_decline_returns_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        answers = [
            "1", "1", "127.0.0.1", "502", "1", "0.5", "1",
            "1", "0", "1", "1", "n",
            "n",  # decline
        ]
        it = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
        rc = main(["--guide"])
        assert rc == 0

    def test_main_guide_keyboard_interrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def raise_ki(_prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_ki)
        rc = main(["--guide"])
        assert rc == 130


# ---------- helpers --------------------------------------------------------


def _run_wizard(answers: list[str]):
    """Run the wizard with scripted answers and a buffered output."""
    answers_iter = iter(answers)
    out_buffer: list[str] = []

    def fake_input(prompt: str = "") -> str:
        return next(answers_iter)

    def fake_print(line: str) -> None:
        out_buffer.append(line)

    return build_namespace(inp=fake_input, out=fake_print)
