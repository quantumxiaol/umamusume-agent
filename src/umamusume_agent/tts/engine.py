"""
TTS Engine - 语音合成引擎
基于 CosyVoice3 官方 API
参考: cosyvoice/example.py 的 cosyvoice3_example()
"""

import logging
from pathlib import Path
from typing import Optional, Union, Generator
from abc import ABC, abstractmethod

import torch
import torchaudio
from cosyvoice.cli.cosyvoice import AutoModel

from ..config import config

logger = logging.getLogger(__name__)


class TTSEngine(ABC):
    """TTS引擎抽象基类"""
    
    @abstractmethod
    def synthesize(
        self,
        text: str,
        ref_audio_path: str,
        ref_text: Optional[str] = None,
        stream: bool = False
    ) -> Union[torch.Tensor, Generator]:
        """
        合成语音
        
        Args:
            text: 要合成的文本
            ref_audio_path: 参考音频路径
            ref_text: 参考音频对应的文本（可选，但提供会提高质量）
            stream: 是否流式生成
        
        Returns:
            音频张量或生成器
        """
        pass
    
    @abstractmethod
    def save_audio(self, audio: torch.Tensor, output_path: str) -> None:
        """
        保存音频到文件
        
        Args:
            audio: 音频张量
            output_path: 输出文件路径
        """
        pass


class CosyVoice3Engine(TTSEngine):
    """CosyVoice 3.0 语音合成引擎"""
    
    def __init__(self, model_dir: Optional[str] = None):
        """
        初始化 CosyVoice 3.0 引擎
        
        Args:
            model_dir: 模型目录路径，默认从配置读取
        """
        self.model_dir = Path(model_dir or config.COSYVOICE_MODEL_DIR)
        
        if not self.model_dir.exists():
            raise FileNotFoundError(
                f"CosyVoice model not found at {self.model_dir}. "
                f"Please download the model first."
            )
        
        logger.info(f"Loading CosyVoice model from {self.model_dir}...")
        self.model = AutoModel(model_dir=str(self.model_dir))
        self.sample_rate = self.model.sample_rate
        
        logger.info(f"CosyVoice model loaded successfully. Sample rate: {self.sample_rate}")
    
    def synthesize(
        self,
        text: str,
        ref_audio_path: str,
        ref_text: Optional[str] = None,
        stream: bool = False,
        instruct_text: Optional[str] = None,
        mode: str = "cross_lingual"
    ) -> Union[torch.Tensor, Generator]:
        """
        合成语音 - 基于 CosyVoice3 官方 API
        
        Args:
            text: 要合成的文本
            ref_audio_path: 参考音频路径（支持mp3, wav等）
            ref_text: 参考音频对应的文本（仅 zero_shot 模式需要）
                     - 必须包含 <|endofprompt|> 标记
                     - 格式: "You are a helpful assistant.<|endofprompt|>实际提示文本"
            stream: 是否流式生成
            instruct_text: 指令文本（用于 instruct 模式，如"用开心的语气说"）
            mode: 推理模式，可选 "cross_lingual" 或 "zero_shot"
                - cross_lingual（推荐）: 跨语言克隆，只需要参考音频
                - zero_shot: 零样本克隆，需要参考音频和带 <|endofprompt|> 的 prompt_text
        
        Returns:
            如果 stream=False，返回完整音频张量
            如果 stream=True，返回生成器
        
        注意:
            - CosyVoice3 的 prompt_text 必须包含 <|endofprompt|> 标记
            - 推荐使用 cross_lingual 模式，最简单且效果好
            - instruct_text 可以用来控制情感、语速等
        """
        # 检查参考音频文件
        ref_audio_path = Path(ref_audio_path)
        if not ref_audio_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref_audio_path}")
        
        try:
            # 根据模式选择不同的推理方法
            if instruct_text:
                # 使用 instruct2 模式（自然语言指令控制）
                logger.info(f"Using instruct2 mode: {instruct_text}")
                result_generator = self.model.inference_instruct2(
                    text,
                    instruct_text,
                    str(ref_audio_path),
                    stream=stream
                )
            elif mode == "cross_lingual":
                # 使用 cross_lingual 模式（推荐）
                # 只需要 tts_text 和 prompt_wav，不需要 prompt_text
                logger.info("Using cross_lingual mode")
                result_generator = self.model.inference_cross_lingual(
                    text,
                    str(ref_audio_path),
                    stream=stream
                )
            elif mode == "zero_shot":
                # 使用 zero_shot 模式
                # 需要提供 prompt_text（必须包含 <|endofprompt|> 标记）
                if not ref_text:
                    # 如果没有提供，使用默认的空提示
                    ref_text = "You are a helpful assistant.<|endofprompt|>"
                    logger.warning("No ref_text provided for zero_shot mode, using default prompt")
                elif "<|endofprompt|>" not in ref_text:
                    # 如果提供了但没有标记，自动添加
                    logger.warning("<|endofprompt|> not found in ref_text, adding it automatically")
                    ref_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"
                
                logger.info("Using zero_shot mode")
                result_generator = self.model.inference_zero_shot(
                    text,
                    ref_text,
                    str(ref_audio_path),
                    stream=stream
                )
            else:
                raise ValueError(f"Unknown mode: {mode}. Must be 'cross_lingual' or 'zero_shot'")
            
            if stream:
                # 流式模式：返回生成器
                return result_generator
            else:
                # 非流式模式：收集所有结果
                audio_chunks = []
                for i, result in enumerate(result_generator):
                    audio_chunks.append(result['tts_speech'])
                
                if not audio_chunks:
                    raise RuntimeError("No audio generated")
                
                # 拼接所有音频块
                full_audio = torch.cat(audio_chunks, dim=1)
                return full_audio
                
        except Exception as e:
            logger.error(f"Failed to synthesize audio: {e}")
            raise RuntimeError(f"TTS synthesis failed: {e}") from e
    
    def save_audio(self, audio: torch.Tensor, output_path: str) -> None:
        """
        保存音频到文件
        
        Args:
            audio: 音频张量 (shape: [1, samples])
            output_path: 输出文件路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            torchaudio.save(
                str(output_path),
                audio,
                self.sample_rate
            )
            logger.info(f"Audio saved to {output_path}")
        except Exception as e:
            logger.error(f"Failed to save audio to {output_path}: {e}")
            raise
    
    def synthesize_and_save(
        self,
        text: str,
        ref_audio_path: str,
        output_path: str,
        ref_text: Optional[str] = None,
        instruct_text: Optional[str] = None,
        mode: str = "cross_lingual"
    ) -> str:
        """
        合成语音并保存到文件（便捷方法）
        
        Args:
            text: 要合成的文本
            ref_audio_path: 参考音频路径
            output_path: 输出文件路径
            ref_text: 参考音频对应的文本（仅 zero_shot 模式需要，必须包含 <|endofprompt|>）
            instruct_text: 指令文本（用于 instruct 模式，控制情感/语速等）
            mode: 推理模式，"cross_lingual"（推荐）或 "zero_shot"
        
        Returns:
            输出文件路径
        """
        audio = self.synthesize(
            text=text,
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
            stream=False,
            instruct_text=instruct_text,
            mode=mode
        )
        
        self.save_audio(audio, output_path)
        return output_path
    
    def get_model_info(self) -> dict:
        """
        获取模型信息
        
        Returns:
            模型信息字典
        """
        return {
            "model_dir": str(self.model_dir),
            "sample_rate": self.sample_rate,
            "model_type": "CosyVoice 3.0"
        }


# 全局单例
_global_engine: Optional[CosyVoice3Engine] = None


def get_tts_engine(force_reload: bool = False) -> CosyVoice3Engine:
    """
    获取全局 TTS 引擎单例
    
    Args:
        force_reload: 是否强制重新加载模型
    
    Returns:
        TTS 引擎实例
    """
    global _global_engine
    
    if _global_engine is None or force_reload:
        _global_engine = CosyVoice3Engine()
    
    return _global_engine

