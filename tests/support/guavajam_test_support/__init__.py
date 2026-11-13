"""Workspace-only signed publisher/media fixtures; never shipped as core or view."""
from pathlib import Path
from guavajam_core.resources import ROOT as RESOURCES

FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures'
