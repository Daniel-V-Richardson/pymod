"""Tests for `pymod.transport.serial.SerialTransport`."""

from __future__ import annotations

import asyncio
import struct

import pytest

from pymod.errors import (
    IllegalDataAddress,
    ModbusError,
    ModbusTimeoutError,
)
from pymod.protocol.adu_rtu import crc16, encode_rtu_frame
from pymod.protocol.pdu import (
    decode_read_holding_registers,
    encode_read_holding_registers,
    encode_write_single_register,
)
from pymod.transport.serial import SerialTransport

from ._fake_serial import FakeSerial, make_serial_factory
from ._rtu_slave import RtuSlave


# ---------- basic round-trip ----------


class TestRoundTrip:
    async def test_read_holding_registers(self) -> None:
        slave = RtuSlave(holding={0: 0x000A, 1: 0x000B, 2: 0x000C})
        factory = make_serial_factory(slave)
        async with SerialTransport("/dev/null", 9600, serial_factory=factory) as t:
            response_pdu = await t.send_and_receive(
                encode_read_holding_registers(0, 3),
                unit_id=1,
                timeout_s=1.0,
            )
            assert decode_read_holding_registers(response_pdu, 3) == [0x000A, 0x000B, 0x000C]

    async def test_write_single_register_round_trip(self) -> None:
        slave = RtuSlave()
        factory = make_serial_factory(slave)
        async with SerialTransport("/dev/null", 9600, serial_factory=factory) as t:
            req = encode_write_single_register(0x0010, 0x1234)
            response_pdu = await t.send_and_receive(req, unit_id=1, timeout_s=1.0)
            # FC06 echoes request.
            assert response_pdu == req
            assert slave.holding[0x0010] == 0x1234

    async def test_lifecycle(self) -> None:
        factory = make_serial_factory(RtuSlave())
        t = SerialTransport("/dev/null", 9600, serial_factory=factory)
        assert not t.is_connected
        await t.connect()
        assert t.is_connected
        await t.close()
        assert not t.is_connected


# ---------- exception responses ----------


class TestExceptionResponses:
    async def test_illegal_data_address_propagates(self) -> None:
        slave = RtuSlave(holding={0: 0xABCD})  # only address 0 valid
        factory = make_serial_factory(slave)
        async with SerialTransport("/dev/null", 9600, serial_factory=factory) as t:
            response_pdu = await t.send_and_receive(
                encode_read_holding_registers(5, 1),
                unit_id=1,
                timeout_s=1.0,
            )
            with pytest.raises(IllegalDataAddress):
                decode_read_holding_registers(response_pdu, 1)

    async def test_illegal_function_response_decodable(self) -> None:
        # FC02 (read discrete inputs) is not implemented by RtuSlave;
        # it returns ILLEGAL_FUNCTION.
        slave = RtuSlave()
        factory = make_serial_factory(slave)
        async with SerialTransport("/dev/null", 9600, serial_factory=factory) as t:
            response_pdu = await t.send_and_receive(
                bytes([0x02, 0x00, 0x00, 0x00, 0x10]),  # FC02 read 16 inputs at 0
                unit_id=1,
                timeout_s=1.0,
            )
            assert response_pdu[0] == 0x82  # FC02 with exception bit
            assert response_pdu[1] == 0x01  # ILLEGAL_FUNCTION


# ---------- sequential throughput ----------


class TestSequentialThroughput:
    async def test_100_sequential_round_trips(self) -> None:
        # Acceptance criterion for M2: 100 sequential round-trips on a
        # serial-equivalent transport with no errors.
        regs = {i: i * 7 for i in range(100)}
        slave = RtuSlave(holding=regs)
        factory = make_serial_factory(slave)
        # Set inter_frame_delay_s=0 so the test runs fast; the real device
        # path enforces the 3.5-char delay.
        async with SerialTransport(
            "/dev/null", 115200, inter_frame_delay_s=0.0, serial_factory=factory
        ) as t:
            for i in range(100):
                response_pdu = await t.send_and_receive(
                    encode_read_holding_registers(i, 1),
                    unit_id=1,
                    timeout_s=1.0,
                )
                values = decode_read_holding_registers(response_pdu, 1)
                assert values == [i * 7]


# ---------- bus serialization ----------


class TestConcurrentCallers:
    async def test_ten_concurrent_callers_serialize_on_bus(self) -> None:
        # With 10 tasks sharing a Client, the bus must observe strictly
        # one transaction at a time. The fake's `max_in_flight` counter
        # records the peak overlap of write() calls.
        slave = RtuSlave(holding={i: i for i in range(20)})
        factory = make_serial_factory(slave)
        async with SerialTransport(
            "/dev/null", 115200, inter_frame_delay_s=0.0, serial_factory=factory
        ) as t:
            tasks = [
                t.send_and_receive(
                    encode_read_holding_registers(i % 20, 1),
                    unit_id=1,
                    timeout_s=1.0,
                )
                for i in range(10)
            ]
            await asyncio.gather(*tasks)
        fake: FakeSerial = factory.fake  # type: ignore[attr-defined]
        assert fake.max_in_flight == 1, (
            f"expected strict bus serialization, peak in-flight was "
            f"{fake.max_in_flight}"
        )


# ---------- timeouts ----------


class TestTimeouts:
    async def test_timeout_when_slave_silent(self) -> None:
        # Slave drops the request → buffer stays empty → read returns 0
        # bytes → ModbusTimeoutError.
        slave = RtuSlave(drop_next=1)
        factory = make_serial_factory(slave)
        async with SerialTransport(
            "/dev/null", 115200, inter_frame_delay_s=0.0, serial_factory=factory
        ) as t:
            with pytest.raises(ModbusTimeoutError):
                await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=0.1,
                )

    async def test_recovers_after_timeout(self) -> None:
        # After a missed response, subsequent calls should still work.
        slave = RtuSlave(holding={0: 0x42}, drop_next=1)
        factory = make_serial_factory(slave)
        async with SerialTransport(
            "/dev/null", 115200, inter_frame_delay_s=0.0, serial_factory=factory
        ) as t:
            with pytest.raises(ModbusTimeoutError):
                await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=0.1,
                )
            response_pdu = await t.send_and_receive(
                encode_read_holding_registers(0, 1),
                unit_id=1,
                timeout_s=1.0,
            )
            assert decode_read_holding_registers(response_pdu, 1) == [0x42]


# ---------- closed transport ----------


class TestClosedTransport:
    async def test_send_after_close_raises(self) -> None:
        factory = make_serial_factory(RtuSlave())
        t = SerialTransport("/dev/null", 9600, serial_factory=factory)
        await t.connect()
        await t.close()
        with pytest.raises(ModbusError):
            await t.send_and_receive(
                encode_read_holding_registers(0, 1),
                unit_id=1,
                timeout_s=0.5,
            )


# ---------- garbage on the wire ----------


class TestCorruptedResponse:
    async def test_bad_crc_raises(self) -> None:
        # Build a response with deliberately corrupted CRC.
        def bad_slave(_frame: bytes) -> bytes:
            response_pdu = bytes([0x03, 0x02, 0x00, 0x42])
            body = bytes([0x01]) + response_pdu
            crc = crc16(body)
            # Flip CRC bits.
            return body + struct.pack("<H", crc ^ 0xFFFF)

        factory = make_serial_factory(bad_slave)
        async with SerialTransport(
            "/dev/null", 115200, inter_frame_delay_s=0.0, serial_factory=factory
        ) as t:
            with pytest.raises(ModbusError):  # ModbusCRCError ⊂ ModbusError
                await t.send_and_receive(
                    encode_read_holding_registers(0, 1),
                    unit_id=1,
                    timeout_s=0.5,
                )
