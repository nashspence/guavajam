"""Check that Git preserves the renderer's tracked JavaScript fingerprint inputs."""

import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RENDERER_ROOTS = (
    "projects/core/src/guavajam_core",
    "projects/view/src/guavajam",
)
JS_SUFFIXES = {".js", ".mjs"}


def git(repo, *args, **kwargs):
    return subprocess.check_output(("git", "-C", str(repo), *args), **kwargs)


class RendererCheckoutTests(unittest.TestCase):
    def test_javascript_fingerprint_inputs_keep_checkout_bytes(self):
        head = git(ROOT, "rev-parse", "HEAD").decode().strip()
        paths = git(ROOT, "ls-tree", "-r", "-z", "--name-only", head)
        tracked = {PurePosixPath(os.fsdecode(p)) for p in paths.split(b"\0") if p}
        selected = {p for p in tracked if p.suffix in JS_SUFFIXES
                    and any(p.is_relative_to(root) for root in RENDERER_ROOTS)}
        self.assertTrue(selected, "no tracked JavaScript fingerprint inputs")
        attributes = {
            parent / ".gitattributes"
            for path in selected
            for parent in path.parents
            if parent / ".gitattributes" in tracked
        }
        blobs = {p: git(ROOT, "cat-file", "blob", f"{head}:{p.as_posix()}")
                 for p in sorted(selected | attributes)}
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")

        # Preserve the index's committed bytes, without running git add's filters
        # or changing the developer's working copy, index or Git configuration.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            git(scratch, "init", "-q", env=env)
            for path, raw in blobs.items():
                oid = git(scratch, "hash-object", "-w", "--stdin", input=raw, env=env).decode().strip()
                git(scratch, "update-index", "--add", "--cacheinfo",
                    f"100644,{oid},{path.as_posix()}", env=env)
                if path in attributes:
                    working_path = scratch / path
                    working_path.parent.mkdir(parents=True, exist_ok=True)
                    working_path.write_bytes(raw)
            for mode in ("false", "input", "true"):
                git(scratch, "-c", f"core.autocrlf={mode}", "checkout-index", "--force", "--",
                    *(p.as_posix() for p in sorted(selected)), env=env)
                drift = [p.as_posix() for p in sorted(selected)
                         if (scratch / p).read_bytes() != blobs[p]]
                with self.subTest(autocrlf=mode):
                    self.assertEqual([], drift, f"core.autocrlf={mode} changed tracked bytes: "
                                                + ", ".join(drift))