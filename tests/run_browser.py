"""Qualify actual rendered files; browser scripts never replace their CSP."""
from pathlib import Path
import argparse,json,subprocess,sys,tempfile
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from browser_fixture import make_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.prepare:
        fixture=make_fixture(args.prepare);print('Prepared browser fixture revision:',fixture['revision']);return 0
    with tempfile.TemporaryDirectory(prefix='guavajam-browser-') as directory:
        root=Path(directory);make_fixture(root/'fixture');output=args.output or root/'evidence'
        result=subprocess.run(['mise','exec','--','node',str(Path(__file__).with_name('test_browser.mjs')),str(root/'fixture/fixture.json'),str(output)],check=False)
        if result.returncode==0:
            from verify_print import verify
            verify(output)
        return result.returncode
if __name__=='__main__':sys.exit(main())
