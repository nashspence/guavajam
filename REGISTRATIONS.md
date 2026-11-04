# Record an external identifier

Check the issuing registry's record before entering an identifier. Confirm who
or what it identifies, including the specific edition and format where relevant.
An identifier with a valid check digit can still belong to another publication.

| What the registry identifies | Where to record it |
| --- | --- |
| The work across editions, such as a concept DOI | Manuscript front matter. For an identifier discovered after publication, use `registrations/works/<slug>.json`. |
| A particular edition or format, such as a version DOI or a print ISBN | `registrations/editions/<slug>/vX.Y.Z.json`. Match each ISBN to the format to which it was assigned. |
| The author or the imprint | `registrations/identities/author.json` or `registrations/identities/imprint.json`. An author's ORCID can also appear in manuscript front matter. |
| A continuing series with its own registry record, such as an assigned ISSN | `registrations/series/<slug>.json`. |

Create the edition record after publication. Take the release URL and time
from the published release. Download its attachments and verify the canonical
artifact and SHA-256 before recording them. Add identifiers as registries assign
them. [Publication protocol](PUBLICATION.md)

Use the validated record examples when adding an entry. The schema lists
accepted fields and identifier formats. [Examples](tests/test_validate_sources.py) · [Registration schema](scripts/identifier_schema.py)
