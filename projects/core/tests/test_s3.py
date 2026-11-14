"""S3 wire contracts: plain objects first, optional APIs only by explicit choice."""
from dataclasses import replace
import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

from guavajam_core.asset_policy import AssetError
from guavajam_core.asset_storage import S3, xml_document, timestamp, md5
from guavajam_core.object_store import StoredObject, check_response, same_version
from guavajam_core.s3 import S3Config, S3Error, headers, response, NoRedirect, object_query, read_headers
from guavajam_test_support import FIXTURES
from guavajam_test_support.storage_fixture import VALUES, RAW, KEY, Response

STAMP = '2026-11-12T00:00:00.000Z'
HTTP_STAMP = 'Thu, 12 Nov 2026 00:00:00 GMT'
MODIFIED = timestamp(STAMP)
ETAG = '"opaque-multipart-etag-2"'


def metadata(*, version=None, etag=ETAG, size=len(RAW), **extra):
    return {'Content-Type': 'image/svg+xml', 'Content-Length': str(size), 'Last-Modified': HTTP_STAMP,
            'ETag': etag, **({'x-amz-version-id': version} if version is not None else {}), **extra}


def item(*, version=None):
    return StoredObject(KEY, version, ETAG, len(RAW), MODIFIED, 'image/svg+xml')


def page(*, versioned=False, versions=(None,), truncated=False, cursor=None, key=KEY):
    root = ET.Element('ListVersionsResult' if versioned else 'ListBucketResult')
    ET.SubElement(root, 'Name').text = VALUES['S3_BUCKET']
    ET.SubElement(root, 'Prefix').text = 'sha256/'
    ET.SubElement(root, 'IsTruncated').text = str(truncated).lower()
    for i, version in enumerate(versions):
        row = ET.SubElement(root, 'Version' if versioned else 'Contents')
        for name, value in [('Key', key), ('LastModified', STAMP), ('Size', str(len(RAW))), ('ETag', ETAG)]:
            ET.SubElement(row, name).text = value
        if versioned:
            ET.SubElement(row, 'VersionId').text = version
            ET.SubElement(row, 'IsLatest').text = 'true' if i == 0 else 'false'
    if cursor:
        if versioned:
            ET.SubElement(root, 'NextKeyMarker').text = key
            ET.SubElement(root, 'NextVersionIdMarker').text = cursor
        else:
            ET.SubElement(root, 'NextContinuationToken').text = cursor
    return root


class SigningTests(unittest.TestCase):
    def test_shared_independent_sigv4_vectors(self):
        for case in json.loads((FIXTURES / 'storage/signing.json').read_text()):
            with self.subTest(name=case['name']):
                config = S3Config.from_values(case['env'])
                url, signed = headers(config, case['key'], method=case['method'],
                                      query=object_query(case['version']), extra=read_headers(case['etag'], case['range']),
                                      now=datetime.datetime.fromisoformat(case['time']))
                self.assertEqual(case['url'], url)
                self.assertEqual(case['authorization'], signed['authorization'])
                self.assertNotIn(config.secret_key, url)
                self.assertNotIn(config.session_token or 'not-present', url)

    def test_endpoints_and_headers_cannot_redirect_or_inject_credentials(self):
        for endpoint in ['http://objects.example.org', 'https://user@objects.example.org',
                         'https://objects.example.org/path', 'https://objects.example.org?x=y',
                         'https://objects.example.org/#x', 'https://objects.example.org\\@evil.test',
                         'https://objects.example.org\n.evil.test', 'https://objects.example.org:99999']:
            with self.subTest(endpoint=endpoint), self.assertRaises(AssetError):
                S3Config.from_values({**VALUES, 'S3_ENDPOINT': endpoint})
        config = S3Config.from_values(VALUES)
        self.assertNotIn(VALUES['S3_SECRET_ACCESS_KEY'], repr(config))
        with self.assertRaises(AssetError):
            headers(config, KEY, extra={'Host': 'evil.test'})
        with self.assertRaises(AssetError):
            headers(config, KEY, extra={'If-Match': 'x\r\nAuthorization:bad'})
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, None, None, None))

    def test_optional_configuration_is_explicit_and_does_not_probe(self):
        with patch('guavajam_core.asset_storage.response', side_effect=AssertionError('unexpected probe')):
            store = S3(VALUES, operation='upload')
            self.assertFalse(store.versioning)
            self.assertFalse(store.object_lock)
        for changes in [{'S3_VERSIONING': 'auto'}, {'S3_OBJECT_LOCK': 'on'}, {'S3_ADDRESSING_STYLE': 'automatic'},
                        {'S3_CONDITIONAL_WRITES': 'yes'}]:
            with self.subTest(changes=changes), self.assertRaises(AssetError):
                S3({**VALUES, **changes}, operation='inspect')

    def test_http_error_body_credentials_and_redirect_location_are_not_logged(self):
        secret = VALUES['S3_SECRET_ACCESS_KEY']
        for status in [301, 403, 404, 412, 501]:
            error = urllib.error.HTTPError('https://objects.example.org', status, secret, {}, io.BytesIO(secret.encode()))
            with patch('guavajam_core.s3.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(S3Error) as raised:
                    response(S3Config.from_values(VALUES), KEY)
                self.assertEqual(status, raised.exception.status)
                self.assertNotIn(secret, str(raised.exception))


class StorageApiTests(unittest.TestCase):
    def test_plain_head_and_get_do_not_call_versioning_or_lock_apis(self):
        store = S3(VALUES, operation='inspect')
        with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata())) as request:
            stored = store.head(KEY)
        self.assertIsNone(stored.version_id)
        self.assertEqual(ETAG, stored.etag)
        self.assertIsNone(stored.retain_until)
        self.assertEqual([], request.call_args.kwargs['query'])
        request.assert_called_once()
        with patch('guavajam_core.asset_storage.response', return_value=Response(RAW, headers=metadata())) as request:
            with store.read(stored) as received:
                check_response(received, stored)
        self.assertEqual({'If-Match': ETAG}, request.call_args.kwargs['extra'])
        self.assertEqual([], request.call_args.kwargs['query'])

    def test_only_a_404_means_missing(self):
        store = S3(VALUES, operation='inspect')
        for status in [403, 412, 500, 501]:
            with patch('guavajam_core.asset_storage.response', side_effect=S3Error(status)), self.assertRaises(S3Error):
                store.head(KEY)
        with patch('guavajam_core.asset_storage.response', side_effect=S3Error(404)):
            self.assertIsNone(store.head(KEY))

    def test_head_and_readback_reject_weak_etags_identity_drift_and_encoding(self):
        store = S3(VALUES, operation='inspect')
        for changes in [{'ETag': 'W/"weak"'}, {'ETag': '*'}, {'Content-Length': '-1'}, {'Content-Encoding': 'gzip'}]:
            with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata(**changes))), self.assertRaises(AssetError):
                store.head(KEY)
        with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata(version='replacement'))), self.assertRaises(AssetError):
            store.head(KEY, 'pinned')
        for changed in [metadata(etag='"replacement"'), metadata(version='unexpected'), metadata(size=1)]:
            with self.assertRaises(AssetError):
                check_response(Response(RAW, headers=changed), item())

    def test_opaque_versions_and_null_versions_are_not_provider_specific(self):
        store = S3(VALUES, operation='inspect')
        for version in ['opaque+/=.id', 'null', None]:
            with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata(version=version))):
                stored = store.head(KEY, version)
                self.assertEqual(version, stored.version_id)
            with patch('guavajam_core.asset_storage.response', return_value=Response(RAW, headers=metadata(version=version))) as request:
                with store.read(stored, range_value='bytes=0-9'):
                    pass
                self.assertEqual(object_query(version), request.call_args.kwargs['query'])
        self.assertTrue(same_version('null', None))
        self.assertFalse(same_version('real-version', None))

    def test_unversioned_inventory_uses_list_objects_v2_and_checks_every_object(self):
        store = S3(VALUES, operation='preview')
        with patch.object(store, '_xml', return_value=(page(), None)) as listing, patch.object(store, 'head', return_value=item()):
            self.assertEqual([item()], store.inventory())
        self.assertIn(('list-type', '2'), listing.call_args.kwargs['query'])
        self.assertNotIn(('versions', ''), listing.call_args.kwargs['query'])
        with patch.object(store, '_xml', return_value=(page(), None)), patch.object(store, 'head', return_value=item(version='real')):
            with self.assertRaisesRegex(AssetError, 'S3_VERSIONING'):
                store.inventory()

    def test_version_inventory_merges_versions_and_delete_markers(self):
        store = S3({**VALUES, 'S3_VERSIONING': 'on'}, operation='preview')
        listing = page(versioned=True, versions=('old+/version',))
        listing.find('Version/IsLatest').text = 'false'
        marker = ET.SubElement(listing, 'DeleteMarker')
        for name, value in [('Key', KEY), ('VersionId', 'marker'), ('IsLatest', 'true'), ('LastModified', STAMP)]:
            ET.SubElement(marker, name).text = value
        with patch.object(store, '_xml', return_value=(listing, None)) as call, patch.object(store, 'head', return_value=item(version='old+/version')):
            objects = store.inventory()
        self.assertEqual(2, len(objects))
        self.assertTrue(objects[0].delete_marker)
        self.assertFalse(objects[1].is_latest)
        self.assertIn(('versions', ''), call.call_args.kwargs['query'])

    def test_inventory_pagination_is_bounded_complete_and_never_falls_back_on_errors(self):
        store = S3(VALUES, operation='preview')
        first = page(truncated=True, cursor='opaque+/token')
        last = page(versions=())
        with patch.object(store, '_xml', side_effect=[(first, None), (last, None)]) as listing, patch.object(store, 'head', return_value=item()):
            self.assertEqual([item()], store.inventory())
            self.assertIn(('continuation-token', 'opaque+/token'), listing.call_args.kwargs['query'])
        for bad in [page(versions=(None, None)), page(truncated=True), page(truncated=True, versions=(), cursor='repeat')]:
            with patch.object(store, '_xml', return_value=(bad, None)), patch.object(store, 'head', return_value=item()), self.assertRaises(AssetError):
                store.inventory()
        with patch.object(store, '_xml', side_effect=S3Error(403)) as listing, self.assertRaises(S3Error):
            store.inventory()
        listing.assert_called_once()
        with patch.object(store, '_xml', return_value=(page(), None)), patch.object(store, 'head', return_value=None), self.assertRaises(AssetError):
            store.inventory()

    def test_upload_uses_s3_bytes_checksum_and_receipt_not_an_etag_as_hash(self):
        store = S3(VALUES, operation='upload')
        with patch.object(store, 'head', return_value=item()), patch('guavajam_core.asset_storage.response', return_value=Response(headers={'ETag': ETAG})) as request:
            self.assertEqual(item(), store.upload(KEY, RAW, 'image/svg+xml'))
        self.assertEqual(RAW, request.call_args.kwargs['body'])
        self.assertEqual(md5(RAW), request.call_args.kwargs['extra']['Content-MD5'])
        self.assertNotIn('If-None-Match', request.call_args.kwargs['extra'])
        self.assertNotIn('x-amz-object-lock-mode', request.call_args.kwargs['extra'])

    def test_conditional_writes_are_opt_in_and_never_retried_unconditionally(self):
        store = S3({**VALUES, 'S3_CONDITIONAL_WRITES': 'on'}, operation='upload')
        with patch.object(store, 'current', return_value=None), patch.object(store, 'head', return_value=item()), patch('guavajam_core.asset_storage.response', return_value=Response(headers={'ETag': ETAG})) as request:
            store.upload(KEY, RAW, 'image/svg+xml', create_only=True)
        self.assertEqual('*', request.call_args.kwargs['extra']['If-None-Match'])
        with patch.object(store, 'current', return_value=None), patch('guavajam_core.asset_storage.response', side_effect=S3Error(412)) as request, self.assertRaises(S3Error):
            store.upload(KEY, RAW, 'image/svg+xml', create_only=True)
        request.assert_called_once()
        with patch.object(store, 'current', return_value=item()), self.assertRaises(AssetError):
            store.upload(KEY, RAW, 'image/svg+xml', create_only=True)

    def test_client_modes_limit_mutations(self):
        for operation in ['inspect', 'preview', 'delete']:
            store = S3(VALUES, operation=operation)
            with self.assertRaises(AssetError):
                store.upload(KEY, RAW, 'image/svg+xml')
        for operation in ['inspect', 'preview', 'upload', 'pin']:
            with self.assertRaises(AssetError):
                S3(VALUES, operation=operation).delete(item())

    def test_retention_and_legal_hold_are_read_only_when_enabled(self):
        store = S3({**VALUES, 'S3_VERSIONING': 'on', 'S3_OBJECT_LOCK': 'on'}, operation='inspect')
        lock = ET.fromstring('<Retention><Mode>GOVERNANCE</Mode><RetainUntilDate>2030-01-01T00:00:00Z</RetainUntilDate></Retention>')
        hold = ET.fromstring('<LegalHold><Status>ON</Status></LegalHold>')
        with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata(version='fixed'))), patch.object(store, '_xml', side_effect=[(lock, None), (hold, None)]) as api:
            stored = store.head(KEY)
        self.assertEqual('governance', stored.retention_mode)
        self.assertTrue(stored.legal_hold)
        self.assertIn(('retention', ''), api.call_args_list[0].kwargs['query'])
        with patch('guavajam_core.asset_storage.response', return_value=Response(headers=metadata(version='fixed'))), patch.object(store, '_xml', side_effect=S3Error(403)), self.assertRaises(S3Error):
            store.head(KEY)

    def test_explicit_lock_is_atomic_on_sidecar_upload_and_never_bypassed(self):
        until = int(time.time()*1000)+100000
        store = S3({**VALUES, 'S3_VERSIONING': 'on', 'S3_OBJECT_LOCK': 'on'}, operation='pin')
        held = replace(item(version='fixed'), retention_mode='compliance', retain_until=until)
        with patch.object(store, 'head', return_value=held), patch('guavajam_core.asset_storage.response', return_value=Response(headers={'ETag': ETAG, 'x-amz-version-id': 'fixed'})) as request:
            store.upload(KEY, RAW, 'image/svg+xml', retention_mode='compliance', retain_until=until)
        self.assertEqual('COMPLIANCE', request.call_args.kwargs['extra']['x-amz-object-lock-mode'])
        self.assertNotIn('x-amz-bypass-governance-retention', request.call_args.kwargs['extra'])
        with patch('guavajam_core.asset_storage.response') as request, self.assertRaises(AssetError):
            S3(VALUES, operation='pin').upload(KEY, RAW, 'image/svg+xml', retention_mode='governance', retain_until=until)
        request.assert_not_called()

    def test_delete_revalidates_unversioned_identity_and_never_bypasses_locks(self):
        for versioned, version in [(False, None), (True, 'exact+/version')]:
            store = S3({**VALUES, 'S3_VERSIONING': 'on' if versioned else 'off'}, operation='delete')
            stored = item(version=version)
            with patch.object(store, 'head', return_value=stored), patch('guavajam_core.asset_storage.response', return_value=Response(status=204)) as request:
                store.delete(stored)
            self.assertEqual(object_query(version), request.call_args.kwargs['query'])
            self.assertNotIn('extra', request.call_args.kwargs)
            for changed in [replace(stored, etag='"changed"'), replace(stored, modified_ms=MODIFIED+1000), replace(stored, legal_hold=True)]:
                with patch.object(store, 'head', return_value=changed), patch('guavajam_core.asset_storage.response') as request, self.assertRaises(AssetError):
                    store.delete(stored)
                request.assert_not_called()

    def test_xml_rejects_entities_wrong_roots_and_http_200_embedded_errors(self):
        for raw in [b'<Error><Code>AccessDenied</Code></Error>', b'broken',
                    b'<!DOCTYPE Result [<!ENTITY value "expanded">]><Result>&value;</Result>', b'\xff']:
            with self.assertRaises(AssetError):
                xml_document(raw, 'Result')

    def test_multipart_upload_streams_parts_preserves_cache_metadata_and_aborts_on_embedded_error(self):
        store = S3(VALUES, operation='upload')
        initiated = ET.fromstring('<InitiateMultipartUploadResult><Bucket>test-bucket</Bucket><Key>'+KEY+'</Key><UploadId>opaque+/id</UploadId></InitiateMultipartUploadResult>')
        complete = ET.fromstring('<CompleteMultipartUploadResult><Bucket>test-bucket</Bucket><Key>'+KEY+'</Key><ETag>'+ETAG+'</ETag></CompleteMultipartUploadResult>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'media'
            raw = b'x'*(5*1024*1024+3)
            path.write_bytes(raw)
            with patch.object(store, 'current', return_value=None), patch.object(store, '_xml', side_effect=[(initiated, None), (complete, None)]) as xml, patch.object(store, '_receipt', return_value=item()) as receipt, patch('guavajam_core.asset_storage.response', side_effect=lambda *a, **k: Response(headers={'ETag': ETAG})) as request:
                store.upload_file(KEY, path, 'image/svg+xml', part_size=5*1024*1024)
            self.assertEqual(2, request.call_count)
            self.assertEqual([5*1024*1024, 3], [len(c.kwargs['body']) for c in request.call_args_list])
            self.assertEqual(len(raw), receipt.call_args.args[3])
            self.assertIn('Cache-Control', xml.call_args_list[0].kwargs['extra'])
            with patch.object(store, 'current', return_value=None), patch.object(store, '_xml', side_effect=[(initiated, None), AssetError('embedded completion error')]), patch('guavajam_core.asset_storage.response', side_effect=lambda *a, **k: Response(headers={'ETag': ETAG}, status=204 if k['method']=='DELETE' else 200)) as request, self.assertRaisesRegex(AssetError, 'completion error'):
                store.upload_file(KEY, path, 'image/svg+xml', part_size=5*1024*1024)
            self.assertEqual('DELETE', request.call_args.kwargs['method'])
            self.assertEqual([('uploadId', 'opaque+/id')], request.call_args.kwargs['query'])
