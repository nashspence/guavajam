"""Native-input recognition is content-based, bounded and shared at custody boundaries."""
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
from asset_policy import AssetError, verify_bytes
from source_custody import native_conversation, native_path
from manage_assets import upload
from validate_sources import git_sources, validate_repository
from verify_source_assets import verify_source_assets
from verify_edition import verify_edition
from test_assets import EditionTests, SVG, ORIGIN
import hashlib

NATIVE = (ROOT / 'tests/fixtures/capture/conversation.json').read_bytes()
ORDINARY = b'{"measurements":[1,2,3],"description":"ordinary dataset"}'


def archive(*entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in entries:
            bundle.writestr(name, raw)
    return output.getvalue()


def header_edit(raw, *, flag=None, method=None):
    data = bytearray(raw)
    for signature, flag_offset, method_offset in ((b'PK\x03\x04', 6, 8), (b'PK\x01\x02', 8, 10)):
        position = data.index(signature)
        if flag is not None:
            struct.pack_into('<H', data, position + flag_offset, flag)
        if method is not None:
            struct.pack_into('<H', data, position + method_offset, method)
    return bytes(data)


class NativeInputTests(unittest.TestCase):
    def test_native_content_is_recognized_independently_of_names_and_json_encoding(self):
        for name in ('renamed.json', 'nested/NOTES.JSON', 'data', 'directory/'):
            for raw in (NATIVE, NATIVE.decode().encode('utf-16'), NATIVE.decode().encode('utf-32')):
                with self.subTest(name=name, encoding=raw[:4]):
                    self.assertTrue(native_conversation(archive((name, raw))))
        manifest = json.dumps({'schema':'press-chatgpt-capture-reference/v1'}).encode()
        self.assertTrue(native_conversation(manifest))
        self.assertTrue(native_conversation(archive(('anything', manifest))))

    def test_nested_and_prefixed_zip_transports_do_not_hide_native_content(self):
        nested = archive(('payload', archive(('renamed', NATIVE))))
        self.assertTrue(native_conversation(nested))
        self.assertTrue(native_conversation(b'ordinary executable stub\n' + archive(('renamed', NATIVE))))
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'unfamiliar.bin'; path.write_bytes(nested)
            self.assertTrue(native_path(path, 10 * 1024 * 1024))

    def test_ordinary_datasets_and_archives_keep_their_original_bytes(self):
        for raw in (ORDINARY, ORDINARY.decode().encode('utf-16'), b'ordinary data', b'PK,measurement\n1,2\n', b'', archive(),
                    archive(('conversations.json', ORDINARY), ('figure.bin', b'opaque data')),
                    archive(('nested.zip', archive(('dataset.json', ORDINARY))))):
            with self.subTest(size=len(raw)):
                self.assertFalse(native_conversation(raw))
                self.assertEqual('application/octet-stream', verify_bytes(raw, 'bin', 'application/octet-stream'))

    def test_uninspectable_zip_is_rejected_without_leaking_member_contents(self):
        valid = archive(('private-name.json', ORDINARY))
        corrupt = bytearray(valid)
        with zipfile.ZipFile(io.BytesIO(valid)) as bundle:
            info = bundle.infolist()[0]
            start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        corrupt[start] ^= 0xFF
        for raw in (b'PK\x03\x04truncated', valid[:-15], bytes(corrupt),
                    header_edit(valid, flag=1), header_edit(valid, method=99)):
            with self.subTest(size=len(raw)), self.assertRaises(AssetError) as caught:
                native_conversation(raw)
            self.assertNotIn('private-name', str(caught.exception))
            self.assertNotIn('ordinary dataset', str(caught.exception))

    def test_archive_budgets_cover_all_members_and_nested_archives(self):
        cases = (
            ('MAX_ARCHIVE_MEMBERS', 1, archive(('a', b''), ('b', b'')), 'member'),
            ('MAX_ARCHIVE_MEMBER_BYTES', 3, archive(('not-json', b'abcd')), 'member'),
            ('MAX_ARCHIVE_TOTAL_BYTES', 5, archive(('a', b'abc'), ('b', b'def')), 'expanded'),
            ('MAX_ARCHIVE_DEPTH', 1, archive(('a.zip', archive(('b', ORDINARY)))), 'nesting'),
            ('MAX_ARCHIVE_MEMBERS', 2, archive(('inner', archive(('a', b''), ('b', b'')))), 'member'),
        )
        for setting, limit, raw, message in cases:
            with self.subTest(setting=setting), patch('source_custody.' + setting, limit), \
                    self.assertRaisesRegex(AssetError, message):
                native_conversation(raw)
        # Member size/count checks happen before any decompression.
        oversized = archive(('large', b'abcd'))
        with patch('source_custody.MAX_ARCHIVE_MEMBER_BYTES', 3), patch.object(zipfile.ZipFile, 'open') as opening, \
                self.assertRaises(AssetError):
            native_conversation(oversized)
        opening.assert_not_called()

    def test_tracked_json_and_zip_cannot_exceed_inspection_bounds_silently(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'large.json'; path.write_bytes(ORDINARY)
            with self.assertRaisesRegex(AssetError, 'inspection'):
                native_path(path, 4)
            path = Path(scratch) / 'large.bin'; path.write_bytes(archive(('data', ORDINARY)))
            with patch('source_custody.MAX_ARCHIVE_BYTES', 4), self.assertRaisesRegex(AssetError, 'inspection'):
                native_path(path, 10 * 1024 * 1024)


class CustodyBoundaryTests(EditionTests):
    def test_tracked_and_upload_boundaries_reject_renamed_and_uninspectable_archives(self):
        for raw in (archive(('renamed', NATIVE)), archive(('inner', archive(('renamed', NATIVE)))),
                    b'PK\x03\x04truncated'):
            path = self.repo / 'review-data.bin'; path.write_bytes(raw); self.git('add', path.name)
            self.assertTrue(validate_repository(self.repo, git_sources(self.repo)))
            store = Mock()
            with self.assertRaises(AssetError): upload(self.repo, path, store)
            store.upload.assert_not_called()
            self.git('rm', '-f', path.name)
        self.assertEqual([], validate_repository(self.repo, git_sources(self.repo)))

    def source(self):
        raw = archive(('renamed', NATIVE))
        url = ORIGIN + '/sha256/' + hashlib.sha256(raw).hexdigest() + '.bin'
        with (self.repo / 'works/sample/manuscript.md').open('a') as stream:
            stream.write('\n[Reviewed download](' + url + ').\n')
        self.git('add', 'works'); self.git('commit', '-qm', 'Review synthetic opaque reference')
        self.commit = self.git('rev-parse', 'HEAD')
        return lambda policy, target: (raw, 'application/octet-stream') if target == url else (SVG, 'image/svg+xml')

    def test_source_verifier_and_builder_reject_native_payload_even_with_matching_sha256(self):
        fetcher = self.source()
        with self.assertRaisesRegex(AssetError, 'native'):
            verify_source_assets(self.repo, fetcher=fetcher)
        with self.assertRaisesRegex(AssetError, 'native'):
            self.build(fetcher=fetcher)
        self.assertFalse(self.output.exists(), 'rejection must precede writing a publication artifact')

    def test_offline_verification_rejects_an_otherwise_coherent_older_package(self):
        fetcher = self.source()
        # Simulate a package accepted by the former filename-dependent guard.
        with patch('source_custody.native_conversation', return_value=False):
            self.build(fetcher=fetcher)
        with patch('socket.create_connection', side_effect=AssertionError('offline')), \
                self.assertRaisesRegex(AssetError, 'native'):
            verify_edition(self.output)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name, cls) for cls in (NativeInputTests, CustodyBoundaryTests)
                             for name in cls.__dict__ if name.startswith('test_'))
