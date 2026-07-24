# TTS Pipeline

The active voice path produces Japanese character audio while keeping Chinese
dialogue as the visible subtitle. It deliberately keeps the MCP boundary:

```text
Dialogue / Director route
    -> VoiceService
    -> TTSMCPClient
    -> project-local TTS MCP Server
        -> JapaneseDialoguePreparer (OpenAI-compatible LLM)
        -> FishSpeechHttpClient
        -> external fish-tts-server
```

The original `IndexTTSMCPClient` remains in `tts/mcp_client.py` for
compatibility, but the active path uses `TTSMCPClient`.

## Request boundary

A TTS job is created only when all of the following are true:

- backend `ENABLE_TTS=true`;
- the frontend TTS switch was on when the user sent that turn;
- the generated event is a character reply with a non-empty `dialogue`.

Trainer dialogue, trainer actions, environment events, narration, and character
`action` fields are never synthesized. Turning TTS on does not scan or backfill
older replies.

The submit request contains one current Chinese subtitle plus stable character
metadata, cast names, and public context events. It never mutates dialogue or
director history.

## Translation and prefix reuse

`JapaneseDialoguePreparer` is intentionally narrow. It asks the model for only:

```json
{"subtitle_ja":"...","spoken_text_ja":"..."}
```

It must preserve meaning, persona, proper names, first-person wording, and
honorifics. The Fish Speech input comes only from validated `spoken_text_ja`.
The default output budget is 1024 tokens. HTTP 200 responses with empty content
are retried without provider JSON mode, `finish_reason=length` responses are
retried with a larger output budget, and malformed JSON receives bounded repair
turns. Failed attempts are removed from the persistent translation prefix.

Each `user_uuid + dialogue/scene thread + speaker` owns an append-only
translation thread. Single-character chat uses a stable per-character thread
key even when `/load_character` creates a new HTTP session; a director scene
uses its scene ID. The system prefix contains fixed rules, character card,
Japanese names, cast, reference text, and speaking constraints. Previously
unseen public events are appended in timeline order. When supported, the stable
prefix receives provider `cache_control` metadata.

Idle translation threads and excess anonymous-browser threads are removed by
`TTS_TRANSLATION_THREAD_TTL_SECONDS` and `TTS_TRANSLATION_MAX_THREADS`.

Dialogue created while TTS was off may later appear only as translation context.
It is never submitted as a synthesis target.

## Asynchronous jobs

`tts_submit` returns immediately with a `job_id`. Jobs then move through:

```text
queued
  -> translating
  -> validating
  -> synthesizing
  -> downloading
  -> ready
```

`failed` and `cancelled` are terminal states. A bounded semaphore controls the
number of concurrent Fish Speech requests. Repeated submit calls for the same
`user_uuid + session + utterance + speaker` return the same job.

MCP tools:

- `tts_submit(request)`
- `tts_get_job(job_id, user_uuid)`
- `tts_cancel(job_id, user_uuid)`
- `tts_health()`

The FastAPI facade exposes job status, cancellation, and an owner-scoped audio
endpoint. Ownership uses the same browser-generated `user_uuid` isolation as
dialogue/director history.

## Audio and browser storage

The browser polls job status and shows a play button only after `ready`. It does
not autoplay. Local browser history stores only lightweight `job_id` and status
metadata; it never stores audio Blob/Base64 data or the short-lived audio URL.
After refresh it queries the job again to recover a playable URL.

Audio is a temporary backend file under `outputs/tts_jobs/`. Job TTL cleanup
removes both the in-memory record and its file. Audio responses include
`Cache-Control: private, no-store`.

Hugging Face restarts lose in-memory jobs and temporary audio. The Chinese
conversation or public director timeline remains recoverable using the existing
browser snapshot behavior, but old audio is not regenerated automatically.

## Fish Speech contract

`FishSpeechHttpClient` follows the same HTTP shape used by
`umamusume-anime`:

- `GET /fishspeech/health`
- multipart `POST /fishspeech/tts/voice_clone`
- form fields include `text`, `format`, optional `ref_text`, output name, and
  generation options;
- the reference audio is uploaded as `ref_audio`;
- the returned audio URL/path is streamed into the temporary job file.

The default timeout is 900 seconds because synthesis can be much slower than the
dialogue API.

## Deployment

For local development, start the external Fish Speech service, then:

```bash
uv run python -m umamusume_agent.tts.mcp_server
uvicorn umamusume_agent.server.dialogue_server:app --host 0.0.0.0 --port 1111
```

The current production policy keeps both sides disabled:

```text
GitHub Pages: VITE_ENABLE_TTS=false
Hugging Face: ENABLE_TTS=false
```

TTS is currently local-development only. Locally, set `ENABLE_TTS=true`, keep
the frontend `VITE_ENABLE_TTS=true`, start the project-local MCP process, and
point `FISHSPEECH_BASE_URL` at the local Fish Speech service.

The Docker entrypoint can still auto-start the MCP process when both
`ENABLE_TTS=true` and `TTS_MCP_AUTOSTART=true`; this capability is retained for
local Docker testing or a future explicit production opt-in.
