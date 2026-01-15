# 音频筛选流程（TTS Prompt 选样）

本项目用于从 `result-voices/` 中选出更适合作为 TTS Prompt 的参考音频，目标是：尽量避免鼻音、唱歌/哼唱、长静音，同时保证一定能选出样本。

## 流程概览

1) 文本硬过滤（仅日文）：基于正则规则判定是否可用，若不合格则扣分或标记问题。  
2) 音频特征分析（librosa）：统计有效时长、静音比例、频谱质心、F0 波动、鼻音能量比等。  
3) 综合评分：对每条候选打分，选择分数最高的样本作为角色参考音频。  
4) 必选策略：不再硬淘汰所有样本，确保每个角色至少会选出一个参考音频（哪怕质量一般）。

## 文本过滤规则（日文 `*_jp.txt`）

来源：`src/umamusume_agent/builder/quality_filter.py`

- 符号占比限制：标点/省略号占比过高会扣分（避免“……/...”密集）。
- 拟声词/呼吸词黑名单：如 `ふんふん/ラララ/すぅ/はっ` 等。
- 重复假名检测：`あああ/ふふふ` 这类重复句。
- 汉字缺失检查：长句且无汉字判为低质量。

当前阈值（可调）：
- `max_symbol_ratio=0.3`
- `max_ellipsis_ratio=0.12`
- `min_length=5`
- `max_length=50`

## 音频分析指标（librosa）

来源：`src/umamusume_agent/builder/quality_filter.py`

1) 有效时长  
- 使用 `librosa.effects.split/trim` 去静音，统计有效时长和静音占比。  
- 允许 10-15 秒优先，最长 35 秒（超过会扣分）。

2) 鼻音/闷音  
- 频谱质心（spectral centroid）：过低视为“闷/鼻音重”。  
- 鼻音能量比：1000-2500Hz / 200-800Hz 作为鼻音强度参考。

3) 唱歌/哼唱  
- `librosa.pyin` 提取 F0，低波动或异常范围会扣分。

## 综合评分（而非硬过滤）

评分逻辑在 `score_audio()` 中实现，核心思路：

- 有效时长在 10-15 秒加分最多；3-10 秒次之；15-35 秒少量加分。  
- 静音比例/静音段长度会扣分，但权重较低（更容忍静音）。  
- 鼻音能量比高会重罚（更排斥鼻音）。  
- F0 波动过低或音高范围异常会扣分（过滤哼唱/唱歌）。  

> 结果：仍能保证选出“相对最优”的样本，即便全局质量一般。

## 并行处理

`build_character.py` 支持并行分析，提高筛选速度：

```bash
python build_character.py --workers 2
```

推荐 2 或 4，根据机器 CPU 核数调整。

## 关键配置位置

- 文本规则：`src/umamusume_agent/builder/quality_filter.py`  
- 音频阈值：`AudioFilterConfig`（同文件）  
- 评分逻辑：`score_audio()`（同文件）  
- 入口脚本：`build_character.py`
