"""Real signed single-work history for browser qualification."""
from pathlib import Path
from guavajam_test_support.discovery_fixture import Publisher


def make_catalog_fixture(directory):
    publisher = Publisher(directory)
    supporting = publisher.repo / 'content/supporting'; supporting.mkdir()
    (supporting / 'note.md').write_text('<a name="document"></a>\n\n# Supporting note\n\n<a name="claim"></a>\n\nThe exact independently citable claim.\n')
    publisher.source('Field Notes, Revised', 'Revised observation. See [the supporting claim](supporting/note.md#claim).')
    publisher.publish('Improve the field observations\n\nAdd independently citable supporting evidence.')
    return {'repo': publisher.repo, 'trust': publisher.trust, 'revisions': publisher.revisions}
