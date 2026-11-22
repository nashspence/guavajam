"""Local Lucide links preserve source text, navigation and offline rendering."""
from html.parser import HTMLParser
from importlib.resources import files
import json
import unittest

from a_guavajam_static_website_generator.icons import finish_html, icon
from a_guavajam_static_website_generator.site import detail_link, page
from a_guavajam_static_website_generator.citation import citation_control


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.tags = []; self.feed(html)
    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


class IconTests(unittest.TestCase):
    def test_links_keep_their_targets_and_labels_with_distinct_destination_and_download_cues(self):
        original = '<html><body><p>Before <a href="https://example.org/paper">Paper</a> ' + \
            '<a href="mailto:reader@example.org">Email the author</a> ' + \
            '<a href="report.pdf" download>Report</a> ' + \
            '<a href="https://example.org/audio" download type="audio/mpeg">Recording</a> ' + \
            '<a href="opaque" download>Supplement</a> <a href="#local">Local</a>.</p></body></html>'
        result = finish_html(original)
        self.assertEqual(result, finish_html(result), 'site wrapping must not add duplicate icons or MIME labels')
        tags = Tags(result).tags
        self.assertEqual([attrs['href'] for tag, attrs in Tags(original).tags if tag == 'a'],
                         [attrs['href'] for tag, attrs in tags if tag == 'a'])
        self.assertEqual(2, result.count('guavajam-icon-external-link'))
        for name in ['mail','file-text','file-audio','file-down']:
            self.assertEqual(1, result.count('guavajam-icon-' + name))
        self.assertIn('Report <span class="guavajam-link-mime">(application/pdf)</span>', result)
        self.assertIn('Recording <span class="guavajam-link-mime">(audio/mpeg)</span>', result)
        self.assertNotIn('application/octet-stream', result)
        self.assertIn('<a href="#local">Local</a>', result)
        for tag, attrs in tags:
            if tag == 'svg':
                self.assertEqual('true', attrs['aria-hidden']);self.assertEqual('false', attrs['focusable'])
        self.assertIn('Copyright (c)', result)
        self.assertIn('Permission to use, copy, modify', result)
        self.assertIn('Cole Bemis', result)
        self.assertNotIn('<script', result)

    def test_history_and_citation_share_the_pinned_artwork_without_changing_download_identity(self):
        citation = citation_control({'revision':'a'*40}, 'source.citation.json')
        rendered = page('Example', citation + detail_link('index.html', 'Work versions'))
        self.assertIn('guavajam-icon-history', rendered)
        self.assertIn('guavajam-icon-file-json', rendered)
        self.assertIn(citation, rendered, 'site-owned citation replacement uses the same complete component')
        self.assertEqual(1, rendered.count('guavajam-icon-file-json'))
        self.assertIn('type="application/json"', rendered)
        self.assertIn('aria-label="Download official citation JSON"', rendered)
        data = json.loads(files('a_guavajam_static_website_generator').joinpath('assets/lucide.json').read_text())
        self.assertEqual('0.577.0',data['version'])
        self.assertEqual(set(data['icons']),set(data['sha256']))
        self.assertEqual(set(data['icons']),set(data['sources']))


if __name__ == '__main__': unittest.main()
