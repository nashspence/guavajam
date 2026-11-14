#!/usr/bin/env python3
"""Plan/apply the public asset Worker and its certificate-managed Custom Domain."""
from .repository import repository_root
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .asset_policy import AssetError, load_policy
from .resources import ROOT as RESOURCES


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cf_request(account, path, token, *, body=None, content_type="application/json",
               method="GET", missing_ok=False):
    request = urllib.request.Request(
        "https://api.cloudflare.com/client/v4/accounts/" + account + "/workers/" + path,
        data=body, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise AssetError(f"Cloudflare operation failed (HTTP {exc.code}); response omitted") from None
    except OSError:
        raise AssetError("Cloudflare connection failed; request details omitted") from None
    if not data.get("success"):
        raise AssetError("Cloudflare rejected operation; response omitted")
    return data["result"]


def upload_worker(source, account, name, token, bindings):
    metadata = {"main_module": "worker.js", "compatibility_date": "2026-11-04", "bindings": bindings}
    boundary = "press" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        'Content-Type: application/json\r\n\r\n' + json.dumps(metadata) +
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n'
        'Content-Type: application/javascript+module\r\n\r\n'
    ).encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    cf_request(account, "scripts/" + name, token, body=body,
               content_type="multipart/form-data; boundary=" + boundary, method="PUT")


def deploy(root, *, apply=False):
    from .cloudflare import cloudflare_values
    from .asset_storage import credentials,S3
    policy=load_policy(root)
    if policy is None:raise AssetError('approved asset origin required')
    hostname=urlsplit(policy.origin).hostname
    worker=os.environ.get('ASSET_WORKER') or 'guavajam-assets'
    if not re.fullmatch('[a-z0-9][a-z0-9-]{1,61}',worker):raise AssetError('invalid Worker name')
    if not apply:
        print('Plan: deploy authenticated, identity-pinned S3 reads at '+policy.origin)
        print('Private digest-object sidecars share the configured S3 bucket; no API writes or credential reads')
        return
    account,token=cloudflare_values()
    store=S3(credentials(root),operation='inspect')
    domains=cf_request(account,'domains',token)
    existing=next((d for d in domains if d['hostname']==hostname),None)
    if existing and existing['service']!=worker:raise AssetError('approved hostname belongs to another Worker')
    zone=existing['zone_id'] if existing else os.environ.get('CLOUDFLARE_ZONE_ID')
    if not zone or not re.fullmatch('[a-f0-9]{32}',zone):raise AssetError('new Custom Domain requires CLOUDFLARE_ZONE_ID')
    settings=cf_request(account,'scripts/'+worker+'/settings',token,missing_ok=True)
    if settings is not None:
        previous={item['name']:item for item in settings.get('bindings',[])}
        if previous.get('ASSET_HOST',{}).get('text')!=hostname:raise AssetError('existing Worker belongs to a different approved host')
    bindings=[{'name':'ASSET_HOST','type':'plain_text','text':hostname}]
    values=store.config.values()
    for name in ('S3_ENDPOINT','S3_REGION','S3_BUCKET','S3_ADDRESSING_STYLE',
                 'S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY','S3_SESSION_TOKEN'):
        if name=='S3_SESSION_TOKEN' and not values[name]:continue
        kind='secret_text' if name in {'S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY','S3_SESSION_TOKEN'} else 'plain_text'
        bindings.append({'name':name,'type':kind,'text':values[name]})
    upload_worker(RESOURCES/'storage/worker.js',account,worker,token,bindings)
    cf_request(account,'domains',token,method='PUT',body=json.dumps({'hostname':hostname,'service':worker,'environment':'production','zone_id':zone}).encode())
    print('S3 asset gateway deployed: '+policy.origin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Deploy; otherwise print a plan without API writes")
    args = parser.parse_args()
    try:
        deploy(repository_root(), apply=args.apply)
    except (AssetError, OSError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
