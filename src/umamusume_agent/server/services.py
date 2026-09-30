"""Per-application dependency assembly; no clients or sessions at import time."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from ..character import CharacterManager
from ..config import config
from ..dialogue.context import LegacyDialogueContextBuilder
from ..dialogue.runtime import CharacterRuntime
from ..dialogue.service import DialogueService
from ..director.context import CharacterSceneContextBuilder, DirectorContextBuilder
from ..director.runtime import DirectorRuntime
from ..director.service import DirectorService
from ..director.session import SceneSession
from ..director.templates import SceneTemplateRepository
from ..llm_usage import DeepSeekUsageTracker
from ..stage import StageDirectorContextBuilder, StageSceneService
from ..tts import TTSMCPClient, TTSMCPConfig, VoiceService
from .sessions import DialogueSessionStore


@dataclass
class ServerServices:
    settings: Any
    character_manager: CharacterManager
    character_runtime: CharacterRuntime
    dialogue_service: DialogueService
    session_store: DialogueSessionStore
    voice_service: VoiceService
    usage_tracker: DeepSeekUsageTracker
    director_service: DirectorService
    stage_director_service: DirectorService
    stage_scene_service: StageSceneService
    director_sessions: dict[str, SceneSession] = field(default_factory=dict)
    stage_sessions: dict[str, SceneSession] = field(default_factory=dict)


def build_services(
    *, settings=config, llm_client=None, tts_client=None,
    character_manager: CharacterManager | None = None,
) -> ServerServices:
    """Build the production graph, allowing offline clients/settings in tests."""
    outputs_dir = Path(settings.OUTPUTS_DIRECTORY)
    history_dir = Path(settings.DIALOGUE_HISTORY_DIRECTORY)
    director_history_dir = Path(settings.DIRECTOR_HISTORY_DIRECTORY)
    characters_dir = Path(settings.CHARACTERS_DIRECTORY)
    for directory in (outputs_dir, history_dir, director_history_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if character_manager is None:
        character_manager = CharacterManager(characters_dir=str(characters_dir))
    if llm_client is None:
        llm_client = AsyncOpenAI(
            api_key=settings.ROLEPLAY_LLM_MODEL_API_KEY,
            base_url=settings.ROLEPLAY_LLM_MODEL_BASE_URL,
            timeout=max(5.0, settings.ROLEPLAY_LLM_TIMEOUT_SECONDS),
            max_retries=max(0, settings.ROLEPLAY_LLM_MAX_RETRIES),
        )
    if tts_client is None:
        tts_client = TTSMCPClient(TTSMCPConfig(
            base_url=settings.TTS_MCP_URL, transport=settings.TTS_MCP_TRANSPORT,
        ))
    usage_tracker = DeepSeekUsageTracker(
        base_url=settings.ROLEPLAY_LLM_MODEL_BASE_URL,
        max_events=settings.LLM_USAGE_MAX_EVENTS,
        recent_operations=settings.LLM_USAGE_RECENT_OPERATIONS,
        display_timezone=settings.LLM_USAGE_TIMEZONE,
    )
    voice_service = VoiceService(
        client=tts_client, outputs_dir=outputs_dir, characters_dir=characters_dir,
    )
    character_runtime = CharacterRuntime(
        llm_client=llm_client, settings=settings, usage_tracker=usage_tracker,
    )
    context_builder = LegacyDialogueContextBuilder(settings=settings)
    dialogue_service = DialogueService(
        runtime=character_runtime, context_builder=context_builder,
    )
    session_store = DialogueSessionStore(
        history_dir=history_dir, context_builder=context_builder,
        voice_service=voice_service,
        ttl_seconds=settings.DIALOGUE_SESSION_TTL_SECONDS,
        history_max_messages=settings.DIALOGUE_SESSION_HISTORY_MAX_MESSAGES,
    )

    templates = SceneTemplateRepository(settings.SCENE_TEMPLATES_DIRECTORY)
    character_scene_builder = CharacterSceneContextBuilder(settings=settings)
    director_builder = DirectorContextBuilder(
        settings=settings, max_speakers=settings.DIRECTOR_MAX_SPEAKERS_PER_TURN,
    )
    stage_builder = StageDirectorContextBuilder(
        settings=settings, max_speakers=settings.DIRECTOR_MAX_SPEAKERS_PER_TURN,
    )
    director_runtime = DirectorRuntime(
        json_runtime=character_runtime, settings=settings,
        max_speakers=settings.DIRECTOR_MAX_SPEAKERS_PER_TURN,
    )
    stage_runtime = DirectorRuntime(
        json_runtime=character_runtime, settings=settings,
        max_speakers=settings.DIRECTOR_MAX_SPEAKERS_PER_TURN,
        max_stage_actions=settings.DIRECTOR_MAX_STAGE_ACTIONS_PER_TURN,
        thinking_mode=settings.STAGE_DIRECTOR_LLM_THINKING_MODE,
    )
    common = dict(
        character_manager=character_manager, character_runtime=character_runtime,
        template_repository=templates, character_context_builder=character_scene_builder,
        history_dir=director_history_dir, max_participants=settings.DIRECTOR_MAX_PARTICIPANTS,
    )
    director_service = DirectorService(
        **common, director_runtime=director_runtime,
        director_context_builder=director_builder,
    )
    stage_director_service = DirectorService(
        **common, director_runtime=stage_runtime,
        director_context_builder=stage_builder,
    )
    return ServerServices(
        settings=settings, character_manager=character_manager,
        character_runtime=character_runtime, dialogue_service=dialogue_service,
        session_store=session_store, voice_service=voice_service,
        usage_tracker=usage_tracker, director_service=director_service,
        stage_director_service=stage_director_service,
        stage_scene_service=StageSceneService(stage_director_service),
    )
