/* Press ChatGPT Capture reference implementation.
 * Intentionally self-contained: Chrome serializes this function for injection
 * into the active tab's ISOLATED world. No network calls leave chatgpt.com.
 * There is NO DOM/flattening fallback for the native branching graph.
 */
export async function captureCurrentConversation() {
  const MAX_JSON_CHARACTERS = 64 * 1024 * 1024;
  const MAX_ZIP_BYTES = 128 * 1024 * 1024;
  const MAX_PAGES = 500;
  const CURRENT_ORIGIN = "https://chatgpt.com";

  function fail(message) { throw new Error(message); }
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const idOf = (value) => {
    if (typeof value === "string") return value;
    if (isObject(value) && typeof value.id === "string") return value.id;
    return null;
  };
  function conversationId() {
    if (location.origin !== CURRENT_ORIGIN) fail("Capture requires a chatgpt.com tab.");
    const segments = location.pathname.split("/").filter(Boolean);
    // Allows /c/{id}, and project/GPT routes ending in /c/{id}.
    const index = segments.lastIndexOf("c");
    if (index < 0 || index !== segments.length - 2) {
      fail("Open a specific conversation (/c/{id}) before capturing.");
    }
    const id = segments[index + 1];
    if (!/^[a-zA-Z0-9_-]{16,128}$/.test(id)) fail("Unexpected conversation ID in URL.");
    return id;
  }

  function validateNative(conversation, expectedId) {
    if (!isObject(conversation) || !isObject(conversation.mapping)) {
      fail("Native mapping unavailable. Refusing to invent a conversation graph.");
    }
    const responseId = conversation.conversation_id ?? conversation.id;
    if (responseId !== expectedId) fail("Conversation ID does not match the open tab.");
    const mapping = conversation.mapping;
    const ids = Object.keys(mapping);
    if (!ids.length) fail("Native mapping is empty.");
    if (typeof conversation.current_node !== "string" || !own(mapping, conversation.current_node)) {
      fail("Native mapping has no valid current_node.");
    }
    const childCounts = new Map(ids.map((id) => [id, 0]));
    let roots = 0;
    for (const id of ids) {
      const node = mapping[id];
      if (!isObject(node) || !own(node, "parent") || !own(node, "message") ||
          (node.message !== null && !isObject(node.message))) {
        fail("Native mapping has an invalid node.");
      }
      if (node.parent === null) {
        roots++;
      } else if (typeof node.parent === "string" && own(mapping, node.parent)) {
        childCounts.set(node.parent, childCounts.get(node.parent) + 1);
      } else {
        fail("Native mapping contains a dangling parent reference.");
      }
      if (own(node, "children")) {
        if (!Array.isArray(node.children) || new Set(node.children).size !== node.children.length) {
          fail("Native mapping contains invalid child references.");
        }
        for (const child of node.children) {
          if (typeof child !== "string" || !own(mapping, child) || mapping[child]?.parent !== id) {
            fail("Native mapping contains an inconsistent child reference.");
          }
        }
      }
    }
    if (roots !== 1) fail("Native mapping does not have exactly one root.");
    // Linear-time ancestor traversal with memoization; reject cycles and disconnected roots.
    const completed = new Set();
    for (const start of ids) {
      let cursor = start;
      const inPath = new Set();
      while (cursor !== null && !completed.has(cursor)) {
        if (inPath.has(cursor)) fail("Native mapping contains a parent cycle.");
        inPath.add(cursor);
        cursor = mapping[cursor].parent;
      }
      for (const id of inPath) completed.add(id);
    }
    for (const [id, node] of Object.entries(mapping)) {
      if (node.parent !== null && Array.isArray(mapping[node.parent]?.children) &&
          !mapping[node.parent].children.includes(id)) {
        fail("Native mapping parent/child links disagree.");
      }
    }
    let chainNodes = 0;
    for (let cursor = conversation.current_node; cursor !== null; cursor = mapping[cursor].parent) {
      chainNodes++;
    }
    const messageIds = new Set();
    for (const node of Object.values(mapping)) {
      if (node.message === null) continue;
      const id = idOf(node.message?.id) || idOf(node.id);
      if (id) messageIds.add(id);
    }
    return {
      nodes: ids.length,
      branchPoints: [...childCounts.values()].filter((n) => n > 1).length,
      leaves: [...childCounts.values()].filter((n) => n === 0).length,
      currentChainNodes: chainNodes,
      messageIds
    };
  }

  async function apiRequest(path, auth, accountId) {
    const target = new URL(path, location.origin);
    if (target.origin !== CURRENT_ORIGIN || !target.pathname.startsWith("/backend-api/conversation")) {
      fail("Unexpected API request blocked.");
    }
    async function attempt(includeAccount) {
      const headers = { Accept: "application/json" };
      if (auth) headers.Authorization = `Bearer ${auth}`;
      if (includeAccount && accountId) headers["ChatGPT-Account-ID"] = accountId;
      const response = await fetch(target.pathname + target.search, {
        method: "GET", credentials: "include", cache: "no-store",
        redirect: "error", headers, signal: AbortSignal.timeout(30000)
      });
      return response;
    }
    let response = await attempt(true);
    if (accountId && (response.status === 403 || response.status === 404)) {
      response = await attempt(false);
    }
    if (!response.ok) {
      const error = new Error(`ChatGPT API returned HTTP ${response.status}.`);
      error.status = response.status;
      throw error;
    }
    const length = Number(response.headers?.get("content-length"));
    if (length > MAX_JSON_CHARACTERS) fail("Conversation JSON is above the size limit.");
    const raw = await response.text();
    if (raw.length > MAX_JSON_CHARACTERS) fail("Conversation JSON is above the size limit.");
    try { return JSON.parse(raw); }
    catch { fail("ChatGPT returned non-JSON content."); }
  }

  async function readSession() {
    try {
      const response = await fetch("/api/auth/session", {
        method: "GET", credentials: "include", cache: "no-store",
        redirect: "error", signal: AbortSignal.timeout(15000)
      });
      if (!response.ok) return { token: null, accountId: null };
      const session = await response.json();
      return {
        token: typeof session?.accessToken === "string" ? session.accessToken : null,
        accountId: session?.account?.id || session?.activeAccount?.id || null
      };
    } catch {
      // Cookies may authorize the ChatGPT endpoint even when this endpoint
      // does not expose a bearer token. Never request or persist credentials.
      return { token: null, accountId: null };
    }
  }

  async function readSelectedBranch(id, auth, accountId, native, graph) {
    const prefix = `/backend-api/conversations/${encodeURIComponent(id)}`;
    const query = "include_has_versions=true&num_turns=100";
    const pages = [];
    const seenCursors = new Set();
    const seenIds = new Set();
    let before = null;
    let missing = 0;
    let uncomparable = 0;
    for (let pageNumber = 0; pageNumber < MAX_PAGES; pageNumber++) {
      let page;
      if (!before) {
        page = await apiRequest(`${prefix}?${query}`, auth, accountId);
      } else {
        // ChatGPT has used both /messages?before and ?before variants.
        try {
          page = await apiRequest(`${prefix}/messages?${query}&before=${encodeURIComponent(before)}`, auth, accountId);
        } catch (error) {
          if (![400, 404, 405].includes(error.status)) throw error;
          page = await apiRequest(`${prefix}?${query}&before=${encodeURIComponent(before)}`, auth, accountId);
        }
      }
      if (!isObject(page) || !Array.isArray(page.messages) || !isObject(page.page_info)) {
        fail("Selected-branch endpoint did not return a paginated messages array.");
      }
      if (page.conversation_id != null && page.conversation_id !== id) {
        const error = new Error("Selected-branch response references a different conversation. Retry.");
        error.fatal = true;
        throw error;
      }
      if (page.current_node != null && page.current_node !== native.current_node) {
        const error = new Error("Current branch changed during capture. Retry after the conversation stops updating.");
        error.fatal = true;
        throw error;
      }
      if (typeof page.page_info.has_previous_page !== "boolean") {
        fail("Selected-branch response omits a reliable pagination flag.");
      }
      let newIds = 0;
      for (const message of page.messages) {
        if (!isObject(message)) fail("Selected-branch page contains a malformed message.");
        const messageId = idOf(message.id);
        if (!messageId) {
          uncomparable++;
          continue;
        }
        if (!seenIds.has(messageId)) {
          seenIds.add(messageId);
          newIds++;
          if (!graph.messageIds.has(messageId)) missing++;
        }
      }
      if (pageNumber > 0 && newIds === 0) {
        fail("Selected-branch pagination repeated messages without progress.");
      }
      pages.push(page); // Unmodified page objects, newest page first.
      if (!page.page_info.has_previous_page) {
        return { pages, uniqueMessages: seenIds.size, missing, uncomparable };
      }
      const cursor = page.page_info.start_cursor;
      if (typeof cursor !== "string" || !cursor || seenCursors.has(cursor) ||
          (pageNumber > 0 && newIds === 0)) {
        fail("Selected-branch pagination stalled or repeated a cursor.");
      }
      seenCursors.add(cursor);
      before = cursor;
    }
    fail("Selected-branch pagination exceeded its safe page limit.");
  }

  const encoder = new TextEncoder();
  function jsonBytes(value) { return encoder.encode(`${JSON.stringify(value, null, 2)}\n`); }
  async function sha256(data) {
    const digest = await crypto.subtle.digest("SHA-256", data);
    return [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, "0")).join("");
  }
  function buildZip(files) {
    // ZIP STORE (no compression), three fixed names; no libraries, ZIP64 or permissions.
    const crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let j = 0; j < 8; j++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      crcTable[n] = c >>> 0;
    }
    function crc32(bytes) {
      let c = 0xffffffff;
      for (const b of bytes) c = crcTable[(c ^ b) & 255] ^ (c >>> 8);
      return (c ^ 0xffffffff) >>> 0;
    }
    const chunks = [], directory = [];
    let offset = 0, directorySize = 0;
    for (const [name, contents] of files) {
      const filename = encoder.encode(name);
      const size = contents.byteLength;
      const crc = crc32(contents);
      const local = new Uint8Array(30 + filename.byteLength);
      const h = new DataView(local.buffer);
      h.setUint32(0, 0x04034b50, true);
      h.setUint16(4, 20, true);
      h.setUint16(6, 0x0800, true);
      h.setUint16(8, 0, true);
      h.setUint16(12, 33, true); // DOS date 1980-01-01; deterministic metadata.
      h.setUint32(14, crc, true);
      h.setUint32(18, size, true);
      h.setUint32(22, size, true);
      h.setUint16(26, filename.byteLength, true);
      local.set(filename, 30);
      chunks.push(local, contents);

      const central = new Uint8Array(46 + filename.byteLength);
      const d = new DataView(central.buffer);
      d.setUint32(0, 0x02014b50, true);
      d.setUint16(4, 20, true);
      d.setUint16(6, 20, true);
      d.setUint16(8, 0x0800, true);
      d.setUint16(14, 33, true);
      d.setUint32(16, crc, true);
      d.setUint32(20, size, true);
      d.setUint32(24, size, true);
      d.setUint16(28, filename.byteLength, true);
      d.setUint32(42, offset, true);
      central.set(filename, 46);
      directory.push(central);
      directorySize += central.byteLength;
      offset += local.byteLength + size;
      if (offset + directorySize + 22 > MAX_ZIP_BYTES) fail("Capture exceeds the 128 MiB ZIP limit.");
    }
    const end = new Uint8Array(22);
    const d = new DataView(end.buffer);
    d.setUint32(0, 0x06054b50, true);
    d.setUint16(8, files.length, true);
    d.setUint16(10, files.length, true);
    d.setUint32(12, directorySize, true);
    d.setUint32(16, offset, true);
    return new Blob([...chunks, ...directory, end], { type: "application/zip" });
  }

  try {
    const id = conversationId();
    const session = await readSession();
    // Branch-preserving native endpoint is required, never synthesized.
    const raw = await apiRequest(`/backend-api/conversation/${encodeURIComponent(id)}`,
      session.token, session.accountId);
    const native = isObject(raw?.conversation) ? raw.conversation : raw;
    const graph = validateNative(native, id);

    let evidence = null;
    let audit = { status: "unavailable", reason: "Selected-branch endpoint not checked." };
    try {
      evidence = await readSelectedBranch(id, session.token, session.accountId, native, graph);
      audit = {
        status: "audited",
        page_count: evidence.pages.length,
        unique_message_ids: evidence.uniqueMessages,
        selected_branch_messages_absent_from_native_mapping: evidence.missing,
        uncomparable_message_ids: evidence.uncomparable,
        pagination_reached_oldest_page: true
      };
    } catch (error) {
      if (error?.fatal) throw error;
      // The native graph remains an authentic capture; disclose that the
      // independent cross-check could not run. Never claim completeness.
      evidence = null;
      audit = { status: "unavailable", reason: String(error?.message || "Selected-branch check failed.") };
    }

    if (conversationId() !== id) fail("Conversation tab changed during capture. Retry.");
    const nativeBytes = jsonBytes([native]); // Direct input to Press chat-extract.
    const files = [["conversations.json", nativeBytes]];
    if (evidence) files.push(["selected-branch-pages.json", jsonBytes(evidence.pages)]);
    const report = {
      schema: "press-chatgpt-capture-reference/v1",
      captured_at_utc: new Date().toISOString(),
      conversation_id: id,
      source: {
        native_graph: "/backend-api/conversation/{id}",
        selected_branch: "/backend-api/conversations/{id} and paginated /messages"
      },
      native_graph: {
        mapping_nodes: graph.nodes,
        branch_points: graph.branchPoints,
        leaves: graph.leaves,
        selected_chain_nodes: graph.currentChainNodes,
        sha256_conversations_json: await sha256(nativeBytes)
      },
      selected_branch_audit: audit,
      guarantees: [
        "conversations.json contains exactly one native mapping conversation, without synthesized nodes.",
        "Parent/child relationships and the selected parent chain passed structural checks.",
        "All selected-branch pages reached by the API pagination flags were saved separately if the audit succeeded."
      ],
      limitations: [
        "This is an undocumented live API snapshot, NOT the official ChatGPT account export.",
        "Neither endpoint proves that every historical branch or hidden message is present.",
        "The native mapping may omit messages included in the paginated selected branch.",
        "Attachments and generated files are not downloaded; references may be present.",
        "Unfiltered native JSON may contain hidden context, tool messages, and private metadata. Keep it outside Press source control."
      ]
    };
    files.push(["capture-manifest.json", jsonBytes(report)]);
    const blob = buildZip(files);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const filename = `chatgpt-capture-${id}.zip`;
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    try { anchor.click(); }
    finally {
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
    return {
      ok: true, filename, nodes: graph.nodes,
      branchPoints: graph.branchPoints,
      audit: audit.status,
      missingFromGraph: evidence?.missing ?? null,
      uncomparableMessages: evidence?.uncomparable ?? null
    };
  } catch (error) {
    // Never return authentication tokens, cookies, or response body text.
    return { ok: false, error: String(error?.message || "Capture failed.") };
  }
}
