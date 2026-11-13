"""Source/AST correspondence tests against the actual pinned GFM reader."""
import copy
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from table_captions import normalize_table_captions
from validate_sources import FORMAT, parse_document, parse_pandoc

TABLE = '| Symbol | Meaning |\n|---|---|\n| S | Set |\n'
CAPTION = ': Table 7b — Notation, © Author & Co.'
PATH = Path('works/sample/manuscript.md')


class TableCaptionTests(unittest.TestCase):
    def parse(self, source, path=PATH):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'source.md'
            file.write_text(source, encoding='utf-8')
            return parse_document(file, path)

    def good(self, source):
        ast, errors = self.parse(source)
        self.assertEqual(errors, [])
        return ast

    def bad(self, source, line, message):
        ast, errors = self.parse(source)
        self.assertIsNone(ast)
        self.assertTrue(any(f'{PATH}:{line}: table caption:' in e and message in e for e in errors), errors)

    def test_native_caption_and_author_owned_label(self):
        source = TABLE + '\n' + CAPTION + '\n'
        ast = self.good(source)
        raw = parse_pandoc(source)
        self.assertEqual(FORMAT, 'gfm+tex_math_dollars+raw_html+yaml_metadata_block')
        expected = copy.deepcopy(raw['blocks'][0])
        expected['c'][1] = [None, [{'t': 'Plain', 'c': raw['blocks'][1]['c'][2:]}]]
        self.assertEqual(ast['blocks'], [expected])
        self.assertEqual([None, []], raw['blocks'][0]['c'][1])
        self.good(TABLE + '\n: Table Notation without a number or dash\n')
        self.good(TABLE + '\n: Table A  spaced label\n')

    def test_captionless_and_ordinary_prose_are_identical(self):
        for suffix in ('', '\nTable 1 — Prose label.\n', '\nNotation: prose.\n'):
            with self.subTest(suffix=suffix):
                source = TABLE + suffix
                self.assertEqual(self.good(source), parse_pandoc(source))
        # Templates and repository prose do not opt into manuscript compilation.
        source = TABLE + '\n' + CAPTION + '\n'
        ast, errors = self.parse(source, Path('templates/work/manuscript.md'))
        self.assertEqual(errors, [])
        self.assertEqual(ast, parse_pandoc(source))

    def test_captionless_starter_preserves_the_previous_reader_ast(self):
        source = (Path(__file__).resolve().parents[1] / "templates/work/manuscript.md").read_text()
        source = source.replace("\n: Table 1 — Notation\n", "")
        self.assertEqual(self.good(source), parse_pandoc(source))

    def test_metadata_and_literal_contexts_do_not_interfere_with_real_captions(self):
        for literal in ("```\n: Table literal\n```", "> Quoted text\n: Table lazy continuation",
                        "    : Table literal", ": Ordinary prose."):
            source = literal + "\n\n" + TABLE + "\n" + CAPTION + "\n"
            ast = self.good(source)
            table = next(b for b in ast["blocks"] if b["t"] == "Table")
            self.assertTrue(table["c"][1][1])
        source = "---\ntitle: |\n  : Table literal metadata\n---\n\n" + TABLE + "\n" + CAPTION + "\n"
        ast = self.good(source)
        self.assertEqual(ast["meta"], parse_pandoc(source)["meta"])

    def test_multiple_identical_tables_and_captions_have_proven_source_order(self):
        source = TABLE + '\n' + CAPTION + '\n\n' + TABLE + '\nProse label.\n\n' + TABLE + '\n' + CAPTION + '\n'
        ast = self.good(source)
        tables = [b for b in ast['blocks'] if b['t'] == 'Table']
        self.assertEqual(len(tables), 3)
        self.assertEqual(tables[0]['c'][1], tables[2]['c'][1])
        self.assertEqual(tables[1]['c'][1], [None, []])
        self.assertEqual(ast['blocks'][2]['t'], 'Para')

    def test_exact_blank_line_and_no_intervening_material(self):
        for separator in ('', '\n\n', '\nProse.\n\n', '\n<!-- comment -->\n\n'):
            source = TABLE + separator + CAPTION + '\n'
            with self.subTest(separator=separator):
                self.bad(source, source.splitlines().index(CAPTION) + 1, 'exactly one blank line')
        self.bad(CAPTION + '\n', 1, 'top-level GFM pipe table')

    def test_duplicate_and_multiline_directives(self):
        for suffix in ('\n' + CAPTION, '\n\n' + CAPTION, '\ncontinued caption', '\n    continued caption'):
            source = TABLE + '\n' + CAPTION + suffix + '\n'
            with self.subTest(suffix=suffix):
                ast, errors = self.parse(source)
                self.assertIsNone(ast)
                self.assertTrue(any('table caption:' in e for e in errors), errors)
                self.assertTrue(any(f'{PATH}:5:' in e or f'{PATH}:7:' in e for e in errors), errors)

    def test_malformed_indented_escaped_and_arbitrary_caption_variants(self):
        for caption in (': Table', ': Table ', ': Table   ', ':Table 1', ': table 1', ': TABLE 1',
                        ' : Table 1', '   : Table 1', '    : Table 1', '\t: Table 1',
                        r'\: Table 1', r'\\: Table 1', ': **Table** 1', ': Notation', ':: Table 1', '    : Table 1\n    continued'):
            with self.subTest(caption=caption):
                self.bad(TABLE + '\n' + caption + '\n', 5, 'caption:')

    def test_plain_text_only_even_when_markdown_would_be_literal(self):
        for text in ('*emphasis*', '**strong**', '_emphasis_', '~~deleted~~', '[link](https://example.org)',
                     '`code`', '$x$', '<b>HTML</b>', r'escaped\*', 'www.example.org',
                     'https://example.org', '&amp;', '&#58;', '[^note]'):
            with self.subTest(text=text):
                self.bad(TABLE + '\n: Table 1 — ' + text + '\n', 5, 'caption:')

    def test_literal_code_and_quotes_never_attach(self):
        literals = (
            '```markdown\n' + TABLE + '\n' + CAPTION + '\n```\n',
            '~~~\n' + CAPTION + '\n~~~\n',
            '    ' + CAPTION + '\n    another code line\n',
            '> ' + CAPTION + '\n',
            '> Quoted prose\n' + CAPTION + '\n',  # GFM lazy blockquote continuation
            '> | A |\n> |---|\n> | x |\n>\n> ' + CAPTION + '\n',
        )
        for literal in literals:
            with self.subTest(literal=literal):
                source = TABLE + '\nParagraph separating literal examples.\n\n' + literal
                self.assertEqual(self.good(source), parse_pandoc(source))
        self.assertEqual(self.good(TABLE + '\n```\n' + CAPTION + '\n```\n'),
                         parse_pandoc(TABLE + '\n```\n' + CAPTION + '\n```\n'))

    def test_nested_table_is_not_a_top_level_target(self):
        source = '- Item\n\n' + ''.join('  ' + l + '\n' for l in TABLE.splitlines()) + '\n  ' + CAPTION + '\n'
        self.bad(source, 7, 'caption:')

    def test_missing_pipe_borders_and_empty_body_use_native_gfm_semantics(self):
        for table in ('A | B\n---|---\nx | y\n', '| A |\n|---|\n'):
            with self.subTest(table=table):
                self.good(table + '\n' + CAPTION + '\n')

    def test_invisible_intervening_definitions_fail_span_proof(self):
        for separator in ('[unused]: https://example.org\n\n', '\n[unused]: https://example.org\n\n'):
            source = TABLE + separator + CAPTION + '\n'
            self.bad(source, len(source.splitlines()), 'table bound' if separator.startswith('\n') else 'table span')

    def test_inconsistent_ast_or_marker_parse_fails_closed_without_mutation(self):
        source = TABLE + '\n' + CAPTION + '\n'
        raw = parse_pandoc(source)
        for corrupt in ('caption', 'table', 'other', 'boundary', 'warning'):
            snapshot = copy.deepcopy(raw)
            def parse(text):
                if corrupt == 'warning':
                    raise ValueError('parser warned')
                ast = parse_pandoc(text)
                if corrupt == 'caption':
                    ast['blocks'][-1] = {'t': 'Plain', 'c': ast['blocks'][-1]['c']}
                elif corrupt == 'table':
                    ast['blocks'][0]['c'][1] = [None, [{'t': 'Plain', 'c': []}]]
                elif corrupt == 'other':
                    ast['meta']['changed'] = {'t': 'MetaString', 'c': 'changed'}
                elif corrupt == 'boundary' and 'BOUNDARY' in text:
                    ast['blocks'][0]['t'] = 'Plain'
                return ast
            with self.subTest(corrupt=corrupt):
                result, errors = normalize_table_captions(raw, source, PATH, parse)
                self.assertIsNone(result)
                self.assertTrue(any(f'{PATH}:5:' in e for e in errors), errors)
                self.assertEqual(raw, snapshot)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (TableCaptionTests,) for name in cls.__dict__ if name.startswith("test_"))
