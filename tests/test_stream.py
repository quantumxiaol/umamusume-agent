#!/usr/bin/env python3
"""
测试流式接口的脚本

使用方法:
    python tests/test_stream.py
    python tests/test_stream.py --url http://127.0.0.1:1111 --question "你的问题"
"""

import argparse
import json
import sys
import requests
from datetime import datetime


class Colors:
    """终端颜色"""
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    YELLOW = '\033[1;33m'
    CYAN = '\033[0;36m'
    RED = '\033[0;31m'
    MAGENTA = '\033[0;35m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color


def print_colored(color, prefix, message):
    """打印带颜色的消息"""
    print(f"{color}{prefix}{Colors.NC} {message}")


def format_timestamp():
    """格式化时间戳"""
    return datetime.now().strftime("%H:%M:%S")


def test_stream_api(server_url: str, question: str):
    """测试流式 API"""
    url = f"{server_url}/askstream"
    
    print("=" * 60)
    print(f"{Colors.BOLD}测试流式接口{Colors.NC}")
    print(f"URL: {url}")
    print(f"问题: {question}")
    print("=" * 60)
    print()
    
    # 统计信息
    stats = {
        'token_count': 0,
        'total_chars': 0,
        'start_time': datetime.now(),
        'stages': []
    }
    
    try:
        # 发送流式请求
        response = requests.post(
            url,
            json={"question": question},
            stream=True,
            timeout=600
        )
        
        if response.status_code != 200:
            print_colored(Colors.RED, "[错误]", f"HTTP {response.status_code}: {response.text}")
            return
        
        # 处理流式响应
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            try:
                data = json.loads(line)
                event = data.get('event', 'unknown')
                event_data = data.get('data', '')
                
                timestamp = format_timestamp()
                
                if event == 'status':
                    # 状态更新
                    print_colored(Colors.CYAN, f"[{timestamp}] [状态]", event_data)
                    stats['stages'].append(event_data)
                
                elif event == 'rag_result':
                    # RAG 搜索结果
                    length = len(event_data)
                    print_colored(Colors.GREEN, f"[{timestamp}] [RAG完成]", f"结果长度: {length} 字符")
                    print(f"{Colors.YELLOW}  RAG 结果预览:{Colors.NC}")
                    print(f"  {event_data[:1000]}..." if len(event_data) > 1000 else f"  {event_data}")
                    print()
                
                elif event == 'web_result':
                    # Web 搜索结果
                    length = len(event_data)
                    print_colored(Colors.GREEN, f"[{timestamp}] [Web完成]", f"结果长度: {length} 字符")
                    print(f"{Colors.YELLOW}  Web 结果预览:{Colors.NC}")
                    print(f"  {event_data[:2000]}..." if len(event_data) > 2000 else f"  {event_data}")
                    print()
                
                elif event == 'token':
                    # 小说内容流式输出
                    stats['token_count'] += 1
                    stats['total_chars'] += len(event_data)
                    
                    # 每10个token显示一次进度
                    if stats['token_count'] % 10 == 0:
                        print_colored(
                            Colors.BLUE, 
                            f"[{timestamp}] [生成中...]",
                            f"已接收 {stats['token_count']} 个token块, 共 {stats['total_chars']} 字符"
                        )
                    
                    # 实时显示内容（可选，注释掉避免刷屏）
                    # print(event_data, end='', flush=True)
                
                elif event == 'done':
                    # 完成
                    elapsed = (datetime.now() - stats['start_time']).total_seconds()
                    print()
                    print_colored(Colors.GREEN, f"[{timestamp}] [完成]", "流式生成完成！")
                    print(f"\n{Colors.BOLD}📊 统计信息:{Colors.NC}")
                    print(f"  • 总耗时: {elapsed:.2f} 秒")
                    print(f"  • Token块数: {stats['token_count']}")
                    print(f"  • 总字符数: {stats['total_chars']}")
                    print(f"  • 平均速度: {stats['total_chars']/elapsed:.1f} 字符/秒")
                    print(f"\n{Colors.BOLD}📝 经历阶段:{Colors.NC}")
                    for i, stage in enumerate(stats['stages'], 1):
                        print(f"  {i}. {stage}")
                    print()
                
                elif event == 'error':
                    # 错误
                    print_colored(Colors.RED, f"[{timestamp}] [错误]", event_data)
                
                else:
                    # 未知事件
                    print_colored(Colors.YELLOW, f"[{timestamp}] [未知事件]", f"{event}: {event_data[:100]}")
            
            except json.JSONDecodeError as e:
                print_colored(Colors.YELLOW, "[解析错误]", f"{e}")
                print(f"  原始数据: {line[:200]}")
        
        print("=" * 60)
        print(f"{Colors.GREEN}✓ 测试完成{Colors.NC}")
        print("=" * 60)
    
    except requests.exceptions.RequestException as e:
        print_colored(Colors.RED, "[网络错误]", str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print_colored(Colors.YELLOW, "[中断]", "用户取消测试")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="测试赛马娘小说生成流式接口",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '-u', '--url',
        default='http://127.0.0.1:1111',
        help='服务器 URL (默认: http://127.0.0.1:1111)'
    )
    parser.add_argument(
        '-q', '--question',
        default='请创作一篇关于米浴和训练员的温馨故事',
        help='提问内容'
    )
    
    args = parser.parse_args()
    
    test_stream_api(args.url, args.question)


if __name__ == '__main__':
    main()

