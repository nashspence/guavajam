"""Run outside the checkout with only the selected built distributions installed."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.PIPE).strip()


def main():
    root = Path.cwd()
    repo = root / 'publisher'
    view = sys.argv[1] == 'view'
    if not view:
        assert importlib.util.find_spec('guavajam') is None, 'core installed the view'
    assert importlib.util.find_spec('guavajam_test_support') is None, 'test helpers leaked into a runtime distribution'
    import guavajam_core
    assert Path(guavajam_core.__file__).is_relative_to(Path(sys.prefix)), 'core came from a checkout'
    resources = Path(guavajam_core.__file__).parent / 'resources'
    for name in ('storage/worker.js', 'chatgpt-capture/manifest.json',
                 'templates/work/manuscript.md', 'schemas/press-site-v1.schema.json'):
        assert (resources / name).is_file(), name
    if not repo.exists():
        repo.mkdir()
        run('git', 'init', '-q', '-b', 'main', cwd=repo)
        run('git', 'config', 'user.name', 'Artifact Publisher', cwd=repo)
        run('git', 'config', 'user.email', 'fixture@example.org', cwd=repo)
        run(sys.executable, '-m', 'guavajam_core', 'new-work', 'artifact-work',
            '--title', 'Artifact Work', '--author', 'Example Author', cwd=repo)
        run('git', 'add', '-A', cwd=repo)
        run(sys.executable, '-m', 'guavajam_core', 'validate-sources', cwd=repo)
        run('git', 'commit', '-qm', 'Reviewed source', cwd=repo)
        key = root / 'signing-key'
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        run('git', 'config', 'gpg.format', 'ssh', cwd=repo)
        run('git', 'config', 'user.signingkey', str(key), cwd=repo)
        run('git', 'commit', '--allow-empty', '-S', '-m', 'Publish', '-m',
            'Press-Publication: 2\nPress-Work: works/artifact-work/manuscript.md\nPress-Published: 2026-11-13', cwd=repo)
        (root / 'allowed-signers').write_text('fixture namespaces="git" ' + key.with_suffix('.pub').read_text())
        # The CLI must read the caller's repository even from a nested directory.
        run(sys.executable, '-m', 'guavajam_core', 'validate-sources', cwd=repo / 'works')
        run(sys.executable, '-m', 'guavajam_core', 'resources', 'capture', '--output', str(root / 'capture'), cwd=repo)
        assert (root / 'capture/capture.mjs').is_file()
    sha = run('git', 'rev-parse', 'HEAD', cwd=repo)
    signers = root / 'allowed-signers'
    from guavajam_core.publication_commit import verify
    assert verify(repo, sha, allowed_signers=signers)['revision'] == sha
    from guavajam_core.source_archive import export, check
    archive = root / 'archive'
    if not archive.exists():
        export(repo, sha, archive, allowed_signers=signers)
    check(archive, sha, allowed_signers=signers)
    if view:
        from guavajam.renderer import render
        render(repo, sha, 'artifact-work', root / 'view', allowed_signers=signers)
        assert 'Artifact Work' in (root / 'view/index.html').read_text()
        assert (Path(__import__('guavajam').__file__).parent / 'styles/reading.css').read_text() in (root / 'view/index.html').read_text()
    print('Installed ' + ('core + view' if view else 'core alone') + ': signed source, resources and preservation verified.')


if __name__ == '__main__':
    main()
