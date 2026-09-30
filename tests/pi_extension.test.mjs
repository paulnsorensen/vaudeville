import assert from "node:assert/strict";
import { once } from "node:events";
import { copyFile, mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { pathToFileURL } from "node:url";
import test from "node:test";
import extension from "../pi/extensions/vaudeville.ts";

const exec = promisify(execFile);

async function host(t, verdict = { action: "allow" }, load = extension) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "vd-pi-"));
  const socketPath = path.join(directory, "daemon.sock");
  const previous = process.env.VAUDEVILLE_SOCKET;
  process.env.VAUDEVILLE_SOCKET = socketPath;
  const requests = [], messages = [], notifications = [], handlers = new Map();
  let connections = 0;
  const sockets = new Set();
  const server = net.createServer(socket => {
    connections++;
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    let buffer = "";
    socket.on("data", chunk => {
      buffer += chunk;
      if (!buffer.includes("\n")) return;
      requests.push(JSON.parse(buffer.split("\n")[0]));
      socket.end(JSON.stringify({ stdout: JSON.stringify(verdict) }) + "\n");
    });
  });
  server.listen(socketPath);
  await once(server, "listening");
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    if (previous === undefined) delete process.env.VAUDEVILLE_SOCKET;
    else process.env.VAUDEVILLE_SOCKET = previous;
    await rm(directory, { recursive: true, force: true });
  });
  load({
    on: (name, handler) => handlers.set(name, handler),
    sendMessage: (message, options) => messages.push({ message, options }),
    exec: async (command, args, options) => {
      const result = await exec(command, args, options);
      return { ...result, code: 0, killed: false };
    },
  });
  const emit = (name, event = {}) => handlers.get(name)(
    { type: name, ...event },
    { cwd: directory, ui: { notify: (...args) => notifications.push(args) } },
  );
  return { emit, requests, messages, notifications, directory, connections: () => connections };
}

for (const kind of ["circular", "bigint"]) {
  test(`unserializable ${kind} result fails open before connecting`, async t => {
    const h = await host(t);
    const details = kind === "bigint" ? 1n : {};
    if (kind === "circular") details.self = details;
    assert.equal(await h.emit("tool_result", { details }), undefined);
    assert.deepEqual(h.requests, []);
    assert.equal(h.connections(), 0);
    await h.emit("tool_call", { input: { command: "ok" } });
    assert.equal(h.requests[0].event, "tool_call");
  });
}

test("startup executes the real script beneath encoded installation paths", async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "vd Jane é'"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(path.join(directory, "pi/extensions"), { recursive: true });
  await mkdir(path.join(directory, "hooks"));
  await writeFile(path.join(directory, "package.json"), '{"type":"module"}');
  await copyFile(new URL("../pi/extensions/vaudeville.ts", import.meta.url), path.join(directory, "pi/extensions/vaudeville.ts"));
  await writeFile(path.join(directory, "hooks/session-start.sh"), 'printf "%s" "$CLAUDE_PLUGIN_ROOT" > "$CLAUDE_PLUGIN_ROOT/started"');
  const { default: installed } = await import(pathToFileURL(path.join(directory, "pi/extensions/vaudeville.ts")).href);
  const h = await host(t, undefined, installed);
  await h.emit("session_start");
  const { readFile } = await import("node:fs/promises");
  assert.equal(await readFile(path.join(directory, "started"), "utf8"), await realpath(directory));
});

for (const channel of ["tool_call", "tool_result", "input", "agent_before_settle", "agent_end"]) {
  for (const action of ["allow", "warn", "context", "block", "rewrite", "continue"]) {
    test(`${channel} preserves secondary context beside ${action}`, async t => {
      const h = await host(t, { action, message: "primary", reason: "denied", context: "secondary", input: { command: "safe" } });
      const event = { input: { command: "unsafe" }, outcome: "completed", willContinue: undefined };
      const result = await h.emit(channel, event);
      assert.deepEqual(h.messages.filter(x => !x.options.triggerTurn), [{
        message: { customType: "vaudeville", content: action === "context" ? "primary\n\nsecondary" : "secondary", display: false },
        options: { deliverAs: "nextTurn", triggerTurn: false },
      }]);
      if (action === "warn") assert.deepEqual(h.notifications, [["primary", "warning"]]);
      if (channel === "tool_call" && action === "block") assert.deepEqual(result, { block: true, reason: "denied" });
      if (channel === "tool_call" && action === "rewrite") {
        assert.deepEqual(result, { input: { command: "safe" } });
        assert.deepEqual(event.input, { command: "safe" });
      }
      if (channel === "input" && action === "block") assert.deepEqual(result, { action: "handled", handled: true });
    });
  }
}

test("Pi end before settlement evaluates Stop once", async t => {
  const h = await host(t);
  await h.emit("turn_end", { message: { role: "assistant", content: "done" } });
  await h.emit("agent_end", { messages: [] });
  await h.emit("agent_before_settle", { outcome: "completed" });
  assert.deepEqual(h.requests.map(x => x.event), ["agent_before_settle"]);
  assert.deepEqual(h.messages, []);
});

for (const willContinue of [undefined, false, true]) {
  test(`OMP own willContinue=${willContinue} selects terminal Stop`, async t => {
    const h = await host(t, { action: "continue", message: "keep going" });
    await h.emit("turn_end", { message: { role: "assistant", content: "stale" } });
    const message = { role: "assistant", content: "current", stopReason: "stop" };
    await h.emit("agent_end", { willContinue, messages: [message] });
    assert.deepEqual(h.requests.map(x => x.payload.message), willContinue ? [] : [message]);
    assert.deepEqual(h.messages, willContinue ? [] : [{
      message: { customType: "vaudeville", content: "keep going", display: true },
      options: { deliverAs: "followUp", triggerTurn: true },
    }]);
  });
}

for (const stopReason of ["aborted", "error"]) {
  test(`OMP ${stopReason} never resumes`, async t => {
    const h = await host(t, { action: "continue" });
    await h.emit("agent_end", { willContinue: undefined, messages: [{ role: "assistant", stopReason }] });
    assert.deepEqual(h.requests, []);
    assert.deepEqual(h.messages, []);
  });
}

for (const outcome of ["aborted", "error"]) {
  test(`Pi ${outcome} never resumes`, async t => {
    const h = await host(t, { action: "continue" });
    await h.emit("agent_end");
    assert.equal(await h.emit("agent_before_settle", { outcome }), undefined);
    assert.deepEqual(h.requests, []);
    assert.deepEqual(h.messages, []);
  });
}

for (const channel of ["agent_before_settle", "agent_end"]) {
  test(`${channel} caps continuation and resets only on user input`, async t => {
    const h = await host(t, { action: "continue", message: "keep going" });
    const event = { outcome: "completed", willContinue: undefined };
    const results = [];
    for (let i = 0; i < 4; i++) results.push(await h.emit(channel, event));
    assert.equal(h.requests.length, 3);
    if (channel === "agent_before_settle") {
      assert.deepEqual(results.slice(0, 3), Array(3).fill({
        entries: [{ type: "custom_message", customType: "vaudeville", content: "keep going", display: true }],
        continue: true,
      }));
      assert.equal(results[3], undefined);
    } else assert.equal(h.messages.length, 3);
    await h.emit("input", { source: "extension" });
    await h.emit(channel, event);
    assert.equal(h.requests.filter(x => x.event === channel).length, 3);
    await h.emit("input", { source: "user" });
    await h.emit(channel, event);
    assert.equal(h.requests.filter(x => x.event === channel).length, 4);
  });
}
