# API reference

Auto-generated from the source. The top-level `pymod` package re-exports
every public symbol below — `from pymod import Holding, AsyncClient, ...`
just works.

## Clients

```{eval-rst}
.. autoclass:: pymod.AsyncClient
   :members:
   :show-inheritance:

.. autoclass:: pymod.Client
   :members:
   :show-inheritance:
```

## Read items

```{eval-rst}
.. autoclass:: pymod.Holding
   :members:

.. autoclass:: pymod.Input
   :members:

.. autoclass:: pymod.Coil
   :members:

.. autoclass:: pymod.Discrete
   :members:

.. autoclass:: pymod.ReadResult
   :members:
```

## Write items

```{eval-rst}
.. autoclass:: pymod.WriteHolding
   :members:

.. autoclass:: pymod.WriteCoils
   :members:

.. autoclass:: pymod.WriteResult
   :members:
```

## Server

```{eval-rst}
.. autoclass:: pymod.Server
   :members:
   :show-inheritance:

.. autodata:: pymod.Area
```

## Retry policy

```{eval-rst}
.. autoclass:: pymod.RetryPolicy
   :members:
```

## Exceptions

```{eval-rst}
.. autoexception:: pymod.ModbusError
.. autoexception:: pymod.ModbusTransportError
.. autoexception:: pymod.ModbusTimeoutError
.. autoexception:: pymod.ModbusConnectionError
.. autoexception:: pymod.ModbusProtocolError
.. autoexception:: pymod.ModbusExceptionResponse
.. autoexception:: pymod.IllegalFunction
.. autoexception:: pymod.IllegalDataAddress
.. autoexception:: pymod.IllegalDataValue
.. autoexception:: pymod.SlaveDeviceFailure
.. autoexception:: pymod.SlaveDeviceBusy
.. autoexception:: pymod.MemoryParityError
.. autoexception:: pymod.GatewayPathUnavailable
.. autoexception:: pymod.GatewayTargetFailedToRespond
```

## Codec helpers

Lower-level encode/decode functions, useful for custom function-code
codecs and integration tests.

```{eval-rst}
.. automodule:: pymod.codec.values
   :members:

.. automodule:: pymod.codec.bits
   :members:
```

## Protocol helpers

The pure-functions PDU layer. Most users never need these directly —
they're exposed for advanced cases (custom FCs, building a non-standard
slave, etc.).

```{eval-rst}
.. automodule:: pymod.protocol.pdu
   :members:
   :undoc-members:
```

## Logging helpers

```{eval-rst}
.. automodule:: pymod.logging
   :members:
```
