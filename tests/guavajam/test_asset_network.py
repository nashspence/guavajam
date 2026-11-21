"""Private destinations require independent per-origin operator authority."""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from guavajam.asset_policy import AssetError, public_addresses


class PrivateAssetNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        previous = Path.cwd()
        self.source = self.root / 'source'; self.source.mkdir(); (self.source / '.git').mkdir()
        os.chdir(self.source); self.addCleanup(os.chdir, previous)
        self.policy = self.root / 'network.json'
        self.policy.write_text(json.dumps({'origin': 'https://assets.publisher.test', 'networks': ['192.168.50.10/32']}))
        self.env = patch.dict(os.environ, {'GUAVAJAM_ASSET_NETWORK_POLICY': str(self.policy)})
        self.env.start(); self.addCleanup(self.env.stop)

    def resolve(self, host, addresses):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
        with patch('socket.getaddrinfo', return_value=records):
            return public_addresses(host)

    def test_only_named_origin_and_complete_dns_answers_are_authorized(self):
        self.assertEqual(['192.168.50.10'], self.resolve('assets.publisher.test', ['192.168.50.10']))
        for host, addresses in [('other.publisher.test', ['192.168.50.10']),
                                ('assets.publisher.test', ['192.168.50.11']),
                                ('assets.publisher.test', ['192.168.50.10', '169.254.169.254'])]:
            with self.subTest(host=host, addresses=addresses), self.assertRaises(AssetError):
                self.resolve(host, addresses)
        self.assertEqual(['93.184.216.34'], self.resolve('other.publisher.test', ['93.184.216.34']))

    def test_public_default_and_independent_policy_boundary(self):
        with patch.dict(os.environ, {'GUAVAJAM_ASSET_NETWORK_POLICY': ''}):
            with self.assertRaises(AssetError):self.resolve('assets.publisher.test', ['192.168.50.10'])
        self.policy.chmod(0o666)
        with self.assertRaises(AssetError):self.resolve('assets.publisher.test', ['192.168.50.10'])
        self.policy.chmod(0o600)
        inside = self.source / 'network.json'; inside.write_bytes(self.policy.read_bytes())
        with patch.dict(os.environ, {'GUAVAJAM_ASSET_NETWORK_POLICY': str(inside)}):
            with self.assertRaises(AssetError):self.resolve('assets.publisher.test', ['192.168.50.10'])

    def test_invalid_authorities_fail_without_widening_destinations(self):
        for networks in [[], ['0.0.0.0/0'], ['169.254.0.0/16'], ['93.184.216.0/24'], ['192.168.50.10/24']]:
            self.policy.write_text(json.dumps({'origin': 'https://assets.publisher.test', 'networks': networks}))
            with self.subTest(networks=networks), self.assertRaises(AssetError):
                self.resolve('assets.publisher.test', ['192.168.50.10'])
