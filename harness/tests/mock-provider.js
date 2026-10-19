/** Explicitly loaded test fixture; never shipped or enabled by the bundle. */
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
export const inject = ['llm'];
class MockProvider extends LlmAdapter {
  providerInfo(provider) { return { id: provider, name: 'Umamusume offline test' }; }
  async listModels(provider) { return [{ provider, id: 'mock', name: 'Offline mock', contextWindow: 128000 }]; }
  async resolveModel(provider, model) { return { provider, id: model, name: 'Offline mock', context: { contextWindow: 128000 } }; }
  async *stream(request) {
    request.signal?.throwIfAborted();
    if (request.messages.some(message => message.content.some(block => block.type === 'text' && block.text.includes('__uma_cancel_test__')))) {
      await new Promise((_resolve, reject) => request.signal.addEventListener('abort', () => reject(request.signal.reason), { once: true }));
    }
    const text = JSON.stringify({ action: '轻轻点头，露出微笑', dialogue: '训练员，今天也一起加油吧！' });
    yield { type: 'block-start', index: 0, blockType: 'text' };
    yield { type: 'text-delta', index: 0, text };
    yield { type: 'block-end', index: 0, block: { type: 'text', text } };
    yield { type: 'usage', usage: { inputTokens: 20, cacheReadTokens: 100, outputTokens: 25, totalTokens: 145 } };
    yield { type: 'finish', reason: { kind: 'stop' } };
  }
}
export function apply(ctx) { ctx.llm.registerAdapter(['uma-test'], new MockProvider()); }
