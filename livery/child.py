"""Fresh-interpreter build for `livery check`: writes the output digests as JSON to --out, not stdout,
which a design's print() would share."""

import argparse
import json
from pathlib import Path

from livery.check import digests
from livery.config import Workspace
from livery.designs import render_outputs

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("design")
    a = ap.parse_args()
    found = digests(render_outputs(Workspace(a.root, a.assets), a.design))
    a.out.write_text(json.dumps(found), encoding="utf-8", newline="\n")
