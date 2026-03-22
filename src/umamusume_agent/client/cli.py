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
import getpass
import json
import re
import uuid
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


_ACTION_LABELS = {"动作", "神态", "场景", "神情", "表情"}
_DIALOGUE_LABELS = {"对白", "台词", "对话", "tts", "dialogue", "speech"}
_ACTION_MARKERS = ("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")
_DIALOGUE_MARKERS = ("对白：", "对白:", "台词：", "台词:", "对话：", "对话:", "TTS：", "TTS:")
_LABELLED_LINE_PATTERN = re.compile(r"^(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<content>.*)$")
_INLINE_SECOND_LABEL_PATTERN = re.compile(
    r"^(?P<action>.*?[。！？；;…])\s*(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<dialogue>.+)$"
)


class StreamHandler:
    """处理流式输出"""

    def __init__(self, debug: bool = False):
        self.reply_content = ""
        self.raw_reply_content = ""
        self.token_count = 0
        self.start_time = datetime.now()
        self.debug = debug
        self.debug_tokens: list[tuple[int, str, str]] = []

    def handle_event(self, event: str, data):
        """处理流式事件"""
        if event == "token":
            if self.token_count == 0:
                print(f"\n{Colors.BOLD}角色回复:{Colors.NC}\n")
                print("-" * 60)

            raw_data = str(data)
            normalized_data = self._normalize_dialogue_markers(raw_data)
            self.raw_reply_content += raw_data
            self.reply_content += normalized_data
            self.token_count += 1
            if self.debug:
                self.debug_tokens.append((self.token_count, raw_data, normalized_data))
            print(normalized_data, end="", flush=True)

        elif event == "done":
            if self.token_count > 0:
                print()
                print("-" * 60)
            formatted = _format_reply_for_display(self.reply_content)
            if formatted and formatted.strip() != self.reply_content.strip():
                print(f"\n{Colors.CYAN}格式化显示:{Colors.NC}")
                print("-" * 60)
                print(formatted)
                print("-" * 60)
            if self.debug:
                action_text, dialogue_text = _split_action_dialogue(self.reply_content)
                print(f"\n{Colors.YELLOW}[DEBUG] 流式聚合(raw):{Colors.NC}")
                print(self.raw_reply_content)
                print(f"\n{Colors.YELLOW}[DEBUG] 流式聚合(normalized):{Colors.NC}")
                print(self.reply_content)
                print(f"\n{Colors.YELLOW}[DEBUG] 解析结果:{Colors.NC}")
                print(f"  action={action_text!r}")
                print(f"  dialogue={dialogue_text!r}")
                print(f"\n{Colors.YELLOW}[DEBUG] Token 明细(raw -> normalized):{Colors.NC}")
                for idx, raw_token, normalized_token in self.debug_tokens:
                    print(f"  #{idx:03d} {raw_token!r} -> {normalized_token!r}")
            elapsed = (datetime.now() - self.start_time).total_seconds()
            print(f"\n{Colors.GREEN}✓ 生成完成！{Colors.NC}")
            print(f"{Colors.BOLD}统计信息:{Colors.NC}")
            print(f"  • 耗时: {elapsed:.2f} 秒")
            print(f"  • Token块数: {self.token_count}")
            print(f"  • 总字符数: {len(self.reply_content)}")
            if elapsed > 0:
                print(f"  • 平均速度: {len(self.reply_content)/elapsed:.1f} 字符/秒")

        elif event == "voice":
            if isinstance(data, dict):
                audio_path = data.get("audio_path")
                print(f"\n{Colors.MAGENTA}🔊 语音已生成: {audio_path}{Colors.NC}")
                if self.debug:
                    print(f"{Colors.YELLOW}[DEBUG] voice payload:{Colors.NC}")
                    print(_debug_json(data))
            else:
                print(f"\n{Colors.MAGENTA}🔊 语音事件: {data}{Colors.NC}")

        elif event == "voice_pending":
            if isinstance(data, dict):
                audio_path = data.get("audio_path")
                print(f"\n{Colors.MAGENTA}⏳ 正在合成语音: {audio_path}{Colors.NC}")
                if self.debug:
                    print(f"{Colors.YELLOW}[DEBUG] voice_pending payload:{Colors.NC}")
                    print(_debug_json(data))
            else:
                print(f"\n{Colors.MAGENTA}⏳ 语音合成中: {data}{Colors.NC}")

        elif event == "error":
            print(f"\r{Colors.RED}[错误] {data}{Colors.NC}")

        else:
            print(f"\r{Colors.YELLOW}[未知事件] {event}: {str(data)[:100]}{Colors.NC}")

    def _normalize_dialogue_markers(self, chunk: str) -> str:
        normalized_chunk = chunk
        has_action_marker = any(marker in self.reply_content for marker in _ACTION_MARKERS)
        has_dialogue_marker = any(marker in self.reply_content for marker in _DIALOGUE_MARKERS)

        if has_action_marker and not has_dialogue_marker:
            match = _LABELLED_LINE_PATTERN.match(normalized_chunk.strip())
            if match:
                label = match.group("label").strip().lower()
                content = match.group("content")
                if label not in _ACTION_LABELS:
                    normalized_chunk = f"对白：{content}"

        for marker in _DIALOGUE_MARKERS:
            if marker in normalized_chunk:
                if "动作：" in self.reply_content and "\n" + marker not in self.reply_content:
                    return normalized_chunk.replace(marker, f"\n{marker}", 1)
                if "动作:" in self.reply_content and "\n" + marker not in self.reply_content:
                    return normalized_chunk.replace(marker, f"\n{marker}", 1)
        return normalized_chunk


def handle_question_stream(
    client: UmamusumeClient,
    session_id: str,
    question: str,
    generate_voice: bool,
    debug: bool = False,
    show_question: bool = True,
):
    """处理流式问答"""
    if show_question:
        print(f"\n{Colors.BOLD}You:{Colors.NC} {question}\n")

    handler = StreamHandler(debug=debug)

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
    debug: bool = False,
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
        if debug:
            print(f"{Colors.YELLOW}[DEBUG] chat 响应:{Colors.NC}")
            print(_debug_json(result))
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
    action_text, dialogue_text = _split_action_dialogue(text)
    if dialogue_text:
        if action_text:
            return f"动作：{action_text}\n对白：{dialogue_text}"
        return dialogue_text
    if action_text:
        return f"动作：{action_text}"
    return text.strip()


def _debug_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def _derive_user_uuid_from_local_user() -> str:
    username = (getpass.getuser() or "").strip() or "unknown"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"umamusume-agent-cli:{username}"))


def _resolve_user_uuid(user_uuid_arg: str | None) -> str:
    if user_uuid_arg:
        try:
            return str(uuid.UUID(user_uuid_arg))
        except ValueError:
            print(f"{Colors.YELLOW}⚠ 提供的 user_uuid 非法，改用本机用户名派生 UUID。{Colors.NC}")
    return _derive_user_uuid_from_local_user()


def _strip_dialogue_leading_punct(text: str) -> str:
    return re.sub(r"^[：:\s]+", "", text).strip()


def _parse_labelled_line(line: str) -> tuple[str, str]:
    for marker in _ACTION_MARKERS:
        if line.startswith(marker):
            return "action", line[len(marker):].strip()
    for marker in _DIALOGUE_MARKERS:
        if line.startswith(marker):
            return "dialogue", line[len(marker):].strip()

    match = _LABELLED_LINE_PATTERN.match(line)
    if not match:
        return "none", line.strip()

    label = match.group("label").strip().lower()
    content = match.group("content").strip()
    if label in _ACTION_LABELS:
        return "action", content
    if label in _DIALOGUE_LABELS:
        return "dialogue", content
    return "unknown", content


def _split_action_payload_by_inline_label(payload: str) -> tuple[str, str]:
    match = _INLINE_SECOND_LABEL_PATTERN.match(payload.strip())
    if not match:
        return "", ""

    action = match.group("action").strip()
    label = match.group("label").strip().lower()
    dialogue = match.group("dialogue").strip()
    if not action or not dialogue:
        return "", ""
    if label in _ACTION_LABELS:
        return "", ""
    return action, dialogue


def _split_action_dialogue(text: str) -> tuple[str, str]:
    if not text:
        return "", ""

    action_lines = []
    dialogue_lines = []
    capture_dialogue = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label_kind, content = _parse_labelled_line(line)
        if label_kind == "action":
            capture_dialogue = False
            if content:
                action_lines.append(content)
            continue
        if label_kind == "dialogue":
            capture_dialogue = True
            if content:
                dialogue_lines.append(_strip_dialogue_leading_punct(content))
            continue
        if label_kind == "unknown" and (capture_dialogue or action_lines):
            capture_dialogue = True
            if content:
                dialogue_lines.append(_strip_dialogue_leading_punct(content))
            continue
        if capture_dialogue:
            dialogue_lines.append(line)
        else:
            action_lines.append(line)

    action_text = " ".join(action_lines).strip()
    dialogue_text = " ".join(dialogue_lines).strip()
    if dialogue_text:
        return action_text, dialogue_text

    inline_action, inline_dialogue = _split_action_line_fallback(text.strip())
    if inline_dialogue:
        return inline_action, inline_dialogue

    if action_text and not dialogue_text and text.strip().startswith(("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")):
        return action_text, ""
    return "", text.strip()


def _split_action_line_fallback(text: str) -> tuple[str, str]:
    prefix = next((p for p in _ACTION_MARKERS if text.startswith(p)), None)
    if not prefix:
        return "", ""

    payload = text[len(prefix):].strip()
    if not payload:
        return "", ""

    for marker in _DIALOGUE_MARKERS:
        if marker in payload:
            left, right = payload.split(marker, 1)
            left = left.strip()
            right = _strip_dialogue_leading_punct(right)
            return left, right

    inline_action, inline_dialogue = _split_action_payload_by_inline_label(payload)
    if inline_dialogue:
        return inline_action, _strip_dialogue_leading_punct(inline_dialogue)

    quote_match = re.search(r"[「“\"]([^」”\"]+)[」”\"]", payload)
    if quote_match:
        dialogue = quote_match.group(1).strip()
        action = (payload[:quote_match.start()] + payload[quote_match.end():]).strip(" ，。;；")
        if dialogue:
            return action, dialogue

    sentence_split = re.search(r"[。！？；;](?=.)", payload)
    if sentence_split:
        split_idx = sentence_split.end()
        action = payload[:split_idx].strip()
        dialogue = _strip_dialogue_leading_punct(payload[split_idx:])
        if dialogue:
            return action, dialogue

    keyword_patterns = ["我是", "我叫", "我会", "我必须", "我不", "我想", "我现在", "你", "训练员"]
    candidate_indexes = []
    for pat in keyword_patterns:
        idx = payload.find(pat)
        if idx > 6:
            candidate_indexes.append(idx)
    if candidate_indexes:
        split_idx = min(candidate_indexes)
        action = payload[:split_idx].strip(" ，。;；")
        dialogue = payload[split_idx:].strip()
        if action and dialogue:
            return action, dialogue

    return payload, ""


def _show_history(
    client: UmamusumeClient,
    user_uuid: str,
    current_character: str,
    command_arg: str | None = None,
):
    target_character: str | None
    if command_arg is None or not command_arg.strip():
        target_character = current_character
    elif command_arg.strip().lower() in {"all", "*"}:
        target_character = None
    else:
        target_character = command_arg.strip()

    result = client.get_history(user_uuid=user_uuid, character_name=target_character, limit=200)
    if "error" in result:
        print(f"{Colors.RED}查看历史失败: {result['error']}{Colors.NC}\n")
        return

    scope_label = target_character or "全部角色"
    total_messages = int(result.get("total_messages") or 0)
    returned_messages = int(result.get("returned_messages") or 0)
    messages = result.get("messages") or []

    print(f"\n{Colors.BOLD}历史记录（{scope_label}）{Colors.NC}")
    print("-" * 60)
    print(f"总条数: {total_messages}，当前返回: {returned_messages}")

    if not messages:
        print("暂无历史消息。")
        print("-" * 60)
        print()
        return

    start_index = max(0, len(messages) - 20)
    for idx, item in enumerate(messages[start_index:], start=start_index + 1):
        role = "训练员" if item.get("role") == "user" else "角色"
        character_name_en = item.get("character_name_en") or "unknown"
        timestamp = item.get("timestamp") or "-"
        content = str(item.get("content") or "").strip()
        print(f"[{idx}] {timestamp} | {character_name_en} | {role}")
        print(content)
        print("-" * 60)
    print()


def _clear_history(
    client: UmamusumeClient,
    user_uuid: str,
    current_character: str,
    command_arg: str | None = None,
):
    target_character = command_arg.strip() if command_arg and command_arg.strip() else current_character
    confirm = input(
        f"{Colors.YELLOW}确认清空你与「{target_character}」的历史对话？输入 yes 确认: {Colors.NC}"
    ).strip()
    if confirm.lower() != "yes":
        print(f"{Colors.CYAN}已取消清理历史。{Colors.NC}\n")
        return

    result = client.clear_history(user_uuid=user_uuid, character_name=target_character)
    if "error" in result:
        print(f"{Colors.RED}清理历史失败: {result['error']}{Colors.NC}\n")
        return

    deleted_files = int(result.get("deleted_files") or 0)
    deleted_messages = int(result.get("deleted_messages") or 0)
    cleared_active_sessions = int(result.get("cleared_active_sessions") or 0)
    print(f"{Colors.GREEN}历史已清理完成。{Colors.NC}")
    print(f"  • 删除会话文件: {deleted_files}")
    print(f"  • 删除消息条数: {deleted_messages}")
    print(f"  • 清空中的活跃会话: {cleared_active_sessions}\n")

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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="调试模式：输出流式分块与解析细节"
    )
    parser.add_argument(
        "--user-uuid",
        type=str,
        default=None,
        help="指定用户 UUID；不传时默认由本机用户名稳定派生"
    )

    args = parser.parse_args()

    client = UmamusumeClient(server_url=args.server_url)

    character_name = args.character
    if not character_name:
        character_name = input(f"{Colors.BOLD}请输入角色名称:{Colors.NC} ").strip()
    if not character_name:
        print(f"{Colors.RED}未提供角色名称，退出。{Colors.NC}")
        return

    cli_user_uuid = _resolve_user_uuid(args.user_uuid)
    load_result = client.load_character(
        character_name,
        force_rebuild=args.force_rebuild,
        user_uuid=cli_user_uuid,
    )
    if "error" in load_result:
        print(f"{Colors.RED}加载角色失败: {load_result['error']}{Colors.NC}")
        return

    # 以服务端回传为准（服务端会做规范化）
    cli_user_uuid = load_result.get("user_uuid") or cli_user_uuid

    session_id = load_result.get("session_id")
    output_dir = load_result.get("output_dir")
    restored_history_messages = int(load_result.get("restored_history_messages") or 0)
    if not session_id:
        print(f"{Colors.RED}加载角色失败: 未返回 session_id{Colors.NC}")
        return

    print(f"\n{Colors.BOLD}{'='*60}{Colors.NC}")
    print(f"{Colors.BOLD}{'  赛马娘交互式Agent':^56}{Colors.NC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.NC}")
    print(f"\n模式: {Colors.CYAN}{'流式' if args.stream else '非流式'}{Colors.NC}")
    print(f"服务器: {Colors.CYAN}{args.server_url}{Colors.NC}")
    print(f"角色: {Colors.CYAN}{character_name}{Colors.NC}")
    print(f"User UUID: {Colors.CYAN}{cli_user_uuid}{Colors.NC}")
    print(f"已恢复历史: {Colors.CYAN}{restored_history_messages} 条{Colors.NC}")
    if output_dir:
        print(f"音频输出: {Colors.CYAN}{output_dir}{Colors.NC}")
    print(f"TTS: {Colors.CYAN}{'开启' if args.voice else '关闭'}{Colors.NC}")
    print(f"DEBUG: {Colors.CYAN}{'开启' if args.debug else '关闭'}{Colors.NC}")
    print(f"\n命令:")
    print(f"  • 输入问题开始生成")
    print(f"  • 输入 'exit' 或 'quit' 退出")
    print(f"  • 输入 'mode' 切换流式/非流式模式")
    print(f"  • 输入 'voice' 切换语音开关")
    print(f"  • 输入 'character <name>' 切换角色")
    print(f"  • 输入 'history [all|角色名]' 查看历史记录")
    print(f"  • 输入 'clear_history [角色名]' 清空某个角色的历史")
    print(f"{Colors.BOLD}{'='*60}{Colors.NC}\n")

    if args.debug:
        print(f"{Colors.YELLOW}[DEBUG] local user: {getpass.getuser() or 'unknown'}{Colors.NC}")
        print(f"{Colors.YELLOW}[DEBUG] resolved user_uuid: {cli_user_uuid}{Colors.NC}")
        print(f"{Colors.YELLOW}[DEBUG] load_character 响应:{Colors.NC}")
        print(_debug_json(load_result))
        print()

    if args.question:
        if args.stream:
            handle_question_stream(client, session_id, args.question, args.voice, debug=args.debug, show_question=True)
        else:
            handle_question_normal(client, session_id, args.question, args.voice, debug=args.debug, show_question=True)
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
                load_result = client.load_character(new_name, user_uuid=cli_user_uuid)
                if "error" in load_result:
                    print(f"{Colors.RED}加载角色失败: {load_result['error']}{Colors.NC}")
                    continue
                cli_user_uuid = load_result.get("user_uuid") or cli_user_uuid
                session_id = load_result.get("session_id")
                character_name = new_name
                output_dir = load_result.get("output_dir")
                restored_history_messages = int(load_result.get("restored_history_messages") or 0)
                print(f"\n{Colors.CYAN}已切换角色: {character_name}{Colors.NC}")
                print(f"{Colors.CYAN}User UUID: {cli_user_uuid}{Colors.NC}")
                print(f"{Colors.CYAN}已恢复历史: {restored_history_messages} 条{Colors.NC}")
                if output_dir:
                    print(f"{Colors.CYAN}音频输出: {output_dir}{Colors.NC}\n")
                if args.debug:
                    print(f"{Colors.YELLOW}[DEBUG] load_character 响应:{Colors.NC}")
                    print(_debug_json(load_result))
                    print()
                continue

            if user_input.lower() == "history":
                _show_history(client, cli_user_uuid, character_name, None)
                continue

            if user_input.lower().startswith("history "):
                arg = user_input.split(" ", 1)[1].strip()
                _show_history(client, cli_user_uuid, character_name, arg)
                continue

            if user_input.lower() == "clear_history":
                _clear_history(client, cli_user_uuid, character_name, None)
                continue

            if user_input.lower().startswith("clear_history "):
                arg = user_input.split(" ", 1)[1].strip()
                _clear_history(client, cli_user_uuid, character_name, arg)
                continue

            if not user_input:
                continue

            if stream_mode:
                handle_question_stream(client, session_id, user_input, voice_enabled, debug=args.debug, show_question=False)
            else:
                handle_question_normal(client, session_id, user_input, voice_enabled, debug=args.debug, show_question=False)

        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.GREEN}再见！{Colors.NC}\n")
            break
        except Exception as e:
            print(f"\n{Colors.RED}错误: {e}{Colors.NC}\n")
            continue


if __name__ == "__main__":
    main()
