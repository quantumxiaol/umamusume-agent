"""FastAPI application factory, router assembly and lifecycle."""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from ..dialogue.models import EVENT_SCHEMA_VERSION
from .dialogue_routes import create_dialogue_router
from .director_routes import create_director_router
from .http_utils import require_valid_user_uuid
from .middleware import install_api_protection
from .body_limit import RequestBodyLimitMiddleware
from ..input_limits import MAX_INPUT_CHARS, MAX_TURN_EVENTS, MAX_HISTORY_BYTES
from .services import ServerServices, build_services
from .stage_routes import create_stage_router
from .tts_routes import create_tts_router

logger = logging.getLogger(__name__)


def create_app(*, services: ServerServices | None = None) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    if services is None:
        services = build_services()
    settings = services.settings
    store = services.session_store
    cleanup_interval = max(5, settings.DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS)
    app = FastAPI(title="Umamusume-Dialogue-Server", version="0.2.0")
    app.state.services = services
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=['*'], allow_credentials=True,
        allow_methods=['*'], allow_headers=['*'],
    )
    install_api_protection(app, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Default validation responses echo input, potentially the entire large
        # archive. Keep the standard detail list without private text/ctx.
        return JSONResponse(status_code=422, content={'detail': [
            {key: item[key] for key in ('loc', 'msg', 'type') if key in item}
            for item in exc.errors()
        ]})
    app.include_router(create_director_router(
        service=services.director_service, sessions=services.director_sessions,
        session_ttl_seconds=settings.DIRECTOR_SESSION_TTL_SECONDS,
        voice_service=services.voice_service, enable_tts=settings.ENABLE_TTS,
        usage_tracker=services.usage_tracker,
    ))
    app.include_router(create_stage_router(
        director_service=services.stage_director_service,
        stage_service=services.stage_scene_service, sessions=services.stage_sessions,
        session_ttl_seconds=settings.DIRECTOR_SESSION_TTL_SECONDS,
        max_stage_actions=settings.DIRECTOR_MAX_STAGE_ACTIONS_PER_TURN,
        usage_tracker=services.usage_tracker,
    ))

    async def session_cleanup_worker():
        logger.info(
            "Session cleanup worker started: ttl=%ss, interval=%ss",
            store.ttl_seconds, cleanup_interval,
        )
        try:
            while True:
                await asyncio.sleep(cleanup_interval)
                store.cleanup_expired()
        except asyncio.CancelledError:
            logger.info("Session cleanup worker stopped")
            raise

    @app.on_event("startup")
    async def startup_session_cleanup():
        logger.info(
            "DeepSeek usage tracker %s (scope=current backend instance)",
            "enabled" if services.usage_tracker.enabled else "disabled",
        )
        if store.ttl_seconds <= 0:
            logger.info("Session TTL disabled, cleanup worker not started")
            app.state.session_cleanup_task = None
            return
        app.state.session_cleanup_task = asyncio.create_task(session_cleanup_worker())

    @app.on_event("shutdown")
    async def shutdown_session_cleanup():
        task = getattr(app.state, "session_cleanup_task", None)
        if not task:
            for session in list(store.sessions.values()):
                session.mark_closed("server_shutdown")
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            for session in list(store.sessions.values()):
                session.mark_closed("server_shutdown")

    @app.get("/")
    async def root():
        """根路径"""
        return {
            "service": "Umamusume Dialogue Server",
            "version": "0.2.0",
            "status": "running"
        }

    @app.get("/capabilities")
    async def capabilities():
        """Expose additive protocol features for independently deployed clients."""
        return {
            "dialogue_api_version": 2,
            "input_limits": {"max_input_chars": MAX_INPUT_CHARS, "max_turn_events": MAX_TURN_EVENTS,
                             "max_history_bytes": MAX_HISTORY_BYTES},
            "dialogue_events": EVENT_SCHEMA_VERSION,
            "dialogue_memory": 1 if settings.DIALOGUE_COMPACTION_ENABLED else 0,
            "context_event_batch": 1,
            "director_mode": 1,
            "director_memory": 1 if settings.DIRECTOR_COMPACTION_ENABLED else 0,
            "director_schema_version": 1,
            "director_custom_scenes": 1,
            "director_story_outline": 1,
            "director_history_resume": 1,
            "director_browser_recovery": 1,
            "director_reply_regenerate": 1,
            "deepseek_usage": 1 if services.usage_tracker.enabled else 0,
            "stage_api": 1,
            "stage_api_schema_version": "agent_stage_api.v1",
            "tts_jobs": 1 if settings.ENABLE_TTS else 0,
            "tts_manual_playback": 1 if settings.ENABLE_TTS else 0,
            "director_max_participants": settings.DIRECTOR_MAX_PARTICIPANTS,
            "director_max_speakers_per_turn": (
                settings.DIRECTOR_MAX_SPEAKERS_PER_TURN
            ),
            "supported_event_types": [
                "dialogue",
                "action",
                "narration",
                "scene_event",
            ],
        }

    @app.get("/usage/recent")
    async def recent_llm_usage(user_uuid: str):
        """Return this browser's in-memory DeepSeek usage, never account balance."""

        normalized_user_uuid = require_valid_user_uuid(user_uuid)
        return services.usage_tracker.snapshot(user_uuid=normalized_user_uuid)


    app.include_router(create_dialogue_router(
        service=services.dialogue_service, session_store=store,
        compactor=services.compactor,
        character_manager=services.character_manager,
        voice_service=services.voice_service, usage_tracker=services.usage_tracker,
        settings=settings,
    ))
    app.include_router(create_tts_router(voice_service=services.voice_service))
    return app
