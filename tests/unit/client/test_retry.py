"""Tests for `pymod.retry.RetryPolicy`."""

from __future__ import annotations

import math

from pymod.errors import ModbusConnectionError, ModbusTimeoutError
from pymod.retry import RetryPolicy


class TestDelayFor:
    def test_no_delay_before_first_attempt(self) -> None:
        p = RetryPolicy(backoff_initial_s=0.05, backoff_factor=2.0, backoff_cap_s=1.0)
        assert p.delay_for(0) == 0.0

    def test_first_retry_uses_initial(self) -> None:
        p = RetryPolicy(backoff_initial_s=0.05, backoff_factor=2.0, backoff_cap_s=1.0)
        assert math.isclose(p.delay_for(1), 0.05)

    def test_exponential_growth(self) -> None:
        p = RetryPolicy(backoff_initial_s=0.05, backoff_factor=2.0, backoff_cap_s=10.0)
        assert math.isclose(p.delay_for(1), 0.05)
        assert math.isclose(p.delay_for(2), 0.10)
        assert math.isclose(p.delay_for(3), 0.20)
        assert math.isclose(p.delay_for(4), 0.40)

    def test_cap_clamps_growth(self) -> None:
        p = RetryPolicy(backoff_initial_s=0.05, backoff_factor=2.0, backoff_cap_s=0.15)
        assert math.isclose(p.delay_for(1), 0.05)
        assert math.isclose(p.delay_for(2), 0.10)
        assert math.isclose(p.delay_for(3), 0.15)  # would be 0.20 without cap
        assert math.isclose(p.delay_for(99), 0.15)


class TestDefaults:
    def test_default_max_attempts_is_2(self) -> None:
        p = RetryPolicy()
        assert p.max_attempts == 2

    def test_default_retry_on_excludes_exception_responses(self) -> None:
        p = RetryPolicy()
        assert ModbusTimeoutError in p.retry_on
        assert ModbusConnectionError in p.retry_on
        # ModbusExceptionResponse is NOT in the default — retrying an
        # illegal address would be pointless.
        from pymod.errors import ModbusExceptionResponse
        assert ModbusExceptionResponse not in p.retry_on


class TestImmutability:
    def test_policy_is_frozen(self) -> None:
        import dataclasses
        p = RetryPolicy()
        try:
            p.max_attempts = 99  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("RetryPolicy should be frozen")
