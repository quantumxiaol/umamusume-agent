"""
Character configuration data models
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path


class Pronouns(BaseModel):
    """人称代词"""
    self: str = Field(default="我", description="自称")
    user: str = Field(default="训练员桑", description="对用户的称呼")


class Personality(BaseModel):
    """角色性格配置"""
    traits: List[str] = Field(default_factory=list, description="性格特征关键词")
    speaking_style: str = Field(default="", description="说话风格描述")
    pronouns: Pronouns = Field(default_factory=Pronouns)
    catchphrases: List[str] = Field(default_factory=list, description="口癖/标志性台词")


class VoiceConfig(BaseModel):
    """语音配置"""
    model: str = Field(default="IndexTTS2", description="TTS模型名称")
    ref_audio_path: str = Field(..., description="参考音频路径（相对于角色目录）")
    ref_text_path: Optional[str] = Field(None, description="参考文本路径")
    language_code: str = Field(default="ja-JP", description="语言代码")
    sample_rate: int = Field(default=22050, description="采样率")


class Metadata(BaseModel):
    """元数据"""
    cv: Optional[str] = Field(None, description="声优")
    birthday: Optional[str] = Field(None, description="生日")
    height: Optional[str] = Field(None, description="身高")
    source_wiki: Optional[str] = Field(None, description="来源Wiki URL")
    retrieved_at: Optional[datetime] = Field(None, description="数据获取时间")


class CharacterConfig(BaseModel):
    """角色配置完整模型"""
    id: str = Field(..., description="角色唯一标识符")
    name_zh: str = Field(..., description="中文名")
    name_en: str = Field(..., description="英文名")
    name_jp: str = Field(..., description="日文名")
    version: str = Field(default="1.0", description="配置版本")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    last_updated: datetime = Field(default_factory=datetime.now, description="最后更新时间")
    
    system_prompt: str = Field(..., description="LLM系统提示词")
    personality: Personality = Field(default_factory=Personality, description="性格配置")
    voice_config: VoiceConfig = Field(..., description="语音配置")
    metadata: Metadata = Field(default_factory=Metadata, description="元数据")
    
    # 运行时字段（不序列化到JSON）
    character_dir: Optional[Path] = Field(default=None, exclude=True, description="角色目录路径")
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return self.system_prompt
    
    def get_voice_config(self) -> Dict:
        """获取语音配置字典"""
        config = self.voice_config.model_dump()
        # 转换为绝对路径
        if self.character_dir:
            config['ref_audio_path'] = str(self.character_dir / config['ref_audio_path'])
            if config.get('ref_text_path'):
                config['ref_text_path'] = str(self.character_dir / config['ref_text_path'])
        return config
    
    def get_ref_audio_text(self) -> Optional[str]:
        """读取参考音频对应的文本"""
        if not self.character_dir or not self.voice_config.ref_text_path:
            return None
        
        text_path = self.character_dir / self.voice_config.ref_text_path
        if text_path.exists():
            return text_path.read_text(encoding='utf-8').strip()
        return None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
