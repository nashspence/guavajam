# GuavaJam

One Git repository, one work in `content/`, optional views. Trusted signed commits
that change `content/` on the selected first-parent history define editions.
The commit identifies the edition; its ordinary message describes the change.

Install `guavajam-core`, then run `guavajam-core new-work --title "My Work" --author "My Name"`
in a Git repository. The starter supplies `content/manuscript.md` and a Makefile
using the installed core commands. `make check` validates sources and assets;
`git commit -S` records an edition. Draft branches remain ordinary Git branches.

A signed merge publishes the combined draft relative to its first parent. A
fast-forward preserves each individual edition, so every content-changing commit
it introduces must already be signed. Discovery retains the previous accepted tip
to detect rewritten history; commit dates are publisher claims, not ordering rules.

The optional `guavajam-view` reads a public YAML list of repository URLs and
an independent operator trust policy. See the [index example](deploy/index.example.yaml),
[trust example](deploy/trust.example.yaml), and [Compose configuration](deploy/compose.env.example).
Core can validate, discover, and preserve a work without a view or index.

[Developer onboarding](AGENTS.md). Run `make help` for repository commands.
