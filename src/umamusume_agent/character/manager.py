"""
Character Manager - 角色管理器
负责加载、缓存和构建角色配置
"""

import json
import asyncio
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
import logging

from .model import CharacterConfig
from ..config import config

logger = logging.getLogger(__name__)


class CharacterManager:
    """角色管理器"""
    
    def __init__(self, characters_dir: Optional[str] = None):
        """
        初始化角色管理器
        
        Args:
            characters_dir: 角色目录路径，默认从配置读取
        """
        self.characters_dir = Path(characters_dir or config.CHARACTERS_DIRECTORY)
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        
        # 内存缓存
        self._cache: Dict[str, CharacterConfig] = {}
        self._dir_index: Dict[str, Path] = self._build_dir_index()
        self._name_aliases: Dict[str, str] = self._load_name_aliases()
        
        logger.info(f"CharacterManager initialized with directory: {self.characters_dir}")
    
    def get_character_dir(self, character_name: str) -> Path:
        """
        获取角色目录路径
        
        Args:
            character_name: 角色名称（中文/英文/日文均可）
        
        Returns:
            角色目录路径
        """
        resolved = self._resolve_character_dir(character_name)
        if resolved:
            return resolved
        dir_name = self._normalize_character_name(character_name)
        return self.characters_dir / dir_name

    def _resolve_character_dir(self, character_name: str) -> Optional[Path]:
        normalized = self._normalize_character_name(character_name)
        if normalized in self._dir_index:
            return self._dir_index[normalized]
        alias = self._name_aliases.get(normalized)
        if alias:
            alias_normalized = self._normalize_character_name(alias)
            if alias_normalized in self._dir_index:
                return self._dir_index[alias_normalized]
            return self.characters_dir / alias_normalized
        return None
    
    def _normalize_character_name(self, name: str) -> str:
        """
        标准化角色名称为目录名格式
        
        Args:
            name: 角色名称
        
        Returns:
            标准化后的目录名
        """
        # 简单策略：转换为小写，空格替换为下划线
        # 后续可以通过CSV映射表进行精确转换
        return name.lower().replace(' ', '_').replace('　', '_')

    def _build_dir_index(self) -> Dict[str, Path]:
        index: Dict[str, Path] = {}
        if not self.characters_dir.exists():
            return index

        for item in self.characters_dir.iterdir():
            if not item.is_dir():
                continue
            config_file = item / "config.json"
            if not config_file.exists():
                continue
            index[self._normalize_character_name(item.name)] = item
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for key in ("name_zh", "name_en", "name_jp"):
                    value = data.get(key)
                    if value:
                        index[self._normalize_character_name(str(value))] = item
            except Exception:
                continue

        return index

    def _load_name_aliases(self) -> Dict[str, str]:
        root_dir = Path(__file__).resolve().parents[3]
        mapping_path = root_dir / "umamusume_characters.json"
        if not mapping_path.exists():
            return {}
        try:
            with open(mapping_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        aliases: Dict[str, str] = {}
        for zh, en in data.items():
            if isinstance(zh, str) and isinstance(en, str):
                aliases[self._normalize_character_name(zh)] = en
                aliases[self._normalize_character_name(en)] = en
        return aliases
    
    def character_exists(self, character_name: str) -> bool:
        """
        检查角色配置是否存在
        
        Args:
            character_name: 角色名称
        
        Returns:
            是否存在
        """
        character_dir = self.get_character_dir(character_name)
        config_file = character_dir / "config.json"
        return config_file.exists()
    
    async def load_character(self, character_name: str, force_rebuild: bool = False) -> CharacterConfig:
        """
        加载角色配置（支持缓存）
        
        Args:
            character_name: 角色名称
            force_rebuild: 是否强制重新构建
        
        Returns:
            角色配置对象
        
        Raises:
            FileNotFoundError: 角色配置不存在且无法构建
            ValueError: 配置文件格式错误
        """
        # 检查内存缓存
        cache_key = character_name.lower()
        if cache_key in self._cache and not force_rebuild:
            logger.info(f"Character '{character_name}' loaded from memory cache")
            return self._cache[cache_key]
        
        # 检查文件系统缓存
        if self.character_exists(character_name) and not force_rebuild:
            character = await self._load_from_file(character_name)
            self._cache[cache_key] = character
            logger.info(f"Character '{character_name}' loaded from disk cache")
            return character
        
        # 缓存未命中且未发现配置文件，抛出异常
        logger.error(f"Character '{character_name}' not found in cache or disk.")
        raise FileNotFoundError(
            f"Character '{character_name}' not found. "
            f"Please ensure it exists in {self.get_character_dir(character_name)}"
        )
    
    async def _load_from_file(self, character_name: str) -> CharacterConfig:
        """
        从文件加载角色配置
        
        Args:
            character_name: 角色名称
        
        Returns:
            角色配置对象
        """
        character_dir = self.get_character_dir(character_name)
        config_file = character_dir / "config.json"
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            character = CharacterConfig(**data)
            character.character_dir = character_dir
            
            return character
        except Exception as e:
            logger.error(f"Failed to load character config from {config_file}: {e}")
            raise ValueError(f"Invalid character config file: {config_file}") from e
    
    async def _save_to_file(self, character: CharacterConfig) -> None:
        """
        保存角色配置到文件
        
        Args:
            character: 角色配置对象
        """
        if not character.character_dir:
            raise ValueError("Character directory not set")
        
        character.character_dir.mkdir(parents=True, exist_ok=True)
        config_file = character.character_dir / "config.json"
        
        # 更新时间戳
        character.last_updated = datetime.now()
        
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(
                    character.model_dump(mode='json', exclude={'character_dir'}),
                    f,
                    ensure_ascii=False,
                    indent=2
                )
            logger.info(f"Character config saved to {config_file}")
        except Exception as e:
            logger.error(f"Failed to save character config to {config_file}: {e}")
            raise
    

    def list_characters(self) -> list[str]:
        """
        列出所有已缓存的角色
        
        Returns:
            角色名称列表
        """
        characters = []
        for item in self.characters_dir.iterdir():
            if item.is_dir() and (item / "config.json").exists():
                try:
                    with open(item / "config.json", 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    characters.append(data.get('name_zh', item.name))
                except Exception:
                    continue
        return characters
    
    def clear_cache(self, character_name: Optional[str] = None) -> None:
        """
        清除缓存
        
        Args:
            character_name: 角色名称，如果为None则清除所有缓存
        """
        if character_name:
            cache_key = character_name.lower()
            if cache_key in self._cache:
                del self._cache[cache_key]
                logger.info(f"Cache cleared for character '{character_name}'")
        else:
            self._cache.clear()
            logger.info("All character cache cleared")
