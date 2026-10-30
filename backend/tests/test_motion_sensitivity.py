"""Motion (gyroscope vs camera) sensitivity, using the real score/fusion/constants.

Guards two opposing requirements against each other: a slight hand shake must verify, and
an unrelated gyro/camera pair (a still phone in front of a replay) must not. If a constant in
liveness/constants.py is loosened, the false-accept test fails; if tightened, the genuine
test fails. Synthetic signals -- confirm on real captures when possible.
"""

import numpy as np

from app.liveness import align, fusion, imu, score
from app.liveness.constants import AXIS_MAP_FRONT, P_TRUST
from app.liveness.worker import AXIS_MAP_LOCK_MIN_SCORE, AXIS_MAP_MIN_ENERGY

FS, WIN, HOP, N = 50, 100, 25, 30 * 50


def _bp_noise(rng, rms, n=N):
    x = imu.bandpass(rng.normal(0, 1, (n + 200, 3)), fs=FS, lo=0.4, hi=8.0)[100:-100]
    return x * rms / np.sqrt((x**2).sum(axis=1).mean())


def _reaches_trusted(vis, dev):
    fsm = fusion.TrustFSM()
    for s in range(0, N - WIN + 1, HOP):
        r = score.window_score(vis[s:s + WIN], dev[s:s + WIN], conf=0.8)
        if r["verdict"] == "ok":
            fsm.update(fusion.fuse(r["S_A"], None))
        if fsm.state == "TRUSTED":
            return True
    return False


def test_the_slightest_shaking_verifies():
    rng = np.random.default_rng(11)
    # rad/s RMS (+ camera-estimate noise): barely perceptible hand tremor up to gentle movement
    for energy, vis_noise in ((0.01, 0.005), (0.015, 0.008), (0.03, 0.02), (0.05, 0.02)):
        hits = 0
        for _ in range(40):
            dev = _bp_noise(rng, energy)
            vis = dev * rng.uniform(0.8, 1.2) + _bp_noise(rng, vis_noise)
            hits += _reaches_trusted(vis, dev)
        assert hits >= 38, f"only {hits}/40 genuine shakes at {energy} rad/s verified"


def test_unrelated_motion_signals_rarely_verify():
    # Measured trade-off of the sensitive gate (see constants.py): ~0-4%. Fails loudly if the
    # constants are loosened further (trust 0.15 / 2 windows measured 36-40%).
    rng = np.random.default_rng(12)
    for energy in (0.02, 0.05, 0.1):
        hits = sum(_reaches_trusted(_bp_noise(rng, max(energy, 0.02)), _bp_noise(rng, energy)) for _ in range(80))
        assert hits <= 8, f"{hits}/80 unrelated signals reached TRUSTED at {energy} rad/s"


def test_a_perfectly_still_phone_gives_no_evidence():
    rng = np.random.default_rng(13)
    dev = _bp_noise(rng, 0.004)  # gyro noise floor
    r = score.window_score(_bp_noise(rng, 0.004), dev, conf=0.8)
    assert r["verdict"] == "no_evidence"


def test_axis_calibration_does_not_lock_onto_noise():
    rng = np.random.default_rng(14)
    wrong = sum(
        align.find_axis_mapping(_bp_noise(rng, 0.03, 100), _bp_noise(rng, 0.03, 100))[0] > AXIS_MAP_LOCK_MIN_SCORE
        for _ in range(400)
    )
    assert wrong <= 4, f"axis mapping locked onto pure noise {wrong}/400 times"


def test_axis_calibration_locks_a_non_default_mapping_on_slight_motion():
    rng = np.random.default_rng(15)
    perm, signs = (2, 0, 1), (1, -1, 1)  # not the Pixel default: another phone's layout
    assert (tuple(zip(perm, signs))) != AXIS_MAP_FRONT
    assert 0.03 >= AXIS_MAP_MIN_ENERGY * 0.5  # slight motion is allowed to calibrate
    for _ in range(30):
        dev = _bp_noise(rng, 0.045, 100)
        vis = np.stack([signs[i] * dev[:, perm[i]] for i in range(3)], axis=1) + _bp_noise(rng, 0.02, 100)
        s, p, sg = align.find_axis_mapping(vis, dev)
        assert s > AXIS_MAP_LOCK_MIN_SCORE and p == perm and sg == signs
