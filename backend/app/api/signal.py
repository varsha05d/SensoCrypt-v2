"""WebRTC signaling relay: forwards SDP offer/answer, ICE candidates, and liveness verdicts
between exactly two peers in the same call. No media or verdict content is inspected or
persisted here -- it's a pure relay; each peer signs its own verdict on its own end.

v2 change from the original SensoCrypt: connecting here no longer immediately starts
negotiation. It first waits on app.core.call_coordinator's outcome for this call_id (both
sides' pre-connect liveness verification, or the 30s window expiring) -- the client's
explicit requirement that "only when verified must the call connect to both parties." A
call that fails verification never reaches the offer/answer/ICE exchange at all.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core import call_coordinator
from app.db.models import Call
from app.db.session import async_session

logger = logging.getLogger(__name__)
router = APIRouter()

_rooms: dict[str, list[WebSocket]] = {}
# Once either side explicitly ends a call, or it fails verification, the call_id is retired
# so it can't be rejoined/replayed.
_ended_calls: set[str] = set()


async def _set_call_state(call_id: str, state: str, **fields) -> None:
    try:
        call_uuid = call_id
        async with async_session() as db:
            call = await db.get(Call, call_uuid)
            if call is None:
                return
            call.state = state
            for k, v in fields.items():
                setattr(call, k, v)
            await db.commit()
    except Exception:  # noqa: BLE001 -- call_id may not be a real Call row in ad-hoc testing
        logger.exception("failed to update Call %s to state %s", call_id, state)


@router.websocket("/ws/signal/{call_id}")
async def signal_ws(ws: WebSocket, call_id: str):
    await ws.accept()

    if call_id in _ended_calls:
        await ws.close(code=4410)  # call has already ended or failed verification
        return

    # Blocks (up to the 30s verification window) until both sides have proven liveness, or
    # the window expires. This is the server-side enforcement of "only when verified must
    # the call connect" -- a client that skips the verification WS entirely and connects
    # straight here gets nothing relayed, because no verification window will ever have
    # been started for this call_id (wait_for_outcome returns "failed" immediately when
    # call_coordinator has no record of this call_id at all).
    outcome = await call_coordinator.wait_for_outcome(call_id)
    if outcome != "verified":
        _ended_calls.add(call_id)
        await _set_call_state(call_id, "FAILED_VERIFICATION")
        await ws.close(code=4412)  # verification failed or timed out -- see Call Logs
        return

    peers = _rooms.setdefault(call_id, [])
    if len(peers) >= 2:
        await ws.close(code=4409)  # call already has two peers
        return
    peers.append(ws)

    # Tell both sides once the room actually has two peers -- otherwise whichever peer
    # joins first can send its SDP offer into an empty room before the second peer ever
    # connects, and that offer is just lost (nothing replays it to a late joiner). Both
    # peers run identical client code, so without an explicit role assignment BOTH would
    # try to become the offerer simultaneously (WebRTC "glare"). Whoever joined first
    # (peers[0]) is the offerer; the second peer only answers.
    if len(peers) == 2:
        await peers[0].send_text('{"type":"ready","role":"offerer"}')
        await peers[1].send_text('{"type":"ready","role":"answerer"}')
        await _set_call_state(call_id, "CONNECTED")

    try:
        while True:
            msg = await ws.receive_text()
            for peer in peers:
                if peer is not ws:
                    await peer.send_text(msg)
            try:
                msg_type = json.loads(msg).get("type")
            except (json.JSONDecodeError, AttributeError):
                msg_type = None
            if msg_type == "end":
                _ended_calls.add(call_id)
                call_coordinator.clear(call_id)
                await _set_call_state(call_id, "ENDED")
                break
    except WebSocketDisconnect:
        pass
    finally:
        if ws in peers:
            peers.remove(ws)
        if not peers:
            _rooms.pop(call_id, None)
