import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSceneMarkdown, sceneMarkdownFilename, downloadSceneMarkdown } from '../src/services/sceneMarkdown.js';

const snapshot = {
  template: { name: '河边的约定', initial_state: { location: '河边', time: '黄昏' } },
  participants: [{ actor: { display_name: '训练员' } }, { actor: { display_name: '米浴' } }],
  user_uuid: 'private-user', session_id: 'private-session', turn_index: 555,
  context_checkpoint: { summary: '隐藏摘要' }, story_outline: '尚未发生的剧透',
  events: [
    { event_type: 'narration', turn_index: 0, content: '晚风吹过河面。' },
    { event_type: 'dialogue', turn_index: 1, actor: { display_name: '训练员' }, content: '明天一起训练吧。' },
    { event_type: 'character_reply', turn_index: 1, actor: { display_name: '米浴' }, action: '轻轻点头', dialogue: '好，约好了。', model_content: '原始模型输入', voice: { audio_url: 'https://private/audio' } },
    { event_type: 'scene_change', turn_index: 555, content: '天色暗下来。' },
    { event_type: 'director_plan', content: '秘密计划' },
    { event_type: 'actor_directive', content: '秘密指令' },
    { event_type: 'narration', hidden: true, content: '隐藏事件' },
    { event_type: 'dialogue', visible_to: ['player'], content: '私密对白' },
  ],
};

test('keepsake exports full public story, not compressed model memory or internal fields', () => {
  const before = JSON.stringify(snapshot);
  const text = buildSceneMarkdown(snapshot);
  for (const word of ['# 河边的约定', '参与人物：训练员、米浴', '黄昏', '### 开场', '### 第 1 轮', '动作：轻轻点头', '好，约好了。', '### 第 555 轮']) assert.ok(text.includes(word), word);
  for (const word of ['private', '隐藏摘要', '剧透', '秘密', '隐藏事件', '私密对白', '原始模型输入']) assert.ok(!text.includes(word), word);
  assert.equal(JSON.stringify(snapshot), before);
});

test('old snapshots and empty scenes can be exported; failed replies are not passed off as dialogue', () => {
  assert.match(buildSceneMarkdown({}), /导演场景/);
  assert.equal(buildSceneMarkdown(null), '');
  const text = buildSceneMarkdown({ events: [{ event_type: 'character_reply', source_format: 'parse_error', dialogue: '错误的角色兜底', action: '无' }] });
  assert.match(text, /回复生成失败/);
  assert.ok(!text.includes('错误的角色兜底'));
  assert.ok(!text.includes('动作：无'));
});

test('export escapes HTML, links and images; filenames cannot contain path separators', () => {
  const text = buildSceneMarkdown({ events: [{ event_type: 'dialogue', content: '<script>alert(1)</script>\n![remote](https://image)' }] });
  assert.ok(!text.includes('<script>'));
  assert.ok(text.includes('&lt;script&gt;'));
  assert.ok(text.includes('\\!\\[remote\\]'));
  assert.equal(sceneMarkdownFilename({ template: { name: '../恶意/名称:*' } }), '.._恶意_名称__-第0轮.md');
});

test('download creates UTF-8 Markdown and releases the temporary object URL', async () => {
  const old = { document: globalThis.document, create: URL.createObjectURL, revoke: URL.revokeObjectURL, timeout: globalThis.setTimeout };
  let blob; let clicked = false; let removed = false; let revoked; let cleanup;
  const anchor = { click() { clicked = true; }, remove() { removed = true; } };
  try {
    globalThis.document = { createElement: () => anchor, body: { appendChild: () => {} } };
    URL.createObjectURL = (value) => { blob = value; return 'blob:download'; };
    URL.revokeObjectURL = (url) => { revoked = url; };
    globalThis.setTimeout = (callback) => { cleanup = callback; };
    downloadSceneMarkdown(snapshot);
    assert.equal(anchor.download, '河边的约定-第555轮.md');
    assert.ok(clicked && removed);
    assert.match(await blob.text(), /河边的约定/);
    assert.equal(blob.type, 'text/markdown;charset=utf-8');
    cleanup();
    assert.equal(revoked, 'blob:download');
  } finally {
    globalThis.document = old.document;
    URL.createObjectURL = old.create; URL.revokeObjectURL = old.revoke; globalThis.setTimeout = old.timeout;
  }
});
