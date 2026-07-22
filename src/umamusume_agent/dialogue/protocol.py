"""Structured reply protocol shared by legacy chat and future dialogue modes."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from pydantic import BaseModel

from ..config import config


logger = logging.getLogger(__name__)

JSON_OUTPUT_MODES = {"auto", "response_format", "prompt_only", "disabled"}
STRUCTURED_REPLY_SCHEMA_VERSION = 2

JSON_RESPONSE_FORMAT_INSTRUCTION = (
    "【JSON 回复格式硬性规范】\n"
    "你正在进行沉浸式角色扮演。你必须只输出一个合法 JSON object。\n"
    "不要输出 Markdown，不要输出代码块，不要输出解释，不要输出 JSON 以外的任何文字。\n\n"
    "JSON 格式必须是：\n"
    "{\n"
    '  "action": "角色动作、神态或心理描写；没有则写“无”",\n'
    '  "dialogue": "角色对训练员说的话；自然口语；可直接用于 TTS"\n'
    "}\n\n"
    "字段要求：\n"
    "1) action 必须是 string。\n"
    "2) dialogue 必须是 string，不能为空。\n"
    "3) 不要替训练员说话、行动、思考或决定关系进展。\n"
    "4) dialogue 中不要混入动作描写。\n"
    "5) action 中不要混入对白。"
)

LEGACY_RESPONSE_FORMAT_INSTRUCTION = (
    "【回复格式硬性规范】\n"
    "你正在进行沉浸式角色扮演。请始终使用中文，并且只输出两行，顺序固定如下：\n"
    "动作：<描写角色动作、神态或心理活动；简洁；不写台词>\n"
    "对白：<角色说的话；只写口语台词；不写动作或旁白>\n\n"
    "【输出边界】\n"
    "1) 必须包含且只包含这两行。\n"
    "2) 不要添加额外标题、编号、解释或第三行。\n"
    "3) 每行必须以完整标签“动作：”和“对白：”开头。\n"
    "4) 对白将直接用于 TTS，请保证自然可朗读。\n\n"
    "【正确示例（模板）】\n"
    "动作：【角色】耳朵轻轻抖动。\n"
    "对白：我是【角色名】，目标是成为优秀的赛马娘。"
)

PLAIN_TEXT_RESPONSE_FORMAT_INSTRUCTION = (
    "本次不需要生成语音文件，但仍必须遵守当前回复格式；dialogue/对白要自然可读。"
)

HIDDEN_JSON_FORMAT_REINJECTION_PROMPT = (
    "【后端隐藏 JSON 格式提醒】\n"
    "继续只输出一个合法 JSON object，不要 Markdown，不要代码块。\n"
    '格式固定为：{"action":"...","dialogue":"..."}'
)

HIDDEN_LEGACY_FORMAT_REINJECTION_PROMPT = (
    "【后端隐藏格式约束提醒】\n"
    "继续严格遵守输出格式，只输出两行，且顺序固定：\n"
    "动作：<只写角色自己的动作、神态或心理；简短；不要写台词>\n"
    "对白：<只写角色对训练员说的话；自然口语；不要写动作或旁白>\n"
    "不要输出第三行、标题、解释、总结或列表；不要替训练员说话、行动、思考或决定关系进展。"
)

REPAIR_JSON_PROMPT = (
    "你刚才没有输出合法 JSON。\n"
    "请只输出一个 JSON object，不要解释，不要 Markdown，不要代码块。\n"
    '格式必须是：{"action":"...","dialogue":"..."}'
)

REGENERATE_JSON_PROMPT = (
    "上一条 assistant 回复没有通过后端 JSON 解析。\n"
    "请忽略那次失败输出，基于最近一条训练员发言重新生成角色回复。\n"
    "必须只输出一个合法 JSON object，不要解释，不要 Markdown，不要代码块。\n"
    '格式必须是：{"action":"...","dialogue":"..."}'
)

SAFE_PARSE_FAILURE_REPLY = "光钻有点没听清，训练员可以再说一次吗？"

_ACTION_PREFIXES = ("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")
_DIALOGUE_PREFIXES = (
    "对白：", "对白:",
    "台词：", "台词:",
    "对话：", "对话:",
    "TTS：", "TTS:",
)
_ACTION_LABELS = {"动作", "神态", "场景", "神情", "表情"}
_DIALOGUE_LABELS = {"对白", "台词", "对话", "tts", "dialogue", "speech"}
_LABELLED_LINE_PATTERN = re.compile(r"^(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<content>.*)$")
_INLINE_SECOND_LABEL_PATTERN = re.compile(
    r"^(?P<action>.*?[。！？；;…])\s*(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<dialogue>.+)$"
)
_STAGE_PATTERNS = [
    r"\\*[^\\*]+\\*",
    r"（[^）]*）",
    r"\\([^)]*\\)",
    r"【[^】]*】",
    r"\\[[^\\]]*]",
    r"〔[^〕]*〕",
    r"＜[^＞]*＞",
    r"<[^>]*>",
    r"《[^》]*》",
]


class StructuredReply(BaseModel):
    """Validated assistant reply used by all dialogue entry points."""

    action: str = "无"
    dialogue: str
    source_format: str = "json_v2"
    schema_version: int = STRUCTURED_REPLY_SCHEMA_VERSION


def json_output_mode(settings=config) -> str:
    mode = (settings.LLM_JSON_OUTPUT_MODE or "auto").strip().lower()
    if mode not in JSON_OUTPUT_MODES:
        logger.warning(
            "Invalid LLM_JSON_OUTPUT_MODE=%s, fallback to auto",
            settings.LLM_JSON_OUTPUT_MODE,
        )
        return "auto"
    return mode


def is_json_reply_enabled(settings=config) -> bool:
    return bool(settings.LLM_JSON_ENABLED) and json_output_mode(settings) != "disabled"


def strip_stage_directions(text: str) -> str:
    cleaned = text
    for pattern in _STAGE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned)
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[~!！。，、.…\\-—·\\s]+", stripped):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _parse_labelled_line(line: str) -> tuple[str, str]:
    for prefix in _ACTION_PREFIXES:
        if line.startswith(prefix):
            return "action", line[len(prefix):].strip()
    for prefix in _DIALOGUE_PREFIXES:
        if line.startswith(prefix):
            return "dialogue", line[len(prefix):].strip()

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


def _split_action_payload(payload: str) -> tuple[str, str]:
    if not payload:
        return "", ""

    for marker in _DIALOGUE_PREFIXES:
        if marker in payload:
            left, right = payload.split(marker, 1)
            return left.strip(), right.strip()

    inline_action, inline_dialogue = _split_action_payload_by_inline_label(payload)
    if inline_dialogue:
        return inline_action, inline_dialogue

    quote_match = re.search(r'[「“"]([^」”"]+)[」”"]', payload)
    if quote_match:
        dialogue = quote_match.group(1).strip()
        action = (payload[:quote_match.start()] + payload[quote_match.end():]).strip(" ，。;；")
        if dialogue:
            return action, dialogue

    sentence_split = re.search(r"[。！？；;…](?=.)", payload)
    if sentence_split:
        split_idx = sentence_split.end()
        action = payload[:split_idx].strip()
        dialogue = payload[split_idx:].strip()
        if dialogue:
            return action, dialogue

    keyword_patterns = ("我是", "我叫", "我会", "我必须", "我不", "我想", "我现在", "你", "训练员")
    candidate_indexes = []
    for pattern in keyword_patterns:
        index = payload.find(pattern)
        if index > 6:
            candidate_indexes.append(index)
    if candidate_indexes:
        split_idx = min(candidate_indexes)
        action = payload[:split_idx].strip(" ，。;；")
        dialogue = payload[split_idx:].strip()
        if action and dialogue:
            return action, dialogue

    return payload.strip(), ""


def split_action_dialogue(reply: str) -> tuple[str, str]:
    if not reply:
        return "", ""

    action_lines: list[str] = []
    dialogue_lines: list[str] = []
    unmarked_lines: list[str] = []
    capture_dialogue = False
    has_marker = False

    for raw_line in reply.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        label_kind, content = _parse_labelled_line(line)
        if label_kind == "action":
            has_marker = True
            capture_dialogue = False
            if content:
                action_lines.append(content)
            continue

        if label_kind == "dialogue":
            has_marker = True
            capture_dialogue = True
            if content:
                dialogue_lines.append(content)
            continue

        if label_kind == "unknown" and has_marker:
            has_marker = True
            capture_dialogue = True
            if content:
                dialogue_lines.append(content)
            continue

        if capture_dialogue:
            dialogue_lines.append(line)
        elif has_marker:
            action_lines.append(line)
        else:
            unmarked_lines.append(line)

    action_text = " ".join(item for item in action_lines if item).strip()
    dialogue_text = " ".join(item for item in dialogue_lines if item).strip()
    if dialogue_text:
        return action_text, dialogue_text

    if action_text:
        inline_action, inline_dialogue = _split_action_payload(action_text)
        if inline_dialogue:
            return inline_action, inline_dialogue
        return inline_action, ""

    if unmarked_lines:
        return "", " ".join(unmarked_lines).strip()

    return "", reply.strip()


def normalize_structured_reply(reply: str) -> str:
    action_text, dialogue_text = split_action_dialogue(reply)
    if not dialogue_text:
        dialogue_text = strip_stage_directions(reply)
    if not dialogue_text:
        dialogue_text = reply.strip()
    if not action_text:
        action_text = "无"
    return f"动作：{action_text}\n对白：{dialogue_text}"


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    embedded = re.search(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if embedded:
        return embedded.group(1).strip()
    return stripped


def load_json_object_from_text(text: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty model output")

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        if not config.LLM_JSON_PARSE_LOOSE_JSON:
            raise

        cleaned = _strip_json_code_fence(stripped)
        if cleaned != stripped:
            try:
                payload = json.loads(cleaned)
            except json.JSONDecodeError:
                payload = None
            else:
                if isinstance(payload, dict):
                    return payload

        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", stripped):
            try:
                payload, _end = decoder.raw_decode(stripped[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise

    if not isinstance(payload, dict):
        raise ValueError("JSON output must be an object")
    return payload


def parse_structured_reply(raw: str, *, source_format: str = "json_v2") -> StructuredReply:
    payload = load_json_object_from_text(raw)
    action = payload.get("action", "无")
    dialogue = payload.get("dialogue", "")

    if not isinstance(action, str):
        action = "无"
    if not isinstance(dialogue, str) or not dialogue.strip():
        raise ValueError("JSON output missing non-empty dialogue")

    return StructuredReply(
        action=action.strip() or "无",
        dialogue=dialogue.strip(),
        source_format=source_format,
    )


def structured_reply_from_legacy_text(
    text: str,
    *,
    source_format: str = "legacy_text",
) -> StructuredReply:
    normalized = normalize_structured_reply(text)
    action_text, dialogue_text = split_action_dialogue(normalized)
    if not dialogue_text:
        dialogue_text = strip_stage_directions(text) or text.strip()
    return StructuredReply(
        action=action_text.strip() or "无",
        dialogue=dialogue_text.strip() or SAFE_PARSE_FAILURE_REPLY,
        source_format=source_format,
    )


def structured_reply_message(reply: StructuredReply, *, role: str = "assistant") -> Dict[str, Any]:
    return {
        "schema_version": reply.schema_version,
        "role": role,
        "content": reply.dialogue,
        "action": reply.action or "无",
        "dialogue": reply.dialogue,
        "source_format": reply.source_format,
    }


def normalize_assistant_record(record: Dict[str, Any]) -> Dict[str, Any]:
    content = record.get("content")
    if not isinstance(content, str):
        content = ""

    action = record.get("action")
    dialogue = record.get("dialogue")
    source_format = record.get("source_format") or record.get("sourceFormat") or "json_v2"

    if isinstance(dialogue, str) and dialogue.strip():
        return {
            **record,
            "role": "assistant",
            "content": dialogue.strip(),
            "action": action.strip() if isinstance(action, str) and action.strip() else "无",
            "dialogue": dialogue.strip(),
            "source_format": source_format,
            "schema_version": (
                record.get("schema_version")
                or record.get("schemaVersion")
                or STRUCTURED_REPLY_SCHEMA_VERSION
            ),
        }

    if content.strip():
        try:
            reply = parse_structured_reply(content, source_format="json_v2")
        except Exception:
            reply = structured_reply_from_legacy_text(content, source_format="legacy_text")
        return {
            **record,
            "role": "assistant",
            "content": reply.dialogue,
            "action": reply.action,
            "dialogue": reply.dialogue,
            "source_format": reply.source_format,
            "schema_version": reply.schema_version,
        }

    return {
        **record,
        "role": "assistant",
        "content": SAFE_PARSE_FAILURE_REPLY,
        "action": "无",
        "dialogue": SAFE_PARSE_FAILURE_REPLY,
        "source_format": "parse_error",
        "schema_version": STRUCTURED_REPLY_SCHEMA_VERSION,
    }


def to_compact_context_message(record: Dict[str, Any]) -> Dict[str, str]:
    role = record.get("role")
    if role == "user":
        return {"role": "user", "content": str(record.get("content") or "").strip()}

    assistant_record = normalize_assistant_record({**record, "role": "assistant"})
    action = str(assistant_record.get("action") or "").strip()
    dialogue = str(assistant_record.get("dialogue") or assistant_record.get("content") or "").strip()
    if action and action != "无":
        content = f"角色动作：{action}\n角色对白：{dialogue}"
    else:
        content = f"角色对白：{dialogue}"
    return {"role": "assistant", "content": content}


def extract_dialogue_text(text: str) -> str:
    _, dialogue_text = split_action_dialogue(text)
    if dialogue_text:
        return dialogue_text
    return strip_stage_directions(text).strip()

