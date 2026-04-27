"""Retry policy for client-level retries.

Retries live at the client layer, not the transport. The transport surfaces
a single attempt's outcome; the client decides whether to issue another.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ModbusConnectionError, ModbusError, ModbusTimeoutError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Per-call retry policy.

    Defaults match the user-stated requirement: 1 retry (= 2 total attempts),
    transport errors only. Modbus exception responses are NOT retried by
    default — the same illegal address will fail forever.
    """

    max_attempts: int = 2
    backoff_initial_s: float = 0.05
    backoff_factor: float = 2.0
    backoff_cap_s: float = 1.0
    retry_on: tuple[type[ModbusError], ...] = field(
        default=(ModbusTimeoutError, ModbusConnectionError)
    )

    def delay_for(self, attempt: int) -> float:
        """Backoff delay before the n-th retry (attempt is 1-indexed; first
        retry is `attempt=1`)."""
        if attempt <= 0:
            return 0.0
        delay = self.backoff_initial_s * (self.backoff_factor ** (attempt - 1))
        return min(delay, self.backoff_cap_s)
