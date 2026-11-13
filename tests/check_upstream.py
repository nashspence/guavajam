"""Compare independent consumer fixtures with their pinned upstream source contract."""
from pathlib import Path
import sys

def check(upstream):
    local=Path(__file__).resolve().parents[1];upstream=Path(upstream)
    pairs=[('scripts/press_media.py','guavajam/media.py'),
           ('templates/work/manuscript.md','tests/fixtures/manuscript.md'),
           ('templates/work/supporting/note.md','tests/fixtures/note.md'),
           ('tests/fixtures/chatgpt/lantern-current.gfm.md','tests/fixtures/lantern-current.gfm.md'),
           ('tests/fixtures/chatgpt/lantern-alternate.gfm.md','tests/fixtures/lantern-alternate.gfm.md')]
    for source,destination in pairs:
        assert (upstream/source).read_bytes()==(local/destination).read_bytes(),destination+' differs from pinned upstream; review contract migration explicitly'
    print('Pinned upstream media contract, copied starters and both reviewed Lantern branches match.')

if __name__=='__main__':check(sys.argv[1])
