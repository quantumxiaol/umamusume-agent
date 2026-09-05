# Director Mode V1

Director mode is a separate multi-character scene layer. It reuses
`CharacterRuntime` for each character but never writes into legacy
`DialogueSession` history.

## V1 scope

- The player is the trainer.
- A scene starts from one location preset or a transient custom environment,
  plus 1–3 selected characters.
- A story outline is optional and remains separate from the location preset.
- One director plan is generated for each submitted event batch.
- At least one character replies to each submitted batch. The director normally
  schedules one and uses a second only when interaction benefits the scene.
- Character replies are sequential; later speakers see earlier replies.
- A parse-error fallback stops the remaining speakers for that turn and is
  never sent to TTS.
- The latest public character reply can be regenerated in place. Its event ID
  and sequence stay stable while its revision increments.
- Public events are visible to every participant.
- The director may patch scene state and add narration, but cannot author
  character action or dialogue.
- Scene history is text-only. Each browser stores its full public scene snapshot
  in `localStorage`, scoped by that browser's generated `user_uuid`.
- The backend also writes a best-effort JSONL copy under `outputs/director`.
  With the same prompt configuration and character cards, JSONL can exactly
  rebuild every prompt thread while the Space filesystem survives; the browser
  snapshot can rebuild a fresh append-only context after
  an ephemeral Hugging Face container loses both memory and files.
- There is no autonomous loop, private memory, relationship inheritance, or
  mid-scene cast expansion in V1.

## Runtime flow

```text
input events
    -> append-only SceneTimeline
    -> DirectorRuntime -> validated DirectorPlan
    -> scene patch / narration events
    -> CharacterRuntime(A) -> public reply event
    -> CharacterRuntime(B), now seeing A -> public reply event
```

Only these invariants are enforced in Python:

- planned speakers must be present characters;
- target IDs must exist;
- a character may speak only once per turn;
- the number of speakers is bounded;
- an empty/invalid speaker plan falls back to one targeted character;
- a turn always terminates after the planned replies.

Semantic scheduling remains the director model's responsibility.

## Prefix-cache invariant

The director and every character own an independent `PromptThread`.
Threads are append-only:

```text
system(static prompt + template + initial cast)
user(turn packet)
assistant(plan or character reply)
user(next turn packet)
assistant(...)
```

Each thread receives a full `current_scene_state` initially. Later packets
include only changed fields in `scene_state_patch`, including explicit nulls
for removed fields, and omit state when unchanged. The full initial state and
all subsequent patches remain in history; there is no window or compression.
Unseen events and one-turn instructions are also appended at the tail. Earlier
messages are never reordered or rewritten, so the entire previous normal-turn
request is an exact prefix of the next normal-turn request for that runtime.

Validated character replies and director plans retain their original JSON
text when no fields were normalized, removed, or added by validation. That
text is used for the assistant message and persisted as internal `model_content`
in backend JSONL; public event responses and browser snapshots exclude it.
Modified replies and plans use the accepted, normalized representation.
Director plans are asked to omit unchanged `scene_patch` fields instead of
repeating null values.

Manual regeneration is the single bounded exception at the mutable tail: the
latest character assistant message is replaced only after a retry succeeds.
The retry request reuses the unchanged prefix and adds a transient correction
instruction. JSONL persistence remains append-only by writing a later event
revision with the same event ID and sequence; restoration selects the newest
revision.

The selected location or custom opening environment, initial cast, and optional
story outline are fixed when the session is created and therefore stay in the
director's static prefix. The outline guides scheduling but is not exposed to
characters as hidden knowledge and is not treated as a mandatory script.

Each character uses its full character card in its own static system prefix.
The director uses only actor IDs and compact cast metadata. A compact role
constraint is included in the new user packet's `backend_reminder` field according
to that runtime's own reply count, controlled by
`DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES`. Normal director and character
threads keep a single static system message even at turns 26 and 51. Reminders
remain on their original user packets when history grows or is restored.
Application message prefix equality does not guarantee provider cache hits;
request diagnostics and provider usage must be compared together.

Provider usage logs include `prompt_tokens`, `completion_tokens`, reasoning and
cache-hit tokens when the compatible API returns those fields. With an official
DeepSeek base URL, a bounded in-memory ledger also groups these responses by
browser and director turn for `GET /usage/recent`. A future history checkpoint
may intentionally reset the prefix once; V1 keeps the event stream uncompacted.

## API

- `GET /director/templates`
- `POST /director/sessions`
- `POST /director/sessions/recover`
- `GET /director/sessions/{session_id}`
- `DELETE /director/sessions/{session_id}`
- `GET /director/history`
- `POST /director/history/{session_id}/resume`
- `DELETE /director/history/{session_id}`
- `POST /director/turn`
- `POST /director/turn_stream`
- `POST /director/sessions/{session_id}/events/{event_id}/regenerate`

The streaming endpoint emits `scene_event`, `character_reply`, `scene_state`,
and finally `done`.

`POST /director/sessions` accepts either `template_id` or `custom_scene`, never
both. `story_outline` is independent and optional for either form.

The regenerate endpoint accepts `user_uuid` and `generate_voice`. It rejects
anything except the latest public character reply, replaces that reply in
place, and returns the incremented `revision`. When voice is enabled, the new
revision receives a distinct TTS idempotency key. The browser cancels the old
job before calling this endpoint.

Session reads, turns, and deletes include `user_uuid`. Browser recovery accepts
only public events, validates the cast and final scene state, reloads each
character card on the backend, and rejects hidden director events. This UUID is
browser-instance isolation rather than account authentication: clearing browser
storage or copying a UUID changes that boundary.

Deleting an in-memory session ends the current browser scene but keeps its
browser and JSONL history. Deleting the corresponding history removes the
browser snapshot immediately and removes the backend copy when reachable. No
Hugging Face Persistent Storage is required; clearing local browser data loses
the browser-owned recovery copy.
