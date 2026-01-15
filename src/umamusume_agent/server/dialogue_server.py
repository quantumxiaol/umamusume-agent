"""
Dialogue Server - 交互式角色对话服务
提供基于角色人格的对话API和语音生成
"""

import asyncio
import json
import logging
import re
import shutil
from urllib.parse import quote
from pathlib import Path
from typing import Optional, AsyncGenerator, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from openai import AsyncOpenAI

from ..character import CharacterManager, CharacterConfig
from ..tts import IndexTTSMCPClient, MCPToolError
from ..config import config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化组件
character_manager = CharacterManager()
llm_client = AsyncOpenAI(
    api_key=config.ROLEPLAY_LLM_MODEL_API_KEY,
    base_url=config.ROLEPLAY_LLM_MODEL_BASE_URL
)
tts_client = IndexTTSMCPClient()

OUTPUTS_DIR = Path(config.OUTPUTS_DIRECTORY)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
CHARACTERS_DIR = Path(config.CHARACTERS_DIRECTORY)

_RESPONSE_FORMAT_INSTRUCTION = (
    "请严格按以下格式回复：\n"
    "动作：<可选，描述动作/神态/场景>\n"
    "对白：<仅角色台词，不要包含动作或旁白>\n"
    "注意：对白会用于语音合成，请确保可直接朗读。"
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

# FastAPI 应用
app = FastAPI(title="Umamusume-Dialogue-Server", version="0.2.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# 会话存储（简单内存存储，生产环境应使用数据库）
sessions = {}


# ============= 请求/响应模型 =============

class LoadCharacterRequest(BaseModel):
    """加载角色请求"""
    character_name: str
    force_rebuild: bool = False


class DialogueRequest(BaseModel):
    """对话请求"""
    session_id: str
    message: str
    generate_voice: bool = False  # 是否生成语音


class SessionInfo(BaseModel):
    """会话信息"""
    session_id: str
    character_name: str
    created_at: datetime
    message_count: int
    output_dir: Optional[str] = None


# ============= 会话管理 =============

class DialogueSession:
    """对话会话"""
    
    def __init__(self, session_id: str, character: CharacterConfig):
        self.session_id = session_id
        self.character = character
        self.created_at = datetime.now()
        self.history = []
        self.message_count = 0
        self.voice_index = 0
        self.output_dir = _create_output_dir(character, self.created_at)
        self.audio_history: list[Dict[str, Any]] = []
    
    def add_message(self, role: str, content: str):
        """添加消息到历史"""
        self.history.append({"role": role, "content": content})
        self.message_count += 1
    
    def get_messages(self) -> list:
        """获取完整消息列表（包含系统提示）"""
        system_prompt = f"{self.character.get_system_prompt()}\n\n{_RESPONSE_FORMAT_INSTRUCTION}"
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.history)
        return messages


def create_session(character: CharacterConfig) -> DialogueSession:
    """创建新会话"""
    import uuid
    session_id = str(uuid.uuid4())
    session = DialogueSession(session_id, character)
    sessions[session_id] = session
    logger.info(f"Created session {session_id} for character {character.name_zh}")
    return session


def _slugify(name: str) -> str:
    return name.strip().replace(" ", "_").replace("　", "_").lower()


def _create_output_dir(character: CharacterConfig, created_at: datetime) -> Path:
    safe_name = _slugify(character.name_en or character.name_zh)
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    base_name = f"{safe_name}_{timestamp}"
    output_dir = OUTPUTS_DIR / base_name
    if output_dir.exists():
        counter = 1
        while (OUTPUTS_DIR / f"{base_name}_{counter}").exists():
            counter += 1
        output_dir = OUTPUTS_DIR / f"{base_name}_{counter}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_audio_url(path: Path) -> str:
    return f"/audio?path={quote(str(path))}"


def _is_allowed_audio_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(OUTPUTS_DIR) or resolved.is_relative_to(CHARACTERS_DIR)
    except Exception:
        return False


def _strip_stage_directions(text: str) -> str:
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


def _extract_dialogue_text(text: str) -> str:
    tts_lines = []
    capture = False
    dialogue_markers = ("对白：", "对白:", "台词：", "台词:", "对话：", "对话:", "TTS：", "TTS:")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker_hit = next((marker for marker in dialogue_markers if marker in stripped), None)
        if marker_hit:
            _, content = stripped.split(marker_hit, 1)
            capture = True
            content = content.strip()
            if content:
                tts_lines.append(content)
            continue
        if stripped.startswith(("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")):
            if capture:
                break
            continue
        if capture:
            tts_lines.append(stripped)

    return " ".join(tts_lines).strip()


def get_session(session_id: str) -> Optional[DialogueSession]:
    """获取会话"""
    return sessions.get(session_id)


# ============= API 端点 =============

@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Umamusume Dialogue Server",
        "version": "0.2.0",
        "status": "running"
    }


@app.post("/load_character")
async def load_character(request: LoadCharacterRequest):
    """
    加载角色并创建会话
    
    返回: {session_id: str, character_name: str, system_prompt: str}
    """
    try:
        logger.info(f"Loading character: {request.character_name}")
        
        # 加载角色配置
        character = await character_manager.load_character(
            request.character_name,
            force_rebuild=request.force_rebuild
        )
        
        # 创建会话
        session = create_session(character)
        
        return {
            "session_id": session.session_id,
            "character_name": character.name_zh,
            "character_name_jp": character.name_jp,
            "system_prompt": character.get_system_prompt(),
            "personality": character.personality.model_dump(),
            "created_at": session.created_at.isoformat(),
            "output_dir": str(session.output_dir),
            "voice_preview_url": _build_audio_url(Path(character.get_voice_config()["ref_audio_path"])),
        }
    
    except FileNotFoundError as e:
        logger.error(f"Character not found: {e}")
        raise HTTPException(status_code=404, detail=f"角色未找到: {request.character_name}。请先构建角色配置。")
    
    except Exception as e:
        logger.error(f"Failed to load character: {e}")
        raise HTTPException(status_code=500, detail=f"加载角色失败: {str(e)}")


@app.post("/chat")
async def chat(request: DialogueRequest):
    """
    发送消息并获取回复（非流式）
    
    返回: {reply: str, voice_url: str (optional)}
    """
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    try:
        # 添加用户消息
        session.add_message("user", request.message)
        
        # 调用 LLM
        response = await llm_client.chat.completions.create(
            model=config.ROLEPLAY_LLM_MODEL_NAME,
            messages=session.get_messages(),
            temperature=0.7
        )
        
        reply = response.choices[0].message.content
        
        # 添加助手回复
        session.add_message("assistant", reply)
        
        result = {"reply": reply}
        
        # 生成语音（如果需要）
        if request.generate_voice:
            voice_plan = _reserve_voice_output(session)
            voice_info = await _generate_voice_for_reply(session, reply, voice_plan)
            if voice_info:
                result["voice"] = voice_info
        
        return result
    
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")


@app.post("/chat_stream")
async def chat_stream(request: DialogueRequest):
    """
    发送消息并流式获取回复
    
    返回: SSE 流
    """
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 添加用户消息
            session.add_message("user", request.message)
            
            # 流式调用 LLM
            stream = await llm_client.chat.completions.create(
                model=config.ROLEPLAY_LLM_MODEL_NAME,
                messages=session.get_messages(),
                temperature=0.7,
                stream=True
            )
            
            full_reply = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    
                    # 发送 SSE 事件
                    yield f"data: {content}\n\n"
            
            # 添加完整回复到历史
            session.add_message("assistant", full_reply)
            
            # 发送完成事件
            yield f"event: done\ndata: {{}}\n\n"

            if request.generate_voice:
                voice_plan = _reserve_voice_output(session)
                payload = json.dumps(voice_plan, ensure_ascii=False)
                yield f"event: voice_pending\ndata: {payload}\n\n"
                asyncio.create_task(_generate_voice_for_reply(session, full_reply, voice_plan))
        
        except Exception as e:
            logger.error(f"Stream chat failed: {e}")
            yield f"event: error\ndata: {str(e)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@app.get("/sessions")
async def list_sessions():
    """列出所有活跃会话"""
    return [
        {
            "session_id": s.session_id,
            "character_name": s.character.name_zh,
            "created_at": s.created_at.isoformat(),
            "message_count": s.message_count,
            "output_dir": str(s.output_dir)
        }
        for s in sessions.values()
    ]


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    if session_id in sessions:
        del sessions[session_id]
        return {"status": "deleted", "session_id": session_id}
    else:
        raise HTTPException(status_code=404, detail="会话不存在")


@app.get("/characters")
async def list_characters():
    """列出所有可用角色"""
    try:
        characters = character_manager.list_characters()
        return {"characters": characters}
    except Exception as e:
        logger.error(f"Failed to list characters: {e}")
        raise HTTPException(status_code=500, detail=f"获取角色列表失败: {str(e)}")


@app.get("/audio")
async def get_audio(path: str):
    if not path:
        raise HTTPException(status_code=400, detail="Missing audio path")
    audio_path = Path(path)
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    if not _is_allowed_audio_path(audio_path):
        raise HTTPException(status_code=403, detail="Audio path not allowed")
    return FileResponse(audio_path)


# ============= 辅助函数 =============

def _reserve_voice_output(session: DialogueSession) -> Dict[str, Any]:
    session.voice_index += 1
    output_name = f"reply_{session.voice_index:03d}.wav"
    target_path = session.output_dir / output_name
    return {
        "audio_path": str(target_path),
        "audio_url": _build_audio_url(target_path),
        "index": session.voice_index,
        "output_dir": str(session.output_dir),
        "output_name": output_name,
    }


async def _generate_voice_for_reply(
    session: DialogueSession,
    text: str,
    voice_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        voice_config = session.character.get_voice_config()
        prompt_audio_path = voice_config["ref_audio_path"]
        output_name = voice_plan["output_name"]
        target_path = Path(voice_plan["audio_path"])
        tts_text = _extract_dialogue_text(text)
        if not tts_text:
            tts_text = _strip_stage_directions(text)
        if not tts_text:
            tts_text = text

        result = await tts_client.synthesize(
            text=tts_text,
            prompt_wav_path=prompt_audio_path,
            output_name=output_name,
        )

        source_path = Path(result.get("audio_path", ""))
        if not source_path.exists():
            raise FileNotFoundError(f"TTS output not found: {source_path}")

        if source_path.resolve() != target_path.resolve():
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(source_path), str(target_path))

        voice_info = {
            "audio_path": str(target_path),
            "audio_url": _build_audio_url(target_path),
            "prompt_audio_path": result.get("prompt_audio_path"),
            "sample_rate": result.get("sample_rate"),
            "index": voice_plan["index"],
            "output_dir": voice_plan["output_dir"],
            "tts_text": tts_text,
        }
        session.audio_history.append(voice_info)
        return voice_info

    except (MCPToolError, FileNotFoundError) as e:
        logger.error(f"TTS MCP error: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to generate voice: {e}")
        return None


# ============= 启动入口 =============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=1111)
