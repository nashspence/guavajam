#!/usr/bin/env python3
"""Copy pinned runtime sources and remove only three aggregate guard sites."""
import difflib
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get('GUAVAJAM_REPO', HERE.parents[1] / 'repos/guavajam')).resolve()
EXPECTED = '54fce07b619628a5e92be946be4b189873ddf1a0'
actual = subprocess.check_output(['git','-C',str(BASE),'rev-parse','HEAD'],text=True).strip()
if actual != EXPECTED:
    raise SystemExit('This reproduction requires GuavaJam ' + EXPECTED + '; inspected checkout is ' + actual)
local = HERE/'candidate'
for path in ('src','some-implementations/a-guavajam-static-website-generator/src'):
    shutil.copytree(BASE/path,local/path,dirs_exist_ok=True)
edits = {
    'src/guavajam/validate_sources.py': [("    if len(supporting) > 128 or sum((root / relative).stat().st_size for relative in supporting if (root / relative).is_file()) > 100 * 1024 * 1024:\n        errors.append(issue(Path('content'), 'supporting inventory exceeds 128 documents or 100 MiB'))\n", '')],
    'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/renderer.py': [("    if len(paths) > 128: raise RenderError('too many supporting documents')\n", ''), ("        if total_source>100*1024*1024:raise RenderError('supporting source inventory exceeds 100 MiB')\n", '')]
}
patch = []
for path, replacements in edits.items():
    before = (BASE/path).read_text()
    after = before
    for old, new in replacements:
        assert after.count(old) == 1, path
        after = after.replace(old, new)
    (local/path).write_text(after)
    patch.extend(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='a/'+path,tofile='b/'+path))
(HERE/'aggregate-guard-only.patch').write_text(''.join(patch))
print('Prepared isolated copied sources; target checkout was not edited')
