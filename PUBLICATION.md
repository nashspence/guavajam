# Publishing an edition

An edition becomes official when the imprint publishes a full GitHub Release through the protected edition workflow. The attached HTML file named in the release notes is the text to read and cite. Branches, bare tags, draft releases, and prereleases are working material.

## Choose the version

Start a work at `v1.0.0` unless there is an editorial reason to do otherwise. Bump the major version for a substantially new edition, the minor version for a meaningful revision, and the patch version for corrections such as typos or citations.

## Review and publish

1. Review the manuscript on `main`. Run [Prepare verified edition](.github/workflows/prepare-edition.yml) from `main` with the reviewed commit’s full SHA and an edition tag such as `works/example-essay/v1.0.0`. Leave `publish` off to stage a draft.
2. Open the built HTML from the workflow artifact and review the text, figures, and rights. Approve the `press-release` deployment to stage the draft. Add any editorial notes to the draft below the generated fields. Identify alternate formats and link any edition-specific license terms.
3. When ready to issue the edition, run the same workflow with the same SHA and tag, select `publish`, and approve the deployment after reviewing the build.
4. Record any assigned [external identifiers](REGISTRATIONS.md). For a correction or revision, issue a new version and explain its relationship to the earlier edition in the new release notes. Keep issued tags and artifacts intact. Record withdrawals or supersessions in the [catalog](CATALOG.md).
