"""The pictures behind references.md, for builders and the critic: each yes row's Trading Paints showroom
pictures, and the sim shots of the retired designs the owner called boring as negative anchors. They are
other people's work (or retired), so they live in .cache/references/ and are never committed."""

import io
import re
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from livery.config import Workspace
from livery.review.items import reference_rows

# Any slug resolves to the paint's page; the bare id is a 404.
SHOWROOM = "https://www.tradingpaints.com/showroom/view/{id}/{slug}"
PICS = r"https://showroom-assets\.tradingpaints\.gg/pics/{id}_[A-Za-z0-9]+\.(?:jpe?g|png|webp)"
OG_IMAGE = re.compile(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"')
TP_ID = re.compile(r"\(?Trading Paints (\d+)\)?")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TIMEOUT_S = 20
# "they are just too boring" (prefs/log.md, 18 Oct 2026): retired, but their shots show what not to make.
BORING = ("fireproof-sweep", "fireproof-blocks")
BRIEF_ROWS = re.compile(r"^\W*references?\W*:(.*)$", re.I | re.M)
PICTURE = {".jpg", ".jpeg", ".png", ".webp"}

Opener = Callable[..., Any]  # urllib.request.urlopen, or a fake in tests


def folder(ws: Workspace) -> Path:
    return ws.root / ".cache" / "references"


def showroom_rows(path: Path) -> dict[int, tuple[str, str]]:
    """Yes rows with a Trading Paints paint: row number -> (paint id, livery name without the id)."""
    rows = {}
    for n, name, _car, _why, mark in reference_rows(path):
        m = TP_ID.search(name)
        if m and mark == "yes":
            rows[n] = (m[1], TP_ID.sub("", name).strip())
    return rows


def image_urls(html: str, paint_id: str) -> list[str]:
    """The page's og:image, then every showroom picture of this paint in page order, each once."""
    found = [m[1] for m in OG_IMAGE.finditer(html)] + re.findall(PICS.format(id=paint_id), html)
    pics = re.compile(PICS.format(id=paint_id))
    return list(dict.fromkeys(u for u in found if pics.fullmatch(u)))


def _get(url: str, opener: Opener) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=TIMEOUT_S) as r:
        return r.read()


def _row(ws: Workspace, row: int, paint_id: str, name: str, opener: Opener, log) -> dict:
    d = folder(ws) / str(row)
    have = sorted(p.name for p in d.glob("*") if p.suffix.lower() in PICTURE)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "paint"
    try:
        html = _get(SHOWROOM.format(id=paint_id, slug=slug), opener).decode("utf-8", "replace")
    except (OSError, ValueError) as e:  # URLError, HTTPError and timeouts are OSErrors
        log(f"{row} {name}: the showroom page of paint {paint_id} failed ({e}); kept {len(have)} cached")
        return {"cached": len(have), "error": str(e)}
    urls = image_urls(html, paint_id)
    if not urls:
        log(f"{row} {name}: no pictures on the showroom page of paint {paint_id}; kept {len(have)} cached")
        return {"cached": len(have), "error": "no pictures on the page"}
    d.mkdir(parents=True, exist_ok=True)
    got, failed = [], []
    for url in urls:
        dest = d / url.rsplit("/", 1)[1]
        if dest.exists():
            continue
        try:
            data = _get(url, opener)
            Image.open(io.BytesIO(data)).verify()  # an error page served as 200 is not a picture
        except (OSError, ValueError, SyntaxError) as e:
            log(f"{row} {name}: {url} failed ({e})")
            failed.append(url)
            continue
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(data)
        part.replace(dest)
        got.append(dest.name)
    cached = len(urls) - len(got) - len(failed)
    log(
        f"{row} {name}: {len(got)} new, {cached} already cached"
        + (f", {len(failed)} failed" if failed else "")
    )
    return {"new": got, "cached": cached, "failed": failed}


def copy_boring(ws: Workspace, log) -> dict[str, int]:
    """The latest sim shots of each retired boring design, into anchors/boring/<name>/, skipping what is
    there. A design whose shots are gone from .cache keeps whatever was copied before."""
    copied = {}
    for name in BORING:
        runs = sorted((ws.out(name) / "sim").glob("*/"))
        dest = folder(ws) / "anchors" / "boring" / name
        if not runs:
            log(
                f"anchor {name}: no sim shots in {ws.out(name) / 'sim'}; kept {len(list(dest.glob('*.png')))}"
            )
            copied[name] = 0
            continue
        dest.mkdir(parents=True, exist_ok=True)
        new = [p for p in sorted(runs[-1].glob("*.png")) if not (dest / p.name).exists()]
        for p in new:
            shutil.copy2(p, dest / p.name)
        log(f"anchor {name}: {len(new)} new from {runs[-1].name}")
        copied[name] = len(new)
    return copied


def fetch(
    ws: Workspace, path: Path | None = None, opener: Opener = urllib.request.urlopen, log=print
) -> dict:
    """Download each yes row's showroom pictures and copy the boring anchors. A failure is reported per
    row or picture and the rest carry on."""
    showroom = showroom_rows(path or ws.references)
    rows = {row: _row(ws, row, pid, name, opener, log) for row, (pid, name) in showroom.items()}
    return {"rows": rows, "boring": copy_boring(ws, log)}


def brief_rows(brief: str) -> list[int]:
    """The reference rows a brief names, on a line like 'References: 25, 32'."""
    return [int(n) for line in BRIEF_ROWS.findall(brief) for n in re.findall(r"\d+", line)]


def pictures(ws: Workspace, row: int) -> list[Path]:
    return sorted(p for p in (folder(ws) / str(row)).glob("*") if p.suffix.lower() in PICTURE)


def boring(ws: Workspace) -> dict[str, list[Path]]:
    root = folder(ws) / "anchors" / "boring"
    return {name: sorted((root / name).glob("*.png")) for name in BORING if (root / name).is_dir()}
