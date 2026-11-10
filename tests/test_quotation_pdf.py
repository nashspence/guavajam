"""The PDF gate must reject invisible rails and missing quotation text."""
import unittest
import fitz
from verify_quotation_pdf import verify_pdf


class QuotationPDFTests(unittest.TestCase):
    def sample(self, *, hide_rail=False, omit_paragraph=False):
        document = fitz.open()
        for n in range(3):
            page = document.new_page()
            page.insert_text((54, 25), f'QUOTE-CASE-{n}')
            rail = fitz.Rect(40, 40, 43, 140)
            page.draw_rect(rail, color=None, fill=(.4, .4, .4))
            for offset in range(2):
                if not (omit_paragraph and n == offset == 0):
                    page.insert_text((54, 65 + offset * 35), f'QUOTE-PARAGRAPH-{n * 2 + offset:03d}')
            if hide_rail:
                page.draw_rect(rail, color=None, fill=(1, 1, 1), overlay=True)
            if n == 2:
                page.insert_text((54, 180), 'Model credit\nQUOTE-END')
        raw = document.tobytes(); document.close()
        return raw

    def test_visible_rails_and_complete_text_pass(self):
        self.assertEqual({'pages': 3, 'rail_pages': 3, 'paragraphs': 6},
                         verify_pdf(self.sample(), {'credits': {'model': 'Model credit'}, 'paragraphs': 6}))

    def test_a_painted_over_rail_fails_even_when_its_vector_path_survives(self):
        with self.assertRaisesRegex(AssertionError, 'visible quotation rail'):
            verify_pdf(self.sample(hide_rail=True), {'credits': {}, 'paragraphs': 6})

    def test_missing_source_text_fails(self):
        with self.assertRaisesRegex(AssertionError, 'missing printed quotation paragraph'):
            verify_pdf(self.sample(omit_paragraph=True), {'credits': {}, 'paragraphs': 6})

    def test_a_credit_in_another_quotation_cannot_satisfy_the_missing_credit(self):
        cases = {'credits': {'first': 'Model credit', 'last': 'Model credit'}, 'paragraphs': 6,
                 'sections': {'first': 'QUOTE-CASE-0', 'last': 'QUOTE-CASE-2'}}
        with self.assertRaisesRegex(AssertionError, 'credit in its own quotation'):
            verify_pdf(self.sample(), cases)
