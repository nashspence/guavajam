# Works

This directory contains **working sources**, not automatically published editions.

For each work create a lowercase hyphenated folder, e.g. `works/example-essay/`. Copy the files from [templates/work/](../templates/work/) and update the YAML front matter in `manuscript.md`. No separate metadata file is needed. See [FORMAT.md](../FORMAT.md) for executable conventions.

```text
works/
  example-essay/
    manuscript.md   # required, strict YAML front matter + working Markdown
    README.md       # optional editorial notes
    assets/         # existing local images; deprecated for new authoring
```

For new images use approved content-addressed HTTPS references, as described in
[ASSETS.md](../ASSETS.md). The manuscript itself inventories its dependencies;
no per-work asset YAML is required. Existing local images remain supported and
are verified/embedded during edition builds.

Optional work-level scholarly and catalog identifiers go in front matter or the post-publication [work registration index](../REGISTRATIONS.md). Version-specific DOIs, ISBNs and OCLC record numbers belong to **edition registration records**. Commits and untagged source trees remain drafts; see [CATALOG.md](../CATALOG.md) for officially published editions.
