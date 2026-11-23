"""Local Lucide links preserve source text, navigation and offline rendering."""
from html.parser import HTMLParser
from importlib.resources import files
import json
import unittest

from a_guavajam_static_website_generator.icons import finish_html, icon, link_icon, search_icon_templates
from a_guavajam_static_website_generator.site import detail_link, page
from a_guavajam_static_website_generator.citation import citation_control


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.tags = []; self.feed(html)
    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


class TextClientLinks(HTMLParser):
    """Approximate a text-only client: ignore SVG and CSS, retain link text."""
    def __init__(self, html):
        super().__init__(); self.links = []; self.link = None; self.in_svg = False; self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.link = [dict(attrs).get('href'), '']
        if tag == 'svg': self.in_svg = True

    def handle_endtag(self, tag):
        if tag == 'svg': self.in_svg = False
        if tag == 'a' and self.link is not None:
            self.links.append(tuple(self.link)); self.link = None

    def handle_data(self, value):
        if self.link is not None and not self.in_svg: self.link[1] += value


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
        self.assertIn('guavajam-icon-link', result)
        self.assertIn(('#local', 'Link'), TextClientLinks(result).links)
        self.assertIn('class="guavajam-text-link"', result)
        self.assertIn('aria-label="Local"', result)
        self.assertEqual(['Paper','Email the author','Report (application/pdf)',
                          'Recording (audio/mpeg)','Supplement','Local'],
                         [a['aria-label'] for tag,a in tags if tag=='a'])
        self.assertIn('</a><span class="guavajam-link-label" aria-hidden="true">Paper</span>',result)
        self.assertEqual(['Link']*6,[text.strip() for _,text in TextClientLinks(result).links])
        for tag, attrs in tags:
            if tag == 'svg':
                self.assertEqual('true', attrs['aria-hidden']);self.assertEqual('false', attrs['focusable'])
        self.assertIn('Copyright (c)', result)
        self.assertIn('Permission to use, copy, modify', result)
        self.assertIn('Cole Bemis', result)
        self.assertNotIn('<script', result)

    def test_footnote_superscripts_and_return_links_keep_the_native_presentation(self):
        source = ('<p>Text<a href="#fn1" class="footnote-ref" id="fnref1" role="doc-noteref">'
                  '<sup>1</sup></a></p><section role="doc-endnotes"><ol>'
                  '<li id="fn1">A note. <a href="#fnref1" class="footnote-back" role="doc-backlink">↩︎</a>'
                  '</li></ol></section>')
        self.assertEqual(source, finish_html(source))
        self.assertNotIn('guavajam-icon', finish_html(source))

    def test_empty_code_line_anchors_remain_invisible(self):
        source = '<pre><code><a href="#cb1-1" aria-hidden="true" tabindex="-1"></a>print(1)</code></pre>'
        self.assertEqual(source, finish_html(source))
        self.assertNotIn('guavajam-icon', finish_html(source))

    def test_linked_headings_and_navigation_leave_labels_outside_the_click_zone(self):
        original = ('<html><body><ol class="guavajam-record-list"><li><article>'
                    '<h3><a href="edition.html"><em>Title &amp; evidence</em></a></h3>'
                    '</article></li></ol><nav class="guavajam-navigation"><ol><li>'
                    '<a href="../index.html">Work</a></li></ol></nav>'
                    '<nav class="guavajam-version-menu"><a href="contents.html">' + icon('list-tree') +
                    'Contents</a><a href="source.gfm" download>Download</a></nav></body></html>')
        result = finish_html(original)
        self.assertEqual(result, finish_html(result), 'wrapping a generated reader must remain idempotent')
        self.assertEqual([('edition.html','Link'),('../index.html','Link'),
                          ('contents.html','Link'),('source.gfm','Link')], TextClientLinks(result).links)
        self.assertIn('</a><span class="guavajam-link-label" aria-hidden="true"><em>Title &amp; evidence</em></span>', result)
        self.assertIn('aria-label="Title &amp; evidence"', result)
        self.assertIn('aria-label="Contents"', result)
        self.assertIn('aria-label="Download (text/markdown)"', result)
        self.assertEqual(1, result.count('guavajam-icon-list-tree'))
        self.assertEqual(1, result.count('guavajam-icon-file-text'))
        self.assertIn('<h3 class="guavajam-scrollline" tabindex="0">', result)

    def test_icon_only_links_keep_text_fallbacks_without_scripts(self):
        html = ('<a href="/history" aria-label="All versions">' + link_icon('history', 'Versions') + '</a>' +
                '<a href="/cite" aria-label="Cite passage">' + link_icon('quote', 'Cite') + '</a>' +
                '<a href="/json" aria-label="Download citation JSON">' + link_icon('file-json', 'Download JSON') + '</a>' +
                '<a href="#heading" aria-label="Permalink to heading">' + link_icon('link', 'Permalink') + '</a>')
        self.assertEqual([('/history', 'Versions'), ('/cite', 'Cite'),
                          ('/json', 'Download JSON'), ('#heading', 'Permalink')],
                         TextClientLinks(html).links)
        self.assertEqual(4, html.count('class="guavajam-icon-fallback"'))
        self.assertEqual(4, html.count('aria-hidden="true"'))
        self.assertIn('text &amp; more', link_icon('link', 'text & more'))
        templates = search_icon_templates()
        self.assertIn('guavajam-icon-fallback">Versions</span>', templates)
        self.assertIn('guavajam-icon-fallback">Cite</span>', templates)
        self.assertIn('guavajam-icon-fallback">Metadata</span>', templates)
        metadata = detail_link('/version/metadata.html#provenance', 'Metadata for v1', icon_name='info', fallback='Metadata')
        self.assertEqual([('/version/metadata.html#provenance', 'Metadata')], TextClientLinks(metadata).links)
        self.assertIn('aria-label="Metadata for v1"', metadata)
        self.assertNotIn('<script', html + templates)
        from a_guavajam_static_website_generator.pagination import _static_pager
        pager = _static_pager(2, 3, first_href='index.html', page_prefix='page/')
        self.assertIn(('index.html', 'Previous'), TextClientLinks(pager).links)
        self.assertIn(('page/3.html', 'Next'), TextClientLinks(pager).links)
        self.assertIn('guavajam-icon-arrow-left', pager)
        self.assertIn('guavajam-icon-arrow-right', pager)

    def test_external_icon_depends_on_site_origin_when_available(self):
        links = ('<a href="https://site.test/page">Same</a>'
                 '<a href="https://SITE.test:443/a">Same port</a>'
                 '<a href="//site.test/b">Scheme-relative</a>'
                 '<a href="http://site.test/c">Other scheme</a>'
                 '<a href="https://different.test/d">Other host</a>'
                 '<a href="../local">Relative</a>')
        original = '<html><body>' + links + '</body></html>'
        rendered = finish_html(original, site_origin='https://site.test')
        self.assertEqual(2, rendered.count('guavajam-icon-external-link'))
        self.assertEqual(5, finish_html(original).count('guavajam-icon-external-link'))
        self.assertEqual(rendered, finish_html(rendered, site_origin='https://site.test'))
        self.assertEqual(2, page('Site', '<a href="https://site.test/other">Local absolute</a>' +
                                 '<a href="https://other.test/other">Remote absolute</a>',
                                 site_origin='https://site.test').count('guavajam-icon-external-link'))
        self.assertEqual(1, finish_html('<a href="https://site.test:0/path">Other port</a>',
                                       site_origin='https://site.test').count('guavajam-icon-external-link'))
        self.assertEqual(1, finish_html('<a href="https://site.test/path">Other port</a>',
                                       site_origin='https://site.test:0').count('guavajam-icon-external-link'))
        with self.assertRaises(ValueError):
            finish_html(original, site_origin='not-an-origin')

    def test_history_and_citation_share_the_pinned_artwork_without_changing_download_identity(self):
        citation = citation_control({'revision':'a'*40}, 'source.citation.json')
        rendered = page('Example', citation + detail_link('index.html', 'Work versions'))
        self.assertIn('guavajam-icon-history', rendered)
        self.assertIn(('index.html', 'Versions'), TextClientLinks(rendered).links)
        self.assertIn('guavajam-icon-file-json', rendered)
        self.assertIn(('source.citation.json', 'Download JSON'), TextClientLinks(rendered).links)
        self.assertIn(citation, rendered, 'site-owned citation replacement uses the same complete component')
        self.assertEqual(1, rendered.count('guavajam-icon-file-json'))
        self.assertIn('type="application/json"', rendered)
        self.assertIn('aria-label="Download official citation JSON"', rendered)
        data = json.loads(files('a_guavajam_static_website_generator').joinpath('assets/lucide.json').read_text())
        self.assertEqual('0.577.0',data['version'])
        self.assertEqual('0ea8780434e2b4527dc860b18ff94883d080b59c',data['revision'])
        for name in ['list-tree', 'files', 'book-open-text', 'info', 'chevron-right', 'search']:
            self.assertIn(name, data['icons'])
            self.assertEqual(name, data['sources'][name])
            self.assertIn('<', data['icons'][name])
        self.assertEqual(set(data['icons']),set(data['sha256']))
        self.assertEqual(set(data['icons']),set(data['sources']))


if __name__ == '__main__': unittest.main()
