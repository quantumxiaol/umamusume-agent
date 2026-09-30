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
