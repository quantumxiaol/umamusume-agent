"""
Quality filters for prompt text and voice samples.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class TextFilterConfig:
    min_length: int = 5
    max_length: int = 50
    max_symbol_ratio: float = 0.3
    max_ellipsis_ratio: float = 0.12


@dataclass(frozen=True)
class AudioFilterConfig:
    min_duration: float = 3.0
    max_duration: float = 35.0
    min_centroid: float = 1200.0
    min_f0_std: float = 20.0
    max_pitch_range: float = 260.0
    max_silence_ratio: float = 0.4
    max_silence_sec: float = 2.0
    top_db: float = 30.0


@dataclass(frozen=True)
class AudioQuality:
    duration: Optional[float]
    effective_duration: Optional[float]
    sample_rate: Optional[int]
    spectral_centroid: Optional[float]
    f0_std: Optional[float]
    pitch_range: Optional[float]
    silence_ratio: Optional[float]
    max_silence_sec: Optional[float]
    nasal_ratio: Optional[float]


_BAD_TEXT_PATTERNS = [
    r"ふんふん",
    r"ふふ",
    r"ラララ",
    r"ルルル",
    r"はぁ",
    r"はあ",
    r"はっ",
    r"すぅ",
    r"ふー",
    r"ぁっ",
    r"んっ",
    r"えーと",
    r"あの",
]


def is_valid_japanese_text(text: str, config: TextFilterConfig | None = None) -> Tuple[bool, str]:
    cfg = config or TextFilterConfig()
    cleaned = text.strip()
    if not cleaned:
        return False, "empty"

    if len(cleaned) < cfg.min_length:
        return False, "too_short"

    if len(cleaned) > cfg.max_length:
        return False, "too_long"

    noise_chars = re.findall(r"[…—～\.。、]", cleaned)
    if cleaned and (len(noise_chars) / len(cleaned)) > cfg.max_symbol_ratio:
        return False, "too_many_symbols"

    ellipsis_count = cleaned.count("…") + cleaned.count("...")
    if cleaned and ellipsis_count / len(cleaned) > cfg.max_ellipsis_ratio:
        return False, "too_many_ellipsis"

    for pattern in _BAD_TEXT_PATTERNS:
        if re.search(pattern, cleaned):
            if "ふん" in pattern or "ララ" in pattern or "ルル" in pattern:
                return False, "humming_or_singing"

    if re.search(r"([ぁ-んァ-ン])\1{2,}", cleaned):
        return False, "repetitive_kana"

    has_kanji = bool(re.search(r"[\u4e00-\u9fff]", cleaned))
    if not has_kanji and len(cleaned) > 10:
        return False, "no_kanji"

    return True, "ok"


def _silence_stats(samples, sample_rate: int, top_db: float) -> Tuple[float, float, float]:
    import librosa
    import numpy as np

    total_duration = float(samples.shape[0] / sample_rate) if sample_rate else 0.0
    if total_duration <= 0.0:
        return 0.0, 1.0, 0.0

    intervals = librosa.effects.split(samples, top_db=top_db)
    if intervals.size == 0:
        return 0.0, 1.0, total_duration

    voiced = np.sum((intervals[:, 1] - intervals[:, 0]) / sample_rate)
    silence_ratio = max(0.0, min(1.0, 1.0 - voiced / total_duration))

    max_gap = intervals[0][0] / sample_rate
    for (start, end), (next_start, _next_end) in zip(intervals[:-1], intervals[1:]):
        gap = (next_start - end) / sample_rate
        if gap > max_gap:
            max_gap = gap
    tail_gap = (samples.shape[0] - intervals[-1][1]) / sample_rate
    if tail_gap > max_gap:
        max_gap = tail_gap

    return float(voiced), float(silence_ratio), float(max_gap)


def _spectral_centroid(samples, sample_rate: int) -> Optional[float]:
    import librosa
    import numpy as np

    if samples.size == 0:
        return None
    centroid = librosa.feature.spectral_centroid(y=samples, sr=sample_rate)
    if centroid.size == 0:
        return None
    return float(np.mean(centroid))


def _pitch_stats(samples, sample_rate: int) -> Tuple[Optional[float], Optional[float]]:
    import librosa
    import numpy as np

    if samples.size == 0:
        return None, None

    f0, voiced_flag, _voiced_prob = librosa.pyin(
        samples,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
    )
    if f0 is None or voiced_flag is None:
        return None, None
    valid_f0 = f0[voiced_flag]
    if valid_f0.size < 5:
        return None, None
    p10 = np.quantile(valid_f0, 0.1)
    p90 = np.quantile(valid_f0, 0.9)
    pitch_range = float(p90 - p10)
    pitch_std = float(np.std(valid_f0))
    return pitch_std, pitch_range


def _nasal_ratio(samples, sample_rate: int) -> Optional[float]:
    import librosa
    import numpy as np

    if samples.size == 0:
        return None

    max_samples = min(samples.shape[0], int(sample_rate * 12))
    short_samples = samples[:max_samples]
    stft = librosa.stft(short_samples, n_fft=2048, hop_length=512)
    power = np.abs(stft) ** 2
    freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=2048)

    def band_energy(low_hz: float, high_hz: float) -> float:
        mask = (freqs >= low_hz) & (freqs < high_hz)
        if not np.any(mask):
            return 0.0
        return float(np.sum(power[mask]))

    low_band = band_energy(200.0, 800.0)
    nasal_band = band_energy(1000.0, 2500.0)
    if low_band <= 0:
        return None
    return nasal_band / low_band


def analyze_audio(
    audio_path: str,
    config: AudioFilterConfig | None = None,
) -> Tuple[AudioQuality, Optional[str]]:
    cfg = config or AudioFilterConfig()

    try:
        import librosa

        samples, sample_rate = librosa.load(audio_path, sr=None, mono=True)
        if samples.size == 0:
            quality = AudioQuality(None, None, None, None, None, None, None, None, None)
            return quality, "empty_audio"

        duration = float(samples.shape[0] / sample_rate)
        voiced_duration, silence_ratio, max_silence_sec = _silence_stats(samples, sample_rate, cfg.top_db)
        effective_duration = float(voiced_duration)
        trimmed, _ = librosa.effects.trim(samples, top_db=cfg.top_db)

        centroid = _spectral_centroid(trimmed, sample_rate)
        pitch_std, pitch_range = _pitch_stats(trimmed, sample_rate)
        nasal_ratio = _nasal_ratio(trimmed, sample_rate)

        quality = AudioQuality(
            duration=duration,
            effective_duration=effective_duration,
            sample_rate=sample_rate,
            spectral_centroid=centroid,
            f0_std=pitch_std,
            pitch_range=pitch_range,
            silence_ratio=silence_ratio,
            max_silence_sec=max_silence_sec,
            nasal_ratio=nasal_ratio,
        )

        if effective_duration < cfg.min_duration or effective_duration > cfg.max_duration:
            return quality, "duration_out_of_range"

        if silence_ratio is not None and silence_ratio > cfg.max_silence_ratio:
            return quality, "too_much_silence"

        if max_silence_sec is not None and max_silence_sec > cfg.max_silence_sec:
            return quality, "long_silence_gap"

        if centroid is not None and centroid < cfg.min_centroid:
            return quality, "too_muddy"

        if pitch_std is not None and pitch_std < cfg.min_f0_std:
            return quality, "monotone_or_humming"

        if pitch_range is not None and pitch_range > cfg.max_pitch_range:
            return quality, "singing_pitch_range"

        return quality, None

    except Exception:
        quality = AudioQuality(None, None, None, None, None, None, None, None, None)
        return quality, "analysis_failed"


def score_audio(quality: AudioQuality, config: AudioFilterConfig | None = None) -> float:
    cfg = config or AudioFilterConfig()
    score = 0.0

    if quality.effective_duration:
        score += quality.effective_duration
        if 10.0 <= quality.effective_duration <= 15.0:
            score += 3.0
        elif cfg.min_duration <= quality.effective_duration < 10.0:
            score += 2.0
        elif 15.0 < quality.effective_duration <= cfg.max_duration:
            score += 1.0
        elif quality.effective_duration < cfg.min_duration:
            score -= (cfg.min_duration - quality.effective_duration) * 2.0
        elif quality.effective_duration > cfg.max_duration:
            score -= (quality.effective_duration - cfg.max_duration) * 0.5

    if quality.spectral_centroid:
        score += min(quality.spectral_centroid / 1000.0, 3.0)
        if quality.spectral_centroid < cfg.min_centroid:
            score -= (cfg.min_centroid - quality.spectral_centroid) / 1000.0 * 4.0

    if quality.silence_ratio:
        score -= quality.silence_ratio * 4.0

    if quality.max_silence_sec:
        score -= quality.max_silence_sec * 1.0

    if quality.nasal_ratio:
        score -= max(quality.nasal_ratio - 1.0, 0) * 10.0
        if quality.nasal_ratio > 1.6:
            score -= (quality.nasal_ratio - 1.6) * 8.0

    if quality.f0_std is not None:
        if quality.f0_std < cfg.min_f0_std:
            score -= (cfg.min_f0_std - quality.f0_std) / max(cfg.min_f0_std, 1.0) * 6.0
        else:
            score += min(quality.f0_std / 50.0, 2.0)

    if quality.pitch_range is not None and quality.pitch_range > cfg.max_pitch_range:
        score -= (quality.pitch_range - cfg.max_pitch_range) / max(cfg.max_pitch_range, 1.0) * 5.0

    return score
