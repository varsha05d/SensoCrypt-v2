"""Accelerometer movement, for devices that have no gyroscope (many tablets / budget phones).

An accelerometer reads gravity (a constant ~9.8 m/s^2 along some axis) plus any real movement.
Band-passing to 0.4-8 Hz -- the band human hand movement and tremor live in -- removes gravity
and slow drift and leaves only physical movement. A device resting on a table leaves nothing
but sensor noise (about 0.005-0.02 m/s^2 rms for a typical MEMS part); a hand-held device,
even held "still", shows tremor well above it.

What this can and cannot prove: that the DEVICE is physically being moved. Unlike the gyro
channel it cannot check that the camera image moved the same way, so it is only ever used
together with a confirmed flash response and only for devices that have no gyroscope.
"""

import numpy as np

from app.liveness import imu

FS_HZ = 50.0
MIN_SAMPLES = 40          # ~0.8 s at 50 Hz
EDGE_TRIM_S = 0.2         # filtfilt edge effects inflate the ends of a short window


def movement_rms(samples: list[tuple[int, float, float, float]]) -> float | None:
    """RMS (m/s^2) of the band-passed accelerometer vector over `samples` (t_ns, x, y, z).
    None when there is too little data to say."""
    if len(samples) < MIN_SAMPLES:
        return None
    a = np.asarray(samples, dtype=float)
    t = a[:, 0]
    if t[-1] - t[0] < 0.8e9:
        return None
    grid = np.arange(t[0], t[-1], 1e9 / FS_HZ)
    v = imu.resample(t, a[:, 1:4], grid)
    if len(v) < MIN_SAMPLES:
        return None
    f = imu.bandpass(v, fs=FS_HZ)
    trim = int(EDGE_TRIM_S * FS_HZ)
    f = f[trim:-trim] if len(f) > 4 * trim else f
    return float(np.sqrt((f**2).sum(axis=1).mean()))
