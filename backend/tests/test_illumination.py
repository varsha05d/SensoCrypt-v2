"""Synthetic checks for app/liveness/illumination.py.

These prove the logic separates the physical signatures it is built on (diffuse spread vs.
compact/clipped hot spot, skin-like vs. neutral colour response). They do NOT prove the
thresholds hold on real phone captures -- tune against genuine/replay recordings.
"""

import numpy as np

from app.liveness import challenge as challenge_mod
from app.liveness import illumination

H, W = 120, 160
FPS = 15


def _challenge():
    c = challenge_mod.generate_challenge(0)
    c["start_at_ns"] = 0
    return c


_CHALLENGE = _challenge()


def _frames(challenge, response):
    """response(flash_rgb, base_frame) -> frame; builds the observed frames for a challenge."""
    rng = np.random.default_rng(0)
    out = []
    total_ms = sum(s["dur_ms"] for s in challenge["states"])
    t = 0.0
    while t < total_ms:
        flash = illumination._expected_rgb_at(challenge, t * 1e6)
        base = np.full((H, W, 3), 70.0) + rng.normal(0, 1.0, (H, W, 3))
        frame = response(flash, base)
        out.append((int(t * 1e6), np.clip(frame, 0, 255).astype(np.uint8)))
        t += 1000 / FPS
    return out


def _face(flash, base):
    # ~15 cells of smooth, skin-toned response: red reflected strongly, blue weakly.
    yy, xx = np.mgrid[0:H, 0:W]
    blob = np.exp(-(((yy - 60) / 38.0) ** 2 + ((xx - 80) / 45.0) ** 2))
    albedo = np.array([0.30, 0.20, 0.10])  # R, G, B
    delta = blob[:, :, None] * (flash * albedo)[None, None, :]
    return (base + delta)[:, :, ::-1]


def _mirror(flash, base):
    # Compact hot spot, neutral colour, drives into clipping on bright flashes.
    out = base.copy()
    out[40:60, 70:95, :] += flash[None, None, :] * 1.0
    return out[:, :, ::-1]


def test_diffuse_face_like_response_passes():
    res = illumination.analyze(_frames(_CHALLENGE, _face), _CHALLENGE)
    assert res["verdict"] == "ok", res
    assert res["diffuse_ok"], res
    assert res["S_illum"] > 0.5, res


def test_small_mirror_patch_is_never_confirmed():
    # Too small to move the frame's overall brightness: reported as "no variation" (not as
    # a reflection -- glasses lenses look like this on genuine users), but never confirmed.
    res = illumination.analyze(_frames(_CHALLENGE, _mirror), _CHALLENGE)
    assert res["verdict"] in ("no_variation", "reflection"), res
    assert res["S_illum"] == 0.0


def test_flat_scene_is_no_variation():
    flat = lambda flash, base: base[:, :, ::-1]
    res = illumination.analyze(_frames(_CHALLENGE, flat), _CHALLENGE)
    assert res["verdict"] == "no_variation", res



# --- regression matrix: genuine users must not be rejected, attacks must not be confirmed ---

def _confirmed(res):
    return res["verdict"] == "ok" and res.get("diffuse_ok") and res["S_illum"] >= 0.5


def _face_sized(sy, sx, peak):
    def f(flash, base):
        yy, xx = np.mgrid[0:H, 0:W]
        blob = np.exp(-(((yy - 60) / sy) ** 2 + ((xx - 80) / sx) ** 2))
        return (base + blob[:, :, None] * (flash * np.array([peak, peak * 0.66, peak * 0.33]))[None, None, :])[:, :, ::-1]
    return f


def _mirror_sized(h, w, gain):
    def f(flash, base):
        out = base.copy()
        y0, x0 = 60 - h // 2, 80 - w // 2
        out[y0:y0 + h, x0:x0 + w, :] += flash[None, None, :] * gain
        return out[:, :, ::-1]
    return f


def _fresh():
    c = challenge_mod.generate_challenge(0)
    c["start_at_ns"] = 0
    return c


def test_genuine_face_at_armslength_is_not_rejected():
    # ~6-8 responding cells: what a genuine user holding the phone at a normal distance gets.
    for _ in range(3):
        c = _fresh()
        res = illumination.analyze(_frames(c, _face_sized(20, 24, 0.8)), c)
        assert not res.get("reflection"), res
        assert _confirmed(res), res


def test_clipped_glare_patch_is_vetoed_whatever_its_size():
    for h, w in ((40, 50), (70, 90)):
        c = _fresh()
        res = illumination.analyze(_frames(c, _mirror_sized(h, w, 1.0)), c)
        assert res["verdict"] == "reflection" and res["reflection_reason"] == "clipping", res


def test_weak_unclipped_neutral_reflection_is_never_confirmed():
    for _ in range(3):
        c = _fresh()
        res = illumination.analyze(_frames(c, _mirror_sized(40, 50, 0.5)), c)
        assert not _confirmed(res), res


def test_empty_or_tiny_input_is_handled():
    c = _fresh()
    assert illumination.analyze([], c)["verdict"] == "insufficient_frames"
    one = [(0, np.zeros((H, W, 3), np.uint8))]
    assert illumination.analyze(one, c)["verdict"] == "insufficient_frames"


def test_frames_of_a_different_size_are_resized_not_crashed():
    c = _fresh()
    big = [(t, np.kron(f, np.ones((2, 2, 1), np.uint8))) for t, f in _frames(c, _face_sized(38, 45, 0.3))]
    res = illumination.analyze(big, c)
    assert res["verdict"] in ("ok", "no_variation", "reflection")
