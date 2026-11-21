"""Bounded GitHub CLI transport for asset inventory and maintenance."""
import subprocess
import os
import re
import json
from .asset_policy import AssetError

def gh(*args, optional=False, token=None):
    env = {**os.environ, "GH_TOKEN": token} if token is not None else None
    result = subprocess.run(["gh", *args], text=True, capture_output=True, env=env)
    if result.returncode:
        if optional:
            return None
        # Show the operation and HTTP status without logging release notes, signed
        # URLs, or other arguments and response text.
        operation = args[0]
        if args[:2] == ("api", "--method"):
            operation += " " + args[2]
        elif args[0] in {"release", "run"}:
            operation += " " + args[1]
        status = re.search(r"\bHTTP [1-5][0-9]{2}\b", result.stderr)
        detail = status[0] if status else f"exit {result.returncode}"
        raise AssetError(f"gh {operation} failed ({detail})")
    return result.stdout



def api(repo, path):
    return json.loads(gh("api", f"repos/{repo}/{path}"))
