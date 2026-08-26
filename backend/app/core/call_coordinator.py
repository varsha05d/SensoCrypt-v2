"""Tracks per-call pre-connect verification state: which side(s) of a call have reached
TRUSTED, and enforces the 30-second window the client requires -- if both sides aren't
verified within it, the call is marked failed and never gets to unlock signaling.

In-process, like the existing nonce store (backend/app/core/nonce.py) -- same free-tier,
single-instance assumption, same reasoning: state this short-lived doesn't need to survive
a process restart, and a restart only happens after 15 minutes of total inactivity anyway
(Render free tier), at which point no call was in flight.
"""

import asyncio
import time

VERIFY_WINDOW_S = 30.0
_SIDES = ("caller", "callee")


class _CallVerification:
    def __init__(self):
        self.deadline_monotonic = time.monotonic() + VERIFY_WINDOW_S
        self.verified_sides: set[str] = set()
        self.outcome: str | None = None  # None (pending) | "verified" | "failed"
        self.on_settled = asyncio.Event()  # set once outcome is no longer None
        self.session_key: bytes | None = None  # lazily generated, see get_or_create_session_key


_calls: dict[str, _CallVerification] = {}


def start_window(call_id: str) -> None:
    """Called once, when the callee accepts and both sides are about to start their
    liveness capture. Idempotent -- a second call for the same call_id is a no-op, so a
    client retry can't reset an already-running clock."""
    if call_id not in _calls:
        _calls[call_id] = _CallVerification()
        asyncio.create_task(_enforce_timeout(call_id))


async def _enforce_timeout(call_id: str) -> None:
    state = _calls[call_id]
    remaining = state.deadline_monotonic - time.monotonic()
    if remaining > 0:
        await asyncio.sleep(remaining)
    if state.outcome is None:
        state.outcome = "failed"
        state.on_settled.set()


def mark_verified(call_id: str, side: str) -> None:
    """Called by the telemetry WS handler when a side's own LivenessEngine reaches
    TRUSTED. Once both sides have reported in before the deadline, settles the call as
    verified immediately, rather than waiting out the rest of the window."""
    if side not in _SIDES:
        return
    state = _calls.get(call_id)
    if state is None or state.outcome is not None:
        return
    state.verified_sides.add(side)
    if len(state.verified_sides) == len(_SIDES):
        state.outcome = "verified"
        state.on_settled.set()


async def wait_for_outcome(call_id: str) -> str:
    """Blocks until the call's verification settles one way or the other (either both
    sides verified, or the 30s window expired). Used by signal.py before it will relay
    any offer/answer/ICE traffic for this call_id."""
    state = _calls.get(call_id)
    if state is None:
        return "failed"  # no verification window was ever started for this call_id
    await state.on_settled.wait()
    return state.outcome


def is_verified(call_id: str) -> bool:
    state = _calls.get(call_id)
    return state is not None and state.outcome == "verified"


def get_or_create_session_key(call_id: str) -> bytes | None:
    """The shared call session key (see crypto.generate_call_session_key), created once
    verification has actually settled as "verified" and cached for the rest of the call so
    both sides retrieving their wrapped copy get the SAME key. Returns None if the call
    isn't verified (nothing to hand out yet)."""
    state = _calls.get(call_id)
    if state is None or state.outcome != "verified":
        return None
    if state.session_key is None:
        from app.core.crypto import generate_call_session_key

        state.session_key = generate_call_session_key()
    return state.session_key


def clear(call_id: str) -> None:
    """Called once the call ends (or fails) -- drops all state for this call_id,
    including any session key material tied to it (see app/api/call_verify_key.py)."""
    _calls.pop(call_id, None)
