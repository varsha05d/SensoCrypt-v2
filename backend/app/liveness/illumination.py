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
COVERAGE_MIN_CELLS = 8       # a face lights up at least this many of the 48 cells
TOP1_SHARE_MAX = 0.35        # no single cell may carry more than this share of the response
CLIP_DELTA_MAX = 0.03        # flash must not drive >3% of the frame into clipping
CHROMA_RATIO_SPECULAR = 0.90 # blue/red gain this close to 1 = neutral (mirror-like)
CHROMA_FACTOR_SPECULAR = 0.3
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
        mean_luma = np.array([float((_cell_means(f) @ _LUMA).mean()) for _, f in rows])
        if exp_luma.std() < 1e-6:
            continue
        r = float(np.corrcoef(exp_luma, mean_luma)[0, 1])
        if best is None or r > best[0]:
            best = (r, lag_ms, rows)

    if best is None:
        return {"verdict": "insufficient_frames", "S_illum": 0.0, "n": len(observed)}

    r, lag_ms, rows = best
    n = len(rows)
    exp_rgb = np.stack([e for e, _ in rows])            # (n, 3)
    exp_luma = exp_rgb @ _LUMA
    cells = np.stack([_cell_means(f) for _, f in rows])  # (n, R, C, 3)
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

    # A localised, flash-locked response can hide below the GLOBAL noise floor (a small mirror
    # patch barely moves the frame mean) -- so check individual cells before calling the
    # whole thing "no variation".
    cell_std = flat.std(axis=0)
    strong_cells = int(((corrs >= CELL_CORR_MIN) & (cell_std >= OBS_STD_FLOOR)).sum())
    if global_obs.std() < OBS_STD_FLOOR:
        if 0 < strong_cells < COVERAGE_MIN_CELLS:
            return {
                "verdict": "reflection", "S_illum": 0.0, "reflection": True, "diffuse_ok": False,
                "n": n, "r": r, "coverage_cells": strong_cells, "top1_share": top1_share,
                "global_obs_std": float(global_obs.std()),
            }
        # Ambient light swamped the flash. Still "can't tell", exactly as before.
        return {"verdict": "no_variation", "S_illum": 0.0, "n": n, "obs_std": float(global_obs.std())}

    # Clipping: how much MORE of the frame is blown out during bright flashes than dark ones.
    clip = np.array([float((f.min(axis=2) >= 250).mean()) for _, f in rows])
    hi, lo = exp_luma > 200, exp_luma < 30
    clip_delta = float(clip[hi].max() - np.median(clip[lo])) if hi.any() and lo.any() else 0.0

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

    diffuse_ok = (
        coverage >= COVERAGE_MIN_CELLS and top1_share <= TOP1_SHARE_MAX and clip_delta <= CLIP_DELTA_MAX
    )
    if not diffuse_ok:
        # Correlates with the flash like a lit face would, but the light came back as a
        # compact / clipped hot spot: that's a glossy surface mirroring the screen.
        return {"verdict": "reflection", "S_illum": 0.0, "reflection": True, "diffuse_ok": False, **features}

    score = max(0.0, r)
    if chroma is not None and chroma >= CHROMA_RATIO_SPECULAR:
        score *= CHROMA_FACTOR_SPECULAR  # skin should absorb blue; neutral response is suspect
    return {"verdict": "ok", "S_illum": score, "reflection": False, "diffuse_ok": True, **features}
