"""Resolve the caller's repository independently of the installed tool location."""
from pathlib import Path
import subprocess
from .asset_policy import AssetError


def repository_root():
    try:
        result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AssetError('run this command inside a publication Git repository') from exc
    return Path(result.stdout.strip()).resolve()
