"""Tracks per-call pre-connect verification state: which side(s) of a call have reached
TRUSTED, and enforces the 30-second window the client requires -- if both sides aren't
verified within it, the call is marked failed and never gets to unlock signaling.

In-process, like the existing nonce store (backend/app/core/nonce.py) -- same free-tier,
single-instance assumption, same reasoning: state this short-lived doesn't need to survive
a process restart, and a restart only happens after 15 minutes of total inactivity anyway
(Render free tier), at which point no call was in flight.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

VERIFY_WINDOW_S = 30.0
_SIDES = ("caller", "callee")


class _CallVerification:
    def __init__(self):
        self.started_monotonic = time.monotonic()
        self.deadline_monotonic = self.started_monotonic + VERIFY_WINDOW_S
        self.verified_sides: set[str] = set()
        self.outcome: str | None = None  # None (pending) | "verified" | "failed"
        self.on_settled = asyncio.Event()  # set once outcome is no longer None
        self.session_key: bytes | None = None  # lazily generated, see get_or_create_session_key
        self.reports: dict[str, dict] = {}  # side -> latest liveness snapshot (see report())


_calls: dict[str, _CallVerification] = {}


def start_window(call_id: str) -> None:
    """Called once, when the callee accepts and both sides are about to start their
    liveness capture. Idempotent -- a second call for the same call_id is a no-op, so a
    client retry can't reset an already-running clock."""
    if call_id not in _calls:
        _calls[call_id] = _CallVerification()
        asyncio.create_task(_enforce_timeout(call_id))


def report(call_id: str, side: str, verdict: dict) -> None:
    """Keeps each side's latest liveness snapshot, so a failed call can say WHY it failed."""
    state = _calls.get(call_id)
    if state is None or side not in _SIDES:
        return
    state.reports[side] = {
        "motion": verdict.get("verdict"),
        "S_A": verdict.get("S_A"),
        "channel_a_pass": verdict.get("channel_a_pass"),
        "channel_b_pass": verdict.get("channel_b_pass"),
        "gyro_unavailable": verdict.get("gyro_unavailable"),
        "video_motion_pass": verdict.get("video_motion_pass"),
        "accel_motion_pass": verdict.get("accel_motion_pass"),
        "camera_blocked": verdict.get("camera_blocked"),
        "reflection_count": verdict.get("reflection_count"),
        "reflection_veto": verdict.get("reflection_veto"),
        "illum_confirmed": verdict.get("illum_confirmed"),
        "trust_state": verdict.get("trust_state"),
    }


def side_status(r: dict | None) -> str:
    """One side's outcome: 'verified' or the specific reason it did not verify."""
    if r is None:
        return "no_telemetry"                      # never sent liveness data (app closed / no network)
    if r.get("trust_state") == "TRUSTED":
        return "verified"
    if r.get("camera_blocked"):
        return "camera_blocked"                    # lens covered or black feed
    if r.get("reflection_count"):
        return "reflection_detected"               # screen / glossy-photo style flash response
    if r.get("gyro_unavailable"):
        # no gyro data from this phone: it needs visible camera movement AND a strong flash
        return "flash_not_confirmed" if (r.get("video_motion_pass") or r.get("accel_motion_pass")) else "no_motion"
    if not r.get("channel_a_pass"):
        return "no_motion"                         # Channel A: gyro/camera motion not correlated
    if not r.get("channel_b_pass"):
        return "flash_not_confirmed"               # Channel B: no face-like response to the flash
    return "timeout"


def sides_summary(reports: dict[str, dict]) -> str:
    """Stored on the call and shown in call history: "caller=no_motion,callee=verified"."""
    return ",".join(f"{side}={side_status(reports.get(side))}" for side in _SIDES)


def classify_failure(reports: dict[str, dict]) -> tuple[str, str]:
    """(reason, side) for the first side that did not verify, most specific cause first."""
    for side in _SIDES:
        r = reports.get(side)
        if r is None:
            return "no_telemetry", side            # never sent liveness data (app closed / no network)
        if r.get("trust_state") == "TRUSTED":
            continue
        if r.get("camera_blocked"):
            return "camera_blocked", side          # lens covered or black feed
        if r.get("reflection_count"):
            return "reflection_detected", side     # screen / glossy-photo style flash response
        if r.get("gyro_unavailable"):
            return ("flash_not_confirmed" if (r.get("video_motion_pass") or r.get("accel_motion_pass")) else "no_motion"), side
        if not r.get("channel_a_pass"):
            return "no_motion", side               # Channel A: gyro/camera motion not correlated
        if not r.get("channel_b_pass"):
            return "flash_not_confirmed", side     # Channel B: no face-like response to the flash
        return "timeout", side
    return "timeout", "unknown"


async def _set_call_state_in_db(call_id: str, state: str, failure_reason: str | None = None) -> None:
    """Updates the Call row directly, independent of whether either client ever connects
    to /ws/signal/{call_id} -- a call that fails verification is never supposed to reach
    signaling at all (that's the whole point of the gate), so signal.py's own
    _set_call_state can't be relied on to record FAILED_VERIFICATION; nothing else would
    ever flip Call.state off "VERIFYING", leaving fraud attempts permanently mislabeled in
    Call Logs."""
    try:
        from app.db.models import Call
        from app.db.session import async_session

        async with async_session() as db:
            call = await db.get(Call, call_id)
            if call is None:
                return
            call.state = state
            if failure_reason is not None:
                call.failure_reason = failure_reason
            await db.commit()
    except Exception:  # noqa: BLE001 -- call_id may not be a real Call row in ad-hoc testing
        logger.exception("failed to update Call %s to state %s", call_id, state)


async def _enforce_timeout(call_id: str) -> None:
    state = _calls[call_id]
    remaining = state.deadline_monotonic - time.monotonic()
    if remaining > 0:
        await asyncio.sleep(remaining)
    if state.outcome is None:
        state.outcome = "failed"
        state.on_settled.set()
        reason, side = classify_failure(state.reports)
        logger.warning(
            "VERIFICATION_RESULT call=%s outcome=failed reason=%s side=%s window_s=%s sides=%s",
            call_id, reason, side, VERIFY_WINDOW_S, state.reports,
        )
        await _set_call_state_in_db(call_id, "FAILED_VERIFICATION", failure_reason=sides_summary(state.reports))


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
    logger.info("VERIFIED call=%s side=%s after=%.1fs", call_id, side, time.monotonic() - state.started_monotonic)
    if len(state.verified_sides) == len(_SIDES):
        state.outcome = "verified"
        state.on_settled.set()
        logger.info(
            "VERIFICATION_RESULT call=%s outcome=verified both_channels=%s sides=%s",
            call_id, all(r.get("channel_a_pass") and r.get("channel_b_pass") for r in state.reports.values()), state.reports,
        )
        asyncio.create_task(_set_call_state_in_db(call_id, "VERIFIED", failure_reason=sides_summary(state.reports)))


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
