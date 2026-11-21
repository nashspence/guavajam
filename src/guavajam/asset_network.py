"""Operator-owned exceptions for one canonical asset origin's private network."""
import ipaddress
import json
import os
from pathlib import Path
from .asset_policy import AssetError, HOST


def allowed_networks(host):
    configured = os.environ.get('GUAVAJAM_ASSET_NETWORK_POLICY')
    if not configured:
        return ()
    path = Path(configured)
    if (not path.is_absolute() or path.is_symlink() or path.resolve() != path
            or not path.is_file() or path.stat().st_size > 16384
            or path.stat().st_mode & 0o022):
        raise AssetError('asset network policy must be an absolute, operator-controlled file')
    # A checked-out publisher must never provide its own network authority.
    for parent in (Path.cwd(), *Path.cwd().parents):
        if (parent / '.git').exists():
            if path.is_relative_to(parent):
                raise AssetError('asset network authority must be outside the source repository')
            break
    try:
        value = json.loads(path.read_text('utf-8'))
        if not isinstance(value, dict) or set(value) != {'origin', 'networks'}:
            raise ValueError()
        origin = value['origin']
        if not isinstance(origin, str) or not origin.startswith('https://') or not HOST.fullmatch(origin[8:]):
            raise ValueError()
        supplied = value['networks']
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 32:
            raise ValueError()
        networks = tuple(ipaddress.ip_network(item, strict=True) for item in supplied)
        # Never admit every destination or a metadata/link-local address range.
        for network in networks:
            if (network.prefixlen == 0 or network.is_multicast or network.is_link_local
                    or not all(ipaddress.ip_address(address).is_private
                               for address in (network.network_address, network.broadcast_address))):
                raise ValueError()
    except (OSError, ValueError, TypeError, KeyError):
        raise AssetError('invalid private asset network policy') from None
    return networks if origin == 'https://' + host else ()
