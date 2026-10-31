"""Accelerometer movement for devices without a gyro. Synthetic; the real-hardware noise floor is
unverified -- accel_rms is logged on every verdict so ACCEL_MIN_RMS can be tuned from real data."""

import numpy as np

from app.liveness import accel
from app.liveness.worker import ACCEL_MIN_RMS

FS = 50


def _samples(seconds, noise, tremor_amp=0.0, tremor_hz=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / FS)
    a = np.stack([rng.normal(0, noise, len(t)), rng.normal(0, noise, len(t)), 9.81 + rng.normal(0, noise, len(t))], axis=1)
    if tremor_amp:
        a[:, 0] += tremor_amp * np.sin(2 * np.pi * tremor_hz * t)
        a[:, 1] += 0.6 * tremor_amp * np.sin(2 * np.pi * tremor_hz * 1.3 * t + 1)
    return [(int(1e9 + ti * 1e9), *row) for ti, row in zip(t, a)]


def test_a_device_resting_on_a_table_reads_below_the_threshold():
    # gravity (9.81) is on one axis the whole time; only sensor noise remains after filtering
    for noise in (0.005, 0.01, 0.02):
        vals = [accel.movement_rms(_samples(2.0, noise, seed=s)) for s in range(30)]
        assert max(vals) < ACCEL_MIN_RMS, f"noise {noise}: max {max(vals):.4f}"


def test_slight_hand_tremor_reads_above_the_threshold():
    for amp in (0.06, 0.1, 0.3, 1.0):          # m/s^2: barely-held up to a deliberate sway
        vals = [accel.movement_rms(_samples(2.0, 0.01, tremor_amp=amp, seed=s)) for s in range(30)]
        assert min(vals) > ACCEL_MIN_RMS, f"tremor {amp}: min {min(vals):.4f}"


def test_gravity_alone_is_removed_whatever_the_orientation():
    # a tilted device: gravity split across axes, still no movement
    s = [(t, 6.9, 0.0, 6.9) for t, *_ in _samples(2.0, 0.0)]
    assert accel.movement_rms(s) < 0.005


def test_too_little_data_says_nothing():
    assert accel.movement_rms(_samples(0.3, 0.01)) is None
    assert accel.movement_rms([]) is None
