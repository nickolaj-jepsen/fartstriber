# fireproof-v3e: LOD

## The owner's brief

Fireproof v3. Nickolaj, 21 Oct 2026, in his words: "Can you help me do another take on the fireproof livery, i really like the pixel motif, but the rest i'm not really sold on, and i think a couple of factors fed into it not as good as i've hoped: 1. it was one of the first prototypes, before we've nailed down the workflow and preview tools 2. I was lazy with the feedback 3. Irrelevant \"requirements\" from the design skill, like having a huge focus on the wordmark (which would be relevant for a real sponsor, but for my own brand, i'd rather it just being cool-looking). So can you help me do another similar design, please avoid grounding it too much in the initial design, and use the color scheme from this file: github.com/nickolaj-jepsen/nixos modules/base/theme.nix"

What holds:

- A new Fireproof car on the kit mclaren-720s-gt3-evo, not a revision of fireproof-v2 or fireproof-stripes: keep the pixel motif (hard square pixels, stepped blocks, pixel dissolves, embers), drop their layout (orange car, yellow band nose to tail, yellow spine, black tail wedge, flame tongues).
- Palette: his Fireproof desktop theme (Flexoki-based). Neutrals: black #100F0F, bg #1C1B1A, bgAlt #282726, ui #343331, uiAlt #403E3C, muted #878580, fgAlt #B7B5AC, paper #DAD8CE, bright paper #F2F0E5. Accent #CF6A4C (terracotta) with its dark container #6B3528. Other hues (one or two at most, within three hue families): red #D14D41/#AF3029, orange #DA702C/#BC5215, yellow #D0A215/#AD8301, green #879A39/#66800B, cyan #3AA99F/#24837B, blue #4385BE/#205EA6, purple #8B7EC8/#5E409D, magenta #CE5D97/#A02F6F.
- Cool-looking first. No wordmark-led layout and no big type: Fireproof is optional, at most one small discreet mark in one palette colour. The design leads. Small roster sponsors as the raced-car ecosystem, one colour per field.
- Number zones clear, on one solid field with contrast for the white number; edges clearly cross or avoid seams and shut lines.

## The concept

- **Source:** a one-sentence story: the game can't render the car as fast as it goes, so it is crisp at the nose and breaks into ever-bigger pixels towards the tail.
- **Expected (banned):** a paper car with a pixel dissolve from black at the rear to white at the front: Trilateral done with squares, where the colour changes along the car. Here the colour stays the same and only the resolution changes.
- **The move:** the same raked black slash drawn five times along the car, each at half the resolution of the one in front: a crisp vector bar on the nose, huge blocks at the tail.

References: 30, 26, 22

- **50 m:** a paper car (#DAD8CE, about 60%) with a rhythm of dark raked slashes that get heavier and blockier towards the tail. Black #100F0F slashes, sills and lower bumper about 30%; terracotta #CF6A4C as the trailing edge of every slash about 10%. The slashes lean like the windscreen, top towards the tail, and carry over the top as bars across bonnet, roof, engine cover and rear deck, so the top shows the same crisp-to-blocky sequence. The only light-led concept.
- **10 m:** slash 1 (LOD 0), over the front fender and bonnet, is a clean straight-edged bar with a terracotta pinline on its trailing edge. Slash 2 (LOD 1), on the door, has edges stepped in small squares; slash 3 (LOD 2), behind the door shut line, medium steps; slash 4 (LOD 3), on the haunch, big blocks kept off the door light panel; slash 5 (LOD 4), at the tail, a few huge blocks, with loose blocks breaking off backwards over the rear deck and bumper. The terracotta trailing edge pixelates at the same rate. Every stair edge is anti-aliased: its partly covered cells are half tones (#878580 and #B7B5AC on paper, #6B3528 between black and terracotta). The black sill is one height nose to tail and its top edge pixelates with the slash above it. Crisp black splitter and lower bumper with a terracotta splitter lip; terracotta mirror shells. number_l and number_r lie inside slash 2's solid black, number_top inside the crisp bonnet bar.
- **1 m:** the anti-alias half tones; small Space Mono labels in #878580 beside each slash, LOD 0 to LOD 4, like a renderer's debug overlay; race markings in #100F0F on paper; 12-16 roster sponsors, #DAD8CE on the black sills (clear of the carbon side blade), #100F0F on the paper nose corners and haunch. Fireproof: none, or the flame mark once, mono #CF6A4C and small, on a nose corner (the one thing drawn at native resolution).

Swatches: #DAD8CE, #100F0F, #CF6A4C, #6B3528, #B7B5AC, #878580.
