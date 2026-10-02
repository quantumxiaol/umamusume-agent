"""Transport-independent limits; new input and archived history are distinct."""

MAX_INPUT_CHARS = 10_000
MAX_TURN_EVENTS = 20
MAX_REQUEST_BYTES = 1024 * 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024
MAX_HISTORY_MESSAGES = 20_000
MAX_HISTORY_FIELD_CHARS = 200_000
MAX_HISTORY_TEXT_CHARS = 8_000_000
MAX_HISTORY_CHECKPOINTS = 100


def validate_input_batch(contents):
    # Python len(str) counts Unicode code points, not UTF-8 bytes/UTF-16 units.
    if len(contents) > MAX_TURN_EVENTS:
        raise ValueError(f"一次最多发送 {MAX_TURN_EVENTS} 条事件，请分批发送。")
    if sum(len(content) for content in contents) > MAX_INPUT_CHARS:
        raise ValueError(f"一次发送的内容（含已加入事件）不能超过 {MAX_INPUT_CHARS:,} 字符，请缩短或分批发送。")


def _field(item, name):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def validate_history_size(records, checkpoint=None, checkpoints=None):
    """Reject oversized imports without truncating old messages or memory.

    Count all accepted text representations, including raw model JSON and old
    camelCase aliases. Summaries have their own existing 2M-character field cap.
    The request-body cap additionally bounds metadata and unknown JSON fields.
    """
    if len(records) > MAX_HISTORY_MESSAGES:
        raise ValueError(f"历史最多恢复 {MAX_HISTORY_MESSAGES:,} 条记录；原历史未修改。")
    snapshots = checkpoints or []
    if len(snapshots) > MAX_HISTORY_CHECKPOINTS:
        raise ValueError(f"历史最多恢复 {MAX_HISTORY_CHECKPOINTS} 个摘要版本；原历史未修改。")
    total = 0
    for index, item in enumerate(records, 1):
        for name in ("content", "action", "dialogue", "model_content", "modelContent"):
            value = _field(item, name)
            if isinstance(value, str):
                if len(value) > MAX_HISTORY_FIELD_CHARS:
                    raise ValueError(f"历史第 {index} 条的 {name} 超过 {MAX_HISTORY_FIELD_CHARS:,} 字符；原历史未修改。")
                total += len(value)
    for item in [*snapshots, *([checkpoint] if checkpoint else [])]:
        summary = _field(item, "summary")
        if isinstance(summary, str):
            if len(summary) > 2_000_000:
                raise ValueError("单个历史摘要超过 2,000,000 字符；原历史未修改。")
            total += len(summary)
    if total > MAX_HISTORY_TEXT_CHARS:
        raise ValueError(f"历史文本及摘要合计超过 {MAX_HISTORY_TEXT_CHARS:,} 字符；原历史未修改。")
