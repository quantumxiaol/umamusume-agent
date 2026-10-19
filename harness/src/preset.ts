export const name = 'umamusume-controls';
export const inject = ['commands', 'umamusume'];

export function apply(ctx: Context) {
  ctx.commands.register({
    name: 'uma', description: '赛马娘：选择角色、输入类型、事件队列和重生成',
    input: { hint: 'character rice_shower / mode action / retry / clear' },
    handler: invocation => ctx.umamusume.configure(invocation),
  });
}
import type { Context } from '@deepseek-ai/cordis';
import type {} from './index.js';
import type {} from '@deepseek-ai/dsh-commands';
