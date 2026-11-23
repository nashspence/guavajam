"""Installed entry point; repository commands use the caller's Git root."""
import argparse
import importlib
import sys

COMMANDS = {
    'validate-sources': 'validate_sources', 'verify-assets': 'verify_source_assets',
    'check-publications': 'check_publications', 'publication-check': 'publication_commit',
    'publication-prepare': 'prepare_publication', 'prepare-asset': 'prepare_asset',
    'assets': 'manage_assets', 'asset-workflow': 'asset_workflow',
    'assets-qualify': 'qualify_assets', 'assets-release': 'asset_release',
    'asset-policy': 'configure_asset_maintenance', 'asset-pin': 'asset_versions',
    'gateway': 'deploy_asset_gateway', 'archive': 'source_archive',
    'catalog-export': 'export_catalog',
    'init': 'bootstrap', 'resources': 'starters',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, usage='%(prog)s COMMAND [arguments]')
    parser.add_argument('command', choices=COMMANDS)
    args = parser.parse_args(sys.argv[1:2])
    command = args.command
    sys.argv = [sys.argv[0] + ' ' + command, *sys.argv[2:]]
    if command == 'init':
        return importlib.import_module('.bootstrap', __package__).main()
    if command == 'resources':
        return importlib.import_module('.starters', __package__).main()
    try:
        return importlib.import_module('.' + COMMANDS[command], __package__).main()
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
