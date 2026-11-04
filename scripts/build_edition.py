#!/usr/bin/env python3
"""Build a verified standalone edition from an exact committed source snapshot.

This command does not create tags, releases, or other published state.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from asset_policy import (AssetError, MAX_ASSETS, MAX_TOTAL_BYTES, MIME, fetch_asset,
                          check_html_size, load_policy, verify_bytes)
from identifier_schema import TAG, edition_record_url
from validate_sources import parse_document, plain_text, validate_repository, walk

COMMIT = re.compile(r"[0-9a-f]{40}\Z")

from render_profile import (PROFILE, STYLESHEET_PATH, TEMPLATE_PATH,
                            canonical_template, prepare_presentation_ast)



def git(root: Path, *args: str, binary=False):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise AssetError("Git could not resolve the requested committed snapshot")
    return result.stdout if binary else result.stdout.decode().strip()


def snapshot(repo: Path, commit: str, destination: Path) -> list[Path]:
    if not COMMIT.fullmatch(commit) or git(repo, "rev-parse", "--verify", commit + "^{commit}") != commit:
        raise AssetError("source commit must be an existing full 40-character commit SHA")
    archive = git(repo, "archive", "--format=tar", commit, binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        if any(not (m.isfile() or m.isdir()) for m in members):
            raise AssetError("source snapshot may contain only regular files and directories")
        tar.extractall(destination, filter="data")
        return [Path(m.name) for m in members if m.isfile()]


def materialize_images(ast: dict, root: Path, manuscript: Path, commit: str,
                       policy, build: Path, fetcher=fetch_asset) -> list[dict]:
    records = {}
    total = 0
    for node in walk(ast["blocks"]):
        if node.get("t") != "Image":
            continue
        target = node["c"][2][0]
        if target not in records:
            if len(records) >= MAX_ASSETS:
                raise AssetError("edition exceeds 128 distinct images")
            if ":" in target or target.startswith("//"):
                if policy is None:
                    raise AssetError("no approved asset origin configured")
                digest, extension = policy.parse(target)
                raw, media_type = fetcher(policy, target)
                # Verify again at the trust boundary, including injected fetchers.
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise AssetError("asset SHA-256 does not match URL")
                verify_bytes(raw, extension, media_type)
                source = target
                kind = "https"
            else:
                parsed = urlsplit(target)
                if parsed.query or parsed.fragment:
                    raise AssetError("local edition images may not have queries or fragments")
                local = (root / manuscript.parent / unquote(parsed.path)).resolve()
                try:
                    relative = local.relative_to(root)
                except ValueError as exc:
                    raise AssetError("local image escapes source snapshot") from exc
                if relative.parts[:3] != ("works", manuscript.parts[1], "assets") or local.is_symlink():
                    raise AssetError("local image must belong to this work's assets")
                extension = local.suffix[1:]
                raw = local.read_bytes()
                media_type = verify_bytes(raw, extension, MIME.get(extension, ""))
                digest = hashlib.sha256(raw).hexdigest()
                source = f"git:{commit}:{relative.as_posix()}"
                kind = "git"
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise AssetError("edition exceeds 100 MiB total image bytes")
            destination = "verified/" + digest + "." + extension
            (build / destination).parent.mkdir(parents=True, exist_ok=True)
            (build / destination).write_bytes(raw)
            records[target] = {
                "source_url": source, "source_kind": kind, "sha256": digest,
                "byte_count": len(raw), "media_type": media_type, "destination": destination,
            }
        node["c"][2][0] = records[target]["destination"]
    return sorted(records.values(), key=lambda record: record["source_url"])


def build_edition(repo: Path, commit: str, tag: str, output: Path, *, fetcher=fetch_asset):
    match = TAG.fullmatch(tag)
    if match is None:
        raise AssetError("tag must be works/<slug>/vMAJOR.MINOR.PATCH")
    slug, version = match.groups()
    if output.exists():
        raise AssetError("output directory must not already exist")
    with tempfile.TemporaryDirectory(prefix="press-edition-") as temp:
        temp = Path(temp)
        root = temp / "source"
        paths = snapshot(repo, commit, root)
        tag_ref = subprocess.run(["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
                                 cwd=repo, capture_output=True, text=True)
        if tag_ref.returncode == 0 and tag_ref.stdout.strip() != commit:
            raise AssetError("existing edition tag does not match source commit")
        errors = validate_repository(root, paths, enforce_version=True)
        if errors:
            raise AssetError("source snapshot failed validation:\n" + "\n".join(errors))
        manuscript = Path("works") / slug / "manuscript.md"
        if manuscript not in paths:
            raise AssetError("tag must identify an existing manuscript")
        ast, errors = parse_document(root / manuscript, manuscript)
        if errors:
            raise AssetError("\n".join(errors))
        build = temp / "build"
        build.mkdir()
        records = materialize_images(ast, root, manuscript, commit, load_policy(root), build, fetcher)
        # Keep a local-reference build tree for inspection; only verified files are
        # read for embedding. Pandoc gets data URIs, so it has no resource to fetch.
        (build / "document.json").write_text(json.dumps(ast), encoding="utf-8")
        embedded = prepare_presentation_ast(copy.deepcopy(ast))
        embedded_size = 0
        for node in walk(embedded["blocks"]):
            if node.get("t") == "Image":
                local = build / node["c"][2][0]
                extension = local.suffix[1:]
                # Repeated references each occupy space in the standalone HTML.
                embedded_size += 4 * ((local.stat().st_size + 2) // 3)
                check_html_size(embedded_size)
                raw = local.read_bytes()
                node["c"][2][0] = "data:" + MIME[extension] + ";base64," + base64.b64encode(raw).decode("ascii")
        embedded["meta"]["pagetitle"] = embedded["meta"]["title"]
        embedded["meta"]["lang"] = embedded["meta"]["language"]
        embedded["meta"]["edition"] = {"t": "MetaString", "c": version}
        record_url = edition_record_url(root, tag)
        embedded["meta"]["record_url"] = {"t": "MetaString", "c": record_url}
        rights_file = manuscript.parent / "editions" / (version + ".rights.txt")
        rights_statement = ""
        if rights_file in paths:
            rights_statement = (root / rights_file).read_text(encoding="utf-8").removesuffix("\n")
            embedded["meta"]["rights_statement"] = {"t": "MetaString", "c": rights_statement}
        embedded["meta"]["source_commit"] = {"t": "MetaString", "c": commit}
        builder = Path(__file__).resolve().parents[1]
        builder_commit = git(builder, "rev-parse", "--verify", "HEAD^{commit}")
        if not COMMIT.fullmatch(builder_commit):
            raise AssetError("builder commit cannot be established")
        # Provenance must identify the *actual committed builder*, not merely
        # the repository HEAD while uncommitted presentation changes are used.
        for path in [TEMPLATE_PATH, STYLESHEET_PATH, "scripts/build_edition.py",
                     "scripts/asset_policy.py", "scripts/editorial_notices.py", "scripts/identifier_schema.py",
                     "scripts/render_profile.py", "scripts/validate_sources.py", "scripts/verify_edition.py"]:
            committed = git(builder, "show", f"{builder_commit}:{path}", binary=True)
            if committed != (builder / path).read_bytes():
                raise AssetError("publisher builder files must match the recorded builder commit: " + path)
        template, template_digest, stylesheet_digest = canonical_template(builder)
        (build / "template.html").write_text(template, encoding="utf-8")
        result = subprocess.run([
            "pandoc", "--fail-if-warnings", "--from=json", "--to=html5", "--standalone", "--mathml",
            "--template=" + str(build / "template.html"),
        ], input=json.dumps(embedded), text=True, capture_output=True, cwd=build)
        if result.returncode:
            raise AssetError("edition HTML rendering failed: " + result.stderr.strip())
        raw_html = result.stdout.encode("utf-8")
        check_html_size(len(raw_html))
        filename = slug + "-" + version + ".html"
        manifest = {
            "schema": "press.edition.v1", "source_commit": commit, "tag": tag, "record_url": record_url,
            "title": plain_text(ast["meta"]["title"]["c"]),
            "rights": plain_text(ast["meta"]["rights"]["c"]),
            "pandoc": "3.12.1", "asset_origin": load_policy(root).origin if load_policy(root) else None,
            "presentation_profile": PROFILE, "builder_commit": builder_commit,
            "template_path": TEMPLATE_PATH, "template_sha256": template_digest,
            "stylesheet_path": STYLESHEET_PATH, "stylesheet_sha256": stylesheet_digest,
            "canonical_artifact": filename, "html_sha256": hashlib.sha256(raw_html).hexdigest(),
            "html_byte_count": len(raw_html), "assets": records,
        }
        if rights_statement:
            manifest["rights_statement"] = rights_statement
        notes = manuscript.parent / "editions" / (version + ".md")
        if notes in paths:
            manifest["initial_notes"] = (root / notes).read_bytes().decode("utf-8")
        packaged = temp / "packaged"
        packaged.mkdir()
        (packaged / filename).write_bytes(raw_html)
        (packaged / "provenance.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        sums = "".join(hashlib.sha256((packaged / name).read_bytes()).hexdigest() + "  " + name + "\n"
                       for name in [filename, "provenance.json"])
        (packaged / "SHA256SUMS").write_text(sums, encoding="ascii")
        from verify_edition import verify_edition
        verify_edition(packaged, commit=commit, tag=tag)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Copy only the final package, after every verification has passed.
        import shutil
        shutil.copytree(packaged, output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_edition(Path(__file__).resolve().parents[1], args.commit, args.tag, args.output)
    except (AssetError, OSError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(f"Verified edition: {manifest['canonical_artifact']} ({len(manifest['assets'])} assets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
