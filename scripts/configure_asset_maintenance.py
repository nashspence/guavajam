#!/usr/bin/env python3
"""Plan or install the branch-deletion-only ruleset for a dedicated maintenance App."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from asset_policy import AssetError
from manage_assets import pages, repository
from publish_edition import api, gh

RULESET_NAME = "Retire branches through asset maintenance"


def deletion_ruleset(app_id):
    return {"name": RULESET_NAME, "target": "branch", "enforcement": "active",
            "bypass_actors": [{"actor_id": app_id, "actor_type": "Integration", "bypass_mode": "always"}],
            "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}},
            "rules": [{"type": "deletion"}]}


def configure(repo, slug, *, apply=False):
    app = json.loads(gh("api", "apps/" + slug))
    if app.get("permissions") != {"contents": "write", "metadata": "read"} or type(app.get("id")) is not int:
        raise AssetError("maintenance App must have only Contents write and Metadata read")
    expected = deletion_ruleset(app["id"])
    existing = pages(repo, "rulesets?per_page=100")
    main_safe = False
    matches = []
    for item in existing:
        detail = api(repo, "rulesets/" + str(item["id"]))
        if detail["name"] == RULESET_NAME:
            matches.append(detail)
        # Keep a separate rule with no bypass that unconditionally forbids main
        # deletion. The maintenance App never receives a main-protection bypass.
        refs = detail.get("conditions", {}).get("ref_name", {})
        if (detail.get("target") == "branch" and detail.get("enforcement") == "active"
                and refs.get("include") == ["refs/heads/main"] and not refs.get("exclude")
                and not detail.get("bypass_actors")
                and any(rule.get("type") == "deletion" for rule in detail["rules"])):
            main_safe = True
    if not main_safe:
        raise AssetError("main requires its independent active no-bypass deletion protection")
    if len(matches) > 1 or matches and any(matches[0].get(k) != v for k, v in expected.items()):
        raise AssetError("existing maintenance ruleset differs; resolve drift before activating")
    metadata = json.loads(gh("api", "repos/" + repo))
    if metadata["delete_branch_on_merge"]:
        raise AssetError("disable automatic branch deletion before activating maintenance")
    print(json.dumps(expected, indent=2))
    if not apply:
        print("Preview only; no repository settings changed.")
        return
    if not matches:
        result = subprocess.run(["gh", "api", "--method", "POST", f"repos/{repo}/rulesets", "--input", "-"],
                                input=json.dumps(expected), text=True, capture_output=True)
        if result.returncode:
            raise AssetError("cannot create asset maintenance ruleset")
        created = json.loads(result.stdout)
        if any(created.get(k) != v for k, v in expected.items()):
            raise AssetError("created maintenance ruleset differs from the approved plan")
    print("Branch retirement protection is active; main protection was not modified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        slug = os.environ.get("ASSET_APP", "")
        import re
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise AssetError("set ASSET_APP to the dedicated maintenance App slug")
        configure(repository(Path(__file__).resolve().parents[1]), slug, apply=args.apply)
        return 0
    except AssetError as exc:
        print("ERROR:", exc, file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("ERROR: cannot establish branch protection configuration", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
