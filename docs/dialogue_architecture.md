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
  cache metadata, and hidden format reinjection.
- `dialogue/service.py`: one complete user-to-character turn.
- `dialogue/session.py`: mutable in-memory state for a legacy single-character
  session.
- `dialogue/history.py`: JSONL paths, parsing, restoration, filtering, and
  import normalization.
- `tts/service.py`: Dialogue/Director to TTS MCP request adaptation.
- `tts/agent.py`: context-aware Chinese-to-Japanese dialogue preparation.
- `tts/jobs.py`: asynchronous job lifecycle, bounded concurrency, and TTL.
- `tts/fish_client.py`: external Fish Speech multipart HTTP protocol.
- `tts/mcp_server.py`: project-local TTS MCP tools.
- `server/dialogue_server.py`: FastAPI middleware, lifecycle, routes, SSE, and
  HTTP error translation.

## Compatibility invariants

- `POST /chat` accepts the original payload and returns
  `action`, `dialogue`, and `message`.
- Story event fields are additive. Requests without `speaker`, `event_type`,
  and `target_actor_ids` keep the exact legacy response shape.
- `GET /capabilities` lets a separately deployed frontend enable story events
  only after the Hugging Face backend advertises `dialogue_events=1`.
- JSON-mode `POST /chat_stream` emits `structured_reply` before `done`.
- Disabled JSON mode preserves the legacy token stream.
- Assistant history remains schema version 2 and restores legacy records.
- TTS receives only newly generated character `dialogue`; action,
  trainer/environment input, and old off-period dialogue are never synthesized.
- The Hugging Face entry point remains the root `app.py` importing
  `umamusume_agent.server.dialogue_server:app` on port 7860.

## Story events (phase 2)

`ActorRef` identifies the trainer, current Umamusume, narrator/environment, or
a future NPC/director. `event_type` describes whether content is dialogue, an
action, narration, or a scene event. Metadata is stored alongside semantic
history, while the character model receives stable natural-language labels
such as `【训练员动作】` and `【环境变化】`.

The current UI remains a single-character session: environment events cause
the selected character to react. Multi-character scheduling and shared scene
memory remain phase 3 concerns and do not live in `DialogueService`.

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
  tests.test_dialogue_routes
```
