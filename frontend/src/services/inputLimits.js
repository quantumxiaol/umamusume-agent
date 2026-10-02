// Keep these hard limits aligned with input_limits.py. Historical records are
// deliberately NOT restricted to the 10K limit used for new user input.
export const MAX_INPUT_CHARS = 10000;
export const MAX_TURN_EVENTS = 20;
export const MAX_HISTORY_BYTES = 32 * 1024 * 1024;
export const MAX_HISTORY_MESSAGES = 20000;
export const MAX_HISTORY_FIELD_CHARS = 200000;
export const MAX_HISTORY_TEXT_CHARS = 8000000;
export const MAX_HISTORY_CHECKPOINTS = 100;

export const characterCount = (value) => {
  const text = String(value || '');
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    count += 1;
    if (text.codePointAt(index) > 0xffff) index += 1;
  }
  return count;
};

export const inputBatchError = (contents) => {
  if (contents.length > MAX_TURN_EVENTS) return `一次最多发送 ${MAX_TURN_EVENTS} 条事件，请分批发送。`;
  const total = contents.reduce((sum, text) => sum + characterCount(text), 0);
  return total > MAX_INPUT_CHARS
    ? `一次发送的内容（含已加入事件）不能超过 ${MAX_INPUT_CHARS.toLocaleString()} 字符，请缩短或分批发送。` : '';
};

const rejectHistory = (message) => {
  const error = new Error(`${message}；当前历史和缓存未修改。`);
  error.code = 'INPUT_LIMIT';
  throw error;
};

export const assertHistorySize = (records, checkpoint = null, checkpoints = [], { scene = false } = {}) => {
  if (!Array.isArray(records)) rejectHistory('历史记录格式无效');
  if (records.length > (scene ? 5000 : MAX_HISTORY_MESSAGES)) rejectHistory(`历史记录数量超过 ${scene ? 5000 : MAX_HISTORY_MESSAGES} 条上限`);
  const snapshots = checkpoints || [];
  if (!Array.isArray(snapshots)) rejectHistory('历史摘要格式无效');
  if (snapshots.length > MAX_HISTORY_CHECKPOINTS) rejectHistory(`摘要版本超过 ${MAX_HISTORY_CHECKPOINTS} 个上限`);
  let total = 0;
  let sceneText = 0;
  for (const [index, record] of records.entries()) {
    let eventText = 0;
    for (const name of ['content', 'action', 'dialogue', 'model_content', 'modelContent']) {
      const value = record?.[name];
      if (typeof value !== 'string') continue;
      const length = characterCount(value);
      if (length > MAX_HISTORY_FIELD_CHARS) rejectHistory(`历史第 ${index + 1} 条的 ${name} 超过 ${MAX_HISTORY_FIELD_CHARS} 字符`);
      total += length;
      if (['content', 'action', 'dialogue'].includes(name)) eventText += length;
    }
    if (scene && eventText > 50000) rejectHistory(`场景第 ${index + 1} 条事件超过 50000 字符`);
    sceneText += eventText;
  }
  if (scene && sceneText > 2000000) rejectHistory('场景原文超过 2000000 字符');
  for (const item of [...snapshots, ...(checkpoint ? [checkpoint] : [])]) {
    if (typeof item?.summary !== 'string') continue;
    const length = characterCount(item.summary);
    if (length > 2000000) rejectHistory('单个历史摘要超过 2000000 字符');
    total += length;
  }
  if (total > MAX_HISTORY_TEXT_CHARS) rejectHistory(`历史文本及摘要合计超过 ${MAX_HISTORY_TEXT_CHARS} 字符`);
};

export const assertHistoryFileSize = (file) => {
  if (Number(file?.size || 0) > MAX_HISTORY_BYTES) rejectHistory('历史文件超过 32 MiB');
};

export const assertHistoryPayloadSize = (payload) => {
  if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > MAX_HISTORY_BYTES) {
    rejectHistory('历史请求超过 32 MiB');
  }
};
