"""Initialize an optional static website index using GuavaJam repository primitives."""
import argparse
import json
from pathlib import Path

from guavajam.bootstrap import bootstrap as create_repository, git, put, push, command

CHECKPOINT = {'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}, 'feed': None}


def create_checkpoint(repo):
    """Create the independent baseline branch with Git objects, never a view artifact."""
    raw = json.dumps(CHECKPOINT, sort_keys=True, separators=(',', ':')) + '\n'
    blob = git(repo, 'hash-object', '-w', '--stdin', data=raw)
    tree = git(repo, 'mktree', '-z', data='100644 blob ' + blob + '\tSTATE.json\0')
    sha = git(repo, 'commit-tree', tree, '-m', 'Initialize verification checkpoint')
    git(repo, 'update-ref', 'refs/heads/guavajam-state', sha)


def make_index(repo, env, env_file, ref):
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
    return (Path(__file__).parent / 'templates/pages-index.yml').read_text().replace('@@GUAVAJAM_REF@@', ref)


def finish(repo, name, env, workflow):
    if env is not None:
        # Publish the baseline checkpoint before enabling the Pages workflow.
        push(repo, ('origin', 'guavajam-state'), env)
        command('gh', 'api', '--method', 'POST', 'repos/' + name + '/pages',
                '-f', 'build_type=workflow', env=env)
    put(repo / '.github/workflows/pages.yml', workflow)
    git(repo, 'add', '--', '.github/workflows/pages.yml')
    git(repo, 'commit', '-m', 'Enable static Pages deployment')
    if env is not None:
        push(repo, ('origin', 'main'), env)


def bootstrap(settings, output, *, local=False):
    return create_repository(settings, output, local=local, initialize=make_index,
                             configure_remote=None, finish=finish)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('index',))
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--local', action='store_true')
    args = parser.parse_args(argv)
    try:
        bootstrap(args.env, args.output, local=args.local)
    except (ValueError, OSError) as exc:
        parser.exit(1, 'Website index initialization failed: ' + str(exc) + '\n')
    return 0
