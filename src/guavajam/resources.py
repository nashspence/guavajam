"""Locate immutable auxiliary resources bundled with GuavaJam."""
from pathlib import Path

ROOT = Path(__file__).parent / 'resources'

if __name__ == '__main__':
    print(ROOT / 'commands.mk')
