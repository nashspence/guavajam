"""GuavaJam validates reviewed GFM independently of its producer."""
from pathlib import Path
import tempfile
import unittest
from guavajam.asset_policy import AssetError
from guavajam.source_ast import walk
from guavajam.supporting_gfm import load_gfm_record
from guavajam_test_support import RESOURCES
NOTE = (RESOURCES / 'templates/work/supporting/note.md').read_text()

class GFMSourceTests(unittest.TestCase):
    def test_tooltips_are_balanced_title_only_literal_data(self):
        source = NOTE + ('\n<a name="tooltip-text"></a>\n\n<span title=\'&lt;text color="secondary"&gt;\'>Readable **text**</span>\n\n'
                         '<a name="tooltip-icon"></a>\n\n<span title="&lt;icon name=&quot;alert-triangle&quot;/&gt;">□</span>\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'note.md'; path.write_text(source)
            ast, *_ = load_gfm_record(path)
            spans = [node for node in walk(ast['blocks']) if node['t'] == 'Span' and 'title' in dict(node['c'][0][2])]
            self.assertEqual(['<text color="secondary">', '<icon name="alert-triangle"/>'],
                             [dict(node['c'][0][2])['title'] for node in spans])
            for markup in ('<span title="x" onclick="alert(1)">Text</span>',
                           '<span title="x" style="display:none">Text</span>',
                           '<span title="x" id="claim">Text</span>',
                           '<span title="x"><script>alert(1)</script></span>',
                           '<span title="x"><img src="https://example.org/tracker"></span>',
                           '<span title="x">Unclosed',
                           'Unopened</span>',
                           '<span title="&#10;">Line break</span>'):
                with self.subTest(markup=markup):
                    path.write_text(NOTE + '\n' + markup + '\n')
                    with self.assertRaises(AssetError):
                        load_gfm_record(path)

    def test_narrow_source_rejects_unsafe_dangling_duplicate_and_implicit_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'note.md'
            path.write_text(NOTE)
            _,anchors,_,_=load_gfm_record(path)
            self.assertEqual({'document-metadata','scope','claim','calculation','code','return'},set(anchors))
            for bad in [
                NOTE+'\n<script>alert(1)</script>\n', NOTE.replace('#claim','#missing'),
                NOTE.replace('#claim','../manuscript.md'), NOTE.replace('#claim','javascript:alert(1)'),
                NOTE.replace('name="code"','name="claim"'), NOTE+'\n<a name="dangling"></a>\n',
                NOTE.replace('<a name="claim"></a>','<a name="Bad_ID"></a>'),
                NOTE.replace('<a name="claim"></a>','<div id="claim"></div>'),
                NOTE.replace('#claim','#reviewed-supporting-note'),
                NOTE.replace('<a name="claim"></a>','Inline <a name="claim"></a> text.'),
            ]:
                with self.subTest(bad=bad[-100:]):
                    path.write_text(bad)
                    with self.assertRaises((AssetError,ValueError)): load_gfm_record(path)
            path.unlink();path.symlink_to(RESOURCES / 'templates/work/supporting/note.md')
            with self.assertRaises(AssetError):load_gfm_record(path)
