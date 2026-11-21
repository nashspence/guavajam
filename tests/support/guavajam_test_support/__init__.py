"""Workspace-only signed publisher/media fixtures; never shipped as GuavaJam or a website generator."""
from pathlib import Path
from guavajam.resources import ROOT as RESOURCES

FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures'
