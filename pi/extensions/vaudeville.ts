/**
 * Vaudeville extension for Pi and oh-my-pi.
 *
 * Forwards hook events to the vaudeville daemon over its Unix socket
 * (newline-delimited JSON, same framing as `vaudeville/core/client.py`) and
 * applies the daemon's verdict through whichever native return shape the
 * current event supports. Loads under both Pi (`@earendil-works/pi`) and
 * oh-my-pi (`can1357/oh-my-pi`) because it never imports either package by
 * name; only local structural types are used.
 *
 * Fails open everywhere: any socket error, timeout, or malformed response
 * resolves to "allow" (or "continue" for the input channel, which has no bare
 * no-op return). A `tool_call` handler that throws blocks the
 * tool in both Pi and oh-my-pi (fail-closed by design in the host runtime),
 * so every handler body below is wrapped in try/catch and never rethrows.
 */

import * as net from "node:net";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// ---- Local structural types (no hard import of a harness package) --------

interface ExecResult {
  stdout: string;
  stderr: string;
  code: number;
  killed: boolean;
}

interface ExecOptions {
  timeout?: number;
  cwd?: string;
}

interface ExtensionAPI {
  on(event: string, handler: (event: any, ctx: any) => unknown): void;
  exec(command: string, args: string[], options?: ExecOptions): Promise<ExecResult>;
  sendMessage(
    message: { customType: string; content: string; display: boolean },
    options?: { deliverAs?: string; triggerTurn?: boolean },
  ): unknown;
}

// The verdict `PiAdapter.render()` (vaudeville/server/harness/pi.py) writes
// to stdout. `action` is channel-agnostic; this shim picks the native return
// shape for the event that received it.
interface Verdict {
  action: "allow" | "block" | "rewrite" | "continue" | "context" | "warn";
  reason?: string;
  input?: Record<string, unknown>;
  message?: string;
  context?: string;
}

const VERDICT_ACTIONS: ReadonlySet<string> = new Set([
  "allow",
  "block",
  "rewrite",
  "continue",
  "context",
  "warn",
]);

function isVerdict(value: unknown): value is Verdict {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { action?: unknown }).action === "string" &&
    VERDICT_ACTIONS.has((value as { action: string }).action)
  );
}

// ---- Socket transport (matches vaudeville/core/client.py framing) --------

// Matches READ_TIMEOUT in vaudeville/core/client.py (model p95 ~2.3s).
const DEFAULT_TIMEOUT_MS = 8000;
const SESSION_START_TIMEOUT_MS = 5000;

function socketPath(): string {
  const explicit = process.env.VAUDEVILLE_SOCKET;
  if (explicit) {
    return explicit;
  }
  const uid = typeof process.getuid === "function" ? process.getuid() : 0;
  const runtimeDir = process.env.VAUDEVILLE_RUNTIME_DIR || `/tmp/vaudeville-${uid}`;
  return path.join(runtimeDir, "vaudeville.sock");
}

function sendHook(
  nativeEvent: string,
  cwd: string,
  payload: Record<string, unknown>,
): Promise<Verdict | null> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: Verdict | null): void => {
      if (settled) {
        return;
      }
      settled = true;
      resolve(value);
    };

    let request: string;
    let socket: net.Socket;
    try {
      request = JSON.stringify({
        op: "hook",
        harness: "pi",
        event: nativeEvent,
        cwd,
        payload: { ...payload, cwd },
      }) + "\n";
      socket = net.createConnection({ path: socketPath() });
    } catch {
      finish(null);
      return;
    }

    const timer = setTimeout(() => {
      socket.destroy();
      finish(null);
    }, DEFAULT_TIMEOUT_MS);

    let buffer = "";
    socket.on("connect", () => {
      socket.write(request);
    });
    socket.on("data", (chunk: Buffer) => {
      buffer += chunk.toString("utf8");
      const newline = buffer.indexOf("\n");
      if (newline === -1) {
        return;
      }
      clearTimeout(timer);
      socket.destroy();
      try {
        const envelope = JSON.parse(buffer.slice(0, newline)) as { stdout?: unknown };
        const stdout = typeof envelope.stdout === "string" ? envelope.stdout : "";
        if (!stdout) {
          finish(null);
          return;
        }
        const verdict = JSON.parse(stdout);
        finish(isVerdict(verdict) ? verdict : null);
      } catch {
        finish(null);
      }
    });
    socket.on("error", () => {
      clearTimeout(timer);
      finish(null);
    });
    socket.on("close", () => {
      clearTimeout(timer);
      finish(null);
    });
  });
}

// ---- Handlers --------------------------------------------------------------

function deliverContext(pi: ExtensionAPI, verdict: Verdict): void {
  const content = [
    verdict.action === "context" ? verdict.message : undefined,
    verdict.context,
  ].filter(Boolean).join("\n\n");
  if (content) {
    pi.sendMessage(
      { customType: CUSTOM_TYPE, content, display: false },
      { deliverAs: "nextTurn", triggerTurn: false },
    );
  }
}

async function handleToolCall(pi: ExtensionAPI, event: any, ctx: any): Promise<unknown> {
  try {
    const verdict = await sendHook("tool_call", ctx?.cwd ?? "", event);
    if (!verdict) {
      return;
    }
    deliverContext(pi, verdict);
    if (verdict.action === "block") {
      return { block: true, reason: verdict.reason ?? "blocked by vaudeville" };
    }
    if (verdict.action === "rewrite" && verdict.input) {
      // Pi mutates `event.input` in place; oh-my-pi also honors a returned
      // `input` field. Doing both keeps one code path for either host.
      if (event && typeof event.input === "object" && event.input !== null) {
        Object.assign(event.input, verdict.input);
      }
      return { input: verdict.input };
    }
    if (verdict.action === "warn") {
      ctx?.ui?.notify?.(verdict.message ?? "", "warning");
    }
    return;
  } catch {
    return;
  }
}

async function handleToolResult(pi: ExtensionAPI, event: any, ctx: any): Promise<unknown> {
  try {
    // PostToolUse has no block/rewrite channel here; only surface warn/context.
    const verdict = await sendHook("tool_result", ctx?.cwd ?? "", event);
    if (!verdict) {
      return;
    }
    deliverContext(pi, verdict);
    if (verdict.action === "warn") {
      ctx?.ui?.notify?.(verdict.message ?? "", "warning");
    }
    return;
  } catch {
    return;
  }
}

async function handleInput(pi: ExtensionAPI, event: any, ctx: any): Promise<unknown> {
  try {
    const verdict = await sendHook("input", ctx?.cwd ?? "", event);
    if (!verdict) {
      return { action: "continue" };
    }
    deliverContext(pi, verdict);
    if (verdict.action === "block") {
      ctx?.ui?.notify?.(verdict.reason ?? "blocked by vaudeville", "warning");
      // Pi reads `action`; oh-my-pi reads `handled` / `text`.
      return { action: "handled", handled: true };
    }
    if (verdict.action === "rewrite" && typeof verdict.input?.text === "string") {
      return { action: "transform", text: verdict.input.text };
    }
    if (verdict.action === "warn") {
      ctx?.ui?.notify?.(verdict.message ?? "", "warning");
    }
    return { action: "continue" };
  } catch {
    return { action: "continue" };
  }
}

// ---- Stop (end of an agent run) -------------------------------------------
//
// Pi emits agent_end before agent_before_settle; only settlement evaluates Stop.
// OMP has no settlement event. Its producer always sets an own willContinue
// property, including undefined on completion. Pi's producer omits this property.

// Cap on forced continuations in a row, reset by user input. Stops a Stop
// rule that never passes from looping the agent forever.
const MAX_CONSECUTIVE_CONTINUES = 3;

const CUSTOM_TYPE = "vaudeville";

interface StopState {
  lastMessage: unknown;
  continues: number;
}

function newStopState(): StopState {
  return { lastMessage: undefined, continues: 0 };
}

async function stopVerdict(
  pi: ExtensionAPI,
  state: StopState,
  nativeEvent: string,
  ctx: any,
): Promise<string | null> {
  if (state.continues >= MAX_CONSECUTIVE_CONTINUES) {
    return null;
  }
  const verdict = await sendHook(nativeEvent, ctx?.cwd ?? "", {
    type: nativeEvent,
    message: state.lastMessage,
  });
  if (!verdict) {
    return null;
  }
  deliverContext(pi, verdict);
  if (verdict.action === "continue") {
    const message = verdict.message || verdict.reason || "Continue: a vaudeville rule did not pass.";
    state.continues += 1;
    return message;
  }
  if (verdict.action === "warn") {
    ctx?.ui?.notify?.(verdict.message ?? "", "warning");
  }
  return null;
}

function handleTurnEnd(state: StopState) {
  return (event: any): void => {
    try {
      if (event?.message !== undefined) {
        state.lastMessage = event.message;
      }
    } catch {
      // Fail open.
    }
  };
}

function handleBeforeSettle(pi: ExtensionAPI, state: StopState) {
  return async (event: any, ctx: any): Promise<unknown> => {
    try {
      if (event?.outcome !== undefined && event.outcome !== "completed") {
        return;
      }
      const message = await stopVerdict(pi, state, "agent_before_settle", ctx);
      if (message === null) {
        return;
      }
      return {
        entries: [{ type: "custom_message", customType: CUSTOM_TYPE, content: message, display: true }],
        continue: true,
      };
    } catch {
      return;
    }
  };
}

function handleAgentEnd(pi: ExtensionAPI, state: StopState) {
  return async (event: any, ctx: any): Promise<void> => {
    try {
      if (!Object.hasOwn(event, "willContinue") || event.willContinue) {
        return;
      }
      const messageEvent = event.messages?.findLast((message: any) => message.role === "assistant");
      if (messageEvent?.stopReason === "aborted" || messageEvent?.stopReason === "error") {
        return;
      }
      state.lastMessage = messageEvent;
      const message = await stopVerdict(pi, state, "agent_end", ctx);
      if (message === null) {
        return;
      }
      pi.sendMessage(
        { customType: CUSTOM_TYPE, content: message, display: true },
        { deliverAs: "followUp", triggerTurn: true },
      );
    } catch {
      // Fail open.
    }
  };
}

function resetOnInput(state: StopState, handler: (event: any, ctx: any) => Promise<unknown>) {
  return (event: any, ctx: any): Promise<unknown> => {
    if (event?.source !== "extension") {
      state.continues = 0;
    }
    return handler(event, ctx);
  };
}

function shQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function packageRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
}

function handleSessionStart(pi: ExtensionAPI) {
  return async (_event: any, ctx: any): Promise<void> => {
    try {
      const root = packageRoot();
      const script = path.join(root, "hooks", "session-start.sh");
      // `pi.exec`'s ExecOptions has no `env` field (confirmed against
      // `packages/coding-agent/src/core/exec.ts`), so the plugin root is set
      // through an inline shell assignment instead of an exec option.
      const command = `CLAUDE_PLUGIN_ROOT=${shQuote(root)} bash ${shQuote(script)}`;
      await pi.exec("bash", ["-c", command], {
        cwd: ctx?.cwd,
        timeout: SESSION_START_TIMEOUT_MS,
      });
    } catch {
      // Fail open: an unstarted daemon just means every later hook allows.
    }
  };
}

export default function vaudeville(pi: ExtensionAPI): void {
  pi.on("session_start", handleSessionStart(pi));
  pi.on("tool_call", (event, ctx) => handleToolCall(pi, event, ctx));
  pi.on("tool_result", (event, ctx) => handleToolResult(pi, event, ctx));
  const stop = newStopState();
  pi.on("input", resetOnInput(stop, (event, ctx) => handleInput(pi, event, ctx)));
  pi.on("turn_end", handleTurnEnd(stop));
  pi.on("agent_before_settle", handleBeforeSettle(pi, stop));
  pi.on("agent_end", handleAgentEnd(pi, stop));
}
