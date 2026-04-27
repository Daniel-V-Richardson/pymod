"""Modbus RTU transport over a serial port.

pyserial does the actual I/O; pymod calls it from the asyncio default
executor so the event loop is never blocked. A single `asyncio.Lock`
serializes the bus — RS-485 is half-duplex, so even if many tasks share
one client the bus must see strictly one transaction at a time.

Frame end on receive is determined from the function code (and, for read
responses, from the byte-count byte that follows it). For custom FCs the
length is unknown and an exception is raised; future work can add a
pluggable hook for vendor extensions.

The 3.5-character-time inter-frame gap is enforced before each
transmission so the slave (or the line driver) sees a clean boundary
between successive requests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol, Self

import serial as _pyserial  # pyserial; absolute import (Python 3 default)

from ..errors import (
    ModbusConnectionError,
    ModbusError,
    ModbusProtocolError,
    ModbusTimeoutError,
)
from ..protocol.adu_rtu import decode_rtu_frame, encode_rtu_frame
from ._rtu_response import read_rtu_response_sync

_logger = logging.getLogger("pymod.transport.serial")
_wire = logging.getLogger("pymod.wire")

# Spec floor — at very high baud rates the 3.5-char calculation drops below
# 1.75 ms, but the spec mandates 1.75 ms minimum.
_MIN_FRAME_GAP_S = 0.00175


class _SerialLike(Protocol):
    """The slice of pyserial.Serial that pymod actually uses.

    Tests inject a fake matching this shape via `serial_factory`.
    """

    timeout: float | None
    write_timeout: float | None

    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


SerialFactory = Callable[..., _SerialLike]


def _default_serial_factory(**kwargs: Any) -> _SerialLike:
    return _pyserial.Serial(**kwargs)


def _bits_per_char(bytesize: int, parity: str, stopbits: float) -> int:
    parity_bits = 0 if parity.upper() == "N" else 1
    return 1 + bytesize + parity_bits + int(round(stopbits))


class SerialTransport:
    """Async-friendly wrapper around a blocking pyserial port.

    A single instance can be shared by many concurrent callers; the bus is
    serialized internally via an `asyncio.Lock`.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        inter_frame_delay_s: float | None = None,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        if inter_frame_delay_s is None:
            self._frame_gap_s = max(
                3.5 * _bits_per_char(bytesize, parity, stopbits) / baudrate,
                _MIN_FRAME_GAP_S,
            )
        else:
            self._frame_gap_s = inter_frame_delay_s

        self._serial_factory: SerialFactory = serial_factory or _default_serial_factory
        self._serial: _SerialLike | None = None
        self._connected = False
        self._closed = False
        self._lock = asyncio.Lock()
        self._last_op_monotonic = 0.0

    # ---------- public API ----------

    async def connect(self) -> None:
        if self._closed:
            raise ModbusError("transport is closed")
        async with self._lock:
            await self._open_locked()

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            self._connected = False
            ser = self._serial
            self._serial = None
            if ser is not None:
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, ser.close)
                except Exception:
                    pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def send_and_receive(
        self,
        pdu: bytes,
        *,
        unit_id: int,
        timeout_s: float,
    ) -> bytes:
        if self._closed:
            raise ModbusError("transport is closed")
        if len(pdu) == 0:
            raise ValueError("empty PDU")
        async with self._lock:
            await self._open_locked()
            ser = self._serial
            assert ser is not None
            frame = encode_rtu_frame(unit_id, pdu)
            if _wire.isEnabledFor(logging.DEBUG):
                _wire.debug("TX unit=%d frame=%s", unit_id, frame.hex())
            loop = asyncio.get_running_loop()
            response_frame = await loop.run_in_executor(
                None,
                self._exchange_blocking,
                ser,
                frame,
                timeout_s,
            )
            if _wire.isEnabledFor(logging.DEBUG):
                _wire.debug("RX frame=%s", response_frame.hex())
            uid, response_pdu = decode_rtu_frame(response_frame)
            if uid != unit_id:
                raise ModbusProtocolError(
                    f"unit id mismatch in response: got {uid}, expected {unit_id}"
                )
            return response_pdu

    # ---------- internal ----------

    async def _open_locked(self) -> None:
        """Open the serial port. Caller must hold `self._lock`."""
        if self._connected:
            return
        if self._closed:
            raise ModbusError("transport is closed")
        loop = asyncio.get_running_loop()
        try:
            ser = await loop.run_in_executor(
                None,
                lambda: self._serial_factory(
                    port=self._port,
                    baudrate=self._baudrate,
                    bytesize=self._bytesize,
                    parity=self._parity,
                    stopbits=self._stopbits,
                    timeout=0.5,
                    # Without write_timeout, pyserial blocks indefinitely
                    # if the OS write buffer cannot drain (slave gone, dead
                    # USB-serial dongle, etc.). Set a real default so
                    # disconnections surface as ModbusTimeoutError instead
                    # of hanging the executor thread.
                    write_timeout=0.5,
                ),
            )
        except _pyserial.SerialException as e:
            raise ModbusConnectionError(
                f"open serial port {self._port}: {e}"
            ) from e
        except OSError as e:
            raise ModbusConnectionError(
                f"open serial port {self._port}: {e}"
            ) from e
        self._serial = ser
        self._connected = True
        _logger.info("opened serial port %s @ %d", self._port, self._baudrate)

    def _exchange_blocking(
        self,
        ser: _SerialLike,
        frame: bytes,
        timeout_s: float,
    ) -> bytes:
        """Run on the executor thread. Raises ModbusTimeoutError on timeout
        and ModbusConnectionError on serial I/O errors.

        Notes on timeouts:
          * `ser.write_timeout` is set per call so a write into a backed-up
            kernel buffer (slave gone) raises promptly instead of hanging.
          * `ser.timeout` is set per read with a deadline-tracking helper.
          * `ser.flush()` is intentionally NOT called: pyserial's flush has
            no timeout option and blocks indefinitely on Windows when the
            receiver is gone. The kernel will transmit the queued bytes
            anyway; the slave's response (or lack of it) is what we wait on.
        """
        try:
            # Inter-frame gap before transmitting.
            elapsed = time.monotonic() - self._last_op_monotonic
            if elapsed < self._frame_gap_s:
                time.sleep(self._frame_gap_s - elapsed)

            try:
                ser.reset_input_buffer()
            except Exception:
                # Some serial backends don't implement this; non-fatal.
                pass

            ser.write_timeout = timeout_s
            try:
                ser.write(frame)
            except _pyserial.SerialTimeoutException as e:
                raise ModbusTimeoutError(
                    f"serial write timeout after {timeout_s}s: {e}"
                ) from e

            deadline = time.monotonic() + timeout_s

            def read_exactly(n: int) -> bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModbusTimeoutError(
                        f"serial response timeout (deadline reached)"
                    )
                ser.timeout = remaining
                data = ser.read(n)
                if len(data) < n:
                    raise ModbusTimeoutError(
                        f"serial response timeout (got {len(data)}/{n} bytes)"
                    )
                return data

            response = read_rtu_response_sync(read_exactly)
            self._last_op_monotonic = time.monotonic()
            return response
        except (ModbusTimeoutError, ModbusProtocolError):
            raise
        except _pyserial.SerialException as e:
            raise ModbusConnectionError(f"serial I/O error: {e}") from e
        except OSError as e:
            raise ModbusConnectionError(f"serial I/O error: {e}") from e
