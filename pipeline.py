"""Compatibility import; prefer smilesearcher.run_pipeline (GPL-3.0)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from smilesearcher.pipeline import run_pipeline

__all__ = ["run_pipeline"]
