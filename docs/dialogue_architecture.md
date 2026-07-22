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
```

The `dialogue` package must not import FastAPI. Provider errors remain ordinary
Python/OpenAI exceptions until the server layer translates them into HTTP
responses.

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
- `tts/service.py`: output reservation and IndexTTS orchestration.
- `server/dialogue_server.py`: FastAPI middleware, lifecycle, routes, SSE, and
  HTTP error translation.

## Compatibility invariants

- `POST /chat` accepts the original payload and returns
  `action`, `dialogue`, and `message`.
- JSON-mode `POST /chat_stream` emits `structured_reply` before `done`.
- Disabled JSON mode preserves the legacy token stream.
- Assistant history remains schema version 2 and restores legacy records.
- TTS receives only the parsed `dialogue` field.
- The Hugging Face entry point remains the root `app.py` importing
  `umamusume_agent.server.dialogue_server:app` on port 7860.

## Regression tests

Run the runtime-focused suite with:

```bash
.venv/bin/python -m unittest \
  tests.test_dialogue_json_protocol \
  tests.test_dialogue_context \
  tests.test_dialogue_history \
  tests.test_dialogue_routes
```

The older search and MCP scripts under `tests/` require the optional `extras`
dependency set and are not part of the minimal dialogue runtime suite.
