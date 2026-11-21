"""One-time GitHub repository bootstrap; never part of ordinary authoring or publication."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

from .resources import ROOT as RESOURCES

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
KEY = re.compile(r'[A-Z][A-Z0-9_]*\Z')
CHECKPOINT = {'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}}


class BootstrapError(ValueError):
    pass


def dotenv(path):
    """Read a small literal key=value file; never evaluate shell code or expansion."""
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 32768:
        raise BootstrapError('environment file must be a regular file under 32 KiB')
    values = {}
    for line in path.read_text('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise BootstrapError('environment requires literal KEY=value lines')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if not KEY.fullmatch(key) or key in values:
            raise BootstrapError('invalid or repeated environment key')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if '\x00' in value or '\n' in value or '\r' in value or '$(' in value or '`' in value:
            raise BootstrapError('environment values must be literal, not shell expressions')
        values[key] = value
    return values, path


def required(env, name):
    value = env.get(name, '')
    if not value or value.isspace():
        raise BootstrapError('missing ' + name + ' in environment file')
    return value


def command(*args, cwd=None, env=None, data=None):
    try:
        result = subprocess.run(args, cwd=cwd, env=env, input=data, capture_output=True,
                                text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError('required Git/GitHub command is unavailable') from exc
    if result.returncode:
        raise BootstrapError('command failed: ' + ' '.join(args[:3]) +
                             ' (check Git signing, GitHub permissions and repository state)')
    return result.stdout.strip()


def git(repo, *args, data=None):
    return command('git', *args, cwd=repo, data=data)


def push(repo, args, env):
    # GitHub CLI credentials are local bootstrap inputs, never written into a remote URL.
    return command('git', '-c', 'credential.helper=',
                   '-c', 'credential.https://github.com.helper=!gh auth git-credential',
                   'push', *args, cwd=repo, env=env)


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(content)


def from_template(name, ref):
    source = (RESOURCES / 'templates/github' / name).read_text('utf-8')
    if source.count('@@GUAVAJAM_REF@@') != 1:
        raise BootstrapError('invalid packaged workflow template')
    return source.replace('@@GUAVAJAM_REF@@', ref)


def authorized_signers(value, env_file, repo):
    file = Path(value).expanduser()
    if not file.is_absolute():
        file = env_file.parent / file
    file = file.resolve(strict=True)
    if file.is_relative_to(repo) or not file.is_file() or not (0 < file.stat().st_size <= 65536):
        raise BootstrapError('SSH allowed-signers file must be external, nonempty and bounded')
    data = file.read_text('utf-8')
    if not all(line.count(' ssh-') == 1 or line.startswith('#') or not line.strip()
               for line in data.splitlines()):
        raise BootstrapError('expected SSH allowed-signers entries, not private keys')
    return file, data


def create_checkpoint(repo):
    """Create the independent baseline branch with Git objects, never a view artifact."""
    raw = json.dumps(CHECKPOINT, sort_keys=True, separators=(',', ':')) + '\n'
    blob = git(repo, 'hash-object', '-w', '--stdin', data=raw)
    tree = git(repo, 'mktree', '-z', data='100644 blob ' + blob + '\tSTATE.json\0')
    sha = git(repo, 'commit-tree', tree, '-m', 'Initialize verification checkpoint')
    git(repo, 'update-ref', 'refs/heads/guavajam-state', sha)


def make_work(repo, env, env_file, ref):
    title = required(env, 'GUAVAJAM_TITLE')
    author = required(env, 'GUAVAJAM_AUTHOR')
    if any('\n' in value or '\r' in value for value in (title, author)):
        raise BootstrapError('bibliographic fields must be single lines')
    language = env.get('GUAVAJAM_LANGUAGE', 'en')
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language):
        raise BootstrapError('invalid GUAVAJAM_LANGUAGE')
    _, signer_data = authorized_signers(required(env, 'GUAVAJAM_ALLOWED_SIGNERS'), env_file, repo)
    facts = {'schema': 'guavajam.work.v1', 'title': title, 'author': author,
             'language': language, 'rights': env.get('GUAVAJAM_RIGHTS', 'all-rights-reserved')}
    document = '---\n' + yaml.safe_dump(facts, sort_keys=False, allow_unicode=True) + '---\n\nReplace this paragraph with your manuscript.\n'
    put(repo / 'content/manuscript.md', document)
    put(repo / '.gitignore', '.env\n.env.*\n.build/\n__pycache__/\n')
    put(repo / 'README.md', '# ' + title + '\n\nThe work lives in `content/`. Review changes in branches and PRs; '
        'trusted signed changes on `main` publish editions.\n')
    put(repo / '.github/workflows/verify.yml', from_template('verify-work.yml', ref))
    # Commit infrastructure only. The untracked manuscript remains a draft until
    # the publisher explicitly signs its first accepted content-changing commit.
    git(repo, 'add', '--', '.gitignore', 'README.md', '.github/workflows/verify.yml')
    git(repo, 'commit', '-m', 'Initialize work repository')
    return signer_data


def make_index(repo, ref):
    put(repo / 'index.yaml', 'repositories: []\n')
    put(repo / 'trust.yaml', '{}\n')
    put(repo / '.gitignore', '.env\n.env.*\n.build/\n__pycache__/\n')
    put(repo / 'README.md', '# Publications\n\nEdit `index.yaml` to list public work repositories and '
        '`trust.yaml` to configure independent signer trust. GitHub Actions publishes a derived Pages site; '
        'no HTML is committed here.\n')
    git(repo, 'add', '--', 'index.yaml', 'trust.yaml', '.gitignore', 'README.md')
    git(repo, 'commit', '-m', 'Initialize publication index')
    create_checkpoint(repo)
    # Add the deployment workflow only after the checkpoint and Pages have been set up.
    return from_template('pages-index.yml', ref)


def bootstrap(kind, settings, output, *, local=False):
    env, env_file = dotenv(settings)
    name = required(env, 'GUAVAJAM_REPO')
    if not NAME.fullmatch(name) or '..' in name or name.endswith('.git'):
        raise BootstrapError('GUAVAJAM_REPO must be owner/repository')
    ref = required(env, 'GUAVAJAM_TOOL_REF')
    if not SHA.fullmatch(ref):
        raise BootstrapError('GUAVAJAM_TOOL_REF must be a pinned 40-character Git commit SHA')
    repo = (Path(output) if output else Path(name.split('/', 1)[1])).expanduser().absolute()
    if repo.exists() or repo.is_symlink() or repo.resolve() != repo:
        raise BootstrapError('output must be a new directory without symlink ancestors')
    if env_file.is_relative_to(repo):
        raise BootstrapError('environment file must be outside the new repository')
    repo.mkdir(parents=True, exist_ok=False)
    git(repo, 'init', '-b', 'main')
    if kind == 'work':
        signer_data = make_work(repo, env, env_file, ref)
    else:
        workflow = make_index(repo, ref)
    if not local:
        gh_env = {**os.environ, **{k: v for k, v in env.items() if k == 'GH_TOKEN'}}
        command('gh', 'repo', 'create', name, '--public', '--source', str(repo), '--remote', 'origin',
                env=gh_env)
        if kind == 'work':
            command('gh', 'secret', 'set', 'GUAVAJAM_ALLOWED_SIGNERS', '--repo', name,
                    env=gh_env, data=signer_data)
        push(repo, ('-u', 'origin', 'main'), gh_env)
        if kind == 'index':
            push(repo, ('origin', 'guavajam-state'), gh_env)
            command('gh', 'api', '--method', 'POST', 'repos/' + name + '/pages',
                    '-f', 'build_type=workflow', env=gh_env)
    if kind == 'index':
        put(repo / '.github/workflows/pages.yml', workflow)
        git(repo, 'add', '--', '.github/workflows/pages.yml')
        git(repo, 'commit', '-m', 'Enable static Pages deployment')
        if not local:
            push(repo, ('origin', 'main'), gh_env)
    print('Initialized', kind, 'repository:', repo)
    if local:
        print('Local-only bootstrap: no GitHub repository or secrets were created.')
    return repo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('work', 'index'))
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--local', action='store_true', help='Initialize locally, without GitHub operations')
    args = parser.parse_args(argv)
    try:
        bootstrap(args.kind, args.env, args.output, local=args.local)
    except (BootstrapError, OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        parser.exit(1, 'GuavaJam initialization failed: ' + str(exc) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
