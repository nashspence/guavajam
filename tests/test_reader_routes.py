"""Probe the actual citation and notice routes without network access."""
import hashlib
import io
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from asset_policy import AssetError
from reader_routes import require_redirect, verify_reader_routes


class ReaderRouteTests(unittest.TestCase):
    def setUp(self):
        self.tag = 'works/work/v1.0.0'
        self.url = 'https://press.test/' + self.tag
        self.target = 'https://github.com/owner/press/releases/tag'
        self.html = b'<h2 id="methods">Methods</h2>'
        self.manifest = {'tag': self.tag, 'record_url': self.url,
                         'source_commit': 'a' * 40, 'canonical_artifact': 'work-v1.0.0.html',
                         'html_sha256': hashlib.sha256(self.html).hexdigest()}
        self.notice = '2026-11-04 Withdrawal: The result is withdrawn.'
        self.page = ('<p>work-v1.0.0.html Source commit: ' + 'a' * 40 + '</p><ul><li>' +
                     self.notice + '</li></ul><a href="' + self.url + '/read">Read</a>')
        self.content = self.html
        self.disposition = 'inline'
        self.location = None
        self.calls = []
        self.primary_record = None
        self.enterContext(patch('reader_routes.public_response', self.response))
        self.enterContext(patch.dict('os.environ', {'RECORD_TARGET': ''}))

    @contextmanager
    def response(self, url, *args, **kwargs):
        self.calls.append(url)
        target = self.target + '/' + self.tag
        if url == self.url and self.primary_record is not None:
            status, headers, data = 200, {'Content-Type': 'text/html'}, self.primary_record
        elif url == self.url or (url == self.url + '/read' and not target.startswith('https://github.com/')):
            status, headers, data = 302, {'Location': self.location or target + url[len(self.url):]}, b''
        elif url == target:
            status, headers, data = 200, {'Content-Type': 'text/html'}, self.page.encode()
        elif url in {self.url + '/read', target + '/read'}:
            status, headers, data = 200, {'Content-Type': 'text/html', 'Content-Disposition': self.disposition}, self.content
        else:
            self.fail('unexpected destination ' + url)
        file = io.BytesIO(data)
        file.status = status
        file.getheader = headers.get
        yield file

    def check(self, **kwargs):
        verify_reader_routes(self.manifest, 'owner/press', notices=['- ' + self.notice], pending=kwargs.pop('pending', True), **kwargs)

    def test_citation_follows_exact_record_and_verifies_inline_bytes(self):
        self.check()
        self.assertEqual(self.calls, [self.url, self.target + '/' + self.tag, self.url + '/read'])
        self.location = 'https://other.test/' + self.tag
        with self.assertRaisesRegex(AssetError, 'temporary redirect'):
            self.check()

    def test_stale_and_invisible_withdrawal_are_detected(self):
        original = self.page
        for hidden in ['', '<script>' + self.notice + '</script>', '<details>' + self.notice + '</details>',
                       '<!--' + self.notice + '-->', '<div hidden><p>' + self.notice + '</p></div>']:
            self.page = original.replace(self.notice, hidden)
            with self.subTest(hidden=hidden), self.assertRaisesRegex(AssetError, 'committed notice'):
                self.check()

    def test_identifier_scope_value_and_complete_notice_history_match(self):
        original = self.page
        with self.assertRaisesRegex(AssetError, 'registered identifiers'):
            self.check(identifiers={'oclc': '42'})
        self.page += '<ul><li>OCLC number: <code>42</code></li></ul>'
        self.check(identifiers={'oclc': '42'})
        with self.assertRaisesRegex(AssetError, 'registered identifiers'):
            self.check()
        self.page = original.replace('</ul>', '<li>2026-11-05 Correction: Unexpected change.</li></ul>')
        with self.assertRaisesRegex(AssetError, 'committed notice'):
            self.check()

    def test_github_abbreviates_visible_source_commit_and_notice(self):
        sha = self.manifest['source_commit']
        self.notice += ' ' + sha
        self.page = self.page.replace('withdrawn.', 'withdrawn. ' + sha)
        self.page = self.page.replace(sha, f'<a href="/owner/press/commit/{sha}"><code>{sha[:7]}</code></a>')
        self.check()

    def test_wrong_file_and_attachment_download_fail(self):
        self.content = b'<h2>Wrong edition</h2>'
        with self.assertRaisesRegex(AssetError, 'authorized edition bytes'):
            self.check()
        self.disposition = 'attachment; filename=work.html'
        with self.assertRaisesRegex(AssetError, 'inline HTML'):
            self.check()

    def test_publisher_and_recovery_hosts_serve_the_verified_current_record(self):
        from public_record import record_html
        timestamp = '2026-11-01T12:00:00Z'
        self.manifest.update(title='Work', rights='CC0-1.0', author='A. Writer', language='en')
        approved = record_html(self.manifest, published_at=timestamp, notices=['- ' + self.notice], identifiers=None)
        self.primary_record = approved
        self.check(pending=False, published_at=timestamp)
        self.assertEqual(self.calls, [self.url, self.url + '/read'])
        self.primary_record = None
        self.target = 'https://archive.test/press'
        self.page = approved.decode()
        self.check(archive_base=self.target, pending=False, published_at=timestamp)
        record = self.target + '/' + self.tag
        self.calls.clear()
        self.check(direct_base=self.target, pending=False, published_at=timestamp)
        self.assertEqual(self.calls, [record, record + '/read'])
        for value in (timestamp, self.notice, 'CC0-1.0'):
            self.page = approved.decode().replace(value, 'changed')
            with self.subTest(value=value), self.assertRaisesRegex(AssetError, 'approved metadata'):
                self.check(direct_base=self.target, pending=False, published_at=timestamp)


class RedirectDiagnosticTests(unittest.TestCase):
    def test_denial_keeps_only_status_challenge_and_numeric_code(self):
        @contextmanager
        def denied(*args, **kwargs):
            response = io.BytesIO(b'<h1>Error 1010</h1><p>private request details</p>')
            response.status = 403
            response.getheader = {'cf-mitigated': 'challenge'}.get
            yield response
        with patch('reader_routes.public_response', denied):
            with self.assertRaises(AssetError) as caught:
                require_redirect('https://press.test/record', 'https://archive.test/record')
        message = str(caught.exception)
        self.assertIn('HTTP 403; intermediary challenge; intermediary error 1010', message)
        self.assertNotIn('private request details', message)
