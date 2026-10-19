// Generated from harness/src by npm run build. Do not edit.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { createInterface } from "node:readline";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { WorkerEvent, errorMessage } from "./protocol.js";
const root = fileURLToPath(new URL("../", import.meta.url));
function workerCommand(config) {
  if (config.pythonExecutable) return [config.pythonExecutable, ["-u", "-m", "umamusume_agent.harness.worker"]];
  const uv = config.uvExecutable || (existsSync(join(homedir(), ".local/bin/uv")) ? join(homedir(), ".local/bin/uv") : "uv");
  return [uv, [
    "run",
    "--isolated",
    "--no-project",
    "--python",
    "3.12",
    "--with-requirements",
    join(root, "harness/requirements.txt"),
    "python",
    "-u",
    "-m",
    "umamusume_agent.harness.worker"
  ]];
}
async function runWorker({ config, input, signal, onModel, onProgress = () => {
} }) {
  signal.throwIfAborted();
  const [command, args] = workerCommand(config);
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    UMAMUSUME_SKIP_ENV_FILES: "1",
    PYTHONPATH: join(root, "src"),
    ROLEPLAY_LLM_MODEL_API_KEY: "",
    ROLEPLAY_LLM_MODEL_BASE_URL: "harness://local",
    ENABLE_TTS: "false"
  };
  for (const key of Object.keys(env)) {
    if (/(?:API_KEY|APIKEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET)/i.test(key)) delete env[key];
  }
  delete env.VIRTUAL_ENV;
  const started = Date.now();
  onProgress({ stage: "python-startup", phase: "start", label: "准备本地 Python 环境" });
  const scratchDirectory = await mkdtemp(join(tmpdir(), "umamusume-harness-"));
  const child = spawn(command, args, { cwd: root, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  child.on("close", () => {
    void rm(scratchDirectory, { recursive: true, force: true }).catch(() => {
    });
  });
  let stderr = "", settled = false;
  let killTimer, readyAt;
  let prepared = false;
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (data) => {
    stderr = (stderr + data).slice(-6e3);
  });
  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
  const write = (value) => {
    if (!child.stdin.destroyed) child.stdin.write(JSON.stringify(value) + "\n");
  };
  const stop = () => {
    if (child.exitCode !== null) return;
    child.kill("SIGTERM");
    killTimer = setTimeout(() => child.kill("SIGKILL"), 2e3);
    killTimer.unref();
  };
  return new Promise((resolve, reject) => {
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      if (error && !prepared) {
        try {
          onProgress({
            stage: readyAt === void 0 ? "python-startup" : "context",
            phase: "error",
            label: readyAt === void 0 ? "准备本地 Python 环境" : "载入角色与历史记忆",
            durationMs: Date.now() - (readyAt ?? started),
            message: error.message
          });
        } catch {
        }
      }
      signal.removeEventListener("abort", abort);
      lines.close();
      child.stdin.end();
      stop();
      if (error) reject(error);
      else if (value) resolve(value);
      else reject(new Error("Python bridge completed without a result"));
    };
    const abort = () => finish(signal.reason instanceof Error ? signal.reason : new Error("Roleplay cancelled"));
    signal.addEventListener("abort", abort, { once: true });
    child.on("error", (error) => finish(new Error(`无法启动本地 Python：${error.message}。请安装 uv，或配置 pythonExecutable。`)));
    child.on("exit", (code) => {
      clearTimeout(killTimer);
      if (!settled) finish(new Error(`本地角色服务退出 (${code})：${stderr || "no diagnostic output"}`));
    });
    child.stdin.on("error", (error) => {
      if (!settled) finish(error);
    });
    lines.on("line", (line) => {
      if (settled) return;
      let event;
      try {
        event = WorkerEvent.parse(JSON.parse(line));
      } catch (error) {
        finish(new Error(`Invalid Python bridge protocol: ${errorMessage(error)}`));
        return;
      }
      if (event.type === "ready") {
        readyAt = Date.now();
        try {
          onProgress({ stage: "python-startup", phase: "end", label: "准备本地 Python 环境", durationMs: readyAt - started });
          onProgress({ stage: "context", phase: "start", label: "载入角色与历史记忆" });
        } catch (error) {
          finish(new Error(errorMessage(error)));
        }
      } else if (event.type === "result") finish(null, event.result);
      else if (event.type === "error") finish(new Error(event.message));
      else if (event.type === "progress") {
        try {
          onProgress(event.data);
        } catch (error) {
          finish(new Error(errorMessage(error)));
        }
      } else if (event.type === "model") {
        if (!prepared && readyAt !== void 0) {
          prepared = true;
          try {
            onProgress({ stage: "context", phase: "end", label: "载入角色与历史记忆", durationMs: Date.now() - readyAt });
          } catch (error) {
            finish(new Error(errorMessage(error)));
            return;
          }
        }
        Promise.resolve().then(() => onModel(event.call, (chunk) => write({ type: "chunk", id: event.id, chunk }))).then(
          (response) => write({ type: "completion", id: event.id, response }),
          (error) => write({ type: "model_error", id: event.id, message: errorMessage(error) })
        );
      }
    });
    if (signal.aborted) abort();
    else write({ ...input, scratchDirectory, charactersDirectory: config.charactersDirectory || join(root, "characters") });
  });
}
export {
  runWorker,
  workerCommand
};
