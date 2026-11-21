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
    view = sys.argv[1] == 'a-guavajam-static-website-generator'
    if not view:
        assert importlib.util.find_spec('a_guavajam_static_website_generator') is None, 'core installed the view'
    assert importlib.util.find_spec('guavajam_test_support') is None, 'test helpers leaked into a runtime distribution'
    import guavajam_core
    assert Path(guavajam_core.__file__).is_relative_to(Path(sys.prefix)), 'core came from a checkout'
    resources = Path(guavajam_core.__file__).parent / 'resources'
    for name in ('storage/worker.js', 'storage/preview.mjs', 'storage/server.mjs', 'chatgpt-capture/manifest.json',
                 'templates/work/manuscript.md', 'commands.mk'):
        assert (resources / name).is_file(), name
    if not repo.exists():
        key = root / 'signing-key'
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        config = root / 'publisher.gitconfig'
        for setting, value in [('user.name', 'Artifact Publisher'), ('user.email', 'fixture@example.org'),
                               ('gpg.format', 'ssh'), ('user.signingkey', str(key))]:
            run('git', 'config', '--file', str(config), setting, value)
        os.environ['GIT_CONFIG_GLOBAL'] = str(config)
        os.environ['GIT_CONFIG_SYSTEM'] = os.devnull
        signers = root / 'allowed-signers'
        signers.write_text('fixture ' + key.with_suffix('.pub').read_text())
        settings = root / 'bootstrap.env'
        settings.write_text('GUAVAJAM_REPO=example/publisher\n'
                            'GUAVAJAM_TITLE=Artifact Work\n'
                            'GUAVAJAM_AUTHOR=Example Author\n'
                            'GUAVAJAM_ALLOWED_SIGNERS=' + str(signers) + '\n'
                            'GUAVAJAM_TOOL_REF=' + '0' * 40 + '\n')
        run(sys.executable, '-m', 'guavajam_core', 'init', 'work', '--env', str(settings),
            '--output', str(repo), '--local', cwd=root)
        run('git', 'add', 'content', cwd=repo)
        run(sys.executable, '-m', 'guavajam_core', 'validate-sources', cwd=repo)
        run('git', 'commit', '-S', '-m', 'Publish the initial work', cwd=repo)
        # The CLI must read the caller's repository even from a nested directory.
        run(sys.executable, '-m', 'guavajam_core', 'validate-sources', cwd=repo / 'content')
        run(sys.executable, '-m', 'guavajam_core', 'resources', 'capture', '--output', str(root / 'capture'), cwd=repo)
        assert (root / 'capture/capture.mjs').is_file()
        for legal in ['LICENSES/Apache-2.0.txt', 'REUSE.toml']:
            assert (root / 'capture' / legal).read_bytes() == (resources / 'chatgpt-capture' / legal).read_bytes()
    for command in ('assets-qualify', 'assets-release'):
        assert '--revision' in run(sys.executable, '-m', 'guavajam_core', command, '--help', cwd=repo)
    sha = run('git', 'rev-parse', 'HEAD', cwd=repo)
    qualified = json.loads(run(sys.executable, '-m', 'guavajam_core', 'assets-qualify',
                              '--revision', sha, cwd=repo))
    assert qualified['privately_verified'] and not qualified['public_delivery_verified']
    signers = root / 'allowed-signers'
    from guavajam_core.publication_commit import verify
    assert verify(repo, sha, allowed_signers=signers)['revision'] == sha
    from guavajam_core.source_archive import export, check
    archive = root / 'archive'
    if not archive.exists():
        export(repo, sha, archive, allowed_signers=signers)
    check(archive, sha, allowed_signers=signers)
    if view:
        from a_guavajam_static_website_generator.preview import build_preview
        preview = build_preview(repo, sha, root / 'draft-preview')
        assert not preview['publication'] and (root / 'draft-preview/PREVIEW.json').is_file()
        assert '--serve' in run(str(Path(sys.executable).with_name('a-guavajam-static-website-generator')), 'preview', '--help', cwd=repo)
        from a_guavajam_static_website_generator.renderer import render
        render(repo, sha, root / 'view', allowed_signers=signers)
        assert 'Artifact Work' in (root / 'view/index.html').read_text()
        assert (Path(__import__('a_guavajam_static_website_generator').__file__).parent / 'styles/reading.css').read_text() in (root / 'view/index.html').read_text()
    print('Installed ' + ('core + view' if view else 'core alone') + ': signed source, resources and preservation verified.')


if __name__ == '__main__':
    main()
