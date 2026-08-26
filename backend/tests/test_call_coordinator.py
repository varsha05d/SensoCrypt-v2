"""Tests for the pre-connect verification gate's timing/coordination logic -- the client's
explicit requirements: max 30s window, connects only once BOTH sides verify, and one shared
backend-generated session key per call."""

import asyncio

import pytest

from app.core import call_coordinator


@pytest.fixture(autouse=True)
def _short_window(monkeypatch):
    # Real VERIFY_WINDOW_S (30s) would make the timeout test slow -- shrink it for tests.
    monkeypatch.setattr(call_coordinator, "VERIFY_WINDOW_S", 0.2)
    yield
    call_coordinator._calls.clear()


async def test_both_sides_verified_settles_as_verified():
    call_coordinator.start_window("call-a")
    call_coordinator.mark_verified("call-a", "caller")
    assert not call_coordinator.is_verified("call-a")  # only one side so far
    call_coordinator.mark_verified("call-a", "callee")
    outcome = await asyncio.wait_for(call_coordinator.wait_for_outcome("call-a"), timeout=1)
    assert outcome == "verified"
    assert call_coordinator.is_verified("call-a")


async def test_timeout_without_both_sides_fails():
    call_coordinator.start_window("call-b")
    call_coordinator.mark_verified("call-b", "caller")  # only one side ever verifies
    outcome = await asyncio.wait_for(call_coordinator.wait_for_outcome("call-b"), timeout=1)
    assert outcome == "failed"
    assert not call_coordinator.is_verified("call-b")


async def test_unknown_call_id_is_failed_immediately():
    """signal.py relies on this: a client that never went through the verification flow at
    all (skipped straight to /ws/signal) must not get anything relayed."""
    outcome = await call_coordinator.wait_for_outcome("never-started")
    assert outcome == "failed"


async def test_session_key_only_available_once_verified():
    call_coordinator.start_window("call-c")
    assert call_coordinator.get_or_create_session_key("call-c") is None
    call_coordinator.mark_verified("call-c", "caller")
    call_coordinator.mark_verified("call-c", "callee")
    await call_coordinator.wait_for_outcome("call-c")
    key = call_coordinator.get_or_create_session_key("call-c")
    assert key is not None
    assert len(key) == 32


async def test_session_key_stable_across_repeated_calls():
    call_coordinator.start_window("call-d")
    call_coordinator.mark_verified("call-d", "caller")
    call_coordinator.mark_verified("call-d", "callee")
    await call_coordinator.wait_for_outcome("call-d")
    key1 = call_coordinator.get_or_create_session_key("call-d")
    key2 = call_coordinator.get_or_create_session_key("call-d")
    assert key1 == key2  # both sides must retrieve the SAME shared key


async def test_start_window_is_idempotent():
    """A client retry calling accept twice must not reset the clock or wipe progress."""
    call_coordinator.start_window("call-e")
    call_coordinator.mark_verified("call-e", "caller")
    call_coordinator.start_window("call-e")  # simulate a retry
    call_coordinator.mark_verified("call-e", "callee")
    outcome = await asyncio.wait_for(call_coordinator.wait_for_outcome("call-e"), timeout=1)
    assert outcome == "verified"


async def test_clear_removes_all_state():
    call_coordinator.start_window("call-f")
    call_coordinator.mark_verified("call-f", "caller")
    call_coordinator.clear("call-f")
    assert "call-f" not in call_coordinator._calls
