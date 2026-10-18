// 查询 Claude 订阅额度（5 小时 / 7 天窗口），以一行 JSON 打印到标准输出，供 quota_daemon.py 调用。
//
// 用 Claude Agent SDK 的用量接口：不发提示词、不调用模型、不写会话记录、不加载用户设置。
// 需要先用 SDK 自带的 claude 登录一次：
//   ./node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude auth login --claudeai
//
// 输出：{"ok": true, "subscription": "max", "windows": {"5h": {"used": 28, "resets_at": 秒}, "7d": {...}}}
//      {"ok": false, "reason": "..."}
import { query } from "@anthropic-ai/claude-agent-sdk";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WINDOWS = [["five_hour", "5h"], ["seven_day", "7d"]];
const toEpoch = (iso) => (iso ? Date.parse(iso) / 1000 : null);

async function main() {
  const cwd = join(tmpdir(), "rog-oled-usage");
  mkdirSync(cwd, { recursive: true });

  let release;
  const done = new Promise((resolve) => (release = resolve));
  async function* noPrompt() {
    await done; // 流式输入模式，但从不发送提示词
  }

  // env 会整体替换子进程环境：复制当前环境，但去掉会抢在订阅登录之前生效的认证变量
  const DROP = new Set(["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]);
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !DROP.has(k)));

  const q = query({
    prompt: noPrompt(),
    options: {
      cwd,
      env,
      settingSources: [], // 不加载用户设置（钩子、状态栏、插件）
      strictMcpConfig: true, // 不加载 MCP 服务器和 claude.ai 连接器
      persistSession: false, // 不写会话记录
    },
  });
  try {
    // 实验性接口（SDK 0.3.302），名字以后可能会变
    const u = await q.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET({ skipBehaviors: true });
    if (!u.rate_limits_available || !u.rate_limits) {
      return { ok: false, reason: "rate limits unavailable", subscription: u.subscription_type };
    }
    const windows = {};
    for (const [src, key] of WINDOWS) {
      const w = u.rate_limits[src];
      if (w && w.utilization != null) windows[key] = { used: w.utilization, resets_at: toEpoch(w.resets_at) };
    }
    return { ok: true, subscription: u.subscription_type, windows };
  } finally {
    release();
    q.close();
  }
}

main()
  .then((r) => console.log(JSON.stringify(r)))
  .catch((e) => {
    console.log(JSON.stringify({ ok: false, reason: String(e?.message ?? e) }));
    process.exitCode = 1;
  });
