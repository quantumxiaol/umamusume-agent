// Presentation-only rows: never append these markers to the dialogue transcript.
export const buildMemoryTimeline = (messages, checkpoints = []) => {
  const hasContent = (message) => Boolean(String(message.dialogue || message.content || '').trim());
  const total = messages.filter(hasContent).length;
  const markers = new Map();
  const seen = new Set();
  for (const checkpoint of checkpoints) {
    const covered = checkpoint.covered_messages;
    if (!Number.isInteger(covered) || covered < 1 || covered > total || !checkpoint.summary) continue;
    const key = checkpoint.checkpoint_id || `${checkpoint.revision}:${checkpoint.created_at || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const trigger = checkpoint.trigger_message_count;
    const locationKnown = Number.isInteger(trigger) && trigger >= covered && trigger <= total;
    // Old exports have no trigger location. Show the covered boundary, labelled
    // as a fallback; never invent a historical triggering point.
    const position = locationKnown ? trigger : covered;
    const at = markers.get(position) || [];
    at.push({ kind: 'memory', key: `memory-${key}`, checkpoint, locationKnown });
    markers.set(position, at);
  }
  const rows = [];
  let count = 0;
  for (const message of messages) {
    rows.push({ ...message, kind: 'message', key: message.id });
    if (!hasContent(message)) continue; // In-flight/failed empty placeholders don't exist in server history.
    count += 1;
    rows.push(...(markers.get(count) || []));
  }
  return rows;
};

// Scene sequences may change after HF/browser recovery; stable event IDs do not.
export const buildSceneMemoryTimeline = (events, checkpoints = []) => {
  const positions = new Map(events.map((event, index) => [event.event_id, index]));
  const markers = new Map();
  const seen = new Set();
  for (const checkpoint of checkpoints) {
    const covered = positions.get(checkpoint.covered_event_id);
    const trigger = positions.get(checkpoint.trigger_event_id);
    const key = checkpoint.checkpoint_id;
    if (covered === undefined || !key || seen.has(key) || !checkpoint.summary) continue;
    seen.add(key);
    const locationKnown = trigger !== undefined && trigger >= covered;
    const position = locationKnown ? trigger : covered;
    const at = markers.get(position) || [];
    at.push({ kind: 'memory', key: `memory-${key}`, checkpoint, locationKnown });
    markers.set(position, at);
  }
  return events.flatMap((event, index) => [
    { ...event, kind: 'event', key: event.event_id }, ...(markers.get(index) || []),
  ]);
};
