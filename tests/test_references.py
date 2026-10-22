"""`livery references fetch` against a fake showroom: no network in tests."""

import io
import urllib.error
from pathlib import Path

from PIL import Image

from livery import references

PICS = "https://showroom-assets.tradingpaints.gg/pics"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 4), "red").save(buf, "PNG")
    return buf.getvalue()


PAGE = f"""<html><head><meta property="og:image" content="{PICS}/123_AB12.jpg" /></head><body>
<img src="{PICS}/123_AB12.jpg"><img src="{PICS}/123_2.jpg"><img src="{PICS}/123_3.jpg">
[url][img]{PICS}/123_2.jpg[/img][/url]
<img src="https://showroom-assets.tradingpaints.gg/thumbs/123_AB12.jpg"><img src="{PICS}/999_1.jpg">
</body></html>"""


class Showroom:
    """Serves one paint's page and pictures; anything else fails like the network does."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.files = {
            f"{PICS}/123_AB12.jpg": _png(),
            f"{PICS}/123_2.jpg": _png(),
            f"{PICS}/123_3.jpg": b"<html>",
        }

    def __call__(self, req, timeout):
        assert timeout
        assert "Mozilla" in req.get_header("User-agent")
        url = req.full_url
        self.asked.append(url)
        if url.startswith("https://www.tradingpaints.com/showroom/view/123/"):
            return io.BytesIO(PAGE.encode())
        if url in self.files:
            return io.BytesIO(self.files[url])
        raise urllib.error.URLError("unreachable")


def _refs(tmp_path: Path) -> Path:
    path = tmp_path / "references.md"
    path.write_text(
        "| # | Livery | Car | Why | Yes/no |\n|---|---|---|---|---|\n"
        "| 25 | Foo #1 (Trading Paints 123) | x | y | yes |\n"
        "| 26 | Bar (Trading Paints 456) | x | y | yes |\n"
        "| 27 | Baz (Trading Paints 789) | x | y | no |\n"
        "| 28 | Plain | x | y | yes |\n",
        encoding="utf-8",
    )
    return path


def test_fetch_keeps_each_yes_rows_pictures_and_reports_what_failed(ws, tmp_path):
    showroom, said = Showroom(), []

    report = references.fetch(ws, _refs(tmp_path), opener=showroom, log=said.append)

    assert [p.name for p in references.pictures(ws, 25)] == ["123_2.jpg", "123_AB12.jpg"]
    assert report["rows"][25]["failed"] == [f"{PICS}/123_3.jpg"]  # served, but not a picture
    assert "error" in report["rows"][26]
    assert set(report["rows"]) == {25, 26}  # a no row and a row without a paint are left alone
    assert not any("789" in u or "999" in u or "thumbs" in u for u in showroom.asked)
    assert any(f"{PICS}/123_3.jpg" in m for m in said)  # the log names the picture that failed
    assert any("Bar" in m and "456" in m for m in said)  # and the paint whose page did


def test_a_second_fetch_skips_what_it_has(ws, tmp_path):
    refs = _refs(tmp_path)
    references.fetch(ws, refs, opener=Showroom(), log=lambda m: None)
    again = Showroom()

    report = references.fetch(ws, refs, opener=again, log=lambda m: None)

    assert report["rows"][25]["new"] == []
    assert f"{PICS}/123_2.jpg" not in again.asked


def test_the_boring_anchors_come_from_each_designs_latest_sim_run(ws):
    for stamp, colour in (("20261018-130823", "red"), ("20261018-130956", "blue")):
        run = ws.out("fireproof-sweep") / "sim" / stamp
        run.mkdir(parents=True)
        Image.new("RGB", (8, 4), colour).save(run / "side_l.png")

    copied = references.copy_boring(ws, log=lambda m: None)

    assert copied == dict.fromkeys(references.BORING, 0) | {"fireproof-sweep": 1}
    [shot] = references.boring(ws)["fireproof-sweep"]
    assert Image.open(shot).getpixel((0, 0)) == (0, 0, 255)


def test_a_brief_names_its_reference_rows():
    assert references.brief_rows("# Brief\n\nReferences: 25, 32\n") == [25, 32]
    assert references.brief_rows("- **References:** 21 and 30\n") == [21, 30]
    assert references.brief_rows("No anchors here, only 25 words.") == []
