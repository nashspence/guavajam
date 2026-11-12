"""Compatibility with actual authoring starters and reviewed ChatGPT evidence."""
from pathlib import Path
from html.parser import HTMLParser
import re,json,subprocess,sys,tempfile,unittest,yaml
from guavajam.renderer import render,RenderError
from guavajam.supporting_gfm import parse_gfm_record
from guavajam.asset_policy import AssetError

class Indented(yaml.SafeDumper):
    def increase_indent(self,flow=False,indentless=False):return super().increase_indent(flow,False)

ROOT=Path(__file__).resolve().parents[1]
class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.ids=[];self.links=[];self.tags=[];self.attrs=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);self.tags.append(tag);self.attrs.append((tag,attrs))
        if attrs.get('id'):self.ids.append(attrs['id'])
        if tag=='a':self.links.append(attrs.get('href',''))

class PresentationTests(unittest.TestCase):
    def assertIn(self,member,container,msg=None):
        if isinstance(container,str) and len(container)>4000:return self.assertTrue(member in container,msg or ('missing expected fragment: '+repr(member)))
        return super().assertIn(member,container,msg)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'repo';self.root.mkdir()
        for args in [('init','-q','-b','main'),('config','user.name','Fixture'),('config','user.email','fixture@example.org')]:self.git(*args)
        self.work=self.root/'works/sample-work';(self.work/'supporting').mkdir(parents=True)
        (self.root/'asset-policy.json').write_text('{"origin":"https://assets.press.nash.spence.0819870.xyz"}')
        (self.work/'manuscript.md').write_text((ROOT/'tests/fixtures/manuscript.md').read_text())
        (self.work/'supporting/note.md').write_text((ROOT/'tests/fixtures/note.md').read_text())
        self.out=Path(self.temp.name)/'view'
    def git(self,*args):return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.PIPE).decode().strip()
    def build(self):
        self.git('add','-A');self.git('commit','-qm','Reviewed fixture');sha=self.git('rev-parse','HEAD')
        render(self.root,sha,'sample-work',self.out,preview=True);return (self.out/'index.html').read_text(),sha
    def test_copied_starter_has_real_captions_math_navigation_svg_and_element_links(self):
        html,sha=self.build();page=Page(html)
        self.assertEqual(len(page.ids),len(set(page.ids)))
        self.assertIn('caption',page.tags);self.assertIn('math',page.tags)
        self.assertIn('guavajam-contents',page.ids);self.assertIn('sec-1',page.ids);self.assertIn('eq-1',page.ids)
        self.assertIn('href="supporting/note.html#claim"',html)
        self.assertIn('Nash Spence',html);self.assertIn(sha,html);self.assertNotIn('<script',html)
        self.assertTrue(any(tag=='pre' and attrs.get('tabindex')=='0' for tag,attrs in page.attrs))
        note=Page((self.out/'supporting/note.html').read_text());self.assertIn('claim',note.ids);self.assertIn('document-metadata',note.ids)
    def test_ordered_authors_and_literal_abstract_are_visible_and_escaped(self):
        manuscript=self.work/'manuscript.md';text=manuscript.read_text();_,header,body=text.split('---\n',2);facts=yaml.safe_load(header);del facts['author']
        facts['authors']=[{'name':'Zoë <Z>','orcid':'0000-0002-1825-0097'},{'name':'李 *literal*'}]
        facts['abstract']='A literal <script>claim</script>.'
        facts['schema_type']='ScholarlyArticle'
        manuscript.write_text('---\n'+yaml.dump(facts,Dumper=Indented,allow_unicode=True,sort_keys=False)+'---\n'+body)
        html,_=self.build();self.assertIn('Zoë &lt;Z&gt;',html);self.assertIn('李 *literal*',html);self.assertNotIn('<script>',html);self.assertIn('A literal &lt;script&gt;claim&lt;/script&gt;.',html)
        self.assertLess(html.index('Zoë &lt;Z&gt;'),html.index('李 *literal*'))
        self.assertIn('itemscope itemtype="https://schema.org/ScholarlyArticle"',html)
        self.assertIn('Rights: all-rights-reserved',html)
    def test_both_lantern_branches_keep_all_source_anchors_attributed_quotes_and_times(self):
        for name in ['lantern-current.gfm.md','lantern-alternate.gfm.md']:
            with self.subTest(name=name):
                note=self.work/'supporting/note.md';source=(ROOT/'tests/fixtures'/name).read_text();note.write_text(source)
                manuscript=self.work/'manuscript.md';manuscript.write_text(manuscript.read_text().replace('supporting/note.md#claim','supporting/note.md').replace('supporting/note.md#code','supporting/note.md'))
                html,_=self.build();text=(self.out/'supporting/note.html').read_text();page=Page(text)
                _,anchors=parse_gfm_record(source)
                self.assertTrue(set(anchors)<=set(page.ids));self.assertEqual(len(page.ids),len(set(page.ids)))
                self.assertIn('press-turn-quotation',text);self.assertIn('press-turn-attribution',text);self.assertEqual(source.count('<time datetime='),text.count('<time datetime='))
                self.assertGreater(page.tags.count('math'),10);self.assertGreater(page.tags.count('table'),1)
                import shutil;shutil.rmtree(self.out)
    def test_submitter_metadata_is_document_scoped_literal_and_citable(self):
        fields='title: "A submitter title"\nSource: "<script>literal</script>"\nuser_account: "example-user"\ndate_created: 2026-11-08\n'
        note=self.work/'supporting/note.md';note.write_text('---\n'+fields+'---\n\n<a name="details"></a>\n\n<a name="document"></a>\n\n# Supporting note\n\n<a name="claim"></a>\n\nA claim.\n\n<a name="code"></a>\n\nLiteral source.\n')
        html,_=self.build();text=(self.out/'supporting/note.html').read_text();page=Page(text)
        self.assertIn('details',page.ids);self.assertIn('example-user',text);self.assertIn('&lt;script&gt;literal&lt;/script&gt;',text)
        self.assertNotIn('example-user',html);self.assertNotIn('<script>',text)
        self.assertIn('Supporting note',text)
    def test_derived_output_digests_bind_every_emitted_html_page(self):
        self.build();manifest=json.loads((self.out/'VIEW.json').read_text());import hashlib
        self.assertEqual({'index.html','supporting/note.html'},set(manifest['files']))
        for name,digest in manifest['files'].items():self.assertEqual(digest,hashlib.sha256((self.out/name).read_bytes()).hexdigest())

    def test_semantic_time_and_tooltip_preserve_literal_source_values(self):
        note=self.work/'supporting/note.md'
        with note.open('a') as stream:stream.write('\n<a name="timestamp"></a>\n\n*<time datetime="2026-11-08T14:00:00Z">8 Nov 2026 · 14:00 UTC</time>*\n\n<a name="tooltip"></a>\n\n<span title="&lt;icon name=&quot;alert-triangle&quot;/&gt;">□</span>\n')
        self.build();text=(self.out/'supporting/note.html').read_text()
        self.assertIn('<time datetime="2026-11-08T14:00:00Z">',text)
        self.assertIn('press-passage-datetime',text);self.assertIn('id="timestamp"',text)
        self.assertIn('&lt;icon name=&quot;alert-triangle&quot;/&gt;',text)

    def test_source_ids_cannot_collide_with_view_navigation_footnotes_or_code(self):
        source=self.work/'manuscript.md'
        with source.open('a') as stream:
            for anchor in ['press-main','guavajam-main','guavajam-contents','fn1','fnref1','cb1','cb1-1','footnotes']:
                stream.write('\n<a id="'+anchor+'"></a>\n## '+anchor+'\n\nSource content.\n')
        html,_=self.build();page=Page(html)
        self.assertEqual(len(page.ids),len(set(page.ids)))
        for anchor in ['press-main','guavajam-main','guavajam-contents','fn1','fnref1','cb1','cb1-1','footnotes']:self.assertIn(anchor,page.ids)
        self.assertTrue(any('guavajam-view-' in target for target in page.links))

    def test_literal_html_anchor_examples_are_never_dom_identity(self):
        source=self.work/'manuscript.md'
        with source.open('a') as stream:stream.write('\n```html\n<a id="sec-1"></a>\n<a id="sec-1"></a>\n```\n')
        html,_=self.build();self.assertEqual(1,Page(html).ids.count('sec-1'))
