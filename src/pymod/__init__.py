"""pymod — production-grade Modbus TCP/RTU driver."""

from __future__ import annotations

import logging as _logging

from ._types import (
    Area,
    Coil,
    Discrete,
    Holding,
    Input,
    ReadItem,
    ReadResult,
    WriteCoils,
    WriteHolding,
    WriteItem,
    WriteResult,
)
from .client import AsyncClient, Client
from .errors import (
    GatewayPathUnavailable,
    GatewayTargetFailedToRespond,
    IllegalDataAddress,
    IllegalDataValue,
    IllegalFunction,
    MemoryParityError,
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionResponse,
    ModbusProtocolError,
    ModbusTimeoutError,
    ModbusTransportError,
    SlaveDeviceBusy,
    SlaveDeviceFailure,
)
from .retry import RetryPolicy
from .server import Server

_logging.getLogger("pymod").addHandler(_logging.NullHandler())
_logging.getLogger("pymod.wire").addHandler(_logging.NullHandler())

__version__ = "0.1.0"

__all__ = [
    "AsyncClient",
    "Area",
    "Client",
    "Coil",
    "Discrete",
    "GatewayPathUnavailable",
    "GatewayTargetFailedToRespond",
    "Holding",
    "IllegalDataAddress",
    "IllegalDataValue",
    "IllegalFunction",
    "Input",
    "MemoryParityError",
    "ModbusConnectionError",
    "ModbusError",
    "ModbusExceptionResponse",
    "ModbusProtocolError",
    "ModbusTimeoutError",
    "ModbusTransportError",
    "ReadItem",
    "ReadResult",
    "RetryPolicy",
    "Server",
    "SlaveDeviceBusy",
    "SlaveDeviceFailure",
    "WriteCoils",
    "WriteHolding",
    "WriteItem",
    "WriteResult",
    "__version__",
]
