"""A compact verified edition for recurring quotation edge-case qualification."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
from chat_gfm import review_gfm
from supporting_gfm import load_gfm_record
from test_assets import EditionTests
from verify_edition import verify_edition

PARAGRAPHS = 48
CASES = (
    ('m-model', 'assistant', 'Reviewed reply', {'model': 'model-only'}, 'model-only'),
    ('m-effort', 'assistant', 'Reviewed reply', {'thinking_effort': 'high'}, 'Unidentified AI model (high effort)'),
    ('m-unknown', 'assistant', 'Reviewed reply', {}, 'Unidentified AI model'),
    ('m-correspondent', 'responder', 'Reviewed Correspondent', {}, 'Reviewed Correspondent'),
    ('m-long-name', 'responder', 'Correspondent ' + 'LongReviewedName' * 8, {}, 'Correspondent ' + 'LongReviewedName' * 8),
    ('m-long-credit', 'assistant', 'Reviewed reply', {'model': 'model-' + 'x' * 120, 'thinking_effort': 'effort-' + 'y' * 119},
        'model-' + 'x' * 120 + ' (effort-' + 'y' * 119 + ' effort)'),
    ('m-long', 'assistant', 'Reviewed reply', {'model': 'long-reply-model'}, 'long-reply-model'),
)
CREDITS = {identifier: '— ' + credit for identifier, _, _, _, credit in CASES}
SECTIONS = {case[0]: f'QUOTE-CASE-{n:02d}' for n, case in enumerate(CASES)}


def reviewed_source():
    long = ('# Quoted source heading\n\nAn independently citable paragraph.\n\n'
            '\\[\nx+x=2x\n\\]\n\n```python\nprint("Literal quotation code")\n```\n\n'
            '| Label | Value |\n| --- | --- |\n| Alpha | One |\n\n'
            '> An ordinary nested quotation.\n\n')
    long += '\n\n'.join(f'QUOTE-PARAGRAPH-{n:03d} ' + 'A long reviewed reply remains readable documentary prose. ' * 4
                         for n in range(PARAGRAPHS))
    passages = [{'id': 'm-author', 'speaker': 'author', 'label': 'Assistant is only a label',
                 'text': 'Ordinary authorial opening without an inferred quotation.'}]
    passages.extend({'id': identifier, 'speaker': speaker, 'label': label, **details,
                     'timestamp': '2030-01-01T00:00:00Z',
                     'text': SECTIONS[identifier] + '\n\n' + (long if identifier == 'm-long' else 'A reviewed external contribution.')}
                    for identifier, speaker, label, details, _ in CASES)
    passages.append({'id': 'm-end', 'label': 'Unannotated author', 'text': 'QUOTE-END Authorial closing.'})
    return review_gfm({'title': 'Quotation regression', 'scope': 'Synthetic reviewed source', 'passages': passages})[0]


class QuotationPublicationTests(EditionTests):
    def source(self):
        folder = self.repo / 'works/sample/supporting'; folder.mkdir()
        self.source_bytes = reviewed_source()
        (folder / 'quotations.md').write_bytes(self.source_bytes)
        with (self.repo / 'works/sample/manuscript.md').open('a') as manuscript:
            manuscript.write('\n[Reviewed quotations](supporting/quotations.md).\n')
        self.git('add', 'works'); self.git('commit', '-qm', 'Review quotation edge cases')
        self.commit = self.git('rev-parse', 'HEAD')

    def test_source_credits_and_authored_boundaries_survive_verified_publication(self):
        self.source()
        ast, anchors, *_ = load_gfm_record(self.repo / 'works/sample/supporting/quotations.md')
        self.assertTrue(set(CREDITS) | {'m-author', 'm-end'} <= set(anchors))
        credits = {key: dict(ast['blocks'][anchors[key]]['c'][0][2])['data-press-attribution'] for key in CREDITS}
        self.assertEqual({key: value[2:] for key, value in CREDITS.items()}, credits)
        self.assertNotIn(b'2030-01-01', self.source_bytes)
        manifest = self.build()
        entry, = manifest['supporting_documents']
        self.assertEqual(hashlib.sha256(self.source_bytes).hexdigest(), entry['sha256'])
        self.assertTrue(set(CREDITS) <= set(entry['elements']))
        self.assertEqual(manifest, verify_edition(self.output))


@contextmanager
def quotation_fixture():
    fixture = QuotationPublicationTests('test_source_credits_and_authored_boundaries_survive_verified_publication')
    try:
        fixture.setUp(); fixture.source(); fixture.build(); verify_edition(fixture.output)
        yield fixture.output, {'credits': CREDITS, 'paragraphs': PARAGRAPHS, 'sections': SECTIONS}
        verify_edition(fixture.output)
    finally:
        fixture.doCleanups()


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromName('test_source_credits_and_authored_boundaries_survive_verified_publication', QuotationPublicationTests)
