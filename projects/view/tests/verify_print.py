"""Check real browser PDFs with an independent text/layout engine."""
from pathlib import Path
import fitz

def verify(directory):
    for name in ('index','note','lantern-current'):
        for paper,width,height in [('A4',595.28,841.89),('Letter',612,792)]:
            path=Path(directory)/(name+'-'+paper+'.pdf')
            with fitz.open(path) as document:
                assert len(document)>0,path.name
                text=''.join(page.get_text() for page in document)
                assert 'Canonical source' in text,path.name
                if name=='index':
                    for expected in ('Recording transcript','Qualification movie','Column 11','Table 2'):
                        assert ''.join(expected.split()) in ''.join(text.split()),(path.name,expected)
                if name=='lantern-current':assert len(text)>20000,path.name
                for page in document:
                    assert abs(page.rect.width-width)<2 and abs(page.rect.height-height)<2,path.name
                    for word in page.get_text('words'):
                        assert word[0]>=-1 and word[1]>=-1 and word[2]<=page.rect.width+1 and word[3]<=page.rect.height+1,(path.name,page.number,'clipped text')
    print('Six A4/Letter PDFs retain source identity, transcript, wide-table content and bounded text.')
