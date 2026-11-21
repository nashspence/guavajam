"""Real signed single-work history for browser qualification."""
from pathlib import Path
from guavajam_test_support.discovery_fixture import Publisher


def make_catalog_fixture(directory):
    publisher = Publisher(directory)
    supporting = publisher.repo / 'content/supporting'; supporting.mkdir()
    (supporting / 'note.md').write_text('<a name="document"></a>\n\n# Supporting note\n\n<a name="claim"></a>\n\nThe exact independently citable claim.\n')
    publisher.source('Field Notes, Revised', 'Revised observation. See [the supporting claim](supporting/note.md#claim).')
    # Source order deliberately differs from lexical order. Boundary anchors
    # also exercise names used by the generated page shell and search controls.
    anchors = ['opening'] + [f'evidence-{number:02d}' for number in range(1, 45)]
    anchors[19:21] = ['z-source-before-a', 'a-source-after-z']
    anchors[39:45] = ['guavajam-query', 'guavajam-search', 'guavajam-static-section',
                      'main', 'z-last', 'a-last']
    manuscript = publisher.repo / 'content/manuscript.md'
    with manuscript.open('a') as stream:
        for number, anchor in enumerate(anchors[1:], 1):
            stream.write(f'\n<a id="{anchor}"></a>\n## Evidence {number:02d}\n\n'
                         f'Independently citable observation {number}.\n')
    with (supporting / 'note.md').open('a') as stream:
        for number in range(2, 45):
            anchor = 'supporting-tail' if number == 44 else f'supplement-{number:02d}'
            stream.write(f'\n<a name="{anchor}"></a>\n\nAdditional supporting observation {number}.\n')
    publisher.publish('Improve the field observations\n\nAdd independently citable supporting evidence.')
    return {'repo': publisher.repo, 'trust': publisher.trust, 'revisions': publisher.revisions,
            'citation_anchors': anchors, 'passages': len(anchors)}
