"""Locate immutable auxiliary resources bundled with core."""
from pathlib import Path

ROOT = Path(__file__).parent / 'resources'

if __name__ == '__main__':
    print(ROOT / 'commands.mk')
