"""Document links are checked through compact exact disk memberships."""
from pathlib import Path
import unittest
from guavajam.source_inventory import source_inventory


class SourceInventoryTests(unittest.TestCase):
    def test_stable_and_rendered_anchors_keep_manuscript_link_rules_and_reference_order(self):
        with source_inventory() as inventory:
            inventory.add(Path('content/supporting/note.md'),
                {'blocks': [
                    {'t': 'Header', 'c': [2, ['rendered-heading', [], []], [{'t': 'Str', 'c': 'Heading'}]]},
                    {'t': 'Para', 'c': [
                        {'t': 'Link', 'c': [['', [], []], [{'t': 'Str', 'c': 'Follow'}], ['../manuscript.md#arrival', '']]},
                        {'t': 'Image', 'c': [['', [], []], [], ['../assets/image.png', '']]}]}]},
                {'stable-passage'})
            inventory.add(Path('content/manuscript.md'), {'blocks': []}, {'arrival'}, manuscript=True)
            self.assertTrue(inventory.contains('content/supporting/note.md'))
            self.assertTrue(inventory.has_anchor('content/supporting/note.md', 'stable-passage', stable_only=True))
            self.assertTrue(inventory.has_anchor('content/supporting/note.md', 'rendered-heading'))
            self.assertFalse(inventory.has_anchor('content/supporting/note.md', 'rendered-heading', stable_only=True))
            self.assertFalse(inventory.contains('content/missing.md'))
            self.assertEqual([
                (Path('content/supporting/note.md'), False, 'Link', '../manuscript.md#arrival', True),
                (Path('content/supporting/note.md'), False, 'Image', '../assets/image.png', False)],
                list(inventory.references()))
            self.assertEqual('ok', inventory.database.execute('PRAGMA integrity_check').fetchone()[0])
