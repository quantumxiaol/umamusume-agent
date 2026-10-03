// Reading keepsake only: never serialize prompts, identifiers, checkpoints or audio URLs.
const kinds = {
  dialogue: '对白', action: '动作', narration: '旁白', scene_event: '环境事件',
  scene_change: '场景变化', character_reply: '角色回应', actor_enter: '入场', actor_leave: '离场',
};
const stateLabels = {
  location: '地点', sub_location: '位置', time: '时间', weather: '天气',
  lighting: '光线', atmosphere: '氛围', ambient_sound: '环境声',
};
// Preserve literal text in Markdown viewers, including HTML and image/link syntax.
const escape = (value) => String(value ?? '').replace(/&/g, '&amp;')
  .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/([\\`*_{}\[\]()#+.!|~-])/g, '\\$1');
const inline = (value) => escape(String(value ?? '').replace(/[\r\n]+/g, ' '));
const paragraph = (value) => escape(value).replace(/\r\n?/g, '\n').replace(/\n/g, '  \n');

export const sceneMarkdownFilename = (snapshot) => {
  const name = String(snapshot?.template?.name || '导演场景')
    .replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_').replace(/[. ]+$/g, '').slice(0, 80) || '导演场景';
  return `${name}-第${Number(snapshot?.turn_index) || 0}轮.md`;
};

export const buildSceneMarkdown = (snapshot) => {
  if (!snapshot) return '';
  const lines = [`# ${inline(snapshot.template?.name || '导演场景')}`, '',
    '> 导演模式 · 剧情纪念册。仅供阅读，不作为历史恢复文件。', '',
    `- 参与人物：${(snapshot.participants || []).map((item) => inline(item.actor?.display_name || '')).filter(Boolean).join('、')}`,
    `- 进度：第 ${Number(snapshot.turn_index) || 0} 轮`, '', '## 开场环境', ''];
  for (const [key, label] of Object.entries(stateLabels)) {
    const value = snapshot.template?.initial_state?.[key];
    if (value) lines.push(`- ${label}：${inline(value)}`);
  }
  lines.push('', '## 剧情记录', '');
  // Snapshots already contain the current revisions in timeline order. Do not mutate/sort them.
  let lastTurn = null;
  for (const event of snapshot.events || []) {
    if (!kinds[event.event_type] || event.hidden || (event.visible_to && event.visible_to !== 'all')) continue;
    const turn = Number(event.turn_index) || 0;
    if (turn !== lastTurn) {
      lines.push(`### ${turn ? `第 ${turn} 轮` : '开场'}`, '');
      lastTurn = turn;
    }
    lines.push(`**${inline(event.actor?.display_name || '环境')} · ${kinds[event.event_type]}**`, '');
    if (event.source_format === 'parse_error') {
      lines.push('> 此处回复生成失败，未计入角色剧情。', '');
      continue;
    }
    if (event.action && event.action !== '无') lines.push(`动作：${paragraph(event.action)}`, '');
    const text = event.dialogue || event.content;
    if (text && text !== event.action) lines.push(paragraph(text), '');
  }
  return `${lines.join('\n').trim()}\n`;
};

export const downloadSceneMarkdown = (snapshot) => {
  const url = URL.createObjectURL(new Blob([buildSceneMarkdown(snapshot)], { type: 'text/markdown;charset=utf-8' }));
  const anchor = document.createElement('a');
  try {
    anchor.href = url;
    anchor.download = sceneMarkdownFilename(snapshot);
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // Let the browser consume the download before revoking the object URL.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
};
