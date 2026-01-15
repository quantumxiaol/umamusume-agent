"""
Audio processing utilities
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

import torchaudio
import torch

logger = logging.getLogger(__name__)


def load_audio(audio_path: str, target_sr: Optional[int] = None) -> Tuple[torch.Tensor, int]:
    """
    加载音频文件
    
    Args:
        audio_path: 音频文件路径
        target_sr: 目标采样率，如果指定则进行重采样
    
    Returns:
        (音频张量, 采样率)
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    
    try:
        waveform, sample_rate = torchaudio.load(str(audio_path))
        
        # 重采样
        if target_sr and target_sr != sample_rate:
            logger.info(f"Resampling audio from {sample_rate} Hz to {target_sr} Hz")
            resampler = torchaudio.transforms.Resample(sample_rate, target_sr)
            waveform = resampler(waveform)
            sample_rate = target_sr
        
        return waveform, sample_rate
    
    except Exception as e:
        logger.error(f"Failed to load audio from {audio_path}: {e}")
        raise


def convert_to_mono(waveform: torch.Tensor) -> torch.Tensor:
    """
    转换为单声道
    
    Args:
        waveform: 音频张量 (channels, samples)
    
    Returns:
        单声道音频张量 (1, samples)
    """
    if waveform.shape[0] == 1:
        return waveform
    
    # 取平均值转为单声道
    mono = torch.mean(waveform, dim=0, keepdim=True)
    logger.info(f"Converted audio from {waveform.shape[0]} channels to mono")
    return mono


def normalize_audio(waveform: torch.Tensor, target_level: float = 0.9) -> torch.Tensor:
    """
    归一化音频音量
    
    Args:
        waveform: 音频张量
        target_level: 目标音量级别（0-1）
    
    Returns:
        归一化后的音频张量
    """
    max_val = torch.max(torch.abs(waveform))
    if max_val > 0:
        waveform = waveform * (target_level / max_val)
    return waveform


def convert_audio_format(
    input_path: str,
    output_path: str,
    target_sr: int = 22050,
    target_channels: int = 1,
    normalize: bool = True
) -> str:
    """
    转换音频格式（支持 mp3 -> wav 等）
    
    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径
        target_sr: 目标采样率
        target_channels: 目标声道数（1=单声道，2=立体声）
        normalize: 是否归一化音量
    
    Returns:
        输出文件路径
    """
    logger.info(f"Converting audio: {input_path} -> {output_path}")
    
    # 加载音频
    waveform, sample_rate = load_audio(input_path, target_sr=target_sr)
    
    # 转换声道
    if target_channels == 1 and waveform.shape[0] > 1:
        waveform = convert_to_mono(waveform)
    
    # 归一化
    if normalize:
        waveform = normalize_audio(waveform)
    
    # 保存
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    torchaudio.save(str(output_path), waveform, target_sr)
    logger.info(f"Audio converted and saved to {output_path}")
    
    return str(output_path)


def get_audio_duration(audio_path: str) -> float:
    """
    获取音频时长（秒）
    
    Args:
        audio_path: 音频文件路径
    
    Returns:
        时长（秒）
    """
    waveform, sample_rate = load_audio(audio_path)
    duration = waveform.shape[1] / sample_rate
    return duration


def trim_audio(
    waveform: torch.Tensor,
    sample_rate: int,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None
) -> torch.Tensor:
    """
    裁剪音频
    
    Args:
        waveform: 音频张量
        sample_rate: 采样率
        start_sec: 开始时间（秒）
        end_sec: 结束时间（秒），None表示到结尾
    
    Returns:
        裁剪后的音频张量
    """
    start_frame = int(start_sec * sample_rate)
    end_frame = int(end_sec * sample_rate) if end_sec else waveform.shape[1]
    
    return waveform[:, start_frame:end_frame]

