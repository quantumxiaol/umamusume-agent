# Characters 目录说明

此目录用于存储赛马娘角色的配置文件和相关资源。每个角色有一个独立的子目录。

## 目录结构标准

```
characters/
├── README.md (本文件)
├── admire_vega/               # 角色目录（使用英文名小写+下划线）
│   ├── config.json            # 核心配置文件
│   ├── reference.wav          # 语音克隆参考音频（处理后的标准格式）
│   ├── reference_jp.txt       # 参考音频对应的文本
│   ├── reference_zh.txt       # 参考音频对应的中文文本
│   └── avatar.png             # 角色头像（可选）
└── satono_diamond/            # 另一个角色
    ├── config.json
    ├── reference.wav
    └── ...
```

## config.json 结构

```json
{
  "id": "uma_001",
  "name_zh": "特别周",
  "name_en": "Special Week",
  "name_jp": "スペシャルウィーク",
  "version": "1.0",
  "created_at": "2025-12-30T10:00:00Z",
  "last_updated": "2025-12-30T10:00:00Z",
  
  "system_prompt": "你现在扮演赛马娘'特别周'。你性格开朗、直率...",
  
  "personality": {
    "traits": ["开朗", "元气", "天然呆", "贪吃"],
    "speaking_style": "元气满满，偶尔会表现出对大城市的不适应",
    "pronouns": {
      "self": "我",
      "user": "训练员桑"
    },
    "catchphrases": ["Hauu~", "我要成为日本第一！"]
  },
  
  "voice_config": {
    "model": "CosyVoice-3.0",
    "ref_audio_path": "./reference.wav",
    "ref_text_path": "./reference_text.txt",
    "language_code": "ja-JP",
    "sample_rate": 22050
  },
  
  "metadata": {
    "cv": "和气杏未",
    "birthday": "5月2日",
    "height": "158cm",
    "source_wiki": "https://wiki.biligame.com/umamusume/特别周",
    "retrieved_at": "2025-12-30T10:00:00Z"
  }
}
```

## 字段说明

### 基础信息
- `id`: 角色唯一标识符
- `name_*`: 多语言名称
- `version`: 配置版本号
- `created_at/last_updated`: 时间戳

### 人格配置
- `system_prompt`: LLM 角色扮演的系统提示词
- `personality`: 结构化的性格特征
  - `traits`: 性格关键词列表
  - `speaking_style`: 说话风格描述
  - `pronouns`: 人称代词（自称/他称）
  - `catchphrases`: 口癖/标志性台词

### 语音配置
- `voice_config.model`: 使用的 TTS 模型
- `voice_config.ref_audio_path`: 参考音频路径（相对于角色目录）
- `voice_config.ref_text_path`: 参考音频文本路径
- `voice_config.language_code`: 语言代码（ja-JP/zh-CN/en-US）

### 元数据
- `metadata.cv`: 声优名称
- `metadata.source_wiki`: 数据来源 Wiki URL
- `metadata.retrieved_at`: 数据获取时间

## 音频文件要求

### reference.wav 规格
- 格式: WAV (PCM)
- 采样率: 22050 Hz (或根据 config.json 设置)
- 声道: 单声道 (Mono)
- 时长: 3-10 秒（推荐 5 秒左右）
- 质量要求:
  - 清晰无杂音
  - 无背景音乐
  - 无剧烈情绪波动（如尖叫、喘息）
  - 音量适中

### reference_text.txt
包含 reference.wav 对应的精确文本内容，用于提高 TTS 克隆准确度。

## 角色构建流程

1. **触发构建**: 用户输入角色名称
2. **查询 CSV**: 获取角色标准名称和 Wiki URL
3. **爬取数据**: 
   - 从 Wiki 获取角色简介、性格描述
   - 定位并下载语音文件
4. **LLM 提取**: 
   - 生成 system_prompt
   - 提取 personality 字段
5. **音频处理**:
   - 选择最优音频（LLM 辅助筛选）
   - 转码为标准格式 (16kHz/22050Hz mono WAV)
6. **持久化**: 保存到 `characters/<角色名>/`

## 缓存策略

- **Cache Hit**: 如果 `characters/<角色名>/config.json` 存在且有效，直接加载
- **Cache Miss**: 触发构建流程
- **Cache 刷新**: 可手动删除角色目录重新构建

## 使用示例

```python
from umamusume_agent.character import CharacterManager

manager = CharacterManager()

# 加载角色（自动处理缓存）
character = await manager.load_character("特别周")

# 获取系统提示词
system_prompt = character.get_system_prompt()

# 获取语音配置
voice_config = character.get_voice_config()

# 生成语音
tts_engine.synthesize(
    text="训练员桑，今天也要一起加油呢！",
    ref_audio=voice_config['ref_audio_path'],
    ref_text=voice_config['ref_text']
)
```

## 注意事项

1. **文件命名**: 角色目录使用英文名小写+下划线（如 `special_week`）
2. **路径**: config.json 中的路径使用相对于角色目录的相对路径
3. **版本管理**: 更新角色配置时增加 version 并更新 last_updated
4. **备份**: raw_audio/ 保留原始文件以便重新处理
5. **隐私**: 不要上传包含个人信息的音频文件

