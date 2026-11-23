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
    package = sys.argv[1]
    view = package == 'a-guavajam-static-website-generator'
    extractor = 'a_chatgpt_conversation_archive_gfm_extractor'
    if package == 'a-chatgpt-conversation-archive-gfm-extractor':
        assert importlib.util.find_spec('a_guavajam_static_website_generator') is None
        module = __import__(extractor)
        assert Path(module.__file__).is_relative_to(Path(sys.prefix))
        source = root / 'conversations.json'
        source.write_text(json.dumps([{'id':'fixture','title':'Archive review','current_node':'reply',
            'mapping':{'root':{'parent':None,'message':None},'reply':{'parent':'root','message':{
            'author':{'role':'assistant'},'content':{'content_type':'text','parts':['A reviewed observation.']}}}}}]))
        assert json.loads(run(sys.executable, '-m', extractor, source, '--list')) == ['fixture','Archive review']
        output = root / 'review.md'
        run(sys.executable, '-m', extractor, source, '--conversation', 'fixture', '--output', output)
        from guavajam.supporting_gfm import load_gfm_record
        load_gfm_record(output)
        assert output.stat().st_mode & 0o777 == 0o600
        print('Installed archive extractor and GuavaJam contract independently verified.')
        return
    assert importlib.util.find_spec(extractor) is None, 'An unrelated optional authority was installed'
    if not view:
        assert importlib.util.find_spec('a_guavajam_static_website_generator') is None, 'GuavaJam installed the view'
    assert importlib.util.find_spec('guavajam_test_support') is None, 'test helpers leaked into a runtime distribution'
    import guavajam
    assert Path(guavajam.__file__).is_relative_to(Path(sys.prefix)), 'GuavaJam came from a checkout'
    resources = Path(guavajam.__file__).parent / 'resources'
    for name in ('storage/worker.js', 'storage/preview.mjs', 'storage/server.mjs',
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
        run(sys.executable, '-m', 'guavajam', 'init', 'work', '--env', str(settings),
            '--output', str(repo), '--local', cwd=root)
        run('git', 'add', 'content', cwd=repo)
        run(sys.executable, '-m', 'guavajam', 'validate-sources', cwd=repo)
        run('git', 'commit', '-S', '-m', 'Publish the initial work', cwd=repo)
        # The CLI must read the caller's repository even from a nested directory.
        run(sys.executable, '-m', 'guavajam', 'validate-sources', cwd=repo / 'content')
    for command in ('assets-qualify', 'assets-release'):
        assert '--revision' in run(sys.executable, '-m', 'guavajam', command, '--help', cwd=repo)
    sha = run('git', 'rev-parse', 'HEAD', cwd=repo)
    qualified = json.loads(run(sys.executable, '-m', 'guavajam', 'assets-qualify',
                              '--revision', sha, cwd=repo))
    assert qualified['privately_verified'] and not qualified['public_delivery_verified']
    signers = root / 'allowed-signers'
    from guavajam.publication_commit import verify
    assert verify(repo, sha, allowed_signers=signers)['revision'] == sha
    from guavajam.source_archive import export, check
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
    print('Installed ' + ('GuavaJam + generator' if view else 'GuavaJam alone') + ': signed source, resources and preservation verified.')


if __name__ == '__main__':
    main()
