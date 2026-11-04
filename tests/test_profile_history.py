import subprocess
import tempfile
import unittest
from pathlib import Path


GUARD = Path(__file__).resolve().parents[1] / "scripts/check_profile_history.sh"


class ProfileHistoryTests(unittest.TestCase):
    def test_new_versions_allowed_existing_versions_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

            def check(base):
                return subprocess.run(["sh", GUARD, base, "main"], cwd=repo,
                                      text=True, capture_output=True)

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Press test")
            git("config", "user.email", "test@example.invalid")
            css = repo / "styles/edition-v1.css"
            template = repo / "templates/edition-v1.html"
            for path in (css, template):
                path.parent.mkdir()
                path.write_text("v1\n")
            git("add", ".")
            git("commit", "-qm", "Existing profile")
            base = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", base)

            for path in (css, template):
                path.with_name(path.name.replace("v1", "v2")).write_text("v2\n")
            git("add", ".")
            git("commit", "-qm", "New profile")
            self.assertEqual(0, check(base).returncode)

            css.rename(css.with_name("edition-v3.css"))
            template.write_text("changed\n")
            git("add", "-A")
            git("commit", "-qm", "Rename stylesheet and edit existing template")
            for reference in (base, "0" * 40):
                result = check(reference)
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("D\tstyles/edition-v1.css", result.stdout)
                self.assertIn("M\ttemplates/edition-v1.html", result.stdout)
