"""Illumination challenge scoring, colour + spatial (replaces the luma-only global check).

The old check correlated ONE number -- mean frame brightness -- against the flashed
sequence. A phone held in front of the camera defeats that: its glossy screen mirrors the
verifier's flash straight back into the lens, so mean brightness tracks the challenge
exactly as a real face's would, and the replay passes.

What differs between a lit face and a mirror-like reflection is *where* and *how* the light
comes back, not whether it does:

  * A real face is a large, diffuse, curved surface. Screen light spreads over many
    neighbouring regions of the frame, with no single hot spot and no clipping.
  * A glossy screen returns the flash as a compact, very bright patch (the mirrored image of
    the verifier's own screen), concentrated in a few cells and often clipping to white.
  * Skin absorbs blue far more than red; glass reflects every channel about equally. When
    colour frames are available, the per-channel response ratio separates the two.

So the frame is split into a grid, each cell is regressed against the flash sequence, and
the spatial spread / concentration / clipping of the response is scored along with the
global correlation. A strong correlation that fails the spatial test is reported as a
REFLECTION (an attack signal), not as weak evidence.

Thresholds below are starting values chosen from the physics, validated only on synthetic
data (see tests/test_illumination.py) -- tune them against real genuine/replay captures.
Every feature is returned in the result dict so it shows up in the verdict logs.
"""

import numpy as np

GRID_ROWS, GRID_COLS = 6, 8
FRAME_W, FRAME_H = 160, 120

MIN_FRAMES = 6
OBS_STD_FLOOR = 3.0          # 0-255 brightness units; below this the flash left no trace
GLOBAL_R_MIN = 0.5           # global correlation needed to count as a real response at all
CELL_CORR_MIN = 0.5          # a cell "responds" if it correlates with the flash this well
CELL_GAIN_REL_MIN = 0.25     # ...and its gain is at least this fraction of the strongest cell's

# HARD reflection signatures -- physically unambiguous, so they veto outright:
CLIP_DELTA_MAX = 0.05        # the flash drives >5% of the frame into clipping: glare
HOTSPOT_MAX_CELLS = 2        # response confined to <=2 cells...
HOTSPOT_TOP1_MIN = 0.40      # ...carrying >=40% of the total: a single compact hot spot
COMPACT_R_MIN = 0.80         # a very strong flash response...
COMPACT_COVERAGE_FRACTION = 0.5  # ...lighting under half the cells a face would: mirrored screen

# SOFT cues -- ambiguous on their own (a genuine face held at arm's length lights only ~6
# cells, about what a medium glare patch does), so they scale the score down instead of
# vetoing. A genuine user is not rejected on one borderline measurement.
COVERAGE_FULL_CELLS = 6      # with colour: this many responding cells = full marks
COVERAGE_FULL_CELLS_GRAY = 8 # without colour there is less to go on: ask for more spread
TOP1_SOFT_MAX = 0.30         # above this, concentration starts to cost score
CHROMA_RATIO_SPECULAR = 0.85 # blue/red gain this close to 1 = neutral (mirror-like)
CHROMA_FACTOR_SPECULAR = 0.3 # skin absorbs blue, so a neutral response is penalised
LAG_CANDIDATES_MS = (0, 33, 66, 100, 133, 166, 200)

_LUMA = np.array([0.299, 0.587, 0.114])


def _expected_rgb_at(challenge: dict, t_rel_ns: float):
    t_ms = t_rel_ns / 1e6
    cursor = 0.0
    for state in challenge["states"]:
        if cursor <= t_ms < cursor + state["dur_ms"]:
            return np.array(state["rgb"], dtype=float)
        cursor += state["dur_ms"]
    return None


def _cell_means(frame_bgr: np.ndarray) -> np.ndarray:
    """(GRID_ROWS, GRID_COLS, 3) mean RGB per cell."""
    import cv2

    if frame_bgr.shape[:2] != (FRAME_H, FRAME_W):
        frame_bgr = cv2.resize(frame_bgr, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)
    rgb = frame_bgr[:, :, ::-1].astype(float)
    ch, cw = FRAME_H // GRID_ROWS, FRAME_W // GRID_COLS
    return rgb.reshape(GRID_ROWS, ch, GRID_COLS, cw, 3).mean(axis=(1, 3))


def _is_gray_replicated(frames: list[np.ndarray]) -> bool:
    """Old clients send luma copied into R=G=B -- no usable chroma."""
    f = frames[len(frames) // 2].astype(int)
    return float(np.abs(f[:, :, 0] - f[:, :, 1]).mean() + np.abs(f[:, :, 1] - f[:, :, 2]).mean()) < 0.5


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _gain_corr(exp: np.ndarray, obs: np.ndarray) -> tuple[float, float]:
    """Regression slope and correlation of obs against exp (both 1-D)."""
    ev = exp - exp.mean()
    ov = obs - obs.mean()
    var_e = float((ev**2).mean())
    if var_e < 1e-9:
        return 0.0, 0.0
    gain = float((ev * ov).mean() / var_e)
    denom = float(np.sqrt((ev**2).mean() * (ov**2).mean()))
    corr = float((ev * ov).mean() / denom) if denom > 1e-9 else 0.0
    return gain, corr


def analyze(observed: list[tuple[int, np.ndarray]], challenge: dict) -> dict:
    """observed: (t_ns, BGR frame) for frames captured during the challenge window."""
    start = challenge["start_at_ns"]
    cell_cache = {id(f): _cell_means(f) for _, f in observed}  # lags re-use the same frames
    best = None
    for lag_ms in LAG_CANDIDATES_MS:
        rows = []
        for t_ns, frame in observed:
            e = _expected_rgb_at(challenge, t_ns - start - lag_ms * 1e6)
            if e is not None:
                rows.append((e, frame))
        if len(rows) < MIN_FRAMES:
            continue
        exp_luma = np.array([e @ _LUMA for e, _ in rows])
        mean_luma = np.array([float((cell_cache[id(f)] @ _LUMA).mean()) for _, f in rows])
        r = _safe_corr(exp_luma, mean_luma)
        if best is None or r > best[0]:
            best = (r, lag_ms, rows)

    if best is None:
        return {"verdict": "insufficient_frames", "S_illum": 0.0, "n": len(observed)}

    r, lag_ms, rows = best
    # (r above is the global flash correlation at the best-fitting latency)
    n = len(rows)
    exp_rgb = np.stack([e for e, _ in rows])            # (n, 3)
    exp_luma = exp_rgb @ _LUMA
    cells = np.stack([cell_cache[id(f)] for _, f in rows])  # (n, R, C, 3)
    cell_luma = cells @ _LUMA                            # (n, R, C)
    global_obs = cell_luma.mean(axis=(1, 2))

    flat = cell_luma.reshape(n, -1)
    gains = np.zeros(flat.shape[1])
    corrs = np.zeros(flat.shape[1])
    for i in range(flat.shape[1]):
        gains[i], corrs[i] = _gain_corr(exp_luma, flat[:, i])

    pos = np.clip(gains, 0.0, None)
    max_gain = float(pos.max())
    responding = (corrs >= CELL_CORR_MIN) & (pos >= CELL_GAIN_REL_MIN * max_gain) & (max_gain > 0)
    coverage = int(responding.sum())
    top1_share = float(pos.max() / pos.sum()) if pos.sum() > 1e-9 else 1.0

    cell_std = flat.std(axis=0)
    strong_cells = int(((corrs >= CELL_CORR_MIN) & (cell_std >= OBS_STD_FLOOR)).sum())
    if global_obs.std() < OBS_STD_FLOOR:
        # Ambient light swamped the flash: "can't tell", which is never confirmed. A tiny
        # flash-locked patch in an otherwise flat frame is NOT escalated to a reflection --
        # glasses lenses and shiny skin do exactly that on genuine users, and the
        # attack it would catch can't be confirmed from a flat frame anyway.
        return {
            "verdict": "no_variation", "S_illum": 0.0, "n": n, "obs_std": float(global_obs.std()),
            "localised_cells": strong_cells,
        }

    # Clipping: how much MORE of the frame is blown out during bright flashes than dark ones.
    clip = np.array([float((f.min(axis=2) >= 250).mean()) for _, f in rows])
    # Only white flashes can clip all three channels, so judge clipping on those frames
    # (yellow/cyan flashes leave one channel low and would dilute the median).
    hi, lo = exp_rgb.min(axis=1) > 200, exp_luma < 30
    # Median over the bright frames, not the max: glare stays clipped for the whole flash,
    # while a camera's auto-exposure can overshoot for a frame or two after a black->white
    # transition even on a genuine face.
    clip_delta = float(np.median(clip[hi]) - np.median(clip[lo])) if hi.any() and lo.any() else 0.0

    # Colour: per-channel gain of the responding cells against each flashed channel.
    chroma = None
    if not _is_gray_replicated([f for _, f in rows]) and responding.any():
        ch_gain = []
        for c in range(3):
            obs_c = cells[:, :, :, c].reshape(n, -1)[:, responding].mean(axis=1)
            g, _ = _gain_corr(exp_rgb[:, c], obs_c)
            ch_gain.append(g)
        if ch_gain[0] > 1e-6:
            chroma = float(ch_gain[2] / ch_gain[0])  # blue gain / red gain

    features = {
        "n": n,
        "lag_ms": lag_ms,
        "r": r,
        "coverage_cells": coverage,
        "top1_share": top1_share,
        "clip_delta": clip_delta,
        "blue_red_ratio": chroma,
    }

    if r < GLOBAL_R_MIN:
        return {"verdict": "ok", "S_illum": max(0.0, r), "reflection": False, "diffuse_ok": False, **features}

    # Hard signatures: unambiguous glare, veto outright.
    clipped = clip_delta > CLIP_DELTA_MAX
    hotspot = coverage <= HOTSPOT_MAX_CELLS and top1_share >= HOTSPOT_TOP1_MIN
    full_cells = COVERAGE_FULL_CELLS if chroma is not None else COVERAGE_FULL_CELLS_GRAY
    # Strong, flash-locked response confined to less than half of what a face covers:
    # that is the signature of a mirrored screen, not of a lit face.
    compact = r >= COMPACT_R_MIN and coverage < COMPACT_COVERAGE_FRACTION * full_cells
    if clipped or hotspot or compact:
        reason = "clipping" if clipped else ("hotspot" if hotspot else "compact")
        return {
            "verdict": "reflection", "S_illum": 0.0, "reflection": True, "diffuse_ok": False,
            "reflection_reason": reason, **features,
        }

    # Soft cues: graded, so a genuine user with a smaller / further face still passes.
    full = COVERAGE_FULL_CELLS if chroma is not None else COVERAGE_FULL_CELLS_GRAY
    f_cov = min(1.0, max(0.2, coverage / full))
    f_top1 = 1.0 if top1_share <= TOP1_SOFT_MAX else max(0.2, 1.0 - (top1_share - TOP1_SOFT_MAX) * 3.0)
    f_chroma = CHROMA_FACTOR_SPECULAR if (chroma is not None and chroma >= CHROMA_RATIO_SPECULAR) else 1.0
    score = max(0.0, r) * f_cov * f_top1 * f_chroma
    features.update({"f_cov": f_cov, "f_top1": f_top1, "f_chroma": f_chroma})
    return {"verdict": "ok", "S_illum": score, "reflection": False, "diffuse_ok": True, **features}
