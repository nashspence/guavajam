# ChatGPT capture: research and security review (2026-11-08)

## Relevant findings

1. Chrome's `activeTab` permission grants temporary access on an explicit
   extension invocation; `chrome.scripting.executeScript()` can inject code in
   `ISOLATED` world. Official documentation:
   - https://developer.chrome.com/docs/extensions/develop/concepts/activeTab
   - https://developer.chrome.com/docs/extensions/reference/api/scripting
   - https://developer.chrome.com/docs/extensions/reference/manifest/content-scripts

2. Two undocumented ChatGPT endpoints coexist in late 2026:
   - singular `/backend-api/conversation/{id}` returns `mapping`, with branch
     parent/child relationships;
   - plural `/backend-api/conversations/{id}` returns paginated `messages`,
     sometimes containing messages absent from `mapping`.
   The singular route reportedly reappeared on 2026-09-25, after an August
   rollout of the plural route. Measurements are independent, not OpenAI API
   documentation; confirm against a real account before relying on them.
   - https://gptspy.alinr.com/knowledge/endpoint/backend-api-conversation-id/
   - https://gptspy.alinr.com/knowledge/endpoint/backend-api-conversations-id/
   - https://github.com/ThierryAbalea/chatgpt-conversation-export/blob/main/docs/format.md
   - https://community.openai.com/t/chatgpt-web-update-removed-message-version-arrows-cannot-access-edited-message-history/1374666/188

3. Established third-party implementations use current browser session cookies,
   and often a bearer access token when available. Page-origin requests avoid
   extension-service-worker cross-site cookie issues. Sources read:
   - https://github.com/thekester/chatgpt-export/blob/main/src/content.js
   - https://github.com/ThierryAbalea/chatgpt-conversation-export/blob/main/extension/popup.js
   - https://github.com/TacticalOriental/ChatGPT_Conversation_Table_of_Contents/blob/main/ChatGPT_Conversation_Table_of_Contents.user.js
   These are informative but not a substitute for testing on a real account.

4. Press already has the downstream reader:
   - https://github.com/nashspence/press/blob/main/scripts/extract_chatgpt.py
   - https://github.com/nashspence/press/blob/main/Makefile
   - https://github.com/nashspence/press/blob/main/tests/test_extract_chatgpt.py
   Its JSON-array input contract and branch selection are preserved by keeping
   the raw graph in root-level `conversations.json`.

## Why the archive has two native representations

A single merged graph would imply that absent messages have known parentage.
They do not. A ZIP with separate raw graph and paginated selected-branch pages
preserves the distinction. The primary graph is directly consumable by Press;
the secondary evidence documents omissions and can support future analysis.

The two sources are fetched within one operation, not transactionally. If
`current_node` differs, the operation fails because the active branch changed
or the API presentations disagree. If the plural endpoint is unavailable,
only the mapping is exported with explicit audit status "unavailable".

Even if the secondary endpoint reports no missing IDs, this is **not** proof of
branch completeness: omitted messages might appear in neither source, ID types
may vary, and the plural response may cover only the active branch.

## Explicit security properties

- No persistent host rights or background execution, preloaded hooks, data
  uploads, extension storage, or dynamic code fetching.
- Site origin and route are checked twice (popup and injected function).
- The injected function constructs only same-origin API GET requests.
- Network errors do not log tokens, cookies, or response bodies.
- The output is local, but as with other page-level download triggers, Chrome
  controls the final destination and may show download UI.
- Native data is not sanitized before private archival; Press does the separate
  selective transformation for publication.
- Child/parent graph consistency, branch tip, conversation ID, and pagination
  progress are checked. The script refuses a synthetic graph fallback.
- Each ZIP entry uses CRC-32; `capture-manifest.json` records SHA-256 of the
  exact bytes written to `conversations.json`.

## Open risks and acceptance gate before production

- **Live compatibility unverified.** The browser test environment could not
  open `chrome://extensions`; actual installation, injection, and download on a
  signed-in account remain explicit requirements.
- Chrome's implementation of content-script fetch and ChatGPT's session
  endpoint can change; if cookie/Bearer authentication fails, the tool fails.
- `mapping` may be incomplete while structurally valid. A first user test must
  create an edited prompt and regenerated response, then verify both branch
  paths and compare against the ChatGPT UI.
- Some metadata IDs can be objects or non-comparable. The audit reports the
  number of such records instead of claiming a clean comparison.
- The ZIP builder is ZIP32 STORE only; larger or compressed archives require a
  different implementation with additional testing.
- Hostile extensions or a compromised browser can defeat isolated-world
  protections; this tool reduces permissions, not all browser attack surface.
- The private API is not a supported OpenAI interface; maintenance is expected.

Suggested acceptance sequence: (1) run offline tests; (2) review manifest and
network fetch allowlist; (3) load unpacked; (4) capture a disposable conversation
with a known fork; (5) validate ZIP and SHA-256; (6) run Press `chat-extract` on
that ZIP; (7) compare every expected fork and selected response to the UI;
(8) only then use it on valuable conversations.

## Code provenance

The implementation was authored for this reference prototype, based on the
published behavior of the above APIs, not copied from thekester or ThierryAbalea.
Those projects are MIT-licensed, but attribution and license retention would be
required if actual source snippets are incorporated in subsequent revisions.
