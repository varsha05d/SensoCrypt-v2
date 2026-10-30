"""Single source of truth for liveness engine tuning (plan.md §17.3)."""

FS_HZ = 50.0  # common resample grid
WINDOW_S = 2.0
HOP_S = 0.5
BAND_LO_HZ = 0.4
BAND_HI_HZ = 8.0
MAX_LAG_S = 0.35
# Tuned so the SLIGHTEST hand movement verifies -- by explicit product decision: a calm
# user won't shake the phone, and a phone in a hand carries only ~0.01-0.05 rad/s of tremor.
# The first gate (0.08) never counted that (0% of genuine 0.03-0.05 rad/s sessions verified in
# simulation); 0.02 fixed 0.03+ but still missed the faintest tremor. Gyro noise is ~0.003,
# so 0.008 is still above the sensor floor. Chosen by simulation (gate 0.008, trust 0.20, 3
# windows, gain tolerance 2.0): genuine shakes of 0.008-0.015 rad/s verify 100% while
# UNRELATED gyro/camera noise (a phone lying still showing a replay) passes only 0-4%.
# Looser settings were measured too and are NOT free: trust 0.15 with 2 windows -> 36-40%
# false accepts. tests/test_motion_sensitivity.py pins both sides.
MOTION_GATE = 0.008  # rad/s RMS
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
P_TRUST, P_DEGRADE, P_SUSPECT = 0.20, 0.12, 0.04  # was 0.35/0.20/0.08; see MOTION_GATE note
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
