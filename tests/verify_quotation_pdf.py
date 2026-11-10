"""Inspect actual browser print output, including visible continuation rails."""
import json
from pathlib import Path
import re
import sys

import fitz


def verify_pdf(raw, cases):
    assert len(raw) <= 10 * 1024 * 1024, 'quotation PDF exceeds the test byte bound'
    with fitz.open(stream=raw, filetype='pdf') as document:
        assert len(document) >= 3, 'quotation must span several printed pages'
        packed = re.sub(r'\s+', '', ''.join(page.get_text() for page in document))
        assert 'QUOTE-END' in packed, 'missing authorial closing after the quotation'
        for credit in cases['credits'].values():
            assert re.sub(r'\s+', '', credit) in packed, 'missing printed source credit'
        for n in range(cases['paragraphs']):
            assert f'QUOTE-PARAGRAPH-{n:03d}' in packed, 'missing printed quotation paragraph'
        rail_pages = 0
        for page in document:
            words = page.get_text('words')
            assert all(word[0] >= -1 and word[1] >= -1 and word[2] <= page.rect.width + 1
                       and word[3] <= page.rect.height + 1 for word in words), 'printed text extends outside the page'
            markers = [word for word in words if re.fullmatch(r'QUOTE-PARAGRAPH-[0-9]{3}', word[4])]
            if not markers:
                continue
            # Locate the thin native rule beside each paragraph, then sample
            # rendered pixels: a clipped or painted-over path is not visible.
            rails = [item['rect'] for item in page.get_drawings()
                     if 0 < item['rect'].width <= 6 and item['rect'].height >= 10]
            pixels = page.get_pixmap(alpha=False)
            for word in markers:
                y = (word[1] + word[3]) / 2
                candidates = [rail for rail in rails if rail.y0 <= y <= rail.y1
                              and 0 <= word[0] - rail.x1 <= 20]
                assert any(0 <= round((rail.x0 + rail.x1) / 2) < pixels.width
                           and 0 <= round(y) < pixels.height
                           and min(pixels.pixel(round((rail.x0 + rail.x1) / 2), round(y))[:3]) < 210
                           for rail in candidates), 'missing visible quotation rail beside a printed paragraph'
            rail_pages += 1
        assert rail_pages >= 3, 'quotation rail must continue over several pages'
        return {'pages': len(document), 'rail_pages': rail_pages, 'paragraphs': cases['paragraphs']}


if __name__ == '__main__':
    print(json.dumps(verify_pdf(Path(sys.argv[1]).read_bytes(), json.load(sys.stdin))))
