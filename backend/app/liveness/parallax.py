"""Depth cue: does the middle of the frame move like the rest of it?

A real face sits closer to the camera than the room behind it, so when the phone translates
even slightly the face and the background shift by different amounts (parallax). A flat
screen showing a face is ONE plane: every feature, face included, moves as that plane does.

For each consecutive frame pair, features in the central region (where the face is) are
compared with the motion predicted by an affine model fitted ONLY to features in the border.
The ratio of the central residual to the border's own fit residual is ~1 for a single plane
and well above 1 when the centre is at a different depth.

Honest limits, which is why this is a supporting cue and never decides alone:
  * rotation-only motion produces no parallax even for a real 3D scene, and hand tremor is
    mostly rotation;
  * at 160x120 a tremor shifts the image by a fraction of a pixel, which cannot be measured.
So the cue only speaks when there is enough measurable movement (MIN_MOTION_PX) and is
silent otherwise.
"""

import cv2
import numpy as np

FEATURE_PARAMS = dict(maxCorners=200, qualityLevel=0.01, minDistance=5, blockSize=5)
LK_PARAMS = dict(winSize=(15, 15), maxLevel=2, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.02))

MIN_FRAMES = 6
MAX_PAIRS = 8
MIN_POINTS_PER_REGION = 10
MIN_MOTION_PX = 0.3        # median feature motion per frame pair needed to say anything
RESIDUAL_FLOOR_PX = 0.05   # noise floor for the border's own fit residual
FLAT_PLANE_RATIO_MAX = 1.3 # centre moves like the border (ratio this low) => single plane


def _region_masks(shape):
    h, w = shape
    cx0, cx1 = int(0.25 * w), int(0.75 * w)
    cy0, cy1 = int(0.20 * h), int(0.80 * h)
    inner = np.zeros(shape, bool)
    inner[cy0:cy1, cx0:cx1] = True
    return inner, ~inner


def _pair_ratio(prev, cur):
    p0 = cv2.goodFeaturesToTrack(prev, mask=None, **FEATURE_PARAMS)
    if p0 is None or len(p0) < 2 * MIN_POINTS_PER_REGION:
        return None
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, **LK_PARAMS)
    p0b, st_b, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, **LK_PARAMS)
    ok = (st.ravel() == 1) & (st_b.ravel() == 1) & (np.linalg.norm(p0 - p0b, axis=2).ravel() < 1.0)
    p0, p1 = p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2)
    if len(p0) < 2 * MIN_POINTS_PER_REGION:
        return None
    flow = p1 - p0
    inner, _ = _region_masks(prev.shape)
    xi = np.clip(p0[:, 0].astype(int), 0, prev.shape[1] - 1)
    yi = np.clip(p0[:, 1].astype(int), 0, prev.shape[0] - 1)
    is_inner = inner[yi, xi]
    if is_inner.sum() < MIN_POINTS_PER_REGION or (~is_inner).sum() < MIN_POINTS_PER_REGION:
        return None
    motion = float(np.median(np.linalg.norm(flow, axis=1)))
    if motion < MIN_MOTION_PX:
        return {"motion_px": motion, "ratio": None}

    border_p0, border_p1 = p0[~is_inner], p1[~is_inner]
    model, _ = cv2.estimateAffine2D(border_p0, border_p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if model is None:
        return None

    def predict(pts):
        return pts @ model[:, :2].T + model[:, 2]

    border_res = float(np.median(np.linalg.norm(predict(border_p0) - border_p1, axis=1)))
    centre_res = float(np.median(np.linalg.norm(predict(p0[is_inner]) - p1[is_inner], axis=1)))
    return {"motion_px": motion, "ratio": centre_res / max(border_res, RESIDUAL_FLOOR_PX)}


def analyze(frames: list[np.ndarray]) -> dict:
    """frames: consecutive gray frames (oldest first), taken while the screen is NOT flashing
    (brightness changes would corrupt optical flow). Returns the parallax verdict features."""
    if len(frames) < MIN_FRAMES:
        return {"parallax_ratio": None, "parallax_motion_px": None, "cue_flat_plane": False}
    frames = frames[-(MAX_PAIRS + 1):]
    ratios, motions = [], []
    for a, b in zip(frames[:-1], frames[1:]):
        r = _pair_ratio(a, b)
        if r is None:
            continue
        motions.append(r["motion_px"])
        if r["ratio"] is not None:
            ratios.append(r["ratio"])
    motion = float(np.median(motions)) if motions else None
    if len(ratios) < 3:  # too little measurable motion to say anything either way
        return {"parallax_ratio": None, "parallax_motion_px": motion, "cue_flat_plane": False}
    ratio = float(np.median(ratios))
    return {"parallax_ratio": ratio, "parallax_motion_px": motion, "cue_flat_plane": ratio < FLAT_PLANE_RATIO_MAX}


def median_motion_px(frames: list[np.ndarray]) -> float | None:
    """Whole-frame COHERENT shift (pixels per frame pair) over the given consecutive gray
    frames: the camera's own movement, measured from video alone. Used for phones that send
    no gyro data. None when there is too little texture to track.

    Sensor noise and compression make every tracked point jitter in a random direction; real
    camera movement shifts all of them the same way. So the per-pair shift is the vector
    median of the point displacements (noise cancels), not the mean displacement length
    (noise adds up: a perfectly still, noisy camera reads 0.1-0.3 px that way)."""
    if len(frames) < 3:
        return None
    motions = []
    for prev, cur in zip(frames[:-1], frames[1:]):
        p0 = cv2.goodFeaturesToTrack(prev, mask=None, **FEATURE_PARAMS)
        if p0 is None or len(p0) < MIN_POINTS_PER_REGION:
            continue
        p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, **LK_PARAMS)
        p0b, st_b, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, **LK_PARAMS)
        ok = (st.ravel() == 1) & (st_b.ravel() == 1) & (np.linalg.norm(p0 - p0b, axis=2).ravel() < 1.0)
        if ok.sum() < MIN_POINTS_PER_REGION:
            continue
        d = (p1 - p0).reshape(-1, 2)[ok]
        motions.append(float(np.hypot(np.median(d[:, 0]), np.median(d[:, 1]))))
    return float(np.median(motions)) if motions else None
