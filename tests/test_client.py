#!/usr/bin/env python3
"""
赛马娘对话客户端测试和使用示例

演示如何使用 UmamusumeClient 进行流式和非流式对话，并触发 TTS。
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from umamusume_agent.client.umamusume_client import UmamusumeClient


def example_normal_mode(character_name: str):
    """示例：非流式模式"""
    print("=" * 60)
    print("示例 1: 非流式模式")
    print("=" * 60)
    print()
    
    # 创建客户端
    client = UmamusumeClient(server_url="http://127.0.0.1:1111")

    # 加载角色
    load_result = client.load_character(character_name)
    if 'error' in load_result:
        print(f"❌ 加载角色失败: {load_result['error']}")
        return
    session_id = load_result.get("session_id")
    
    # 发送问题
    question = "你好，我们今天的训练安排是什么？"
    print(f"问题: {question}\n")
    print("正在生成，请稍候...\n")
    
    # 获取结果
    result = client.chat(session_id, question, generate_voice=True)
    
    # 处理结果
    if 'error' in result:
        print(f"❌ 错误: {result['error']}")
    else:
        answer = result.get('reply', result.get('response', ''))
        print("✅ 生成完成！\n")
        print("-" * 60)
        print(answer)
        print("-" * 60)
        if result.get("voice"):
            print(f"🔊 语音文件: {result['voice'].get('audio_path')}")
    
    print()


def example_stream_mode(character_name: str):
    """示例：流式模式"""
    print("=" * 60)
    print("示例 2: 流式模式")
    print("=" * 60)
    print()
    
    # 创建客户端
    client = UmamusumeClient(server_url="http://127.0.0.1:1111")

    load_result = client.load_character(character_name)
    if 'error' in load_result:
        print(f"❌ 加载角色失败: {load_result['error']}")
        return
    session_id = load_result.get("session_id")
    
    # 定义事件处理器
    class EventHandler:
        def __init__(self):
            self.reply_started = False
        
        def handle(self, event, data):
            if event == 'token':
                if not self.reply_started:
                    print(f"\r{' ' * 60}\r", end='')
                    print("\n🤖 角色回复:\n")
                    print("-" * 60)
                    self.reply_started = True
                print(data, end='', flush=True)
            
            elif event == 'done':
                if self.reply_started:
                    print()
                    print("-" * 60)
                print("\n✅ 生成完成！\n")

            elif event == 'voice_pending':
                if isinstance(data, dict):
                    print(f"\n🔊 语音合成中: {data.get('audio_path')}\n")
                else:
                    print(f"\n🔊 语音合成中: {data}\n")
            
            elif event == 'error':
                print(f"\n❌ 错误: {data}\n")
    
    # 发送问题
    question = "今天训练完可以一起做拉伸吗？"
    print(f"问题: {question}\n")
    
    # 创建事件处理器
    handler = EventHandler()
    
    # 流式生成
    try:
        client.chat_stream(session_id, question, handler.handle, generate_voice=True)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消生成\n")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}\n")


def main():
    """主函数"""
    print("\n")
    print("*" * 60)
    print("*" + " " * 58 + "*")
    print("*" + "   赛马娘对话客户端测试示例".center(56) + "*")
    print("*" + " " * 58 + "*")
    print("*" * 60)
    print()
    
    print("本示例将演示两种使用方式：")
    print("1. 非流式模式 - 等待完整结果")
    print("2. 流式模式 - 实时显示生成过程")
    print()

    character_name = "爱慕织姬"
    
    import time
    
    try:
        # 示例 1: 非流式模式
        print("按回车开始示例 1...")
        input()
        example_normal_mode(character_name)
        time.sleep(1)
        
        # 示例 2: 流式模式
        print("按回车开始示例 2...")
        input()
        example_stream_mode(character_name)
        
        print("\n" + "=" * 60)
        print("所有示例完成！")
        print("=" * 60)
        print()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消\n")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
