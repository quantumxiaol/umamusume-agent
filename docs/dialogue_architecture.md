# Dialogue Runtime Architecture

The existing single-character API remains rooted at
`umamusume_agent.server.dialogue_server:app`. Internally, the dialogue flow is
split into reusable layers so later scene orchestration does not need to call
the HTTP API or duplicate model handling.

## Dependency direction

```text
FastAPI routes
    -> DialogueService
        -> LegacyDialogueContextBuilder
        -> CharacterRuntime
        -> DialogueSession
            -> JSONL history helpers

FastAPI routes
    -> VoiceService
        -> TTSMCPClient
            -> project-local TTS MCP
                -> JapaneseDialoguePreparer
                -> FishSpeechHttpClient
```

The `dialogue` package must not import FastAPI. Provider errors remain ordinary
Python/OpenAI exceptions until the server layer translates them into HTTP
responses.

The active dialogue, director, and TTS translation runtimes call the
OpenAI-compatible SDK directly. The dialogue backend uses the official MCP
client to submit asynchronous jobs to the project-local TTS MCP service, which
then calls the external Fish Speech HTTP service. The legacy IndexTTS MCP
client remains as a compatibility implementation. LangChain, LangGraph, and
the LangChain MCP adapters remain available through the optional
`langchain-mcp` dependency group for future orchestration and tool integrations.

## Module responsibilities

- `dialogue/protocol.py`: structured reply schema, legacy parsing, JSON repair
  prompts, and history-to-model normalization.
- `dialogue/runtime.py`: provider calls, `response_format` capability fallback,
  JSON repair, regeneration, and the final safe reply.
- `dialogue/context.py`: character system prompt, output constraints, prefix
  cache metadata, and hidden format reinjection. Projects legacy assistant
  history to JSON for JSON-mode requests without modifying archived records or
  checkpoint fingerprints.
- `dialogue/compaction.py` / `compaction_runtime.py`: low-frequency history
  compaction with independent long-output budgets; the summary transport also
  supports a separate Director configuration namespace.
- `director/compaction.py` / `memory.py`: one public summary shared by Director,
  Stage and actors, with per-thread watermarks and versioned scene checkpoints.
- `dialogue/memory.py` / `token_budget.py`: validated checkpoints, stable prompt
  views and usage-calibrated token estimates. See [long-history memory](dialogue_memory.md).
- `dialogue/service.py`: one complete user-to-character turn.
- `dialogue/session.py`: mutable in-memory state for a legacy single-character
  session.
- `dialogue/history.py`: JSONL paths, parsing, restoration, filtering, and
  import normalization.
- `dialogue/history_order.py`: shared timezone normalization and deterministic
  ordering for history display, restoration, reset cutoffs and checkpoints.
- `tts/service.py`: Dialogue/Director to TTS MCP request adaptation.
- `tts/agent.py`: context-aware Chinese-to-Japanese dialogue preparation.
- `tts/jobs.py`: asynchronous job lifecycle, bounded concurrency, and TTL.
- `tts/fish_client.py`: external Fish Speech multipart HTTP protocol.
- `tts/mcp_server.py`: project-local TTS MCP tools.
- `server/dialogue_server.py`: stable Uvicorn/Hugging Face entry point only.
- `server/app.py`: `create_app`, router assembly, CORS and application lifecycle.
- `server/services.py`: `build_services` / `ServerServices`, assembling one
  dependency graph per app. Dialogue, Director and Stage reuse the same
  `CharacterRuntime`, while their session registries remain separate.
- `server/middleware.py`: API-key protection and per-app rate-limit buckets.
- `server/schemas.py`: single-character HTTP request models.
- `server/sessions.py`: single-character session registration and expiry.
- `server/dialogue_routes.py`: single-character, character-list, session and
  history endpoints; HTTP adaptation rather than model execution logic.
- `server/streaming.py`: the legacy two-line token-stream protocol.
- `server/dialogue_turns.py`: serialized single-session operations, compaction
  progress, heartbeat delivery and cancellation propagation.
- `server/tts_routes.py`: audio/job endpoints and single-dialogue TTS adaptation.
- `server/http_utils.py`: browser UUID validation and upstream HTTP errors.
- `director/recovery.py`: `SceneRecovery`, validating browser snapshots and
  replaying server JSONL into scene state and prompt threads. It depends on
  character loading and context builders, not on the online turn service.

Application and runtime tests inject fake clients through `build_services` or
`CharacterRuntime`, then call `create_app(services=...)`. They no longer patch
module-level clients or use protocol/history forwarding functions in the entry
point. Importing the factory does not instantiate clients or create sessions;
the deployment entry point still creates the default app.

## Compatibility invariants

- `POST /chat` accepts the original payload and returns
  `action`, `dialogue`, and `message`.
- Story events and history memory are additive. Existing request and response
  fields retain their meanings; optional checkpoints and `model_content` are new.
- `GET /capabilities` lets a separately deployed frontend enable story events
  only after the Hugging Face backend advertises `dialogue_events=1`.
- JSON-mode `POST /chat_stream` emits `structured_reply` before `done`.
- Compaction may emit `context_status` before the reply, plus keepalive comments.
- Disabled JSON mode preserves the legacy token stream.
- Assistant history remains schema version 2 and restores legacy records.
- TTS receives only newly generated character `dialogue`; action,
  trainer/environment input, parse-error fallbacks, and old off-period dialogue
  are never synthesized.
- The Hugging Face entry point remains the root `app.py` importing
  `umamusume_agent.server.dialogue_server:app` on port 7860.

## Story events (phase 2)

`ActorRef` identifies the trainer, current Umamusume, narrator/environment, or
a future NPC/director. `event_type` describes whether content is dialogue, an
action, narration, or a scene event. Metadata is stored alongside semantic
history, while the character model receives stable natural-language labels
such as `【训练员动作】` and `【环境变化】`.

The single-character page remains a `DialogueService` session: environment
events cause the selected character to react. Multi-character scheduling and
shared scene memory are now implemented separately under `director/`.
`DirectorService` reuses `CharacterRuntime`, while `DialogueService` remains
unaware of director orchestration and keeps the legacy `/chat` boundary stable.

The phase-2 composer may stage several events locally. On final send, earlier
items are submitted as `context_events` and the last item remains the ordinary
request message. `DialogueService` appends every input in order, builds context
once, and invokes the character runtime once. Staging alone never mutates the
server session or calls the model.

## Regression tests

Run the runtime-focused suite with:

```bash
.venv/bin/python -m unittest \
  tests.test_dialogue_json_protocol \
  tests.test_dialogue_context \
  tests.test_dialogue_history \
  tests.test_dialogue_routes \
  tests.test_server_app \
  tests.test_director_recovery \
  tests.test_director_service \
  tests.test_director_routes \
  tests.test_stage_integration
```
