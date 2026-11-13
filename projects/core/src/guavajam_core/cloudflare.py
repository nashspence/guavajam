"""Private Cloudflare deployment credentials, parsed only as data."""
import os,re,stat,shlex
from .asset_policy import AssetError

def cloudflare_values(environ=None):
    env=dict(os.environ if environ is None else environ)
    path=env.get('CLOUDFLARE_ENV')
    if path:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>16384:
                raise AssetError('CLOUDFLARE_ENV must be an owner-only regular data file')
            for line in stream.read().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):continue
                key,sep,value=line.partition('=');parts=shlex.split(value,comments=True)
                if sep and len(parts)==1 and key in {'ACCOUNT','TOKEN','CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN','CLOUDFLARE_ZONE_ID'}:env[key]=parts[0]
    account=env.get('CLOUDFLARE_ACCOUNT_ID',env.get('ACCOUNT',''));token=env.get('CLOUDFLARE_API_TOKEN',env.get('TOKEN',''))
    if not re.fullmatch('[0-9a-f]{32}',account) or not token:raise AssetError('configure Cloudflare account and token through environment or CLOUDFLARE_ENV')
    return account,token

