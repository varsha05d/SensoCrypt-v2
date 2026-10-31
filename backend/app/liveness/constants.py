"""Single source of truth for liveness engine tuning (plan.md §17.3)."""

FS_HZ = 50.0  # common resample grid
WINDOW_S = 2.0
HOP_S = 0.5
BAND_LO_HZ = 0.4
BAND_HI_HZ = 8.0
MAX_LAG_S = 0.35
# Tuned so the SLIGHTEST movement verifies -- by explicit, repeated product decision: a
# calm user won't shake the phone, and a phone in a hand carries only ~0.005-0.05 rad/s of
# tremor. History: 0.08 never counted that (0% of genuine 0.03-0.05 rad/s sessions verified in
# simulation); 0.02 and then 0.008 progressively fixed it; 0.004 now verifies motion right at
# the gyro's own noise floor (~0.003). Simulated at gate 0.004, trust 0.15, 3 windows, gain
# tolerance 2.0: genuine 0.004-0.015 rad/s verify 100% in 3-5 s; UNRELATED gyro/camera noise
# (a phone lying still in front of a replay) passes ~6-21% -- this is the accepted cost of that
# sensitivity (0.008/0.20: 0-4%; shortening to 2 windows measured 12-74%, for only 0.5 s
# gain). tests/test_motion_sensitivity.py pins both sides.
# Latest round (simulated, tests/test_motion_sensitivity.py pins it): gate 0.004 -> 0.003, so a
# 0.003 rad/s tremor now counts (verifies in ~4.5 s; >= 0.005 rad/s in 3.0 s, the floor set by
# the 2 s analysis window + 3 dwell windows). Unrelated amplitude-matched noise passes ~13-21%
# (unchanged); a phone lying still (gyro noise <= 0.002 rad/s) passes 0%, 5% at 0.003.
# Rejected on measurement: 2 dwell windows at trust 0.15 (unrelated passes 33-37%), and any gate
# below ~0.0025 (a still phone then passes ~75%). Trust stays at 0.15 because real phones score
# low on this channel (logged S_A ~0.02-0.13).
MOTION_GATE = 0.003  # rad/s RMS
FRAME_W, FRAME_H = 160, 120
TELEMETRY_FPS = 15
CHUNK_MS = 500
NO_EVIDENCE_TIMEOUT_S = 10.0
# Illumination is the PRIMARY continuous signal (see fusion.py's ILLUM_WEIGHT): unlike
# egomotion it needs no deliberate motion from the user at all, which matters for the
# actual threat model here -- a scam/deepfake call target (e.g. an elderly person) cannot
# be expected to keep the phone moving through a calm conversation. By explicit product
# decision, "checking" must never be a silent state -- worker.py pipelines challenges (the
# next one's CHALLENGE_LEAD_S latency-safety wait overlaps the current one's active
# flash+scoring window, not serialized after it) so consecutive flashes land roughly
# CHALLENGE_LEAD_S apart, not 2x that, for as long as the call isn't yet TRUSTED. The
# moment it reaches TRUSTED, worker.py stops scheduling challenges entirely -- checking is
# either constantly visible or completely off, never quietly running in the background.
# plan.md §12 targets these thresholds against a properly trained fusion model on a real
# genuine/attack dataset. This project has neither yet (§0's fuse() is a plain average, not
# a trained model) -- these values are instead calibrated against real observed scores from
# live phone sessions (2026-08-24 logged run: genuine motion windows scored S_A up to 0.52,
# r up to 0.70, but rarely sustained above ~0.35 for 3+ consecutive windows -- the plan's
# 0.80/0.60/0.35 targets a trained fusion model this project doesn't have yet, and were
# unreachable in practice even with good real motion). Tighten back toward those targets
# once there's real labelled genuine/attack data to fit fuse() against (see
# eval/negative_control.py for the kind of check that should gate any change).
P_TRUST, P_DEGRADE, P_SUSPECT = 0.15, 0.10, 0.03  # was 0.35/0.20/0.08; see MOTION_GATE note
# Fast to promote (3 windows ~= 1.5s of good evidence is enough to trust), slow to demote
# (8-12 windows ~= 4-6s of SUSTAINED weak/bad evidence) -- a person calmly talking produces
# occasional noisy, low-confidence motion windows that must not be able to single-handedly
# flip the verdict. Only a real, sustained pattern of bad evidence should raise an alarm.
DWELL_TRUST, DWELL_DEGRADE, DWELL_SUSPECT = 3, 8, 12  # consecutive windows

# Determined empirically per plan.md §5.2, by eval/analyze_session.py's brute-force search
# against a real recording from the Pixel 9a (session 20260824_225100, front camera,
# portrait): mean per-axis r=0.62 on the lag-aligned signal. Re-run the search if the
# camera facing, orientation, or device changes -- this is device/orientation-specific.
AXIS_MAP_FRONT = ((1, -1), (0, 1), (2, -1))  # (device_axis, sign) per camera axis

# Trust needs BOTH channels (gyro/camera egomotion AND a confirmed flash response) -- the
# product definition ("both channels pass, bilateral verification confirmed"). One channel
# alone cannot be fooled-around: a photo moved by hand passes the gyro channel, a lit
# replay screen can pass the flash channel. Set LIVENESS_STRICT=0 in the environment to fall
# back to either-channel trust (the earlier behaviour) without a redeploy of code.
import os

STRICT_BOTH_CHANNELS = os.environ.get("LIVENESS_STRICT", "1") != "0"
CHANNEL_A_RECENT_WINDOWS = 4   # judge the gyro channel over this many recent scored windows...
CHANNEL_A_MIN_PASSES = 2       # ...it must have passed in at least this many of them
