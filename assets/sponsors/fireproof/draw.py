"""Fireproof, Nickolaj's brand, from his Fireproof design system:
the pixel flame, the pixel wordmark ("fire" in flame-outer, "proof" in fg) and the lockup, all on one pixel
grid. They are his artwork, copied as they are and never redrawn; this only measures them.

uv run python assets/sponsors/fireproof/draw.py [<wordmark.svg> <lockup.svg>]

With files given, they replace wordmark.svg and lockup.svg first (the design system's fireproof-wordmark.svg
and fireproof-lockup.svg, the dark-ground versions).
"""

import sys
from pathlib import Path

from livery import sponsors

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    files = {v: HERE / f"{v}.svg" for v in ("lockup", "wordmark", "mark")}
    files.update(zip(("wordmark", "lockup"), map(Path, sys.argv[1:3]), strict=False))
    # Passed to save() as read, so a replaced file counts as changed and goes back to review.
    variants = {v: p.read_bytes().decode("utf-8") for v, p in files.items()}
    print(sponsors.save(HERE, variants, source="owned"))
