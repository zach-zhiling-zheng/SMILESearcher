"""Compatibility launcher for SMILESearcher (modified 2026-09-27, GPL-3.0)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from smilesearcher.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
