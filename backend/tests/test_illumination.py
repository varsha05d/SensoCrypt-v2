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


def test_mirror_hotspot_is_flagged_as_reflection():
    res = illumination.analyze(_frames(_CHALLENGE, _mirror), _CHALLENGE)
    assert res["verdict"] == "reflection", res
    assert res["S_illum"] == 0.0


def test_flat_scene_is_no_variation():
    flat = lambda flash, base: base[:, :, ::-1]
    res = illumination.analyze(_frames(_CHALLENGE, flat), _CHALLENGE)
    assert res["verdict"] == "no_variation", res

