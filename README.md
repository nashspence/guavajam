# GuavaJam

A standalone, optional, **zero-runtime-JavaScript** HTML presentation for any
Git repository following [Press's published GFM conventions](https://github.com/nashspence/press/blob/main/docs/canonical-commits.md).
The signed Git commit, not this view, remains the edition. No GitHub Releases,
GitHub API, B2 keys, database, client-side scripts, or Press Python imports.

GuavaJam reads a complete **pinned Git SHA-1 commit** from a local Git repository,
verifies the Press publication trailers and its independently trusted GPG signer,
parses committed GFM using Pandoc, and writes HTML with embedded CSS. It carries
forward Press's reading typography, focus/print/reflow rules and semantics as
an optional view. It renders `press-media` declarations as native audio/video
players with captions, visible transcript links and ordinary download links.
It also generates supporting-note pages from the same commit. The output embeds
its source revision and has a strict CSP; it is **not a new release**.

## Render

Requires Python 3.12+ and Pandoc 3.12.1 (the Press version); no Python
third-party runtime dependencies. Provide a trusted GPG signing-key fingerprint in your local keyring or an
SSH allowed-signers file outside the source repository:

```sh
python -m guavajam --repo /path/to/any-press-compatible-repo \
  --revision <FULL_40_CHARACTER_COMMIT_SHA> --work my-work \
  --trusted-gpg-fingerprint <FULL_APPROVED_KEY_FINGERPRINT> \
  --output /tmp/my-work-view
```

To verify SSH-signed publication commits, use `--ssh-allowed-signers /path/to/independent/trust-file` instead of the GPG fingerprint.
For local authoring previews only, replace `--trusted-gpg-fingerprint ...` with
`--unsigned-preview`; the generated HTML is visibly marked **NOT A PUBLICATION**.
Never serve an unsigned preview as an issued edition. A renderer does not decide
publication history: it accepts only a caller-selected commit identity. It will
not fetch `main`, dereference a tag, create Git refs, or write into the source.

The single origin declared in the committed `asset-policy.json` supplies
external image/audio/video/caption URLs. Media requires content-addressed paths
and is streamed by the browser; GuavaJam neither fetches nor embeds large media.
The origin must support CORS for WebVTT tracks, proper HTTP byte ranges, fixed
object versions and approved B2 retention. See Press's media contract.

For a browser preview, serve the output directory from a local static HTTP
server, for example `python -m http.server --directory /tmp/my-work-view 8000`.
For offline exports, make a separate byte-verified copy of the referenced media
and adjust paths in an **export**, never in the canonical source. The HTML viewer
by itself is not a complete offline copy of remote video.

## Contract and portability

Source layout: `works/<slug>/manuscript.md` with `press.work.v1` metadata,
`asset-policy.json`, stable `<a id="...">` GFM anchors, optional
`works/<slug>/supporting/<slug>.md`, and fenced `press-media` JSON declarations.
Everything is read from the requested Git tree via `git show`/`ls-tree`, not from
working-tree files. No hardcoded Press repository owner or upstream URL is used.
`VIEW.json` records only the derived output digests and canonical commit/path.
It is explicitly **not** an edition receipt or publication manifest.

The CSS began as Press's `styles/edition-v1.css`; it is copied into this new
optional renderer, with edition-record-specific rules left inert. The new style
profile is `guavajam.v1`; it never changes an already issued Press HTML edition.
Renderer correctness and security, including content-addressed media, raw HTML
restrictions, no JavaScript, signed-vs-preview display, and source pinning, are
covered by `python -m unittest discover -s tests`.

The repository is initially empty on GitHub; integration requires bootstrapping
`main` with a minimal reviewed commit before opening its first PR. The handoff
includes that base commit in a full Git bundle. Do not merge through an
unreviewed direct main-branch code write.
