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


# --- failure classification (TC07 / TC09): a failed call records WHY it failed ---

from app.core.call_coordinator import classify_failure  # noqa: E402

_OK = {"trust_state": "TRUSTED"}


def test_failure_no_telemetry_from_a_side():
    assert classify_failure({"caller": _OK}) == ("no_telemetry", "callee")


def test_failure_camera_blocked_wins():
    r = {"trust_state": "DEGRADED", "camera_blocked": True, "reflection_count": 2}
    assert classify_failure({"caller": _OK, "callee": r}) == ("camera_blocked", "callee")


def test_failure_reflection():
    r = {"trust_state": "SUSPECT", "reflection_count": 1}
    assert classify_failure({"caller": r, "callee": _OK}) == ("reflection_detected", "caller")


def test_failure_no_motion_when_channel_a_fails():
    r = {"trust_state": "DEGRADED", "channel_a_pass": False, "channel_b_pass": True}
    assert classify_failure({"caller": _OK, "callee": r}) == ("no_motion", "callee")


def test_failure_flash_not_confirmed_when_channel_b_fails():
    r = {"trust_state": "DEGRADED", "channel_a_pass": True, "channel_b_pass": False}
    assert classify_failure({"caller": r, "callee": _OK}) == ("flash_not_confirmed", "caller")


def test_failure_plain_timeout_when_both_channels_passed_late():
    r = {"trust_state": "DEGRADED", "channel_a_pass": True, "channel_b_pass": True}
    assert classify_failure({"caller": _OK, "callee": r}) == ("timeout", "callee")


# --- TC07: a call not verified within the window is blocked and the failure is recorded ---

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.core import call_coordinator as cc  # noqa: E402


@pytest.mark.asyncio
async def test_tc07_unverified_call_times_out_blocked_with_reason(monkeypatch):
    saved = {}

    async def fake_db(call_id, state, failure_reason=None):
        saved.update(call_id=call_id, state=state, failure_reason=failure_reason)

    monkeypatch.setattr(cc, "VERIFY_WINDOW_S", 0.05)
    monkeypatch.setattr(cc, "_set_call_state_in_db", fake_db)
    cc.start_window("tc07")
    cc._calls["tc07"].deadline_monotonic = cc.time.monotonic() + 0.05
    cc.report("tc07", "caller", {"trust_state": "TRUSTED"})
    cc.report("tc07", "callee", {"trust_state": "DEGRADED", "channel_a_pass": False, "channel_b_pass": True})
    assert await asyncio.wait_for(cc.wait_for_outcome("tc07"), 2) == "failed"
    assert not cc.is_verified("tc07")
    assert saved["state"] == "FAILED_VERIFICATION" and saved["failure_reason"] == "caller=verified,callee=no_motion"
    cc.clear("tc07")


# --- per-side status shown in call history ---

from app.core.call_coordinator import side_status, sides_summary  # noqa: E402


def test_summary_names_the_side_that_did_not_move():
    reports = {
        "caller": {"trust_state": "DEGRADED", "channel_a_pass": False, "channel_b_pass": True},
        "callee": {"trust_state": "TRUSTED"},
    }
    assert sides_summary(reports) == "caller=no_motion,callee=verified"


def test_summary_when_the_receiver_is_the_one_who_failed():
    reports = {
        "caller": {"trust_state": "TRUSTED"},
        "callee": {"trust_state": "DEGRADED", "channel_a_pass": False, "channel_b_pass": False},
    }
    assert sides_summary(reports) == "caller=verified,callee=no_motion"


def test_summary_both_verified_and_missing_side():
    ok = {"trust_state": "TRUSTED"}
    assert sides_summary({"caller": ok, "callee": ok}) == "caller=verified,callee=verified"
    assert sides_summary({"caller": ok}) == "caller=verified,callee=no_telemetry"
    assert side_status({"trust_state": "DEGRADED", "gyro_unavailable": True}) == "flash_not_confirmed"
