#!/usr/bin/env python3
"""Check incoming publication declarations against repository-external signer policy."""
from pathlib import Path
import argparse,json,os,re,subprocess,tempfile,sys
from publication_commit import git,verify,PublicationError


def incoming(root,base,*,all_history=False):
    if all_history:
        selector='HEAD'
    elif re.fullmatch('[0-9a-f]{40}',base or '') and base!='0'*40:
        selector=base+'..HEAD'
    elif os.environ.get('GITHUB_REF')=='refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch':
        selector='HEAD'
    else:
        result=subprocess.run(['git','--no-replace-objects','merge-base','HEAD','refs/remotes/origin/main'],cwd=root,capture_output=True,check=False)
        selector=result.stdout.strip().decode()+'..HEAD' if result.returncode==0 else 'HEAD'
    revisions=git(root,'rev-list','--reverse',selector).decode().splitlines()
    if len(revisions)>1000:raise PublicationError('incoming publication audit exceeds commit budget')
    return [sha for sha in revisions if re.search(rb'^Press-(?:Publication|Work):',git(root,'show','-s','--format=%B',sha),re.M)]


def check(root,base,*,online=True,all_history=False):
    candidates=incoming(root,base,all_history=all_history)
    if not candidates:return []
    fingerprints=os.environ.get('PRESS_PUBLISHER_GPG_FINGERPRINTS','').split()
    signers=os.environ.get('PRESS_PUBLISHER_SSH_SIGNERS','')
    if bool(fingerprints)==bool(signers):raise PublicationError('declared publications require exactly one externally configured publisher signer policy')
    with tempfile.TemporaryDirectory(prefix='press-trust-') as directory:
        path=Path(directory)/'allowed-signers'
        if signers:path.write_text(signers+'\n');path.chmod(0o600)
        old_home=os.environ.get('GNUPGHOME')
        try:
            if fingerprints:
                key=os.environ.get('PRESS_PUBLISHER_GPG_PUBLIC_KEYS','')
                if not key:raise PublicationError('trusted GPG public keys must be independently configured')
                home=Path(directory)/'gpg';home.mkdir(mode=0o700);os.environ['GNUPGHOME']=str(home)
                imported=subprocess.run(['gpg','--batch','--import'],input=key.encode(),capture_output=True,check=False)
                if imported.returncode:raise PublicationError('independent GPG policy import failed')
            return [verify(root,sha,fingerprints=fingerprints,allowed_signers=path if signers else None,online_media=online,require_retention=online) for sha in candidates]
        finally:
            if old_home is None:os.environ.pop('GNUPGHOME',None)
            else:os.environ['GNUPGHOME']=old_home


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default=os.environ.get('PRESS_BASE_SHA',''));parser.add_argument('--offline',action='store_true');parser.add_argument('--all',action='store_true',dest='all_history');args=parser.parse_args()
    try:results=check(Path(__file__).resolve().parents[1],args.base,online=not args.offline,all_history=args.all_history)
    except (PublicationError,OSError,ValueError) as exc:print('Publication policy failed: '+str(exc),file=sys.stderr);return 1
    print('Verified incoming publication declarations:',len(results));return 0
if __name__=='__main__':sys.exit(main())
