"""Laptop-screen cues: flat flash response, neutral colour, and planarity (parallax).

Synthetic only -- thresholds must be tuned against real captures (the verdict JSON logs
gain_cv / blue_red_ratio / parallax_ratio for that).
"""

import cv2
import numpy as np

from app.liveness import illumination, parallax
from tests.test_illumination import H, W, _face_sized, _fresh, _frames


def _screen(gain, albedo=(1.0, 1.0, 1.0)):
    """Large evenly-lit patch: what a laptop screen returns, covering most of the frame."""
    def f(flash, base):
        out = base.copy()
        out[12:108, 16:144, :] += flash[None, None, :] * np.array(albedo) * gain
        return out[:, :, ::-1]
    return f


def _uneven_face(neutral):
    """Bright centre falling off to the edges (curved face); optionally colour-neutral."""
    peak = 0.30
    albedo = (peak, peak, peak) if neutral else (peak, peak * 0.66, peak * 0.33)

    def f(flash, base):
        yy, xx = np.mgrid[0:H, 0:W]
        blob = np.exp(-(((yy - 60) / 30.0) ** 2 + ((xx - 80) / 36.0) ** 2))
        return (base + blob[:, :, None] * (flash * np.array(albedo))[None, None, :])[:, :, ::-1]
    return f


def test_flat_neutral_screen_is_vetoed():
    c = _fresh()
    res = illumination.analyze(_frames(c, _screen(0.25)), c)
    assert res["verdict"] == "reflection", res
    assert res["reflection_reason"] == "flat_neutral", res


def test_flat_but_skin_coloured_is_not_vetoed():
    c = _fresh()
    res = illumination.analyze(_frames(c, _screen(0.25, (1.0, 0.66, 0.33))), c)
    assert res["verdict"] == "ok" and res["cue_uniform"] and not res["cue_neutral"], res


def test_uneven_neutral_face_is_not_vetoed():
    # Phone AWB can neutralise skin; an uneven (curved) response must still pass.
    for _ in range(3):
        c = _fresh()
        res = illumination.analyze(_frames(c, _uneven_face(neutral=True)), c)
        assert res["verdict"] == "ok" and not res["cue_uniform"], res


def test_uneven_skin_face_still_confirmed():
    for _ in range(3):
        c = _fresh()
        res = illumination.analyze(_frames(c, _uneven_face(neutral=False)), c)
        assert res["verdict"] == "ok" and res["diffuse_ok"] and res["S_illum"] >= 0.5, res


# --- parallax ---

def _scene(seed=1):
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.integers(0, 255, (240, 320), dtype=np.uint8), (0, 0), 1.5)


def _shift(img, dx):
    m = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


def _sequence(depth_gap, n=10, step=1.0):
    """Background shifts `step` px/frame; the centre shifts step*(1+depth_gap): 0 = flat."""
    bg, fg = _scene(1), _scene(2)
    frames = []
    for i in range(n):
        f = _shift(bg, i * step)
        inner = _shift(fg, i * step * (1 + depth_gap))
        f[48:192, 80:240] = inner[48:192, 80:240]
        frames.append(f[::2, ::2].copy())
    return frames


def test_flat_plane_motion_detected():
    res = parallax.analyze(_sequence(0.0, step=3.0))
    assert res["parallax_ratio"] is not None and res["cue_flat_plane"], res


def test_depth_parallax_not_flagged_flat():
    res = parallax.analyze(_sequence(1.5, step=3.0))
    assert res["parallax_ratio"] is not None and not res["cue_flat_plane"], res


def test_tiny_motion_stays_silent():
    res = parallax.analyze(_sequence(0.0, step=0.05))
    assert res["parallax_ratio"] is None and not res["cue_flat_plane"], res


def test_too_few_frames_stays_silent():
    assert not parallax.analyze(_sequence(0.0)[:3])["cue_flat_plane"]
