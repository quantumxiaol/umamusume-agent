# Director Mode V1

Director mode is a separate multi-character scene layer. It reuses
`CharacterRuntime` for each character but never writes into legacy
`DialogueSession` history.

## V1 scope

- The player is the trainer.
- A scene starts from one JSON template and 1–3 selected characters.
- One director plan is generated for each submitted event batch.
- At most two distinct characters reply per turn.
- Character replies are sequential; later speakers see earlier replies.
- Public events are visible to every participant.
- The director may patch scene state and add narration, but cannot author
  character action or dialogue.
- Scene history is text-only and stored separately under `outputs/director`.
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

Dynamic state snapshots, unseen events, and one-turn instructions are appended
at the tail. Earlier messages are never reordered or rewritten, so the entire
previous request is an exact prefix of the next request for that runtime.

Each character uses its full character card in its own static system prefix.
The director uses only actor IDs and compact cast metadata. A compact role
constraint is appended according to that runtime's own reply count, controlled
by `DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES`.

Provider usage logs include `prompt_tokens`, `completion_tokens`, and
`cached_tokens` when the compatible API returns those fields. A future history
checkpoint may intentionally reset the prefix once; V1 keeps the event stream
uncompacted.

## API

- `GET /director/templates`
- `POST /director/sessions`
- `GET /director/sessions/{session_id}`
- `DELETE /director/sessions/{session_id}`
- `POST /director/turn`
- `POST /director/turn_stream`

The streaming endpoint emits `scene_event`, `character_reply`, `scene_state`,
and finally `done`.
