# Fireproof v3b: 8-bit linework

Nickolaj, 21 Oct 2026, in his words: "Can you help me do another take on the fireproof livery, i really like the pixel motif, but the rest i'm not really sold on, and i think a couple of factors fed into it not as good as i've hoped: 1. it was one of the first prototypes, before we've nailed down the workflow and preview tools 2. I was lazy with the feedback 3. Irrelevant \"requirements\" from the design skill, like having a huge focus on the wordmark (which would be relevant for a real sponsor, but for my own brand, i'd rather it just being cool-looking). So can you help me do another similar design, please avoid grounding it too much in the initial design, and use the color scheme from this file: github.com/nickolaj-jepsen/nixos modules/base/theme.nix"

What holds:

- A new Fireproof car on the kit mclaren-720s-gt3-evo, not a revision of fireproof-v2 or fireproof-stripes: keep the pixel motif (hard square pixels, stepped blocks, pixel dissolves, embers), drop their layout (orange car, yellow band nose to tail, yellow spine, black tail wedge, flame tongues). Built from this brief, not from those designs' code.
- Palette: his Fireproof desktop theme (Flexoki-based), not the pixel flame's #f46200/#ff8000/#ffd500. Neutrals: black #100F0F, bg #1C1B1A, bgAlt #282726, ui #343331, uiAlt #403E3C, muted #878580, fgAlt #B7B5AC, paper #DAD8CE, bright paper #F2F0E5. Accent #CF6A4C (terracotta) with its dark container #6B3528. Other hues, each with a darker alt: red #D14D41/#AF3029, orange #DA702C/#BC5215, yellow #D0A215/#AD8301, green #879A39/#66800B, cyan #3AA99F/#24837B, blue #4385BE/#205EA6, purple #8B7EC8/#5E409D, magenta #CE5D97/#A02F6F. The identity is the warm dark greys, the paper and the terracotta; at most three hue families. Hex values as given.
- Cool-looking first. No wordmark-led layout and no big type: Fireproof is optional, at most one small discreet mark in one palette colour (he disliked the pixel wordmark on the car, 19 Oct). The design leads. Small roster sponsors as the raced-car ecosystem, one colour per field.
- Number zones clear, on one solid field with contrast for the white number; edges clearly cross or avoid seams and shut lines.

References: 34, 32

## The concept

- **Source:** Favourite 34, SPIRIT Dallara P217: a dark base covered in panels of geometric linework (triangles, zigzags, hatching) in warm inks, panels of very different sizes over every surface, accent mirrors, cool-coloured numbers. Here every line is drawn as pixels: 8-bit stair patterns in terracotta on warm black, with Flexoki cyan as the cool accent.
- **Expected (banned):** a black car with terracotta squares sprinkled evenly over it plus a terracotta stripe (the pixel motif as wallpaper). Nor a straight SPIRIT copy: smooth diagonal linework that isn't pixel at all, laid out evenly with no direction.
- **The move:** every painted surface is a panel of 8-bit linework. The only solid colour is the black between the panels, the number windows, the sills and the cyan accents.

## Three distances

- **50 m:** a warm black car glowing terracotta all over, brightest along one raked diagonal. Base #100F0F about 55%; panels in terracotta #CF6A4C about 30%, the dim ones in #6B3528; cyan #3AA99F about 10% (mirror shells, splitter lip, wing endplates, the outlines of a few panels at nose and tail). The panels are largest and full terracotta along a band raked with the lean: low on the front fender, up across the top of the door and the haunch, over the roof. Towards the lower nose and lower tail they get smaller and dimmer (#6B3528), so the pattern has one direction and is not an even print.
- **10 m:** raked polygons (parallelograms, trapezoids, stepped triangles) from a quarter door to a whole door in size, each in one ink and filled with one 8-bit pattern: stair-step sawtooth, concentric stepped diamonds, a two-cell checker dither, rows of pixel chevrons pointing back, staircase hatching, a grid of ember squares. Each panel has a stepped outline in its own ink, with black gaps between panels: three weights (solid panel areas, outlines, pattern strokes). Every stair climbs the same way, towards the tail: the one lean. Sills and lower body (below the intake crease, rising to the tail as a wedge) are plain #100F0F and carry the sponsors. The nose is panels too: fender noses, front fenders and bonnet, the intake strakes in terracotta. Number windows are plain black on each door (number_l, number_r) and the bonnet (number_top), clear of every panel and framed by a stepped cyan keyline.
- **1 m:** the large terracotta panels carry a second, finer pixel pattern in #6B3528. The black base carries a tone-on-tone pixel weave in #1C1B1A. Small Space Mono tech labels in #878580; race markings #DAD8CE on the black. 12-16 roster sponsors in #DAD8CE on the black sills (clear of the carbon side blade), nose corners and rear bumper, never on a panel. Fireproof: none, or the flame mark once, mono #CF6A4C and small, on the rear face.

Swatches: #100F0F, #1C1B1A, #CF6A4C, #6B3528, #3AA99F, #DAD8CE, #878580.
