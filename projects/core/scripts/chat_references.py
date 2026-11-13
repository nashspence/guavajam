"""Resolve exported references without evaluating rich markup or fetching URLs."""
import html
import json
import re

REFERENCE = re.compile(r'turn[0-9]+[a-z]+[0-9]+\Z')
ATTRIBUTE = re.compile(r"""\s+([a-z]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\{[^{}]*\}))""")


def _https(value):
    from validate_sources import valid_source_link
    return isinstance(value, str) and value.startswith('https://') and valid_source_link(value)


def reference_index(messages):
    """Index explicit search/view destinations along the selected ancestry only."""
    result = {}
    for message in messages:
        metadata = message.get('metadata', {})
        if not isinstance(metadata, dict):
            continue
        groups = metadata.get('search_result_groups', [])
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get('entries', []) if isinstance(group, dict) else []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ref = entry.get('ref_id')
                if isinstance(ref, dict):
                    turn, kind, index = (ref.get(k) for k in ('turn_index', 'ref_type', 'ref_index'))
                    if (type(turn) is not int or type(index) is not int or turn < 0 or index < 0 or
                            not isinstance(kind, str) or not re.fullmatch('[a-z]+', kind)):
                        continue
                    ref = f'turn{turn}{kind}{index}'
                if not isinstance(ref, str) or not REFERENCE.fullmatch(ref):
                    continue
                url, title = entry.get('url'), entry.get('title')
                target = {'url': url, 'title': title if isinstance(title, str) and title.strip() else url}
                if not _https(url):
                    target = None
                if ref not in result:
                    result[ref] = target
                elif result[ref] is None or target is None or result[ref]['url'] != url:
                    result[ref] = None  # Conflicting or unsafe evidence cannot authorize a link.
    return result


def _link(url, title):
    if (not _https(url) or not isinstance(title, str) or not title.strip() or
            any(ord(ch) < 32 for ch in title)):
        return None
    return {'t': 'Link', 'c': [['', [], []], [{'t': 'Str', 'c': title}], [url, '']]}


def citation_inlines(ids, references):
    if not isinstance(ids, list) or not ids:
        return None
    links = []
    destinations = set()
    for identifier in ids:
        target = references.get(identifier) if isinstance(identifier, str) else None
        link = _link(target.get('url'), target.get('title')) if isinstance(target, dict) else None
        if link is None:
            return None
        # Search and view IDs can denote the same page. Validate every ID before
        # collapsing exact destinations within this group, in first-seen order.
        if target['url'] in destinations:
            continue
        destinations.add(target['url'])
        if links:
            links.append({'t': 'Str', 'c': '; '})
        links.append(link)
    return [{'t': 'Str', 'c': '['}, *links, {'t': 'Str', 'c': ']'}]


def component_inlines(raw, references):
    component = re.fullmatch(r'<(Link|Cite)(\s[^<>]*)/\s*>', raw, re.S)
    if not component:
        return None
    attributes = {}
    expressions = set()
    position = 0
    source = component[2].rstrip()
    while position < len(source):
        match = ATTRIBUTE.match(source, position)
        if match is None or match[1] in attributes:
            return None
        attributes[match[1]] = (html.unescape(match[2] if match[2] is not None else match[3])
                                if match[4] is None else match[4])
        if match[4] is not None:
            expressions.add(match[1])
        position = match.end()
    if (component[1] == 'Link' and set(attributes) <= {'url', 'title'} and
            expressions <= {'url'}):
        url = attributes.get('url', '')
        reference = (re.fullmatch(r'\{(turn[0-9]+[a-z]+[0-9]+)\.url\.uri\}', url)
                     if 'url' in expressions else None)
        target = references.get(reference[1]) if reference else None
        if reference:
            url = target.get('url') if isinstance(target, dict) else None
        title = attributes.get('title', target.get('title') if isinstance(target, dict) else url)
        link = _link(url, title)
        return [link] if link else None
    if component[1] == 'Cite':
        if set(attributes) == {'ref'} and not expressions:
            ids = [attributes['ref']]
        elif set(attributes) == {'refs'} and expressions == {'refs'}:
            try:
                ids = json.loads(attributes['refs'][1:-1])
            except ValueError:
                return None
        else:
            return None
        return citation_inlines(ids, references)
    return None
