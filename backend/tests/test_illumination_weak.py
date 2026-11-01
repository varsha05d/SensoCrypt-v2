"""Weak flash responses and camera auto-exposure.

Regression for "illumination sometimes doesn't work": a fixed whole-frame brightness floor discarded
genuine but weak face responses (0/24 confirmed for a face half as strong as the test fixture), and a
camera whose auto-exposure over-corrects cancels the whole-frame average of a real face response.
"""

import numpy as np

from app.liveness import illumination as il
from tests.test_illumination import H, W, _fresh
from tests.test_liveness_engine_illumination import TEXTURE, _run

FPS = 12


def _frames_with_ae(resp, tau_ms, strength, seed, challenge):
    """Observed BGR frames of `resp`, with a first-order auto-exposure (strength 0 = none)."""
    rng = np.random.default_rng(seed)
    out, gain, dt = [], 1.0, 1000 / FPS
    total = sum(s["dur_ms"] for s in challenge["states"])
    t = 0.0
    while t < total:
        flash = il._expected_rgb_at(challenge, t * 1e6)
        true = np.clip(resp(flash, TEXTURE + rng.normal(0, 1, (H, W, 3))), 0, 255)
        desired = (110.0 / max(float((true * gain).mean()), 1.0)) ** strength
        gain += (desired - gain) * (1 - np.exp(-dt / tau_ms))
        out.append((int(t * 1e6), np.clip(true * gain, 0, 255).astype(np.uint8)[:, :, ::-1].copy()))
        t += dt
    return out


def _face(peak):
    def f(flash, base):
        yy, xx = np.mgrid[0:H, 0:W]
        blob = np.exp(-(((yy - 60) / 30.0) ** 2 + ((xx - 80) / 36.0) ** 2))
        return base + blob[:, :, None] * (flash * np.array([peak, peak * 0.66, peak * 0.33]))[None, None, :]
    return f


def _confirmed(res):
    return res["verdict"] == "ok" and res.get("diffuse_ok") and res["S_illum"] >= 0.4


def test_a_weak_but_genuine_face_response_is_confirmed():
    for peak in (0.15, 0.08, 0.05):
        for seed in range(6):
            c = _fresh()
            res = il.analyze(_frames_with_ae(_face(peak), 1, 0.0, seed, c), c)
            assert _confirmed(res), (peak, seed, res)
            if peak <= 0.08:   # clearly below the old fixed brightness floor: this is exactly the case that used to fail
                assert res["weak_regime"] is True


def test_an_auto_exposure_that_over_corrects_does_not_hide_a_real_face():
    for peak in (0.30, 0.15, 0.08):
        for seed in range(6):
            c = _fresh()
            res = il.analyze(_frames_with_ae(_face(peak), 80, 1.2, seed, c), c)
            assert _confirmed(res), (peak, seed, res)
            assert res["verdict"] != "reflection"


def test_no_flash_response_is_never_confirmed_even_though_the_floor_is_lower():
    flat = lambda flash, base: base  # noqa: E731
    for seed in range(12):
        c = _fresh()
        for tau, strength in ((1, 0.0), (150, 1.0)):
            res = il.analyze(_frames_with_ae(flat, tau, strength, seed, c), c)
            assert not _confirmed(res), (seed, tau, res)


def test_a_flat_neutral_screen_is_still_flagged_when_weak():
    def screen(flash, base):
        out = base.copy()
        out[12:108, 16:144, :] += flash[None, None, :] * 0.05
        return out

    for seed in range(6):
        c = _fresh()
        res = il.analyze(_frames_with_ae(screen, 1, 0.0, seed, c), c)
        assert not _confirmed(res), res


def test_a_weak_face_verifies_a_still_device_through_the_engine():
    peak = 0.08

    def weak_face(flash, base):
        yy, xx = np.mgrid[0:H, 0:W]
        blob = np.exp(-(((yy - 60) / 30.0) ** 2 + ((xx - 80) / 36.0) ** 2))
        return (base + blob[:, :, None] * (flash * np.array([peak, peak * 0.66, peak * 0.33]))[None, None, :])

    from tests.test_liveness_engine_illumination import STILL
    engine, states, last, _ = _run(weak_face, motion=STILL, accel="still")
    assert "TRUSTED" in states and last["verified_via"] == "illumination", last["verified_via"]
