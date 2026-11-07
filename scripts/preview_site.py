#!/usr/bin/env python3
"""Build a disposable simulated site from committed HEAD and serve it on loopback."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from asset_policy import AssetError, fetch_asset
from build_edition import build_edition, git, snapshot
from editorial_notices import edition_notices
from identifier_schema import TAG, edition_origin, valid_utc
from issuance import read_registration
from public_record import (EDITION_RECORDS, REGISTRATIONS, WORK_RECORDS, claimed_registrations,
                           edition_projection, record_html, saved_manifest, work_html)
from verify_edition import verify_edition
from edition_package import package_names

PREVIEW_SCHEMA = "press.local-preview.v1"
NOTICE = ("Local preview — simulated publication. This is not an issued edition or publication authorization. "
          "The publication time is simulated. Canonical/citation links may open the publisher origin; "
          "existing editions’ reading copies and downloads also open there. The candidate reading copy "
          "and its package retain their exact verified bytes.")


def simulated_page(raw):
    marker = b"<body><main>"
    if raw.count(marker) != 1:
        raise AssetError("unexpected public-record page structure for preview notice")
    note = ('<p role="note" lang="en"><strong>' + escape(NOTICE) + '</strong></p>').encode()
    return raw.replace(marker, marker + note, 1)


def assemble_preview(repo, commit, tag, output, *, fetcher=fetch_asset, simulated_at=None):
    """Create only a new external output; never write registrations or Git refs."""
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("TAG must be works/<slug>/vMAJOR.MINOR.PATCH")
    slug, version = match.groups()
    repo = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink() or output.is_relative_to(repo):
        raise AssetError("preview output must be a new directory outside the checkout")
    ref = subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag],
                         cwd=repo, capture_output=True)
    if ref.returncode == 0:
        raise AssetError("edition tag already exists locally; choose an unused edition version")
    if ref.returncode != 1:
        raise AssetError("cannot establish whether the edition tag already exists")
    simulated_at = simulated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not valid_utc(simulated_at):
        raise AssetError("invalid simulated publication timestamp")
    with tempfile.TemporaryDirectory(prefix=".press-preview-build-", dir=output.parent) as temp:
        staging = Path(temp)
        source = staging / "source"
        paths = set(snapshot(repo, commit, source))
        registration = REGISTRATIONS / slug / (version + ".json")
        if registration in paths:
            raise AssetError("edition version is already registered; choose an unused edition version")
        preview = staging / "preview"
        preview.mkdir()
        package = preview / "package"
        build_edition(repo, commit, tag, package, fetcher=fetcher)
        manifest = verify_edition(package, commit=commit, tag=tag)
        origin = edition_origin(source)
        published = {v: receipt for work, v, receipt in claimed_registrations(
            source, [p for p in paths if p.is_relative_to(REGISTRATIONS)])
            if work == slug and "published_at" in receipt}
        manifests, notices, routes = {}, {}, {}

        def page(route, file, raw):
            target = preview / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            routes[route] = {"file": file, "type": "text/html; charset=utf-8", "disposition": "inline"}

        for old_version, receipt in published.items():
            edition_projection(source, slug, old_version, receipt)
            manifests[old_version] = saved_manifest(source, slug, old_version, receipt)
            notices[old_version] = edition_notices(source, f"works/{slug}/{old_version}")
            route = f"/works/{slug}/{old_version}"
            page(route, f"site/works/{slug}/{old_version}/index.html",
                 (source / EDITION_RECORDS / slug / old_version / "index.html").read_bytes())
            for resource in ('read',*package_names(manifests[old_version])):
                routes[route + "/" + resource] = {"redirect": receipt["record_url"] + "/" + resource}
        work_registration = Path("registrations/works") / (slug + ".json")
        work = read_registration(source, work_registration) if work_registration in paths else {}
        if published:
            previous = work_html(origin, slug, published, manifests, notices, work)
            if (source / WORK_RECORDS / slug / "index.html").read_bytes() != previous:
                raise AssetError("committed work page is stale; regenerate it before previewing")
        # This is intentionally not an authorization/receipt schema, has no
        # issuing flag or staging-run ID, and never enters a registration path.
        simulation = {"schema": PREVIEW_SCHEMA, "preview_only": True, "tag": tag,
                      "source_commit": commit, "record_url": manifest["record_url"],
                      "published_at": simulated_at}
        published[version] = simulation
        manifests[version] = manifest
        notices[version] = edition_notices(source, tag)
        page("/" + tag, f"site/{tag}/index.html", simulated_page(record_html(
            manifest, published_at=simulated_at, notices=notices[version], identifiers=None)))
        page(f"/works/{slug}", f"site/works/{slug}/index.html", simulated_page(
            work_html(origin, slug, published, manifests, notices, work)))
        for name in package_names(manifest):
            routes["/" + tag + "/" + name] = {"file": "package/" + name,
                # These are downloads, including on browsers that display known
                # media types despite an attachment disposition.
                "type": "application/octet-stream",
                "disposition": f'attachment; filename="{name}"'}
        routes["/" + tag + "/read"] = {"file": "package/" + manifest["canonical_artifact"],
            "type": "text/html; charset=utf-8", "disposition": "inline"}
        home = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Press local preview</title>'
                '<body><main><h1>Press local preview</h1><p>' + escape(NOTICE) + '</p><ul>' +
                ''.join(f'<li><a href="{path}">{label}</a></li>' for path, label in (
                    (f"/works/{slug}", "Work page"), ("/" + tag, "Candidate edition record"),
                    ("/" + tag + "/read", "Candidate reading copy"))) + '</ul></main></body></html>')
        page("/", "site/index.html", home.encode())
        metadata = {**simulation, "routes": routes}
        (preview / "preview.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        preview.rename(output)
    return metadata


def preview_handler(root, routes):
    root = root.resolve()
    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not copy arbitrary request headers/paths into operator logs.

        def serve(self, head=False):
            port = self.server.server_address[1]
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_error(403, "Loopback Host required")
                return
            route = routes.get(self.path)
            if route is None:
                self.send_error(404, "No preview route")
                return
            if "redirect" in route:
                self.send_response(302)
                self.send_header("Location", route["redirect"])
                self.send_header("Content-Length", "0")
            else:
                path = root / route["file"]
                if path.resolve() != path.absolute() or not path.is_file() or not path.is_relative_to(root):
                    self.send_error(404, "Missing preview file")
                    return
                self.send_response(200)
                self.send_header("Content-Type", route["type"])
                self.send_header("Content-Disposition", route["disposition"])
                self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("X-Press-Preview", "simulated; not-issued")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head and "file" in route:
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile)

        def do_GET(self):
            self.serve()

        def do_HEAD(self):
            self.serve(head=True)

    return PreviewHandler


@contextmanager
def preview_server(root, metadata, port=8000):
    if not 0 <= port <= 65535:
        raise AssetError("PORT must be between 0 and 65535 (0 selects a free port)")
    server = ThreadingHTTPServer(("127.0.0.1", port), preview_handler(root, metadata["routes"]))
    server.daemon_threads = True
    try:
        yield server
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--port", type=int, default=8000)
    mode.add_argument("--output", type=Path, help="retain a new external directory; do not start a server")
    args = parser.parse_args()
    try:
        repo = Path.cwd()
        commit = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        print("LOCAL PREVIEW — SIMULATED PUBLICATION; NOT ISSUED", flush=True)
        print(f"Source: committed HEAD {commit}. Uncommitted edits are not included.", flush=True)
        if args.output:
            assemble_preview(repo, commit, args.tag, args.output)
            print(f"Disposable preview built at {args.output}. No server started.")
            return 0
        with tempfile.TemporaryDirectory(prefix="press-preview-") as temp:
            root = Path(temp) / "preview"
            metadata = assemble_preview(repo, commit, args.tag, root)
            with preview_server(root, metadata, args.port) as server:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                print(NOTICE, flush=True)
                print(f"Work: {base}/{args.tag.rsplit('/', 1)[0]}", flush=True)
                print(f"Record: {base}/{args.tag}", flush=True)
                print(f"Read: {base}/{args.tag}/read", flush=True)
                print("Ctrl-C stops the server and removes the temporary preview.", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview stopped; temporary output removed.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
