# ChatGPT capture (reference prototype)

Read-only, manually invoked Chrome Manifest V3 extension that captures **one**
ChatGPT conversation as a native branching JSON snapshot and locally audits it
against ChatGPT's independently paginated selected-branch endpoint.

**Status: reference implementation, offline-tested; NOT verified against a live,
authenticated ChatGPT account.** The API is undocumented and can change.

## Install and capture

1. Check out this branch of `nashspence/press` to a stable local directory.
2. Open Chrome or Chromium at `chrome://extensions`.
3. Enable **Developer mode** and choose **Load unpacked**.
4. Select `tools/chatgpt-capture/` — the directory containing `manifest.json`.
5. Navigate to a conversation at `https://chatgpt.com/c/<conversation-id>`.
6. Click the extension's toolbar action, then **Capture this conversation**.
7. Chrome should save `chatgpt-capture-<conversation-id>.zip` in Downloads.

The ZIP has these root-level files:

```text
chatgpt-capture-<id>.zip
├── conversations.json             # [native conversation object]; Press reads this
├── selected-branch-pages.json     # only if all pages were audited, newest page first
└── capture-manifest.json          # capture limitations, counts, SHA-256, status
```

`conversations.json` is an array containing exactly one original native
conversation *object*. The object has not been converted into a flat transcript
or merged with paginated records. `JSON.stringify` is used to serialize the
object, so this is semantic preservation, not an identical HTTP byte stream.

The other endpoint may contain messages absent from the native `mapping`. The
extension preserves these as a **separate** record, not invented mapping nodes.
If that endpoint is unavailable, the archive still contains the authentic graph
and a manifest explicitly marking the audit **unavailable**. If the native graph
itself is unavailable or malformed, the extension refuses to download anything.

## Using it with Press

Press's `scripts/extract_chatgpt.py` accepts a ZIP
containing a root-level `conversations.json` array. From a Press checkout:

```sh
make chat-list FILE=../chatgpt-capture-<id>.zip
make chat-extract FILE=../chatgpt-capture-<id>.zip \
  CONVERSATION=<id> OUTPUT=../private-review.json
make chat-gfm FILE=../chatgpt-capture-<id>.zip \
  CONVERSATION=<id> OUTPUT=../private-review.md
```

`TIP=<node-id>` may be passed to `chat-extract` or `chat-gfm` to select a
particular native branch tip. The existing Press importer intentionally filters
tool traffic, hidden instructions, attachments, and other non-reviewable records
when generating a supporting record. **Do not commit the raw ZIP.**

To inspect the graph and audit locally:

```sh
python -m zipfile -t chatgpt-capture-<id>.zip
unzip -p chatgpt-capture-<id>.zip capture-manifest.json | python -m json.tool
unzip -p chatgpt-capture-<id>.zip conversations.json | jq '.[0].mapping | length'
```

## Permissions and privacy

The installed extension declares only:

```json
"permissions": ["activeTab", "scripting"]
```

It has **no** persistent host permissions, background worker, preloaded content
scripts, storage, downloads API, optional permissions, remote libraries, export
server, analytics, deletion operations, or third-party communications. It
injects a single, self-contained function into the **currently active**
`chatgpt.com` tab's **ISOLATED** world after an explicit click. It performs
same-origin GET requests to `/api/auth/session` and the two conversation APIs;
authentication data is used in memory for these requests and never exported.

The downloaded native JSON may include hidden context, non-visible tool output,
attachment references, or other sensitive metadata. Keep it in private storage
outside source control, including Press's published `works/` and `supporting/`
paths. Review Press's filtered supporting-record output before publication.

## Limitations

- This is **not** OpenAI's official data-export artifact, though its primary
  object shape matches the native `mapping` schema expected by Press.
- A valid graph does not prove **all historical branches** are present.
- ChatGPT's singular mapping endpoint can omit messages found by its plural
  selected-branch endpoint. The audit detects known omissions by message ID
  when IDs are comparable; it cannot prove completeness of either endpoint.
- Selected-branch pages remain separate, including messages that don't exist in
  the graph; the extension does not fabricate their ancestry.
- The secondary endpoint is audited to the oldest page or marked unavailable.
- No original attachment bytes or generated files are downloaded.
- The ZIP uses uncompressed entries (ZIP STORE) and has a 128 MiB cap.
- Signed-in, live Chrome integration has **not** been tested in this environment.
  First install should be tested against a disposable branched conversation.
- Chrome itself may rename downloaded files to avoid filename collisions.
- Changes to the private API or login/session handling may break this tool.

## Tests

Node 20+; no npm install or third-party dependencies required:

```sh
mise exec -- node --test tests/test_chatgpt_capture.mjs
```

The fixtures simulate a branched native `mapping` and a two-page selected
branch containing a tool message missing from the native graph, plus error
paths, authentication fallback, pagination URL variants, and permissions.

Tests use only synthetic conversation data. To generate a disposable sample ZIP,
set `CAPTURE_FIXTURE_PATH` to a path outside the repository when running them.
Then verify it using `make chat-list` and `make chat-extract`.

## Source / copyright

This is a fresh, deliberately narrow implementation informed by the observed
API behavior of [thekester/chatgpt-export](https://github.com/thekester/chatgpt-export)
and [ThierryAbalea/chatgpt-conversation-export](https://github.com/ThierryAbalea/chatgpt-conversation-export).
No project source code or third-party runtime packages were copied into this
prototype. The repository's `COPYRIGHT.md` governs this source; see `SECURITY.md` for
research links, limitations, and validation requirements.
