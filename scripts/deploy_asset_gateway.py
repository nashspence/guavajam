#!/usr/bin/env python3
"""Plan/apply the public asset Worker and its certificate-managed Custom Domain."""
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

from asset_policy import AssetError, load_policy


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
    policy = load_policy(root)
    if policy is None:
        raise AssetError("approved asset origin required")
    values = {}
    for name in ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID", "B2_PUBLIC_BASE"]:
        if not os.environ.get(name):
            raise AssetError("missing operator variable: " + name)
        values[name] = os.environ[name]
    parsed = urlsplit(values["B2_PUBLIC_BASE"])
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".backblazeb2.com")
            or parsed.netloc != parsed.hostname or parsed.query or parsed.fragment
            or not re.fullmatch(r"/file/[A-Za-z0-9][A-Za-z0-9.-]*", parsed.path)):
        raise AssetError("B2_PUBLIC_BASE must be a public B2 HTTPS download origin plus /file/<bucket>")
    if not all(re.fullmatch(r"[a-f0-9]{32}", values[name])
               for name in ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID"]):
        raise AssetError("invalid Cloudflare deployment identifiers")
    hostname = urlsplit(policy.origin).hostname
    if not apply:
        print("Plan: upload storage/worker.js as press-assets and attach " + hostname)
        print("Bindings: ASSET_HOST (approved hostname), B2_PUBLIC_BASE (operator configuration)")
        return
    account, token = values["CLOUDFLARE_ACCOUNT_ID"], values["CLOUDFLARE_API_TOKEN"]
    domains = cf_request(account, "domains", token)
    if any(domain["hostname"] == hostname and domain["service"] != "press-assets" for domain in domains):
        raise AssetError("approved hostname is already attached to another Worker")
    settings = cf_request(account, "scripts/press-assets/settings", token, missing_ok=True)
    if settings is not None:
        bindings = {item["name"]: item for item in settings.get("bindings", [])}
        if bindings.get("ASSET_HOST", {}).get("text") != hostname:
            raise AssetError("existing press-assets Worker belongs to a different hostname")
    bindings = [
        {"name": "ASSET_HOST", "type": "plain_text", "text": hostname},
        {"name": "B2_PUBLIC_BASE", "type": "plain_text", "text": values["B2_PUBLIC_BASE"]},
    ]
    upload_worker(root / "storage/worker.js", account, "press-assets", token, bindings)
    cf_request(account, "domains", token, method="PUT", body=json.dumps({
        "hostname": hostname, "service": "press-assets", "environment": "production",
        "zone_id": values["CLOUDFLARE_ZONE_ID"],
    }).encode())
    print("Asset gateway deployed: " + policy.origin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Deploy; otherwise print a plan without API writes")
    args = parser.parse_args()
    try:
        deploy(Path(__file__).resolve().parents[1], apply=args.apply)
    except (AssetError, OSError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
