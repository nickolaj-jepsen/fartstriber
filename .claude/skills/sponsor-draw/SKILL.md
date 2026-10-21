---
name: sponsor-draw
description: Drawing a Fartstriber sponsor brand with livery.sponsors - a roster brand or a design's own themed brand - from its lettering voice to font outlines or exact geometry, the read check, the brand.toml measures and the review sheet. Use before drawing or redrawing any brand.
---

# Drawing a brand

A brand is a folder: its `draw.py`, the one-colour SVG variants it writes (`wordmark`, `mark`, `lockup`) and `brand.toml`. How builds place them is in `docs/architecture.md` (Sponsors).

## Where it goes

- A roster brand is a row in `assets/sponsors/roster.md`, drawn in `assets/sponsors/<brand>/`, in a sponsor session, never a design session. Write the row (status blank) and draw it; the owner judges the name and the drawing together in `livery review --sponsors`, one tile per brand: fine makes the row a yes and approves the variants, no turns the row down and keeps the drawing for a redraw. There is no separate roster review.
- A design's own themed brand goes in `designs/<name>/sponsors/<brand>/`; it is found before the roster and needs no approval. One worth reusing is proposed to the owner as a roster row.

## Draw

1. **Voice first.** The row's lettering voice and its OFL font in `assets/fonts`: the letters are the voice, the mark carries one idea.
2. **The font's own outlines, or exact geometry.** `sponsors.outline(text, font, size, tracking, slant)` sets the font's glyphs as one path; kern pairs by hand in draw.py. Drawn letters are built in shapely from true circles, rectangles and level or plumb cuts.
3. **Never buffer, erode, open or round-fill letters, bridge joins or redraw them at odd widths to pass a size check.** That is what made Altmere and Ostrand read as an "ai generated/diffusion model" vibe (`prefs/log.md`, 19 Oct 2026). A mark that can't hold at sill size keeps its shape and the larger minimum height its measures give; say in roster.md where it goes (roof, bonnet, doors).
4. **Two stroke weights**, the same on every letter.
5. **One colour, paths only, a tight viewBox** (`sponsors.path_svg`, `svg_doc`). `sponsors.lockup_svg(mark, word)` sets the two as drawn. One hand-made touch per brand.
6. `sponsors.save(brand_dir, {...})` writes the SVGs and `brand.toml`. An unchanged variant keeps its approval; a redrawn one goes back to review.

## Check

- **The word reads.** Render each variant 120 px tall (`sponsors.render(svg, 120)`), save it and read it letter by letter: no case flips (telVio, aLtmere), no doubled letters (Z zentiq), no stray diacritic (ØSTRAND), no lumps.
- **The measures.** `brand.toml` gives each variant's aspect, ink share, thinnest stroke and narrowest counter, as shares of its height. Its minimum height is `min_stroke_px` (`policy.toml` `[lint]`) over the smaller of stroke and counter, and `sponsor` refuses it lower.
- **The sheet.** `sponsors.sheet(brand, out, min_stroke_px)` draws what `livery review --sponsors --brand <brand>` shows the owner: each variant dark on light and light on dark, large and at sill size, with its minimum height. Write it from draw.py and look at it before the review.
