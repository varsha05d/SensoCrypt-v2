"""End-to-end: LivenessEngine fed JPEG chunks like the phone sends, through challenge
scheduling, illumination scoring, the reflection veto and the TRUSTED gate.

Synthetic frames -> proves the plumbing and the gate, not the real-world thresholds.
"""

import json

import cv2
import numpy as np

from app.liveness import illumination as il
from app.liveness.worker import LivenessEngine

H, W, FPS = 120, 160, 15
STEP_NS = int(1e9 / FPS)


# A real room has structure; a perfectly uniform backdrop would look like a covered lens.
TEXTURE = 70.0 + np.repeat(
    cv2.GaussianBlur(np.random.default_rng(99).normal(0, 1, (H, W)).astype(np.float32), (0, 0), 3)[:, :, None] * 150.0, 3, axis=2
)


def _drifted(px):
    """The room shifted by px pixels: the camera (phone) moved. px=0 is a phone on a table."""
    if px == 0:
        return TEXTURE
    return cv2.warpAffine(TEXTURE.astype(np.float32), np.float32([[1, 0, px], [0, 1, px * 0.5]]), (W, H), borderMode=cv2.BORDER_REFLECT)


def _face(flash, base):
    yy, xx = np.mgrid[0:H, 0:W]
    blob = np.exp(-(((yy - 60) / 38.0) ** 2 + ((xx - 80) / 45.0) ** 2))
    return base + blob[:, :, None] * (flash * np.array([0.30, 0.20, 0.10]))[None, None, :]


def _mirror(flash, base):
    out = base.copy()
    out[30:75, 60:110, :] += flash[None, None, :] * 1.0  # large bright patch, clips on white
    return out


def _flat(flash, base):
    return base


def _run(response, gray_only=False, seconds=30, seed=0, strict=False, motion=None, drift=0.0, accel=None):  # noqa: C901
    """strict=False: either-channel trust (these tests exercise the flash channel alone).
    motion: optional dict returned in place of the gyro-vs-camera score, i.e. a fake Channel A."""
    rng = np.random.default_rng(seed)
    engine = LivenessEngine(quick=True, strict=strict)
    if motion is not None:
        engine._score_motion = lambda: dict(motion)
    engine.seen_color = set()  # every frame timestamp that was ever decoded in colour
    challenges = []
    states, last, passes = [], {}, []
    frame_no = 0
    engine.passes = passes
    t = 10_000_000_000
    t_end = t + int(seconds * 1e9)
    frames_per_chunk = 5
    while t < t_end:
        chunk_frames = []
        chunk_start = t
        for _ in range(frames_per_chunk):
            flash = np.zeros(3)
            for c in challenges:
                e = il._expected_rgb_at(c, t - c["start_at_ns"])
                if e is not None:
                    flash = e
            base = _drifted(frame_no * drift) + rng.normal(0, 1.0, (H, W, 3))
            frame_no += 1
            rgb = np.clip(response(flash, base), 0, 255).astype(np.uint8)
            bgr = rgb[:, :, ::-1]
            if gray_only:
                g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                bgr = cv2.merge([g, g, g])
            ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 60])
            assert ok
            chunk_frames.append({"t_ns": t, "jpeg_bytes": jpg.tobytes()})
            t += STEP_NS
        # A phone that has a gyro always streams samples (values are irrelevant when Channel A
        # is faked via `motion`); no `motion` = the old gyro-less client.
        gyro = [[t_ns_ + 0, 0.0, 0.0, 0.0] for t_ns_ in (cf["t_ns"] for cf in chunk_frames)] if motion is not None else []
        imu = {"gyro": gyro}
        if accel is not None:   # "still": resting on a table; "moving": hand tremor -- a device with no gyro but an accelerometer
            t_a = chunk_start
            samples = []
            while t_a < t:
                sec = t_a / 1e9
                amp = 0.12 if accel == "moving" else 0.0
                samples.append([t_a, amp * np.sin(2 * np.pi * 3.0 * sec) + rng.normal(0, 0.01),
                                0.6 * amp * np.sin(2 * np.pi * 3.9 * sec + 1) + rng.normal(0, 0.01), 9.81 + rng.normal(0, 0.01)])
                t_a += int(1e9 / 50)
            imu["accel"] = samples
        result = engine.ingest({"frames": chunk_frames, "imu": imu})
        if "new_challenge" in result:
            challenges.append(result["new_challenge"])
        engine.seen_color.update(t_ for t_, _ in engine.color_frames)
        json.dumps(result)  # the verdict goes to the phone as JSON: no numpy types allowed
        states.append(result.get("trust_state"))
        passes.append((result.get("channel_a_pass"), result.get("channel_b_pass")))
        last = result
    return engine, states, last, challenges


def test_real_face_gets_confirmed_and_trusted():
    engine, states, last, challenges = _run(_face)
    assert challenges, "engine must issue challenges"
    assert engine.illum_confirmed
    assert engine.reflection_count == 0
    assert "TRUSTED" in states, states


def test_mirror_reflection_is_never_trusted():
    engine, states, last, challenges = _run(_mirror)
    assert engine.reflection_count >= 1, last
    assert not engine.illum_confirmed
    assert "TRUSTED" not in states, states


def test_flat_scene_is_never_trusted():
    engine, states, last, challenges = _run(_flat)
    assert not engine.illum_confirmed
    assert "TRUSTED" not in states, states


def test_old_gray_only_client_still_verifies_a_real_face():
    engine, states, last, _ = _run(_face, gray_only=True)
    assert engine.illum_confirmed
    assert "TRUSTED" in states, states


def test_a_reflection_after_a_clean_pass_revokes_confirmation():
    engine = LivenessEngine(quick=True)
    engine.illum_confirmed = True
    engine._apply_illumination_verdict({"reflection": True, "verdict": "reflection"}, 1_000_000_000)
    assert not engine.illum_confirmed
    assert engine.reflection_until_ns is not None
    assert engine.trust_fsm.state == "SUSPECT"


def test_inconclusive_illumination_does_not_block_trust():
    # Flash swamped by ambient light / camera auto-exposure fighting the flash / challenge
    # skipped: none of these is evidence of an attack, so motion evidence may carry trust.
    e = LivenessEngine(quick=True)
    assert e._allow_trust(1_000_000_000)
    e._apply_illumination_verdict({"verdict": "no_variation", "S_illum": 0.0}, 1_000_000_000)
    assert e._allow_trust(2_000_000_000)
    e._apply_illumination_verdict({"verdict": "ok", "diffuse_ok": True, "S_illum": 0.0, "r": -0.9}, 3_000_000_000)
    assert e._allow_trust(4_000_000_000)          # weak/negative response: still allowed


def test_a_reflection_blocks_trust():
    e = LivenessEngine(quick=True)
    e._apply_illumination_verdict({"verdict": "reflection", "reflection": True}, 1_000_000_000)
    assert not e._allow_trust(2_000_000_000)
    assert not e._allow_trust(1_000_000_000 + 7_000_000_000)    # still inside the 8s veto
    assert e._allow_trust(1_000_000_000 + 9_000_000_000)        # expired: a false alarm cannot eat the 30 s window
    assert not e.illum_confirmed


def test_colour_is_only_decoded_around_a_challenge_window():
    engine, states, last, challenges = _run(_face, seconds=20)
    assert challenges and len(engine.seen_color) > 0 and len(engine.frames) > 0

    def near_a_challenge(t):
        for c in challenges:
            end = c["start_at_ns"] + int(sum(st["dur_ms"] for st in c["states"]) * 1e6)
            if c["start_at_ns"] - 0.4e9 <= t <= end + 0.4e9:
                return True
        return False

    assert all(near_a_challenge(t) for t in engine.seen_color)
    # ...and plain gray frames exist outside those windows, so motion scoring is unaffected
    assert any(not near_a_challenge(t) for t, _ in engine.frames)


# --- covered / dead camera must never verify ---

def _covered(flash, base):
    # Lens under a finger: almost featureless and dim; a little flash light leaks through.
    return np.full((H, W, 3), 25.0) + flash[None, None, :] * 0.05


def _black(flash, base):
    return np.zeros((H, W, 3))


def _gyro_chunks_run(response, seconds=30):
    """Like _run but the phone is also being moved, so the motion channel has input."""
    engine, states, last, _ = _run(response, seconds=seconds)
    return engine, states, last


def test_covered_camera_is_never_trusted():
    engine, states, last = _gyro_chunks_run(_covered)
    assert "TRUSTED" not in states, states
    assert last.get("camera_blocked") is True


def test_black_feed_is_never_trusted():
    engine, states, last = _gyro_chunks_run(_black)
    assert "TRUSTED" not in states, states


def test_weak_flash_response_alone_is_not_trusted():
    # A 0.17-ish illumination score (what a covered lens leaked) must not reach TRUSTED.
    e = LivenessEngine(quick=True)
    e.last_illum_score = {"verdict": "ok", "diffuse_ok": True, "S_illum": 0.17}
    e.last_illum_score_ns = 1_000_000_000
    for i in range(20):
        frame = np.random.default_rng(i).integers(0, 255, (H, W), dtype=np.uint8)
        ok, jpg = cv2.imencode(".jpg", frame)
        res = e.ingest({"frames": [{"t_ns": 1_000_000_000 + i * STEP_NS, "jpeg_bytes": jpg.tobytes()}], "imu": {"gyro": []}})
    assert res.get("trust_state") != "TRUSTED", res


# --- the test-case matrix (TC04-TC06) at engine level -------------------------------------

MOVING = {"verdict": "ok", "S_A": 0.45, "r": 0.7, "energy": 0.02}
STILL = {"verdict": "no_evidence", "reason": "insufficient optical flow"}


def _flat_neutral_screen(flash, base):
    out = base.copy()
    out[12:108, 16:144, :] += flash[None, None, :] * 0.25  # even, colour-neutral: a lit screen
    return out


def test_tc05_still_phone_with_a_live_face_is_not_trusted():
    # Channel A has nothing (phone held completely still) -> strict trust is withheld even
    # though the flash channel alone is satisfied by a real face.
    engine, states, last, _ = _run(_face, strict=True, motion=STILL)
    assert engine.illum_confirmed, "flash channel should pass for a live face"
    assert not any(a for a, _ in engine.passes) and any(b for _, b in engine.passes)
    assert "TRUSTED" not in states, states


def test_tc06_both_channels_passing_is_trusted():
    engine, states, last, _ = _run(_face, strict=True, motion=MOVING)
    assert any(a and b for a, b in engine.passes), engine.passes[-5:]
    assert "TRUSTED" in states, states


def test_tc04_photo_on_a_screen_moved_by_hand_is_not_trusted():
    # Moving phone satisfies Channel A (a photo shifts in frame like a face would), so the
    # flash channel is what has to stop it: a flat, colour-neutral response is a screen.
    engine, states, last, _ = _run(_flat_neutral_screen, strict=True, motion=MOVING)
    assert any(a for a, _ in engine.passes)
    assert not any(b for _, b in engine.passes)
    assert "TRUSTED" not in states, states


def test_tc04_photo_with_a_still_phone_fails_both_channels():
    engine, states, last, _ = _run(_flat_neutral_screen, strict=True, motion=STILL)
    assert not any(a for a, _ in engine.passes) and not any(b for _, b in engine.passes)
    assert "TRUSTED" not in states, states


def test_either_channel_mode_still_available():
    engine, states, last, _ = _run(_face, strict=False, motion=STILL)
    assert "TRUSTED" in states, states


# --- the two real-phone situations from the production logs -------------------------------

def test_phone_without_a_gyro_verifies_when_it_moves_and_the_flash_is_confirmed():
    # callee in the logs: no gyro samples at all. Visible camera movement + a strong flash verifies.
    engine, states, last, _ = _run(_face, strict=True, motion=None, drift=0.6)
    assert last["gyro_unavailable"] is True
    assert "TRUSTED" in states, states


def test_phone_without_a_gyro_that_does_not_move_is_never_trusted():
    # Reported bug: a phone that "didn't move a bit" still verified. No gyro + no visible
    # movement must fail even though the flash is a perfect face response.
    engine, states, last, _ = _run(_face, strict=True, motion=None, drift=0.0)
    assert last["gyro_unavailable"] is True and last["video_motion_pass"] is False
    assert engine.illum_confirmed, "the flash alone is fine; movement is what is missing"
    assert "TRUSTED" not in states, states


def test_phone_without_a_gyro_is_not_trusted_on_a_screen():
    engine, states, last, _ = _run(_flat_neutral_screen, strict=True, motion=None, drift=0.6)
    assert "TRUSTED" not in states, states


def _weak_flash_engine(motion):
    """Caller in the logs: gyro channel strong, flash response weak (S_illum 0.21)."""
    e = LivenessEngine(quick=True, strict=True)
    e._score_motion = lambda: dict(motion)
    t0 = 10_000_000_000
    e.first_frame_ns = t0
    e._apply_illumination_verdict({"verdict": "ok", "diffuse_ok": True, "S_illum": 0.21, "r": 0.6}, t0)
    states = []
    for i in range(30):
        frame = TEXTURE[:, :, 0] + np.random.default_rng(i).normal(0, 1, (H, W))
        ok, jpg = cv2.imencode(".jpg", np.clip(frame, 0, 255).astype(np.uint8))
        t = t0 + (i + 1) * STEP_NS
        res = e.ingest({"frames": [{"t_ns": t, "jpeg_bytes": jpg.tobytes()}], "imu": {"gyro": [[t, 0.0, 0.0, 0.0]]}})
        states.append(res.get("trust_state"))
    return states


def test_moving_phone_with_a_weak_but_face_like_flash_is_trusted():
    assert "TRUSTED" in _weak_flash_engine(MOVING)


def test_still_phone_with_gyro_and_a_weak_flash_is_not_trusted():
    assert "TRUSTED" not in _weak_flash_engine(STILL)


def test_a_later_clean_strong_flash_clears_an_earlier_reflection_alarm():
    e = LivenessEngine(quick=True)
    e._apply_illumination_verdict({"verdict": "reflection", "reflection": True}, 1_000_000_000)
    assert not e._allow_trust(2_000_000_000)
    e._apply_illumination_verdict({"verdict": "ok", "diffuse_ok": True, "S_illum": 0.8}, 3_000_000_000)
    assert e._allow_trust(3_500_000_000)


def test_phone_without_gyro_is_recognised_after_a_short_grace():
    e = LivenessEngine(quick=True, strict=True)
    frame = TEXTURE[:, :, 0].astype(np.uint8)
    ok, jpg = cv2.imencode(".jpg", frame)
    t0 = 10_000_000_000
    res = None
    for i in range(40):
        res = e.ingest({"frames": [{"t_ns": t0 + i * STEP_NS, "jpeg_bytes": jpg.tobytes()}], "imu": {"gyro": []}})
        if i == 10:                                   # ~0.7 s in: too early to call it
            assert res["gyro_unavailable"] is False
    assert res["gyro_unavailable"] is True            # ~2.6 s in


def test_real_still_phone_video_motion_readings_never_count_as_movement():
    # video_motion values logged by production for a phone that "had no movement at all"
    from app.liveness.worker import VIDEO_MOTION_MIN_PASSES, VIDEO_MOTION_MIN_PX, VIDEO_MOTION_RECENT

    real_still = [0.073, 0.038, 0.061, 0.058, 0.133, 0.061, 0.045, 0.091, 0.038, 0.089,
                  0.108, 0.065, 0.034, 0.038, 0.099, 0.033, 0.031, 0.055]
    assert max(real_still) < VIDEO_MOTION_MIN_PX
    for i in range(len(real_still) - VIDEO_MOTION_RECENT + 1):
        window = real_still[i:i + VIDEO_MOTION_RECENT]
        assert sum(v >= VIDEO_MOTION_MIN_PX for v in window) < VIDEO_MOTION_MIN_PASSES


# --- devices with no gyro but an accelerometer (e.g. a tablet) -----------------------------

def test_tablet_with_accelerometer_verifies_on_slight_movement_without_camera_motion():
    engine, states, last, _ = _run(_face, strict=True, motion=None, drift=0.0, accel="moving")
    assert last["gyro_unavailable"] is True and last["accel_motion_pass"] is True
    assert last["video_motion_pass"] is False, "movement came from the accelerometer, not the picture"
    assert "TRUSTED" in states, states


def test_tablet_resting_on_a_table_is_not_trusted():
    engine, states, last, _ = _run(_face, strict=True, motion=None, drift=0.0, accel="still")
    assert last["gyro_unavailable"] is True and last["accel_motion_pass"] is False
    assert engine.illum_confirmed
    assert "TRUSTED" not in states, states


def test_tablet_that_moves_but_shows_a_screen_is_not_trusted():
    engine, states, last, _ = _run(_flat_neutral_screen, strict=True, motion=None, drift=0.0, accel="moving")
    assert "TRUSTED" not in states, states


# --- default policy: ANY of gyro / accelerometer / illumination verifies, and says which ---

def _flat_scene(flash, base):
    return base  # the flash leaves no trace on the picture


def test_gyro_alone_verifies_and_is_reported_as_gyro():
    engine, states, last, _ = _run(_flat_scene, motion=MOVING, accel="still")
    assert "TRUSTED" in states and last["verified_via"] == "gyro", last["verified_via"]
    assert last["passes"]["illumination"] is False and last["passes"]["accelerometer"] is False


def test_accelerometer_alone_verifies_and_is_reported_as_accelerometer():
    # the device has a gyro that reads nothing (still), but it is physically being moved
    engine, states, last, _ = _run(_flat_scene, motion=STILL, accel="moving")
    assert "TRUSTED" in states and last["verified_via"] == "accelerometer", last["verified_via"]


def test_illumination_alone_verifies_and_is_reported_as_illumination():
    # a real face under the flash on a device that is not moving at all
    engine, states, last, _ = _run(_face, motion=STILL, accel="still")
    assert "TRUSTED" in states and last["verified_via"] == "illumination", last["verified_via"]
    assert last["passes"]["gyro"] is False and last["passes"]["accelerometer"] is False


def test_all_three_failing_is_not_verified():
    engine, states, last, _ = _run(_flat_scene, motion=STILL, accel="still")
    assert "TRUSTED" not in states and last["verified_via"] is None
    assert not any(last["passes"].values())


def test_a_screen_blocks_even_a_moving_device():
    # laptop-screen case: the gyro would pass, but the flash shows a flat neutral screen
    engine, states, last, _ = _run(_flat_neutral_screen, motion=MOVING, accel="moving")
    assert engine.reflection_count >= 1
    assert "TRUSTED" not in states and last["verified_via"] is None, states


def test_a_covered_camera_blocks_even_a_moving_device():
    engine, states, last, _ = _run(_covered, motion=MOVING, accel="moving")
    assert last["camera_blocked"] is True
    assert "TRUSTED" not in states, states


def test_the_reported_reason_matches_the_call_log_status():
    from app.core.call_coordinator import side_status

    assert side_status({"trust_state": "TRUSTED", "verified_via": "accelerometer"}) == "verified_accelerometer"
    assert side_status({"trust_state": "TRUSTED", "verified_via": "gyro"}) == "verified_gyro"
    assert side_status({"trust_state": "TRUSTED", "verified_via": "illumination"}) == "verified_illumination"
    assert side_status({"trust_state": "SUSPECT", "reflection_count": 1}) == "reflection_detected"
    assert side_status({"trust_state": "DEGRADED", "camera_blocked": True}) == "camera_blocked"
    assert side_status({"trust_state": "DEGRADED"}) == "no_evidence"
