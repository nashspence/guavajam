"""Install the official checksummed Pandoc package while building the image."""
import hashlib
from pathlib import Path
import subprocess
import urllib.request

architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
checksums = {
    'amd64': 'b4217ac7c54067c442f9d4cd38bcd0c184a43f3e7053431a12263a6f92a33ba2',
    'arm64': '38f44718c0cb15cb81fc31026bd68c33e8628ca5f7ab15ca86494a7f24f7b8e4',
}
expected = checksums[architecture]
url = 'https://github.com/jgm/pandoc/releases/download/3.12.1/pandoc-3.12.1-1-' + architecture + '.deb'
path = Path('/tmp/pandoc.deb')
try:
    with urllib.request.urlopen(url, timeout=120) as source, path.open('wb') as destination:
        while chunk := source.read(65536):
            destination.write(chunk)
    with path.open('rb') as artifact:
        if hashlib.file_digest(artifact, 'sha256').hexdigest() != expected:
            raise ValueError('Pandoc artifact checksum differs')
    subprocess.run(['dpkg', '-i', str(path)], check=True)
finally:
    path.unlink(missing_ok=True)
