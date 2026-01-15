"""
文本优化器 - 提高 TTS 生成质量
基于测试发现的规律优化文本
"""

from typing import List, Tuple


class TextOptimizer:
    """
    文本优化器
    
    根据测试发现的规律优化文本以提高 TTS 质量：
    1. 确保句子与参考音频风格匹配
    2. 添加角色常用的称呼和表达
    3. 将短句扩展为自然的对话
    4. 避免测试式/陈述式的表达
    """
    
    # 角色常用称呼
    COMMON_ADDRESSES = [
        "训练员桑",
        "トレーナーさん",
    ]
    
    # 自然的对话扩展词
    NATURAL_EXTENSIONS = {
        "zh": [
            "啊", "呢", "哦", "吧", "呀", "嘛"
        ],
        "ja": [
            "ね", "よ", "な", "か", "わ"
        ]
    }
    
    @classmethod
    def optimize(cls, text: str, character_name: str = None, min_length: int = 10) -> str:
        """
        优化文本以提高 TTS 质量
        
        Args:
            text: 原始文本
            character_name: 角色名（可选）
            min_length: 最小长度（字符数）
            
        Returns:
            优化后的文本
        """
        text = text.strip()
        
        # 1. 检查是否是测试式/陈述式句子，转换为对话式
        text = cls._conversational_style(text)
        
        # 2. 添加称呼（如果没有）
        if not cls._has_address(text):
            text = cls._add_address(text)
        
        # 3. 如果太短，自然扩展
        if len(text) < min_length:
            text = cls._natural_extend(text, min_length)
        
        # 4. 添加自然的语气词（如果缺少）
        text = cls._add_tone_particle(text)
        
        return text
    
    @classmethod
    def _conversational_style(cls, text: str) -> str:
        """将测试式/陈述式句子转换为对话式"""
        
        # 常见的测试式开头
        test_patterns = {
            "这是": "嗯，",
            "这里": "这里",
            "测试": "",
        }
        
        for pattern, replacement in test_patterns.items():
            if text.startswith(pattern):
                # 移除测试式开头
                text = text[len(pattern):]
                if replacement:
                    text = replacement + text
                break
        
        return text
    
    @classmethod
    def _has_address(cls, text: str) -> bool:
        """检查是否包含称呼"""
        return any(addr in text for addr in cls.COMMON_ADDRESSES)
    
    @classmethod
    def _add_address(cls, text: str) -> str:
        """添加称呼"""
        # 检测语言
        if cls._is_japanese(text):
            address = "トレーナーさん"
            # 日语通常称呼在前，用逗号分隔
            if not text.startswith(address):
                text = f"{address}、{text}"
        else:
            # 中文
            address = "训练员桑"
            if not text.startswith(address):
                # 如果是问句或陈述，加在开头
                text = f"{address}，{text}"
        
        return text
    
    @classmethod
    def _natural_extend(cls, text: str, min_length: int) -> str:
        """自然扩展短句"""
        
        if len(text) >= min_length:
            return text
        
        # 检测语言
        is_japanese = cls._is_japanese(text)
        
        # 根据句子类型选择扩展方式
        if text.endswith(("好", "好！", "好。")):
            # 问候语
            if is_japanese:
                extensions = ["今日もよろしくお願いします。", "頑張りましょう。"]
            else:
                extensions = ["今天也要加油哦。", "很高兴见到你。"]
            
            # 移除结尾标点再添加
            text = text.rstrip("。！？!?")
            return f"{text}！{extensions[0]}"
        
        elif text.endswith(("谢", "谢谢", "谢谢。", "ありがとう", "ありがとうございます")):
            # 感谢语
            if is_japanese:
                return f"{text.rstrip('。')}。とても嬉しいです。"
            else:
                return f"{text.rstrip('。')}。很感谢你。"
        
        else:
            # 通用扩展：添加语气词和简短补充
            if is_japanese:
                if not text.endswith(("ね", "よ", "な")):
                    text = text.rstrip("。") + "ね。"
            else:
                if not text.endswith(("啊", "呢", "哦", "吧")):
                    text = text.rstrip("。") + "啊。"
        
        return text
    
    @classmethod
    def _add_tone_particle(cls, text: str) -> str:
        """添加自然的语气词"""
        
        # 如果句子已经有语气词，不添加
        if cls._is_japanese(text):
            if any(text.endswith(p) for p in ["ね。", "よ。", "な。", "わ。", "か。"]):
                return text
            # 日语通常在句尾添加
            if text.endswith("。") or text.endswith("！"):
                return text
        else:
            # 中文
            if any(p in text[-3:] for p in ["啊", "呢", "哦", "吧", "呀", "嘛"]):
                return text
            
            # 根据句子类型添加合适的语气词
            if text.endswith("！"):
                # 感叹句已经有强烈语气
                return text
            elif text.endswith("？"):
                # 疑问句可以加"呢"
                return text.replace("？", "呢？")
            elif text.endswith("。"):
                # 陈述句加"哦"或"啊"
                return text.replace("。", "哦。")
        
        return text
    
    @classmethod
    def _is_japanese(cls, text: str) -> bool:
        """检测是否是日语"""
        # 简单检测：是否包含平假名或片假名
        for char in text:
            if '\u3040' <= char <= '\u309F' or '\u30A0' <= char <= '\u30FF':
                return True
        return False
    
    @classmethod
    def suggest_improvements(cls, text: str) -> List[Tuple[str, str]]:
        """
        为给定文本提供改进建议
        
        Args:
            text: 原始文本
            
        Returns:
            改进建议列表 [(原因, 改进后的文本), ...]
        """
        suggestions = []
        
        # 1. 检查长度
        if len(text) < 10:
            improved = cls.optimize(text)
            suggestions.append(("句子太短", improved))
        
        # 2. 检查是否有称呼
        if not cls._has_address(text):
            improved = cls._add_address(text)
            suggestions.append(("添加称呼更自然", improved))
        
        # 3. 检查是否是测试式语句
        if text.startswith(("这是", "这里是", "测试")):
            improved = cls._conversational_style(text)
            suggestions.append(("转换为对话式", improved))
        
        # 4. 检查语气词
        if not any(p in text for p in ["啊", "呢", "哦", "吧", "呀", "ね", "よ", "な"]):
            improved = cls._add_tone_particle(text)
            suggestions.append(("添加语气词", improved))
        
        return suggestions


def demo():
    """演示文本优化器的使用"""
    print("=== 文本优化器演示 ===\n")
    
    optimizer = TextOptimizer()
    
    test_cases = [
        "早上好",
        "早上好。",
        "这是测试句子。",
        "谢谢。",
        "今天天气很好。",
        "おはよう",
        "ありがとうございます",
    ]
    
    for text in test_cases:
        print(f"原文: {text}")
        
        # 优化
        optimized = optimizer.optimize(text)
        print(f"优化: {optimized}")
        
        # 建议
        suggestions = optimizer.suggest_improvements(text)
        if suggestions:
            print(f"建议:")
            for reason, improved in suggestions:
                print(f"  - {reason}: {improved}")
        
        print()


if __name__ == "__main__":
    demo()

