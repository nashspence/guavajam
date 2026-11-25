"""Reproduce generated cache-inventory ceiling using actual renderer paths.

Synthetic renderer output only: no claim of complete large-work rendering.
The production cache writer/reader and real naming/hash/serialization helpers
are exercised without parsing thousands of independent source files.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
BASE=Path(os.environ.get('GUAVAJAM_REPO', HERE.parents[1]/'repos/guavajam')).resolve()
sys.path[:0]=[str(BASE/'src'),str(BASE/'some-implementations/a-guavajam-static-website-generator/src')]
from guavajam.catalog import encoded
from a_guavajam_static_website_generator import view_cache
from a_guavajam_static_website_generator.citation_routes import source_download


def probe(count):
    revision='a'*40
    def rendered(repo,sha,output,**kwargs):
        output.mkdir()
        (output/'supporting').mkdir()
        for i in range(count):
            html=f'supporting/page-{i:05}.html'
            source=source_download(html)
            for filename in (html,source,str(Path(source).with_suffix('.citation.json'))):
                (output/filename).write_text('synthetic fixed-size output\n')
        (output/'VIEW.json').write_bytes(encoded({'preview':False,'canonical':{'hash_algorithm':'sha1','revision':sha,'manuscript':'content/manuscript.md'}}))
        (output/'NAV.json').write_text('{}\n')
        (output/'documents.html').write_text('synthetic directory\n')
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        with patch.object(view_cache,'render',rendered):
            try:
                view_cache.cached_render(root/'cache',root/'repo',revision,root/'output')
                outcome='accepted'
            except ValueError as exc:outcome=str(exc)
        manifests=list((root/'cache').glob('*/CACHE.json'))
        assert len(manifests)==1
        raw=manifests[0].read_bytes()
        return {'supporting_documents':count,'manifest_entries':len(json.loads(raw)),'cache_inventory_bytes':len(raw),'one_mib_limit':1024*1024,'result':outcome}


if __name__=='__main__':
    results=[probe(count) for count in (128,4000,16000)]
    assert results[0]['result']=='accepted'
    assert all('corrupt view cache' in r['result'] for r in results[1:])
    (HERE/'cache-inventory-results.json').write_text(json.dumps({'measurements':results,'method':'Synthetic output under the real renderer naming convention, production cache write/read path. No source validation or full-render claim.'},indent=2)+'\n')
    print(json.dumps(results,indent=2))
