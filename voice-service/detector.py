"""AI-vs-human voice detection (v2, in-progress feature, on its own branch -- not wired
into the app yet).

Uses RawNet2 (Tak et al., ICASSP 2021), a raw-waveform anti-spoofing model, over the
alternatives evaluated during the proof-of-concept:
  - ViT / Wav2Vec2 / WavLM (from Jabberjay's model zoo): each got at least one of two
    obvious test cases (a real recording vs. a clearly TTS-generated clip) wrong.
  - HuBERT / Spectra0: both correct, but 361MB / 1.2GB checkpoints -- too large to run
    comfortably alongside the rest of this backend on Render's free tier.
  - RawNet2: correct (99.998% / 99.98% confidence on the two test cases), and only a 67MB
    checkpoint -- the only option that was both accurate and small enough to be a realistic
    fit here.

Known upstream bug, worked around below: the pretrained checkpoint
(MattyB95/pre_trained_DF_RawNet2 on Hugging Face) is missing the `Sinc_conv.filters` key
that a freshly-constructed RawNet expects in its state_dict. That key is a deterministic,
non-trained buffer (a fixed Mel-scale sinc filterbank computed from a formula in
SincConv.__init__, not something learned via backprop) -- so loading with strict=False and
keeping the freshly-initialized value is mathematically correct, not a workaround that
loses accuracy. Verified during the proof-of-concept: predictions were identical whether or
not this key loaded.
"""

import io
import wave

import numpy as np
import torch
import yaml
from huggingface_hub import hf_hub_download
from scipy.signal import resample_poly

from rawnet2_model import RawNet

_REPO_ID = "MattyB95/pre_trained_DF_RawNet2"
_FILENAME = "pre_trained_DF_RawNet2.pth"
_TARGET_SR = 16_000

_model: RawNet | None = None
_config: dict | None = None


class VoiceDetectionError(Exception):
    pass


def _load_config() -> dict:
    global _config
    if _config is None:
        import os

        config_path = os.path.join(os.path.dirname(__file__), "rawnet2_config.yaml")
        with open(config_path) as f:
            _config = yaml.safe_load(f)
    return _config


def _get_model() -> RawNet:
    global _model
    if _model is None:
        config = _load_config()
        model = RawNet(config["model"], "cpu")
        model_file = hf_hub_download(repo_id=_REPO_ID, filename=_FILENAME)
        state_dict = torch.load(model_file, map_location="cpu", weights_only=True)
        # strict=False: see module docstring -- the one missing key is a deterministic,
        # non-trained buffer, not a real gap in the loaded weights.
        model.load_state_dict(state_dict, strict=False)
        model.eval()
        _model = model
    return _model


def _decode_wav_pcm16(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decodes mono or stereo 16-bit PCM WAV bytes into a float32 array in [-1, 1] plus
    the sample rate. Deliberately stdlib-only (wave + numpy) rather than pulling in
    librosa/soundfile/torchaudio -- the Android side controls the format it records in, so
    we don't need to handle arbitrary codecs, just the PCM WAV it will actually send."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sample_width != 2:
        raise VoiceDetectionError(f"expected 16-bit PCM WAV, got sample_width={sample_width}")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples, sample_rate


def detect_from_wav_bytes(wav_bytes: bytes) -> dict:
    """Returns {"label": "human" | "ai_generated", "confidence": float in [0,1]}."""
    samples, sample_rate = _decode_wav_pcm16(wav_bytes)
    return detect(samples, sample_rate)


def detect(samples: np.ndarray, sample_rate: int) -> dict:
    if samples.size == 0:
        raise VoiceDetectionError("empty audio -- nothing to analyze")

    config = _load_config()
    model = _get_model()

    if sample_rate != _TARGET_SR:
        # resample_poly needs integer up/down factors -- reduce the ratio first so we
        # don't do more work than necessary for arbitrary sample rates.
        from math import gcd

        g = gcd(_TARGET_SR, sample_rate)
        samples = resample_poly(samples, _TARGET_SR // g, sample_rate // g).astype(np.float32)

    max_len = config["model"]["nb_samp"]
    if len(samples) >= max_len:
        samples = samples[:max_len]
    else:
        reps = max_len // len(samples) + 1
        samples = np.tile(samples, reps)[:max_len]

    audio_tensor = torch.from_numpy(samples).float().unsqueeze(0)
    with torch.no_grad():
        out = model(audio_tensor)
        probs = out.exp()  # log_softmax -> probabilities
        predicted = int(out.argmax(dim=1).item())

    # Class indices match the ASVspoof2019-DF convention the checkpoint was trained on:
    # 0 = spoof (AI-generated/synthetic), 1 = bonafide (human). Confirmed empirically
    # during the proof-of-concept against a real recording and a TTS-generated clip.
    label = "human" if predicted == 1 else "ai_generated"
    confidence = float(probs[0][predicted])
    return {"label": label, "confidence": confidence}
