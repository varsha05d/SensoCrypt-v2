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


def _run(response, gray_only=False, seconds=30, seed=0):
    rng = np.random.default_rng(seed)
    engine = LivenessEngine(quick=True)
    challenges = []
    states, last = [], {}
    t = 10_000_000_000
    t_end = t + int(seconds * 1e9)
    frames_per_chunk = 5
    while t < t_end:
        chunk_frames = []
        for _ in range(frames_per_chunk):
            flash = np.zeros(3)
            for c in challenges:
                e = il._expected_rgb_at(c, t - c["start_at_ns"])
                if e is not None:
                    flash = e
            base = np.full((H, W, 3), 70.0) + rng.normal(0, 1.0, (H, W, 3))
            rgb = np.clip(response(flash, base), 0, 255).astype(np.uint8)
            bgr = rgb[:, :, ::-1]
            if gray_only:
                g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                bgr = cv2.merge([g, g, g])
            ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 60])
            assert ok
            chunk_frames.append({"t_ns": t, "jpeg_bytes": jpg.tobytes()})
            t += STEP_NS
        result = engine.ingest({"frames": chunk_frames, "imu": {"gyro": []}})
        if "new_challenge" in result:
            challenges.append(result["new_challenge"])
        json.dumps(result)  # the verdict goes to the phone as JSON: no numpy types allowed
        states.append(result.get("trust_state"))
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
