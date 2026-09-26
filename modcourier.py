#!/usr/bin/env python3
"""Run directly from a checkout; installation is not required."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("ModCourier needs Python 3.11 or newer.")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from modcourier.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
