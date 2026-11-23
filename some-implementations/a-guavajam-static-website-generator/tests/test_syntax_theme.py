"""VS Code-inspired static Pandoc syntax colors without a runtime highlighter."""
from pathlib import Path
import shutil
import unittest

from a_guavajam_static_website_generator.renderer import pandoc
from a_guavajam_static_website_generator.site import page


CSS = (Path(__file__).resolve().parents[1] /
       'src/a_guavajam_static_website_generator/styles/reading.css')


class SyntaxThemeTests(unittest.TestCase):
    def test_exact_editor_surfaces_and_scoped_pandoc_token_styles(self):
        css = CSS.read_text('utf-8')
        for name, value in {
                'paper': '#fff', 'ink': '#000', 'keyword': '#0000ff',
                'control': '#af00db', 'string': '#a31515', 'comment': '#008000',
                'number': '#098658', 'function': '#795e26'}.items():
            self.assertIn('--guavajam-code-' + name + ': ' + value + ';', css)
        dark = css.split('@media (prefers-color-scheme: dark) {', 1)[1]
        for name, value in {
                'paper': '#1e1e1e', 'ink': '#d4d4d4', 'keyword': '#569cd6',
                'control': '#c586c0', 'string': '#ce9178', 'comment': '#6a9955',
                'number': '#b5cea8', 'function': '#dcdcaa'}.items():
            self.assertIn('--guavajam-code-' + name + ': ' + value + ';', dark)
        self.assertRegex(css, r'pre \{[^}]*background: var\(--guavajam-code-paper\);'
                         r'[^}]*color: var\(--guavajam-code-ink\);')
        self.assertIn('code.sourceCode .cf { color: var(--guavajam-code-control); }', css)
        self.assertIn('code.sourceCode :is(.co, .cv, .do)', css)
        self.assertIn('code.sourceCode :is(.st, .ss, .vs, .ch, .sc)', css)
        # Inline code intentionally keeps the existing reading-page tint.
        self.assertRegex(css, r':not\(pre\) > code \{[^}]*background: var\(--guavajam-tint\);')

    def test_high_contrast_and_print_replace_editor_colors(self):
        css = CSS.read_text('utf-8')
        forced = css.split('@media (forced-colors: active) {', 1)[1].split('@media print {', 1)[0]
        self.assertIn('pre { color: CanvasText; background: Canvas; border-color: GrayText; }', forced)
        self.assertIn('code.sourceCode span[class] { color: inherit; }', forced)
        printed = css.split('@media print {', 1)[1]
        self.assertIn('--guavajam-code-paper: #fff;', printed)
        self.assertIn('--guavajam-code-ink: #000;', printed)
        self.assertIn('code.sourceCode span[class] { color: inherit; }', printed)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc is not installed')
    def test_pandoc_emits_static_tokens_and_preserves_plain_blocks(self):
        markdown = (b'```python\ndef greet(name):\n    print("Hello", name)  # comment\n'
                    b'    return 42\n```\n\n```\nplain without language\n```\n'
                    b'\nInline `code` stays plain.\n')
        html = pandoc(markdown, writer='html5').decode('utf-8')
        for token in ['kw', 'st', 'co', 'dv']:
            self.assertIn('class="' + token + '"', html)
        self.assertIn('<pre><code>plain without language', html)
        self.assertIn('<code>code</code>', html)
        self.assertIn('class="sourceCode python"', html)
        wrapped = page('Code sample', html)
        self.assertIn('--guavajam-code-paper: #1e1e1e;', wrapped)
        self.assertNotIn('<script', wrapped)
        self.assertIn('class="sourceCode python"', wrapped)
