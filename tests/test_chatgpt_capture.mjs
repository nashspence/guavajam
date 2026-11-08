import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { captureCurrentConversation } from "../tools/chatgpt-capture/capture.mjs";

const ID = "12345678-1234-1234-1234-123456789abc";
const singular = "/backend-api/conversation/" + ID;
const plural = "/backend-api/conversations/" + ID;
const first = plural + "?include_has_versions=true&num_turns=100";
const older = plural + "/messages?include_has_versions=true&num_turns=100&before=cursor-one";
const native = () => ({
  conversation_id: ID, title: "Branching test", current_node: "a2",
  mapping: {
    root: { id: "root", parent: null, children: ["u"], message: null },
    u: { id: "u", parent: "root", children: ["a1", "a2"],
      message: { id: "u", author: { role: "user" }, content: { content_type: "text", parts: ["Question"] } } },
    a1: { id: "a1", parent: "u", children: [],
      message: { id: "a1", author: { role: "assistant" }, content: { content_type: "text", parts: ["Old answer"] } } },
    a2: { id: "a2", parent: "u", children: [],
      message: { id: "a2", author: { role: "assistant" }, content: { content_type: "text", parts: ["New answer"] } } }
  }
});
const page1 = () => ({
  conversation_id: ID, current_node: "a2",
  messages: [{ id: "a2", author: { role: "assistant" } }],
  page_info: { has_previous_page: true, start_cursor: "cursor-one", has_next_page: false }
});
const page2 = () => ({
  conversation_id: ID, current_node: "a2",
  messages: [{ id: "u", author: { role: "user" } },
             { id: "extra-tool", author: { role: "tool" } }],
  page_info: { has_previous_page: false, start_cursor: "cursor-two", has_next_page: true }
});
const routes = () => ({ [singular]: native(), [first]: page1(), [older]: page2() });
const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function capture(mockRoutes, options = {}) {
  const old = Object.fromEntries(["fetch", "location", "document"].map(key => [key, globalThis[key]]));
  const oldUrls = [URL.createObjectURL, URL.revokeObjectURL];
  const oldTimeout = globalThis.setTimeout;
  let blob = null;
  let filename = null;
  const calls = [];
  globalThis.location = {
    origin: options.origin || "https://chatgpt.com",
    pathname: options.pathname || "/c/" + ID
  };
  globalThis.fetch = async (url, init = {}) => {
    assert.equal(init.method || "GET", "GET", "the extension must be read-only");
    assert.ok(url === "/api/auth/session" || url.startsWith("/backend-api/conversation"),
      "the extension must not contact third-party origins");
    calls.push({ url, init });
    if (url === "/api/auth/session") return jsonResponse(options.session ?? {
      accessToken: "test-secret", account: { id: "account-1" }
    });
    const item = mockRoutes[url];
    if (typeof item === "function") return item(init);
    if (item instanceof Response) return item;
    return item === undefined ? jsonResponse({}, 404) : jsonResponse(item);
  };
  URL.createObjectURL = value => { blob = value; return "blob:test"; };
  URL.revokeObjectURL = () => {};
  globalThis.setTimeout = (callback, delay, ...args) => {
    const timer = oldTimeout(callback, delay, ...args);
    timer.unref?.();
    return timer;
  };
  globalThis.document = {
    body: { appendChild(element) { assert.equal(element.tagName, "A"); } },
    createElement(tag) {
      assert.equal(tag, "a");
      return { tagName: "A", style: {}, click() { filename = this.download; }, remove() {} };
    }
  };
  try {
    const result = await captureCurrentConversation();
    return { result, blob, filename, calls };
  } finally {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
    [URL.createObjectURL, URL.revokeObjectURL] = oldUrls;
    globalThis.setTimeout = oldTimeout;
  }
}

async function unzip(blob) {
  assert.ok(blob instanceof Blob);
  const buffer = Buffer.from(await blob.arrayBuffer());
  const files = new Map();
  let offset = 0;
  while (buffer.readUInt32LE(offset) === 0x04034b50) {
    assert.equal(buffer.readUInt16LE(offset + 8), 0);
    const size = buffer.readUInt32LE(offset + 18);
    const n = buffer.readUInt16LE(offset + 26);
    const e = buffer.readUInt16LE(offset + 28);
    const name = buffer.subarray(offset + 30, offset + 30 + n).toString("utf8");
    const begin = offset + 30 + n + e;
    files.set(name, buffer.subarray(begin, begin + size));
    offset = begin + size;
  }
  assert.equal(buffer.readUInt32LE(offset), 0x02014b50);
  return files;
}

test("preserves the complete native fork and separates missing selected-branch evidence", async () => {
  const x = await capture(routes());
  assert.equal(x.result.ok, true);
  assert.equal(x.result.nodes, 4);
  assert.equal(x.result.branchPoints, 1);
  assert.equal(x.result.missingFromGraph, 1);
  assert.equal(x.filename, "chatgpt-capture-" + ID + ".zip");
  if (process.env.CAPTURE_FIXTURE_PATH) {
    fs.writeFileSync(process.env.CAPTURE_FIXTURE_PATH, Buffer.from(await x.blob.arrayBuffer()));
  }
  const files = await unzip(x.blob);
  assert.deepEqual([...files.keys()], ["conversations.json", "selected-branch-pages.json", "capture-manifest.json"]);
  assert.deepEqual(JSON.parse(files.get("conversations.json")), [native()]);
  assert.deepEqual(JSON.parse(files.get("selected-branch-pages.json")), [page1(), page2()]);
  const report = JSON.parse(files.get("capture-manifest.json"));
  assert.equal(report.native_graph.branch_points, 1);
  assert.equal(report.selected_branch_audit.pagination_reached_oldest_page, true);
  assert.equal(report.selected_branch_audit.selected_branch_messages_absent_from_native_mapping, 1);
  assert.match(report.native_graph.sha256_conversations_json, /^[0-9a-f]{64}$/);
  assert.ok(x.calls.every(c => c.init.method === "GET"));
});
test("refuses a synthetic mapping fallback", async () => {
  const r = routes();
  r[singular] = { conversation_id: ID, messages: [] };
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /Native mapping unavailable/);
  assert.equal(x.blob, null);
});
test("refuses dangling ancestry", async () => {
  const r = routes(); r[singular].mapping.a1.parent = "unknown";
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /dangling parent|inconsistent child/);
  assert.equal(x.blob, null);
});
test("refuses cycles and multiple roots", async () => {
  const r = routes(); r[singular].mapping.root.parent = "a1";
  r[singular].mapping.a1.children = ["root"];
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /root|cycle/);
});
test("refuses inconsistent branch identity across endpoints", async () => {
  const r = routes(); r[first].current_node = "different";
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /Current branch changed/);
  assert.equal(x.blob, null);
});
test("retains a valid native graph but discloses an unavailable optional audit", async () => {
  const r = routes(); delete r[first];
  const x = await capture(r);
  assert.equal(x.result.ok, true);
  assert.equal(x.result.audit, "unavailable");
  const files = await unzip(x.blob);
  assert.deepEqual([...files.keys()], ["conversations.json", "capture-manifest.json"]);
  assert.equal(JSON.parse(files.get("capture-manifest.json")).selected_branch_audit.status, "unavailable");
});
test("rejects non-conversation routes without network access", async () => {
  const x = await capture(routes(), { pathname: "/share/abcdefghijklmnop" });
  assert.equal(x.result.ok, false);
  assert.equal(x.calls.length, 0);
});
test("rejects other origins", async () => {
  const x = await capture(routes(), { origin: "https://example.org" });
  assert.equal(x.result.ok, false);
  assert.equal(x.calls.length, 0);
});
test("allows cookie authentication when no bearer is exposed", async () => {
  const x = await capture(routes(), { session: {} });
  assert.equal(x.result.ok, true);
  assert.ok(x.calls.every(c => !c.init.headers?.Authorization));
});
test("retries account header rejection without widening request scope", async () => {
  const r = routes();
  r[singular] = init => init.headers["ChatGPT-Account-ID"] ? jsonResponse({}, 403) : jsonResponse(native());
  const x = await capture(r);
  assert.equal(x.result.ok, true);
  assert.equal(x.calls.filter(c => c.url === singular).length, 2);
});
test("accepts documented alternate pagination route", async () => {
  const r = routes(); delete r[older];
  r[plural + "?include_has_versions=true&num_turns=100&before=cursor-one"] = page2();
  const x = await capture(r);
  assert.equal(x.result.ok, true);
  assert.equal(x.result.missingFromGraph, 1);
});
test("does not treat stalled pagination as a successful audit", async () => {
  const r = routes(); r[older] = page1();
  const x = await capture(r);
  assert.equal(x.result.ok, true);
  assert.equal(x.result.audit, "unavailable");
});
test("does not download when native auth fails", async () => {
  const r = routes(); r[singular] = jsonResponse({ message: "not authorized" }, 401);
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /401/);
  assert.equal(x.blob, null);
});
test("refuses different conversation ID in selected branch", async () => {
  const r = routes(); r[first].conversation_id = "other-id";
  const x = await capture(r);
  assert.equal(x.result.ok, false);
  assert.match(x.result.error, /different conversation/);
  assert.equal(x.blob, null);
});
test("accepts nested project /c/id routes only at the end", async () => {
  const good = await capture(routes(), { pathname: "/g/g-123/c/" + ID });
  assert.equal(good.result.ok, true);
  const bad = await capture(routes(), { pathname: "/g/g-123/c/" + ID + "/something" });
  assert.equal(bad.result.ok, false);
});
test("manifest has temporary permissions only", async () => {
  const manifest = JSON.parse(fs.readFileSync(new URL("../tools/chatgpt-capture/manifest.json", import.meta.url)));
  assert.deepEqual([...manifest.permissions].sort(), ["activeTab", "scripting"]);
  for (const key of ["host_permissions", "optional_host_permissions", "background", "content_scripts", "externally_connectable"]) {
    assert.equal(Object.hasOwn(manifest, key), false, key);
  }
});
