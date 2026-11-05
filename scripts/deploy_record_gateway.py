"""Plan/apply the publisher-controlled edition-record address and its TLS domain."""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from asset_policy import AssetError, HOST, MAX_HTML_BYTES
from deploy_asset_gateway import cf_request, upload_worker
from identifier_schema import SLUG, TAG, edition_origin


def target_base(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or not HOST.fullmatch(parsed.hostname)
            or parsed.netloc != parsed.hostname or parsed.query or parsed.fragment
            or not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*', parsed.path)
            or any(part in {'.', '..'} for part in parsed.path.split('/'))):
        raise AssetError('RECORD_TARGET requires a canonical HTTPS archive base without credentials or query')
    return value


def deploy(root, *, apply=False):
    origin = edition_origin(root)
    hostname = urlsplit(origin).hostname
    name = os.environ.get('RECORD_WORKER') or 'press-records'
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise AssetError('RECORD_WORKER requires a lowercase Worker name')
    base = target_base(os.environ.get('RECORD_TARGET') or 'https://github.com/nashspence/press/releases/tag')
    values = {}
    for key in ['CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_ZONE_ID']:
        values[key] = os.environ.get(key, '')
        if not values[key]:
            raise AssetError('missing operator variable: ' + key)
    if not all(re.fullmatch(r'[a-f0-9]{32}', values[key])
               for key in ['CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_ZONE_ID']):
        raise AssetError('invalid Cloudflare deployment identifiers')
    bindings = [
        {'name': 'RECORD_HOST', 'type': 'plain_text', 'text': hostname},
        {'name': 'RECORD_TARGET_BASE', 'type': 'plain_text', 'text': base},
        {'name': 'MAX_HTML_BYTES', 'type': 'plain_text', 'text': str(MAX_HTML_BYTES)},
        {'name': 'RECORD_SLUG_PATTERN', 'type': 'plain_text', 'text': SLUG.pattern},
        {'name': 'RECORD_TAG_PATTERN', 'type': 'plain_text', 'text': TAG.pattern},
    ]
    if not apply:
        print(f'Plan: deploy records/worker.js as {name}; attach {origin}; use archive target {base}')
        return
    account, token = values['CLOUDFLARE_ACCOUNT_ID'], values['CLOUDFLARE_API_TOKEN']
    domains = cf_request(account, 'domains?hostname=' + hostname, token)
    if any(item['hostname'] == hostname and item['service'] != name for item in domains):
        raise AssetError('edition origin is already attached to another Worker')
    settings = cf_request(account, f'scripts/{name}/settings', token, missing_ok=True)
    if settings is not None:
        existing = {item['name']: item for item in settings.get('bindings', [])}
        if existing.get('RECORD_HOST', {}).get('text') != hostname:
            raise AssetError('existing record Worker belongs to a different hostname')
    upload_worker(root / 'records/worker.js', account, name, token, bindings)
    cf_request(account, 'domains', token, method='PUT', body=json.dumps({
        'hostname': hostname, 'service': name, 'environment': 'production',
        'zone_id': values['CLOUDFLARE_ZONE_ID'],
    }).encode())
    print('Edition record gateway deployed: ' + origin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Deploy; otherwise print a plan without API writes')
    args = parser.parse_args()
    try:
        deploy(Path(__file__).resolve().parents[1], apply=args.apply)
    except (AssetError, OSError, ValueError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
