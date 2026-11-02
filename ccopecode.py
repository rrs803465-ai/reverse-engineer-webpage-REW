#!/usr/bin/env python3
"""REW — Reverse Engineer Website.

Entry point. Run: python3 ccopecode.py
"""

import sys

if sys.version_info < (3, 11):
    sys.exit("REW requires Python 3.11 or newer.")

from rea.cli import app

if __name__ == "__main__":
    app()
