"""
赛马娘对话 Agent 客户端

使用方法:
    # 非流式模式（默认）
    python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬"
    python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" -q "你好"

    # 流式模式
    python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream
    python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream -q "今天训练什么？"
"""

import argparse
from datetime import datetime
from .umamusume_client import UmamusumeClient


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


class StreamHandler:
    """处理流式输出"""

    def __init__(self):
        self.reply_content = ''
        self.token_count = 0
        self.start_time = datetime.now()

    def handle_event(self, event: str, data):
        """处理流式事件"""
        if event == 'token':
            if self.token_count == 0:
                print(f"\n{Colors.BOLD}角色回复:{Colors.NC}\n")
                print("-" * 60)

            data = self._normalize_dialogue_markers(data)
            self.reply_content += data
            self.token_count += 1
            print(data, end='', flush=True)

        elif event == 'done':
            if self.token_count > 0:
                print()
                print("-" * 60)
            elapsed = (datetime.now() - self.start_time).total_seconds()
            print(f"\n{Colors.GREEN}✓ 生成完成！{Colors.NC}")
            print(f"{Colors.BOLD}统计信息:{Colors.NC}")
            print(f"  • 耗时: {elapsed:.2f} 秒")
            print(f"  • Token块数: {self.token_count}")
            print(f"  • 总字符数: {len(self.reply_content)}")
            if elapsed > 0:
            print(f"  • 平均速度: {len(self.reply_content)/elapsed:.1f} 字符/秒")

        elif event == 'voice':
            if isinstance(data, dict):
                audio_path = data.get("audio_path")
                print(f"\n{Colors.MAGENTA}🔊 语音已生成: {audio_path}{Colors.NC}")
            else:
                print(f"\n{Colors.MAGENTA}🔊 语音事件: {data}{Colors.NC}")

        elif event == 'voice_pending':
            if isinstance(data, dict):
                audio_path = data.get("audio_path")
                print(f"\n{Colors.MAGENTA}⏳ 正在合成语音: {audio_path}{Colors.NC}")
            else:
                print(f"\n{Colors.MAGENTA}⏳ 语音合成中: {data}{Colors.NC}")

        elif event == 'error':
            print(f"\r{Colors.RED}[错误] {data}{Colors.NC}")

        else:
            print(f"\r{Colors.YELLOW}[未知事件] {event}: {str(data)[:100]}{Colors.NC}")

    def _normalize_dialogue_markers(self, chunk: str) -> str:
        markers = ["对白：", "对白:", "台词：", "台词:", "对话：", "对话:", "TTS：", "TTS:"]
        for marker in markers:
            if marker in chunk:
                if "动作：" in self.reply_content and "\n" + marker not in self.reply_content:
                    return chunk.replace(marker, f"\n{marker}", 1)
                if "动作:" in self.reply_content and "\n" + marker not in self.reply_content:
                    return chunk.replace(marker, f"\n{marker}", 1)
        return chunk


def handle_question_stream(
    client: UmamusumeClient,
    session_id: str,
    question: str,
    generate_voice: bool,
    show_question: bool = True,
):
    """处理流式问答"""
    if show_question:
        print(f"\n{Colors.BOLD}You:{Colors.NC} {question}\n")

    handler = StreamHandler()

    try:
        client.chat_stream(session_id, question, handler.handle_event, generate_voice=generate_voice)
        print()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠ 用户取消生成{Colors.NC}")
    except Exception as e:
        print(f"\n\n{Colors.RED}错误: {e}{Colors.NC}")


def handle_question_normal(
    client: UmamusumeClient,
    session_id: str,
    question: str,
    generate_voice: bool,
    show_question: bool = True,
):
    """处理非流式问答"""
    if show_question:
        print(f"\n{Colors.BOLD}You:{Colors.NC} {question}\n")
    print(f"{Colors.CYAN}正在生成中，请稍候...{Colors.NC}\n")

    try:
        result = client.chat(session_id, question, generate_voice=generate_voice)

        if 'error' in result:
            print(f"{Colors.RED}错误: {result['error']}{Colors.NC}")
            return

        answer = result.get('reply', result.get('response', ''))
        if answer:
            display_text = _format_reply_for_display(answer)
            print(f"{Colors.BOLD}角色回复:{Colors.NC}\n")
            print("-" * 60)
            print(display_text)
            print("-" * 60)
        else:
            print(f"{Colors.YELLOW}⚠ 未收到回复内容{Colors.NC}")

        if result.get("voice"):
            audio_path = result["voice"].get("audio_path")
            print(f"\n{Colors.MAGENTA}🔊 语音已生成: {audio_path}{Colors.NC}")

        print(f"\n{Colors.GREEN}✓ 生成完成！{Colors.NC}\n")

    except Exception as e:
        print(f"{Colors.RED}错误: {e}{Colors.NC}")


def _format_reply_for_display(text: str) -> str:
    action_lines = []
    dialogue_lines = []
    capture_dialogue = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")):
            capture_dialogue = False
            action_lines.append(line.split(":", 1)[-1].split("：", 1)[-1].strip())
            continue
        if line.startswith(("对白：", "对白:", "台词：", "台词:", "对话：", "对话:", "TTS：", "TTS:")):
            capture_dialogue = True
            content = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            if content:
                dialogue_lines.append(content)
            continue
        if capture_dialogue:
            dialogue_lines.append(line)
        else:
            action_lines.append(line)

    if dialogue_lines:
        action_text = " ".join(action_lines).strip()
        dialogue_text = " ".join(dialogue_lines).strip()
        if action_text:
            return f"动作：{action_text}\n对白：{dialogue_text}"
        return dialogue_text

    return text.strip()


def main():
    parser = argparse.ArgumentParser(
        description="赛马娘对话 Agent 客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互模式（非流式）
  python -m src.umamusume_agent.client.cli -c "爱慕织姬"

  # 交互模式（流式）
  python -m src.umamusume_agent.client.cli -c "爱慕织姬" --stream

  # 单次问答（非流式）
  python -m src.umamusume_agent.client.cli -c "爱慕织姬" -q "你好"

  # 单次问答（流式）
  python -m src.umamusume_agent.client.cli -c "爱慕织姬" --stream -q "今天训练什么？"
        """
    )
    parser.add_argument(
        "-u", "--server-url",
        type=str,
        default="http://127.0.0.1:1111",
        help="后端服务地址，默认 http://127.0.0.1:1111"
    )
    parser.add_argument(
        "-c", "--character",
        type=str,
        help="角色名称（中文）"
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="强制重新构建角色配置（如果服务端支持）"
    )
    parser.add_argument(
        "-q", "--question",
        type=str,
        help="直接提问并退出（非交互模式）"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="使用流式模式（实时显示生成内容）"
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="启用 TTS 语音输出"
    )

    args = parser.parse_args()

    client = UmamusumeClient(server_url=args.server_url)

    character_name = args.character
    if not character_name:
        character_name = input(f"{Colors.BOLD}请输入角色名称:{Colors.NC} ").strip()
    if not character_name:
        print(f"{Colors.RED}未提供角色名称，退出。{Colors.NC}")
        return

    load_result = client.load_character(character_name, force_rebuild=args.force_rebuild)
    if "error" in load_result:
        print(f"{Colors.RED}加载角色失败: {load_result['error']}{Colors.NC}")
        return

    session_id = load_result.get("session_id")
    output_dir = load_result.get("output_dir")
    if not session_id:
        print(f"{Colors.RED}加载角色失败: 未返回 session_id{Colors.NC}")
        return

    print(f"\n{Colors.BOLD}{'='*60}{Colors.NC}")
    print(f"{Colors.BOLD}{'  赛马娘交互式Agent':^56}{Colors.NC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.NC}")
    print(f"\n模式: {Colors.CYAN}{'流式' if args.stream else '非流式'}{Colors.NC}")
    print(f"服务器: {Colors.CYAN}{args.server_url}{Colors.NC}")
    print(f"角色: {Colors.CYAN}{character_name}{Colors.NC}")
    if output_dir:
        print(f"音频输出: {Colors.CYAN}{output_dir}{Colors.NC}")
    print(f"TTS: {Colors.CYAN}{'开启' if args.voice else '关闭'}{Colors.NC}")
    print(f"\n命令:")
    print(f"  • 输入问题开始生成")
    print(f"  • 输入 'exit' 或 'quit' 退出")
    print(f"  • 输入 'mode' 切换流式/非流式模式")
    print(f"  • 输入 'voice' 切换语音开关")
    print(f"  • 输入 'character <name>' 切换角色")
    print(f"{Colors.BOLD}{'='*60}{Colors.NC}\n")

    if args.question:
        if args.stream:
            handle_question_stream(client, session_id, args.question, args.voice, show_question=True)
        else:
            handle_question_normal(client, session_id, args.question, args.voice, show_question=True)
        return

    stream_mode = args.stream
    voice_enabled = args.voice

    while True:
        try:
            user_input = input(f"{Colors.BOLD}You:{Colors.NC} ").strip()

            if user_input.lower() in ["exit", "quit"]:
                print(f"\n{Colors.GREEN}再见！{Colors.NC}\n")
                break

            if user_input.lower() == "mode":
                stream_mode = not stream_mode
                print(f"\n{Colors.CYAN}已切换到 {'流式' if stream_mode else '非流式'} 模式{Colors.NC}\n")
                continue

            if user_input.lower() == "voice":
                voice_enabled = not voice_enabled
                print(f"\n{Colors.CYAN}语音已{'开启' if voice_enabled else '关闭'}{Colors.NC}\n")
                continue

            if user_input.lower().startswith("character "):
                new_name = user_input.split(" ", 1)[1].strip()
                if not new_name:
                    print(f"\n{Colors.YELLOW}请输入角色名称{Colors.NC}\n")
                    continue
                load_result = client.load_character(new_name)
                if "error" in load_result:
                    print(f"{Colors.RED}加载角色失败: {load_result['error']}{Colors.NC}")
                    continue
                session_id = load_result.get("session_id")
                character_name = new_name
                output_dir = load_result.get("output_dir")
                print(f"\n{Colors.CYAN}已切换角色: {character_name}{Colors.NC}")
                if output_dir:
                    print(f"{Colors.CYAN}音频输出: {output_dir}{Colors.NC}\n")
                continue

            if not user_input:
                continue

            if stream_mode:
                handle_question_stream(client, session_id, user_input, voice_enabled, show_question=False)
            else:
                handle_question_normal(client, session_id, user_input, voice_enabled, show_question=False)

        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.GREEN}再见！{Colors.NC}\n")
            break
        except Exception as e:
            print(f"\n{Colors.RED}错误: {e}{Colors.NC}\n")
            continue


if __name__ == "__main__":
    main()
