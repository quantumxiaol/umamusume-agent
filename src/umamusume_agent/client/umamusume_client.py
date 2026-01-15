import json
from typing import Callable, Dict, Any, Iterable, Tuple

import requests


def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = requests.post(url, json=payload, timeout=600)
        if response.status_code == 200:
            return response.json()
        return {"error": f"HTTP {response.status_code}: {response.text}"}
    except requests.RequestException as exc:
        return {"error": f"Request failed: {exc}"}


def _iter_sse_events(response: requests.Response) -> Iterable[Tuple[str, str]]:
    event_name = None
    data_lines = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                yield (event_name or "token", data)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
            continue

    if data_lines:
        data = "\n".join(data_lines)
        yield (event_name or "token", data)


class UmamusumeClient:
    """
    赛马娘对话 Agent 客户端，支持流式和非流式两种模式。
    """

    def __init__(self, server_url: str = "http://127.0.0.1:1111"):
        """
        初始化客户端。
        
        :param server_url: 服务器基础地址，默认 http://127.0.0.1:1111
        """
        self.server_url = server_url.rstrip('/')
        self.load_url = f"{self.server_url}/load_character"
        self.chat_url = f"{self.server_url}/chat"
        self.chatstream_url = f"{self.server_url}/chat_stream"

    def load_character(self, character_name: str, force_rebuild: bool = False) -> Dict[str, Any]:
        payload = {"character_name": character_name, "force_rebuild": force_rebuild}
        return _post_json(self.load_url, payload)

    def chat(self, session_id: str, message: str, generate_voice: bool = False) -> Dict[str, Any]:
        """
        发送消息并返回 AI 回复（非流式）。

        :param session_id: 会话 ID
        :param message: 用户输入
        :param generate_voice: 是否生成语音
        :return: 回答字典
        """
        payload = {
            "session_id": session_id,
            "message": message,
            "generate_voice": generate_voice,
        }
        return _post_json(self.chat_url, payload)

    def chat_stream(
        self,
        session_id: str,
        message: str,
        callback: Callable[[str, Any], None],
        generate_voice: bool = False,
    ) -> None:
        """
        发送消息并以流式方式接收 AI 回复。

        :param session_id: 会话 ID
        :param message: 用户输入
        :param callback: 回调函数，接收 (event, data) 两个参数
        :param generate_voice: 是否生成语音
        """
        payload = {
            "session_id": session_id,
            "message": message,
            "generate_voice": generate_voice,
        }
        try:
            response = requests.post(
                self.chatstream_url,
                json=payload,
                stream=True,
                timeout=600,
            )
            if response.status_code != 200:
                callback("error", f"HTTP {response.status_code}: {response.text}")
                return

            for event, data in _iter_sse_events(response):
                if event in {"voice", "voice_pending"}:
                    try:
                        payload = json.loads(data)
                        callback(event, payload)
                        continue
                    except json.JSONDecodeError:
                        callback("error", f"{event} payload decode failed: {data}")
                        continue
                callback(event, data)

        except requests.RequestException as exc:
            callback("error", f"Request failed: {exc}")
