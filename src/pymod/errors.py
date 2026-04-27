"""Exception hierarchy for pymod.

Three families with distinct semantics:

* `ModbusTransportError` — the request never got a valid reply (timeout,
  socket dropped, CRC mismatch). Retrying is usually appropriate.
* `ModbusProtocolError` — a frame was received but malformed (bad MBAP,
  mismatched transaction id, length lie). Retrying rarely helps; the device
  or the wire is misbehaving.
* `ModbusExceptionResponse` — the slave returned a valid Modbus exception
  response (FC | 0x80). Retrying will hit the same illegal address forever;
  do not retry by default.
"""

from __future__ import annotations


class ModbusError(Exception):
    """Base class for everything pymod raises."""


# ---------- Transport-level failures -----------------------------------------


class ModbusTransportError(ModbusError):
    """The request did not produce a usable reply on the wire."""


class ModbusTimeoutError(ModbusTransportError):
    """No response within the configured timeout."""


class ModbusConnectionError(ModbusTransportError):
    """TCP connection refused/dropped, or serial port unavailable."""


class ModbusCRCError(ModbusTransportError):
    """RTU frame failed CRC16 check."""


# ---------- Protocol-level failures ------------------------------------------


class ModbusProtocolError(ModbusError):
    """A response was received but is malformed or out-of-spec."""


# ---------- Slave-returned exception responses --------------------------------


class ModbusExceptionResponse(ModbusError):
    """Slave returned a valid Modbus exception response. Code in `code`."""

    code: int = 0

    def __init__(self, message: str = "", code: int | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        if code is not None:
            self.code = code


class IllegalFunction(ModbusExceptionResponse):
    code = 0x01


class IllegalDataAddress(ModbusExceptionResponse):
    code = 0x02


class IllegalDataValue(ModbusExceptionResponse):
    code = 0x03


class SlaveDeviceFailure(ModbusExceptionResponse):
    code = 0x04


class Acknowledge(ModbusExceptionResponse):
    code = 0x05


class SlaveDeviceBusy(ModbusExceptionResponse):
    code = 0x06


class MemoryParityError(ModbusExceptionResponse):
    code = 0x08


class GatewayPathUnavailable(ModbusExceptionResponse):
    code = 0x0A


class GatewayTargetFailedToRespond(ModbusExceptionResponse):
    code = 0x0B


EXCEPTION_BY_CODE: dict[int, type[ModbusExceptionResponse]] = {
    0x01: IllegalFunction,
    0x02: IllegalDataAddress,
    0x03: IllegalDataValue,
    0x04: SlaveDeviceFailure,
    0x05: Acknowledge,
    0x06: SlaveDeviceBusy,
    0x08: MemoryParityError,
    0x0A: GatewayPathUnavailable,
    0x0B: GatewayTargetFailedToRespond,
}
