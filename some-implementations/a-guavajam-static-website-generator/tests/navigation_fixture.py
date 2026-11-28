"""Small synthetic metadata fixtures; real signed-source journeys live separately."""
import hashlib
from a_guavajam_static_website_generator.navigation_manifest import write_document, write_json


def write_navigation(read, view, navigation=None):
    canonical = view.setdefault('canonical', (navigation or {}).get('canonical', {'manuscript': 'content/manuscript.md'}))
    preview = view.setdefault('preview', (navigation or {}).get('preview', False))
    descriptors = {}
    files = view.setdefault('files', {})
    for source, detail in view['documents'].items():
        detail.setdefault('language', 'en')
        detail.setdefault('source_sha256', '0' * 64)
        document = {**detail, **((navigation or {}).get('documents', {}).get(source, {}))}
        document.setdefault('text', '')
        anchors = document.get('anchors', [])
        records = []
        for number, anchor in enumerate(anchors):
            record = dict(anchor) if isinstance(anchor, dict) else {'anchor': anchor}
            defaults = {'title': record['anchor'], 'kind': 'passage', 'level': 0, 'position': number,
                        'parent': None, 'scope': 'document', 'text': '', 'block_start': number, 'block_end': number + 1}
            records.append({**defaults, **record})
        document['anchors'] = records
        detail['anchors'] = [anchor['anchor'] for anchor in records]
        document.setdefault('blocks', [{'position': 0, 'scope': 'document', 'text': document['text']}])
        descriptor = write_document(read, canonical, preview, source, document)
        descriptors[source] = descriptor
        files[descriptor['url']] = descriptor['sha256']
    write_json(read / 'NAV.json', {'schema': 'guavajam.navigation.v1', 'layout': 'documents',
        'canonical': canonical, 'preview': preview, 'documents': descriptors})
    files['NAV.json'] = hashlib.sha256((read / 'NAV.json').read_bytes()).hexdigest()
    write_json(read / 'VIEW.json', view)
    return descriptors
