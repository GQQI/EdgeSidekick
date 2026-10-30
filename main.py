#!/usr/bin/env python3
"""EdgeSidekick entry — run from repo root: python main.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
src = str(SRC)
if src not in sys.path:
    sys.path.insert(0, src)

from metateam.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    raise SystemExit(main())
