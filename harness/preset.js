// Generated from harness/src by npm run build. Do not edit.
const name = "umamusume-controls";
const inject = ["commands", "umamusume"];
function apply(ctx) {
  ctx.commands.register({
    name: "uma",
    description: "赛马娘：选择角色、输入类型、事件队列和重生成",
    input: { hint: "character rice_shower / mode action / retry / clear" },
    handler: (invocation) => ctx.umamusume.configure(invocation)
  });
}
export {
  apply,
  inject,
  name
};
