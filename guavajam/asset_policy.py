"""Local error and URL policy for a standalone view; never reads storage credentials."""
from pathlib import Path
import json,re
from .media import asset
class AssetError(ValueError):pass
class AssetPolicy:
    def __init__(self,origin):
        if not isinstance(origin,str) or not re.fullmatch(r'https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',origin):raise AssetError('invalid canonical HTTPS asset origin')
        self.origin=origin
    def manages(self,url):return url.startswith(self.origin+'/')
    def parse(self,url):return asset(url,self.origin,{'png','jpg','jpeg','webp','svg','bin','mp3','mp4','vtt'})
    def parse_image(self,url):return asset(url,self.origin,{'png','jpg','jpeg','webp','svg'})
def load_policy(root):
    path=Path(root)/'asset-policy.json'
    return AssetPolicy(json.loads(path.read_text())['origin']) if path.exists() else None
