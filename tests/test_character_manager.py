"""
测试角色管理器
"""

import sys
import asyncio
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.umamusume_agent.character import CharacterManager

async def test_character_manager():
    """测试角色管理器基本功能"""
    print("=== Character Manager Test ===\n")
    
    manager = CharacterManager()
    
    print("1. 检查角色目录...")
    print(f"  角色目录: {manager.characters_dir}")
    print(f"  目录存在: {manager.characters_dir.exists()}")
    
    print("\n2. 列出已有角色...")
    characters = manager.list_characters()
    if characters:
        print(f"  找到 {len(characters)} 个角色:")
        for char in characters:
            print(f"    - {char}")
    else:
        print("  暂无缓存角色")
    
    print("\n3. 测试缓存检查...")
    test_names = ["特别周", "无声铃鹿", "东海帝王"]
    for name in test_names:
        exists = manager.character_exists(name)
        print(f"  {name}: {'✓ 存在' if exists else '✗ 不存在'}")
    
    print("\n测试完成！")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_character_manager())
    sys.exit(0 if success else 1)

