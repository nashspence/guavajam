"""Bind supporting citations to exact, structurally contained HTML elements."""
import hashlib
from html.parser import HTMLParser
import re

from asset_policy import AssetError

VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())


def element_fingerprints(raw: bytes, identifiers: set[str]) -> dict:
    text = raw.decode('utf-8')
    offsets = [0] + [match.end() for match in re.finditer('\n', text)]
    found, stack, seen = {}, [], set()

    class Elements(HTMLParser):
        def position(self):
            line, column = self.getpos()
            return offsets[line - 1] + column

        def handle_starttag(self, tag, pairs):
            attrs = dict(pairs)
            identifier = attrs.get('id')
            start = self.position()
            if identifier in identifiers:
                if identifier in seen:
                    raise AssetError('duplicate supporting element identity')
                seen.add(identifier)
            if tag == 'a' and 'press-permalink' in attrs.get('class', '').split():
                target = attrs.get('data-press-passage') or attrs.get('href', '').rsplit('#', 1)[-1]
                if target in identifiers:
                    ancestors = [entry[1] for entry in stack if entry[1] in identifiers]
                    if not ancestors or ancestors[-1] != target:
                        raise AssetError('supporting element permalink is attached to the wrong element')
            if tag in VOID:
                self.save(tag, identifier, start, start + len(self.get_starttag_text()))
            else:
                # A speaker turn has no source-owned ID of its own. Include its
                # exact opening tag in the fingerprints of anchored descendants
                # so removing/relabeling the presentation wrapper is detectable
                # even after external HTML/checksum values are recomputed.
                turn_tag = self.get_starttag_text() if tag in {'div', 'blockquote'} and \
                    'press-turn' in attrs.get('class', '').split() else ''
                stack.append((tag, identifier, start, turn_tag))

        def save(self, tag, identifier, start, end):
            if identifier in identifiers:
                context = ''.join(entry[3] for entry in stack if entry[3])
                found[identifier] = {'tag': tag, 'sha256': hashlib.sha256(
                    (context + text[start:end]).encode()).hexdigest()}

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in VOID:
                saved_tag, identifier, start, _context = stack.pop()
                self.save(saved_tag, identifier, start, self.position() + len(self.get_starttag_text()))

        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not stack or stack[-1][0] != tag:
                raise AssetError('malformed HTML element nesting')
            saved_tag, identifier, start, _context = stack.pop()
            self.save(saved_tag, identifier, start, text.index('>', self.position()) + 1)

    parser = Elements(convert_charrefs=True)
    parser.feed(text); parser.close()
    if stack or set(found) != identifiers:
        raise AssetError('missing or unclosed supporting element')
    return found


def bind_elements(raw: bytes, documents: list[dict]) -> None:
    facts = element_fingerprints(raw, {anchor for doc in documents for anchor in doc['anchors'].values()})
    for doc in documents:
        doc['elements'] = {key: facts[anchor] for key, anchor in doc['anchors'].items()}
