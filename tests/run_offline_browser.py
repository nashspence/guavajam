"""Build with current code, retain only its complete ZIP, then test offline browsers."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from edition_package import edition_directory
from verify_edition import verify_edition
from test_asset_downloads import DownloadPublicationTests


@contextmanager
def browser_fixture():
    fixture=DownloadPublicationTests('test_mixed_inline_external_and_supporting_references_build_and_verify_offline')
    try:
        fixture.setUp();fixture.source()
        manuscript=fixture.repo/'works/sample/manuscript.md'
        with manuscript.open('a') as stream:
            stream.write('\n<a id="sec-2"></a>\n## Comparison of $x=x$\n\n'
                         'The navigation reuses the reviewed section title and anchor.\n\n'
                         '<a id="detail-2"></a>\n### Theorem 2 — Detail\n\n'
                         'This deeper heading stays outside the major-section outline.\n\n'
                         '<a id="sec-3"></a>\n## Reading the evidence\n\n'
                         'The supporting records follow the manuscript.\n')
        fixture.git('add','works');fixture.git('commit','-qm','Review navigable offline edition')
        fixture.commit=fixture.git('rev-parse','HEAD')
        manifest=fixture.build_downloads()
        saved=fixture.base/'complete.zip'
        shutil.copyfile(fixture.output/manifest['supplementary_package']['filename'],saved)
        shutil.rmtree(fixture.output)
        with edition_directory(saved) as directory:
            verify_edition(directory)
            yield directory
    finally:fixture.doCleanups()


if __name__=='__main__':
    with browser_fixture() as directory:
        raise SystemExit(subprocess.run(['node','--test','tests/test_offline_browser.mjs'],cwd=ROOT,
            env={**os.environ,'PRESS_OFFLINE_TEST_DIR':str(directory)}).returncode)
