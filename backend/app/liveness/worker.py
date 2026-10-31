"""Per-session live scoring (plan.md §10.3): one instance per telemetry WS connection,
fed chunk-by-chunk, re-scoring a sliding window each time. Reuses exactly the egomotion /
imu / align / score modules validated offline in Phase 3 (eval/analyze_session.py) against
a real recording -- same code path, just fed incrementally instead of all at once.

Also schedules the illumination challenge (§6.1, Channel B) and fuses it with the
egomotion score (Channel A) through the trust state machine (§8) -- Channel A alone only
proves the camera is live, not that the face in frame is real (§0.1); Channel B is what
actually starts answering that question.
"""

from collections import deque

import cv2
import numpy as np

from app.liveness import accel as accel_mod, align, challenge as challenge_mod, egomotion, fusion, illumination, imu, parallax, score
from app.liveness.constants import (
    AXIS_MAP_FRONT,
    BAND_HI_HZ,
    BAND_LO_HZ,
    FS_HZ,
    MOTION_GATE,
    CHANNEL_A_MIN_PASSES,
    CHANNEL_A_RECENT_WINDOWS,
    NO_EVIDENCE_TIMEOUT_S,
    P_TRUST,
    STRICT_BOTH_CHANNELS,
    WINDOW_S,
)

BUFFER_S = WINDOW_S + 1.0
LAG_LOCK_MIN_STRENGTH = 0.3
# Raised from 0.3 now that axis calibration also runs on slight motion (AXIS_MAP_MIN_ENERGY
# below follows MOTION_GATE): on pure noise the brute-force search over 48 mappings reaches
# a best score above 0.3 about 8% of the time -- a wrong lock persists for the whole session
# (sustained negative r) -- versus ~0.1% at 0.45; genuine motion locks the correct mapping
# 100% of the time at any of 0.3/0.4/0.5 (simulated, including 0.03 rad/s shakes).
AXIS_MAP_LOCK_MIN_SCORE = 0.45
AXIS_MAP_MIN_ENERGY = MOTION_GATE * 2  # needs clearly-real motion, not just above the noise floor
CHALLENGE_END_MARGIN_S = 0.3
ILLUM_SCORE_STALE_S = 20.0  # one good/bad reading shouldn't keep swaying trust indefinitely
# A challenge that responds like a mirrored screen (clipping / hot spot / compact patch --
# illumination.py) blocks trust for this long. A merely weak or inconclusive response does not. A challenge that correlates but fails the spatial test
# (illumination.py: a glossy screen mirroring the flash) vetoes trust for this long.
ILLUM_WEAK_MIN = 0.15      # a weaker flash response that is still face-like and un-vetoed (a phone whose
                           # flash barely registers scored ~0.21 on a genuine face)
VIDEO_MOTION_MIN_PX = 0.3   # coherent camera shift per frame pair that counts as movement. Set from REAL data:
                            # a phone sitting still read 0.03-0.13 on a real handset (sensor noise, auto-exposure,
                            # compression), so 0.08 -- tuned on clean synthetic frames -- passed a still phone.
                            # A phone with no gyro must therefore be visibly moved, not just held.
VIDEO_MOTION_RECENT, VIDEO_MOTION_MIN_PASSES = 6, 4
# Accelerometer movement (devices with no gyro). Band-passed (gravity removed) vector rms: a
# device resting on a table reads ~0.005-0.02 m/s^2 of sensor noise; hand tremor is >= ~0.05.
# Unverified on real hardware -- accel_rms is logged on every verdict to tune this.
ACCEL_MIN_RMS = 0.03
ACCEL_RECENT, ACCEL_MIN_PASSES = 6, 4
ACCEL_WINDOW_S = 2.0
NO_GYRO_GRACE_S = 1.5      # frames flowing this long with zero gyro samples = the phone sends no gyro
ILLUM_CONFIRM_MIN = 0.4  # was 0.5: a weaker (but face-like, un-vetoed) flash response now counts
BLOCKED_FRAMES = 8        # recent frames inspected for a covered / black camera
BLOCKED_MEAN_MAX = 8.0    # 0-255: darker than this is no scene
BLOCKED_STD_MAX = 6.0     # 0-255: flatter than this is no scene (lens under a finger)
REFLECTION_VETO_S = 8.0   # was 20: with a 30 s window one false alarm must not cost two thirds of it


class LivenessEngine:
    def __init__(self, quick: bool = False, strict: bool | None = None):
        self.strict = STRICT_BOTH_CHANNELS if strict is None else strict
        self._a_recent: deque[bool] = deque(maxlen=CHANNEL_A_RECENT_WINDOWS)
        self.frames: deque[tuple[int, np.ndarray]] = deque()
        self.color_frames: deque[tuple[int, np.ndarray]] = deque()  # BGR, same timestamps
        self.gyro: deque[tuple[int, float, float, float]] = deque()
        self.K = egomotion.intrinsics(160, 120)
        self.locked_lag: int | None = None
        self.locked_axis_map: tuple[tuple[int, int], ...] | None = None

        # quick=True: a bounded one-shot verification capture (a few seconds), not an
        # open-ended live session -- fire the illumination challenge almost immediately
        # instead of waiting the normal randomized 8-15s gap, so a short capture window
        # still gets one.
        self.quick = quick
        self._challenge_scheduled_once = False

        self.active_challenge: dict | None = None
        self.pending_challenge: dict | None = None
        self.last_illum_score: dict | None = None
        self.last_illum_score_ns: int | None = None

        self.first_frame_ns: int | None = None
        self.last_motion_ok_ns: int | None = None

        self.trust_fsm = fusion.TrustFSM()
        self.illum_confirmed = False
        self.illum_confirmed_ns: int | None = None  # when the last passing flash was scored
        self.illum_weak_ns: int | None = None       # last face-like (>= ILLUM_WEAK_MIN) un-vetoed flash
        self.gyro_total = 0                         # gyro samples ever received
        self.accel: deque[tuple[int, float, float, float]] = deque()
        self._am_recent: deque[bool] = deque(maxlen=ACCEL_RECENT)  # accelerometer movement checks
        self._vm_recent: deque[bool] = deque(maxlen=VIDEO_MOTION_RECENT)  # video-only movement checks
        # Informational (logged): a challenge produced a usable verdict (ok/reflection).
        self.illum_testable = False
        self.reflection_until_ns: int | None = None
        self.reflection_count = 0

    def _trim(self, latest_ns: int):
        cutoff = latest_ns - int(BUFFER_S * 1e9)
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()
        while self.color_frames and self.color_frames[0][0] < cutoff:
            self.color_frames.popleft()
        while self.gyro and self.gyro[0][0] < cutoff:
            self.gyro.popleft()
        while self.accel and self.accel[0][0] < cutoff:
            self.accel.popleft()

    @staticmethod
    def _center_brightness(frame: np.ndarray) -> float:
        h, w = frame.shape
        return float(frame[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].mean())

    def _score_motion(self) -> dict:
        if len(self.frames) < 6 or len(self.gyro) < 10:
            return {"verdict": "warming_up", "frames": len(self.frames), "gyro": len(self.gyro)}

        mids, omegas = [], []
        frames_list = list(self.frames)
        for i in range(len(frames_list) - 1):
            t0, f0 = frames_list[i]
            t1, f1 = frames_list[i + 1]
            dt = (t1 - t0) / 1e9
            if dt <= 0:
                continue
            omega, _conf = egomotion.omega_from_pair(f0, f1, self.K, dt=dt)
            if omega is None:
                continue
            mids.append((t0 + t1) / 2)
            omegas.append(omega)

        if len(mids) < 3:
            return {"verdict": "no_evidence", "reason": "insufficient optical flow"}

        mids_arr = np.array(mids, dtype=np.int64)
        omegas_arr = np.array(omegas)
        gyro_arr = np.array(self.gyro)
        gyro_ts, gyro_xyz = gyro_arr[:, 0].astype(np.int64), gyro_arr[:, 1:4]

        t0, t1 = max(mids_arr[0], gyro_ts[0]), min(mids_arr[-1], gyro_ts[-1])
        if t1 <= t0:
            return {"verdict": "no_evidence", "reason": "no time overlap"}
        grid = np.arange(t0, t1, int(1e9 / FS_HZ))
        # scipy's sosfiltfilt needs the input longer than its internal pad length (~27
        # samples for this filter order) or it raises -- 80 samples (1.6s) is a safe margin.
        if len(grid) < 80:
            return {"verdict": "warming_up"}

        vis_grid = imu.resample(mids_arr, omegas_arr, grid)
        gyro_grid = imu.resample(gyro_ts, gyro_xyz, grid)
        try:
            vis_bp = imu.bandpass(vis_grid, fs=FS_HZ, lo=BAND_LO_HZ, hi=BAND_HI_HZ)
            gyro_bp = imu.bandpass(gyro_grid, fs=FS_HZ, lo=BAND_LO_HZ, hi=BAND_HI_HZ)
        except ValueError:
            return {"verdict": "warming_up"}

        if self.locked_lag is None:
            lag, strength = align.best_lag(vis_bp, gyro_bp, fs=FS_HZ)
            if strength > LAG_LOCK_MIN_STRENGTH:
                self.locked_lag = lag
        lag = self.locked_lag or 0

        if lag >= 0:
            vis_aligned, gyro_aligned = vis_bp[lag:], gyro_bp[: len(vis_bp) - lag]
        else:
            gyro_aligned, vis_aligned = gyro_bp[-lag:], vis_bp[: len(gyro_bp) + lag]

        n = min(len(vis_aligned), len(gyro_aligned))
        if n < 10:
            return {"verdict": "warming_up"}
        vis_aligned, gyro_aligned = vis_aligned[:n], gyro_aligned[:n]

        # Per-session axis-mapping calibration (plan.md §5.2), not a fixed global constant:
        # a mapping calibrated from one offline recording does not reliably generalise to
        # every way THIS session's user holds and moves the phone -- observed in practice as
        # sustained negative r using a fixed mapping. Same locking pattern as the lag above.
        #
        # Also requires real motion energy in the calibration window, not just enough
        # samples: a low-energy window (someone mostly holding still) produces noise the
        # brute-force search will happily fit an arbitrary-looking "best" mapping to, which
        # then locks in wrong for the rest of the session. Observed in practice as r staying
        # negative for an entire call after locking on a too-quiet window.
        gyro_energy = float(np.sqrt((gyro_aligned**2).sum(axis=1).mean()))
        if self.locked_axis_map is None and n >= int(WINDOW_S * FS_HZ) and gyro_energy >= AXIS_MAP_MIN_ENERGY:
            axis_score, perm, signs = align.find_axis_mapping(vis_aligned, gyro_aligned)
            if axis_score > AXIS_MAP_LOCK_MIN_SCORE:
                self.locked_axis_map = tuple(zip(perm, signs))
        axis_map = self.locked_axis_map or AXIS_MAP_FRONT
        gyro_mapped = np.stack([sign * gyro_aligned[:, axis] for axis, sign in axis_map], axis=1)

        win = min(len(vis_aligned), int(WINDOW_S * FS_HZ))
        result = score.window_score(vis_aligned[-win:], gyro_mapped[-win:], conf=0.8)
        result["lag_ms"] = lag / FS_HZ * 1000
        result["axis_map_locked"] = self.locked_axis_map is not None
        result["lag_locked"] = self.locked_lag is not None
        return result

    def _handle_challenge(self, latest_ts: int) -> dict | None:
        """Schedules, times out, and scores the illumination challenge. Returns a
        new-challenge payload to forward to the client, or None if nothing new was issued
        this tick.

        Pipelined: the NEXT challenge is staged (its CHALLENGE_LEAD_S latency-safety wait
        started) the moment the CURRENT one begins flashing, not after it finishes scoring.
        Serializing lead-wait -> flash -> score -> lead-wait -> flash -> score meant
        consecutive challenges landed roughly 2x CHALLENGE_LEAD_S apart -- most of that a
        silent gap with nothing on screen, which read as "stuck", not "checking". Staging
        the next one early overlaps its wait with the current challenge's active window,
        so flashes land roughly CHALLENGE_LEAD_S apart instead, with the full safety margin
        still intact on every single challenge.
        """
        if self.trust_fsm.state == "TRUSTED":
            # Verified once -- stop flashing entirely, by product decision. Trade-off,
            # stated plainly: the system will not notice if the feed is swapped out after
            # this point (no periodic re-check). Re-arms automatically the moment trust
            # drops below TRUSTED (that happens on its own via the state machine if the
            # motion/illumination evidence turns bad -- this function just won't schedule
            # a new challenge while TRUSTED holds).
            self.active_challenge = None
            self.pending_challenge = None
            return None

        new_challenge = None
        current_has_started = self.active_challenge is not None and latest_ts >= self.active_challenge["start_at_ns"]
        if self.pending_challenge is None and (self.active_challenge is None or current_has_started):
            lead_s = 0.5 if (self.quick and not self._challenge_scheduled_once) else challenge_mod.CHALLENGE_LEAD_S
            start_at = latest_ts + int(lead_s * 1e9)
            self.pending_challenge = challenge_mod.generate_challenge(start_at)
            self._challenge_scheduled_once = True
            new_challenge = self.pending_challenge

        if self.active_challenge is not None:
            total_dur_ns = int(sum(s["dur_ms"] for s in self.active_challenge["states"]) * 1e6)
            window_end = self.active_challenge["start_at_ns"] + total_dur_ns
            if latest_ts >= window_end + int(CHALLENGE_END_MARGIN_S * 1e9):
                observed = [
                    (t, f)
                    for t, f in self.color_frames
                    if self.active_challenge["start_at_ns"] <= t <= window_end
                ]
                result = illumination.analyze(observed, self.active_challenge)
                self._add_parallax_cue(result, self.active_challenge["start_at_ns"])
                self.last_illum_score = result
                self.last_illum_score_ns = latest_ts
                self._apply_illumination_verdict(self.last_illum_score, latest_ts)
                self.active_challenge = None

        if self.active_challenge is None and self.pending_challenge is not None:
            self.active_challenge = self.pending_challenge
            self.pending_challenge = None

        return new_challenge

    def _wants_color(self, t_ns: int) -> bool:
        """Colour frames are only needed to score a flash challenge. Decoding colour (and
        converting to gray) for every frame costs noticeably more than the old gray-only
        decode, and this backend runs on a tiny shared CPU where verdicts were already
        arriving late -- so decode colour only around an active/pending challenge window."""
        for ch in (self.active_challenge, self.pending_challenge):
            if ch is None:
                continue
            start = ch["start_at_ns"]
            end = start + int(sum(st["dur_ms"] for st in ch["states"]) * 1e6)
            if start - int(0.4e9) <= t_ns <= end + int(0.4e9):
                return True
        return False

    def _add_parallax_cue(self, result: dict, challenge_start_ns: int) -> None:
        """Planarity cue from the un-flashed frames just before the challenge. Never decides
        alone: a flat-plane result only turns an otherwise-passing response into a screen
        verdict when the flash response is ALSO flat or colour-neutral."""
        pre = [f for t, f in self.frames if t < challenge_start_ns][-(parallax.MAX_PAIRS + 1):]
        try:
            result.update(parallax.analyze(pre))
        except cv2.error:
            return
        if (
            result.get("verdict") == "ok" and result.get("diffuse_ok")
            and result.get("cue_flat_plane")
            and (result.get("cue_uniform") or result.get("cue_neutral"))
        ):
            result.update(verdict="reflection", reflection=True, diffuse_ok=False,
                          S_illum=0.0, reflection_reason="flat_plane")

    def _camera_blocked(self) -> bool:
        """True when the recent frames show no scene at all: a covered lens or a black feed.
        A lens under a finger/hand is nearly uniform (tiny spatial std) or dark. Normal scenes,
        even dim ones lit by the screen, keep a spatial std well above this floor."""
        recent = [f for _, f in list(self.frames)[-BLOCKED_FRAMES:]]
        if len(recent) < BLOCKED_FRAMES:
            return False
        mean = float(np.median([f.mean() for f in recent]))
        std = float(np.median([f.std() for f in recent]))
        return mean < BLOCKED_MEAN_MAX or std < BLOCKED_STD_MAX

    def _allow_trust(self, latest_ts: int) -> bool:
        """Only EVIDENCE OF A REFLECTION blocks trust. A weak, noisy or inconclusive flash
        response (flash swamped by ambient light, a camera whose auto-exposure fights the
        flash, a challenge skipped as late) must not: requiring a clean illumination pass
        left one of two test phones unable to verify while the other, whose flash was
        simply unreadable, verified at once."""
        return not (self.reflection_until_ns is not None and latest_ts < self.reflection_until_ns)

    def _apply_illumination_verdict(self, result: dict, latest_ts: int) -> None:
        if result.get("verdict") in ("ok", "reflection"):
            self.illum_testable = True
        if result.get("reflection"):
            # Looked like a lit face by brightness alone, but the light came back as a
            # compact/clipped hot spot: a glossy screen mirroring our flash. Revoke any
            # earlier confirmation and block trust until a clean challenge passes.
            self.illum_confirmed = False
            self.illum_confirmed_ns = None
            self.illum_weak_ns = None
            self.reflection_count += 1
            self.reflection_until_ns = latest_ts + int(REFLECTION_VETO_S * 1e9)
            self.trust_fsm.veto()
        elif result.get("verdict") == "ok" and result.get("diffuse_ok"):
            if result.get("S_illum", 0.0) >= ILLUM_WEAK_MIN:
                self.illum_weak_ns = latest_ts
            if result.get("S_illum", 0.0) >= ILLUM_CONFIRM_MIN:
                self.illum_confirmed = True
                self.illum_confirmed_ns = latest_ts
                # A later clean, strong, diffuse response supersedes an earlier reflection
                # alarm (a screen/glare reflects EVERY challenge, so it cannot produce one).
                self.reflection_until_ns = None

    def ingest(self, chunk: dict) -> dict:
        for f in chunk.get("frames", []):
            buf = np.frombuffer(f["jpeg_bytes"], dtype=np.uint8)
            if self._wants_color(f["t_ns"]):
                color = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if color is not None:
                    self.color_frames.append((f["t_ns"], color))
                    self.frames.append((f["t_ns"], cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)))
            else:
                arr = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
                if arr is not None:
                    self.frames.append((f["t_ns"], arr))
        for t_ns, wx, wy, wz in chunk.get("imu", {}).get("gyro", []):
            self.gyro.append((t_ns, wx, wy, wz))
            self.gyro_total += 1
        for t_ns, ax, ay, az in chunk.get("imu", {}).get("accel", []):
            self.accel.append((t_ns, ax, ay, az))

        if not self.frames:
            return {"verdict": "no_data"}
        latest_ts = self.frames[-1][0]
        self._trim(latest_ts)

        result = self._score_motion()
        new_challenge = self._handle_challenge(latest_ts)
        if new_challenge is not None:
            result["new_challenge"] = new_challenge
        if self.last_illum_score is not None:
            result["S_illum"] = self.last_illum_score

        # plan.md §6.2 / §9 L5: a still phone must never register as trusted on nothing --
        # but it also shouldn't be stuck with no way to prove itself. After
        # NO_EVIDENCE_TIMEOUT_S without fresh motion evidence, prompt for a deliberate
        # move instead of just sitting there. This is a pure UX nudge -- the resulting
        # motion is scored through the normal S_A path above, no separate verification.
        if self.first_frame_ns is None:
            self.first_frame_ns = latest_ts
        if result.get("verdict") == "ok":
            self.last_motion_ok_ns = latest_ts
        baseline_ns = self.last_motion_ok_ns if self.last_motion_ok_ns is not None else self.first_frame_ns
        result["motion_prompt"] = (latest_ts - baseline_ns) > int(NO_EVIDENCE_TIMEOUT_S * 1e9)

        illum_fresh = (
            self.last_illum_score_ns is not None
            and (latest_ts - self.last_illum_score_ns) <= int(ILLUM_SCORE_STALE_S * 1e9)
        )
        s_a = result.get("S_A") if result.get("verdict") == "ok" else None
        s_illum = (
            self.last_illum_score.get("S_illum")
            if illum_fresh and self.last_illum_score and self.last_illum_score.get("verdict") in ("ok", "reflection")
            else None
        )
        # A weak flash response is not evidence of a face (a covered lens leaks some light
        # too), and P_TRUST is low on purpose for the gyro channel -- so the flash only
        # contributes to trust once it is strong enough to count as confirmed.
        if s_illum is not None and s_illum < ILLUM_CONFIRM_MIN:
            s_illum = None
        blocked = self._camera_blocked()
        result["camera_blocked"] = blocked
        vetoed = (self.reflection_until_ns is not None and latest_ts < self.reflection_until_ns) or blocked
        allow_trust = self._allow_trust(latest_ts) and not blocked
        if result.get("verdict") in ("ok", "no_evidence"):
            self._a_recent.append(s_a is not None and s_a >= P_TRUST)
        channel_a_pass = sum(self._a_recent) >= CHANNEL_A_MIN_PASSES
        # A passing flash stays valid for ILLUM_SCORE_STALE_S (one later weak reading, e.g. a
        # challenge the auto-exposure fought, must not undo it); a reflection revokes it.
        stale_ns = int(ILLUM_SCORE_STALE_S * 1e9)
        channel_b_pass = (  # face-like flash response, not vetoed as a screen / reflection
            not vetoed and self.illum_weak_ns is not None and (latest_ts - self.illum_weak_ns) <= stale_ns
        )
        channel_b_strong = (
            not vetoed
            and self.illum_confirmed
            and self.illum_confirmed_ns is not None
            and (latest_ts - self.illum_confirmed_ns) <= stale_ns
        )
        # A phone that never sends gyro data cannot pass channel A at all; it may verify on a
        # strongly confirmed flash instead. A phone that DOES send gyro data but is held still
        # still fails channel A (and so is not trusted).
        gyro_unavailable = self.gyro_total == 0 and (latest_ts - self.first_frame_ns) >= int(NO_GYRO_GRACE_S * 1e9)
        # Without a gyro the only movement evidence is the video itself: the camera must visibly
        # move (measured outside the flash windows, where brightness changes would fool the
        # tracker). A still phone shows none and is not trusted, gyro or not.
        video_motion_pass = accel_motion_pass = False
        if gyro_unavailable:
            rms = accel_mod.movement_rms(list(self.accel)[-int(ACCEL_WINDOW_S * 50) - 10:])
            if rms is not None:
                self._am_recent.append(rms >= ACCEL_MIN_RMS)
                result["accel_rms"] = round(rms, 4)
            accel_motion_pass = sum(self._am_recent) >= ACCEL_MIN_PASSES
            if not accel_motion_pass:   # the camera is the second opinion; skip its cost when the accelerometer already says moving
                still_frames = [f for t, f in self.frames if not self._wants_color(t)][-(VIDEO_MOTION_RECENT):]
                try:
                    vm = parallax.median_motion_px(still_frames)
                except cv2.error:
                    vm = None
                if vm is not None:
                    self._vm_recent.append(vm >= VIDEO_MOTION_MIN_PX)
                    result["video_motion_px"] = round(vm, 3)
                video_motion_pass = sum(self._vm_recent) >= VIDEO_MOTION_MIN_PASSES
        movement_pass = accel_motion_pass or video_motion_pass
        result["channel_a_pass"] = channel_a_pass
        result["channel_b_pass"] = channel_b_pass
        result["gyro_unavailable"] = gyro_unavailable
        result["video_motion_pass"] = video_motion_pass
        result["accel_motion_pass"] = accel_motion_pass
        result["strict"] = self.strict
        if self.strict and not (
            (channel_a_pass and channel_b_pass) or (gyro_unavailable and movement_pass and channel_b_strong)
        ):
            allow_trust = False
        result["illum_confirmed"] = self.illum_confirmed
        result["illum_testable"] = self.illum_testable
        result["reflection_veto"] = vetoed
        result["reflection_count"] = self.reflection_count
        if s_a is not None or s_illum is not None:
            p = fusion.fuse(s_a, s_illum)
            if vetoed:
                p = 0.0
            result["p_trust"] = p
            result["trust_state"] = self.trust_fsm.update(p, allow_trust=allow_trust)
        else:
            result["trust_state"] = self.trust_fsm.state

        return result
