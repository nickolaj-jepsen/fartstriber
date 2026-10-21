# The sim, as measured

Measured on the McLaren 720S GT3 EVO and the preview replay (`fartstriper.rpy`: Daytona road course, a team event, Lone Qualify). How the tools use these facts is in `docs/architecture.md`; the camera set formats are in `livery/cameras.py`.

## Paint files

- The paint folder is the session's `CarPath` (`mclaren720sgt3`; the EVO has no folder of its own).
- A team session paints the car from `car_team_<TeamID>.tga` and `car_spec_team_<TeamID>.tga`, and ignores `car_<customerID>.tga`. Other sessions use the customer names. The session's `TeamID` says which applies.
- On a reload the sim writes a `.mip` next to a new spec TGA, only for the name the loaded session uses. While the spec TGA stays in the folder, the sim rebuilds the `.mip` on every launch.
- An uncompressed 24-bit TGA (image type 2, bottom-left origin) renders the right way round. A 32-bit TGA with alpha can render black.
- The sim stamps the car's number inside the template's number blocks: on the McLaren, the doors and the bonnet.
- Texture x runs from nose to tail on both sides and on the top view. The left side is the bottom strip and reads upright; the right side is the top strip, upside down relative to the left.
- Islands need bleed. Without it, every panel is outlined by thin lines of whatever lies between the islands:

| Bleed | Stray pixels per shot |
|---|---|
| 0 px | lines around every panel |
| 4 px | a few hundred |
| 8 px | about a hundred |
| 16 px | none |

## SDK and replay

- pyirsdk sends broadcasts as window messages (`SendNotifyMessageW` to `HWND_BROADCAST`), so there is no input and no focus change. Broadcasts switch cameras, set the camera state, move and search the replay, and reload textures.
- The car is found by `UserID` in `DriverInfo`, in team sessions too.
- In lone qualifying only the viewed car is drawn, so traffic never covers a shot.
- Seeking to a frame number lands exactly. A session-time search lands up to 12 frames (190 ms) off, depending on where the replay was.
- A reload goes old paint, then plain grey (after 0.6–0.8 s), then new paint (0.6–1.8 s later); about 3 s in all when the spec changes too.
  - A paint close to the loading grey barely changes the frame.
  - The change shows as the share of clearly changed pixels where the car is large. A mean over the whole frame misses it.
- `CameraState.ui_hidden` hides the replay UI, but the sim still draws it while the cursor is over its window:
  - the session bar across the top seventh
  - the driver, replay and chat panels across the bottom seventh
  - the track map and frame rate down the right

## Capture

- Windows Graphics Capture finds the sim by its process, `iRacingSim64DX11.exe`. A title match also finds other apps' windows, such as SimHub's "Overlay control : iRacing".
- It captures the sim behind other windows without bringing it forward: 0.5–1.0 s per frame from a new capture session.
- A windowed capture includes the title bar and a 1 px border. At 100% scaling the client area starts 31 px down and 1 px in.
- A run must stop when:
  - the window is minimised (restoring it would take focus)
  - a frame comes back blank
  - the sim comes to the front, most likely from a click into it

  The owner switching between other windows is fine.

## Camera sets

- The sim reads a car set (`cameras\cars\<car>\car.cam`) or a track set (`cameras\tracks\<track>\track.cam`) only when a session opens, and only while `camera.ini` points at it (`LastCarSet` or `LastTrackSet`). The sim rewrites `camera.ini` on exit, so it can only be changed while the sim is closed.
- A replay can't be opened from outside: `iRacingUI.exe <file.rpy>` does nothing, `iracing://` links only switch UI pages, and the sim's command line can't be read. Every camera set change needs the owner to reopen the replay.
- Track sets:
  - A track has no set file until the owner saves one from the camera tool (Ctrl+F12); the stock cameras live in the track's encrypted `.dat`. After a save, the sim sets `LastTrackSet` itself on exit.
  - An iRacing update to the track's cameras doesn't reach a saved set until it is saved again.
  - Groups appended to a set load after the stock ones and leave the car's cameras alone. They show in the owner's group list whenever they drive that track.
  - Side views stay square-on through turns, up to 60 px off-centre.
- Car sets:
  - A car set accepts only the car's own camera names. A set with extra names is rejected: the sim uses its stock cameras and clears `LastCarSet` on exit. Moving the stock-named cameras works.
  - Car-set cameras ride with the car but lag it in turns. On a straight the car sits square and centred.
- A type 3 (chase) camera sits in the car's frame (x, y, z in metres from the car's origin) and aims at the car, so its yaw and pitch are ignored.
  - Field 9 raises the aim point, by about 500 px per unit at 25 m and 0.2 rad.
  - At 0 the car sits about 130 px above centre in a side view; 0.27 centres it.
- A camera looking straight down leaves the roll free, so the top view turns with the car's heading. Tilting it 5° to the side or 10° back holds the car to within 15 px across frames.
- The shots repeat: car pixels match exactly in any order of views and across sim sessions. Only animated foliage and a 1 px window border differ.
- A fence or wall sits between camera and car on some frames, so a frame needs a clear line from every camera.
- Framing at the preview poses:
  - At 25 m and 0.2 rad the McLaren's paint spans 1507 × 360 px of a 1782 × 992 px window in a side view.
  - The three-quarter views, at 0.24 rad, show the car about 1100 px wide.
  - In every preview view the car's paint sits within 60 px of the centre.
  - A shot takes about 0.9 s from a running capture session, including the seek and a 0.6 s settle.

## Rendering

- Shots are deterministic given the same path to them. Frames without a reload, reloads of the same paint, and views reached the same way differ on car pixels by at most 2/255 (dither). The same paint an hour later differs by 0.9 levels on average.
- The owner's `rendererDX11Monitor.ini` turns off antialiasing, motion blur, depth of field, SSAO, SSR and dynamic cubemaps.
- Auto exposure (on, with HDR) doesn't react to the paint: the background doesn't move between a white car and a black one.

### Coded patterns

- The sim samples mip m, where a 4×4 compressed block spans 4·2^m texels.
- Gray bit k flips only at odd multiples of 2^k, so it flips inside blocks when k < m + 2.
- A block holds two colours exactly. So a pattern can carry one bit that flips inside blocks, plus coarse bits: all 11 bits fit in 8 patterns, and decode as reliably as one bit per pattern.
- Texels per pixel along the major axis, median (p90):

  | Views | Texels per pixel |
  |---|---|
  | sides | 1.2 (2.4–2.6) |
  | top | 1.2 (1.8–1.9) |
  | preview three-quarters | 2.2–2.3 (4.2–5.1), sampling mip 1 and in places mip 2 |

- All 11 bits in 8 patterns decode 99.9% of the car's pixels at every preview view. The plane fit lands inside the raw decode's interval on 96% of pixels in the front three-quarter view and 97–99% in the others.
- A plane fit from bits 3–10 places a pixel to a median 0.3 and p95 1.0 texel; from bits 2–10, 0.1 and 0.4.

### Materials

A grey ladder (0, 40, 80, 120, 160, 200, 255) under each kit material, shot at the preview views on frame 2917:

- Light: the sun lights the car's left. White under gloss shows a median 239 on the left side, 197–234 in the three-quarter and top views, and 104 on the shaded right side. Black under gloss shows 47–58 (17 on the right): reflections.
- Two shots of the same paint differ by at most 2 levels at the 99th percentile.
- Metallic renders paint about half as bright (a 150 grey shows about 76), with sharp reflections. Above mid-grey it can't reach what gloss shows:

  | | front34 | side_l | rear34 | top | side_r |
  |---|---|---|---|---|---|
  | brightest gloss grey metallic can match | 112 | 159 | 156 | 117 | 241 |

  Over the median of the views, metallic needs 109 to show what gloss shows at 40, 185 for 80 and 247 for 120. Nothing above about 126 can be matched.
- Reach per colour channel (R, G, B, the median over each view's pixels, then over the views): metallic 105, 125, 157; chrome 89, 115, 146. The light is bluer than the paint.
- White clips at 255 under metallic on 14% of the left side, 5% of the rear three-quarter, 3% of the top, and 1.5% of the front three-quarter and right side. Under chrome it clips on at most 1.4%.
- Satin and clearcoat:
  - With a full clearcoat, satin renders as gloss outside highlights: at most 2–3 levels apart at the 99th percentile.
  - That holds at every roughness from 70 to 190 in the front three-quarter, left side and top views. It differs in the right side's highlights and in the rear three-quarter view.
  - With clearcoat 0, 64 or 128, satin reads apart from gloss in every view. Clearcoat 64 is at least a median 5 levels from gloss and 8 from matte everywhere.
- Matte (roughness 220, clearcoat 0) is p95 7 levels from gloss outside highlights, and lifts black by 3–10 levels.
- Under gloss, clearcoat 0 and 255 are hard to tell apart by eye at preview distance.

## The UI preview bake

- The UI ships a preview of every car in `httproot/cars/<car>/<car>.dat`: about 2 MB, in the same record container as the car's own `.dat`, all plain zstd.
- It holds one front three-quarter view of the car's right side, nose to the right, at three sizes; the largest is 508×272.
- That size has a run of frames, a 1016×544 32-bit UV map and a `WebUVDescriptor` (number placement).
  - The frames: stock liveries; red, green and blue pattern masks; then ten fixed layers. The last is the silhouette with its ground shadow; the fourth from the end is a grey car under the preview light.
  - The frame count varies with the stock liveries: 34–37 on four GT3s.
- In the UV map, u is G·256+B and v is A·256+R, in 65536ths of the paint texture from its top-left corner.
- The Porsche 992 R, Ferrari 296 and BMW M4 GT3 lay out the same way as the McLaren.
- It has no spec map, number or sim light.

## The paint template

The McLaren's PSD is 2048×2048, RGB, 8-bit, with 51 layers. The top view sits in the middle with the sides above and below it, and small parts (mirrors, wing, splitter) fill the edges. psd-tools' `composite()` returns hidden layers empty; `topil()` reads them.

| Layer | Holds |
|---|---|
| `Turn Off Before Exporting TGA/Mask` | brown (83, 71, 65) over the unused texture. 78% of the canvas is paintable, as one connected region, so the islands come from `Wire` |
| `…/Wire` (hidden) | white mesh lines and dashed green (1, 255, 0) island outlines |
| `…/Number Blocks` (hidden) | magenta (236, 0, 140) rectangles, where the sim stamps the number |
| `…/Sponsor Blocks` (hidden) | green (0, 166, 81) series sponsor areas, orange (242, 108, 79) windscreen and rear banners, bright green (1, 255, 0) small blocks |
| `…/Car Mandatory` | a dark overlay on grilles, vents, lights and other parts the paint never shows on |
| `Paintable Area/Car_decal`, `IMSA Decals GTD PRO` | visible decal layers: McLaren marks and series decals |
| `Paintable Area/Car Patterns` (hidden) | 26 stock patterns |
| `Custom Spec Map` | R metallic, G roughness, B clearcoat. Base: metallic 0, roughness 38, clearcoat 0; its `Parts` layers put about 240 metallic and 55 roughness on trim |

## The offline bake

- A capture of the five preview views (`kit capture`: 18 geometry and 35 ladder paints, each a sim run with its own reload) takes about 12 minutes, 11–18 s a paint. It keeps 114 MB once decoded.
- A bake renders the five views in about 5 s (3.5 s with one isotropic sample), after 5 s to load the capture.
- Baked against one preview sim run of the same build (eva-unit01 20261020-230326, fireproof-v2 20261020-222516, both pure gloss), outside a 2 px silhouette band, p95 per view (R/G/B), within 1.5 px of a paint edge (17–35% of the pixels compared) and away from edges:

  | View | eva-unit01, edge | eva-unit01, away | fireproof-v2, edge | fireproof-v2, away |
  |---|---|---|---|---|
  | front34 | 17/10/4 | 4/7/3 | 16/11/3 | 5/7/2 |
  | side_l | 14/10/4 | 4/7/4 | 19/12/3 | 5/8/2 |
  | side_r | 6/4/2 | 2/2/2 | 8/6/2 | 2/2/1 |
  | rear34 | 9/6/3 | 3/4/3 | 11/7/2 | 4/3/1 |
  | top | 10/8/4 | 4/6/4 | 13/8/3 | 5/4/1 |

- The median error is 1–2 per channel; the calibration fixture's wide flat bands bake to a p95 of 1–2. Two sim runs of one build differ by a p95 of 1. Almost every error over 24 levels sits within 1.5 px of a paint edge: per-pixel registration noise of up to a texel, which no uniform sub-texel shift of the uv removes (the best shift is 0). On metallic and chrome, soft halos around highlights remain; dense metallic patterns of random colour are the worst case.
- The sim filters its paint in the texture's own sRGB values, not in linear light. Mips box-filtered and sampled in sRGB against linear light, within 1.5 px of a paint edge, p95 (R/G/B) and the share of pixels more than 24 levels off; away from edges nothing changes:

  | View | eva-unit01, linear | eva-unit01, sRGB | fireproof-v2, linear | fireproof-v2, sRGB |
  |---|---|---|---|---|
  | front34 | 27/18/5, 8.7% | 17/10/4, 4.1% | 31/20/3, 7.9% | 16/11/3, 3.6% |
  | side_l | 21/16/5, 6.0% | 14/10/4, 2.1% | 27/17/3, 6.1% | 19/12/3, 3.4% |
  | side_r | 10/6/3, 1.3% | 6/4/2, 1.1% | 12/8/2, 1.4% | 8/6/2, 1.4% |
  | rear34 | 17/11/4, 3.5% | 9/6/3, 1.8% | 22/12/2, 4.4% | 11/7/2, 1.9% |
  | top | 17/13/5, 3.9% | 10/8/4, 1.3% | 26/13/3, 5.7% | 13/8/3, 1.7% |

- Rounding the paint to RGB565 (the sim's BC1 compression keeps its endpoint colours at 16 bits) doesn't model the sim. Away from edges it cuts fireproof-v2's mean error 1.3 to 1.0, but raises eva-unit01's blue p95 from 3–4 to 5–6 in four views. Per flat colour it moves some away from what the sim shows: blue 63 (eva-unit01's green) rounds to 57 and bakes 4.4 levels too dark against 0.8 unrounded, and grey 71 rounds to 66, 4.3 against 0.8 too dark. BC1 holds those exactly between two 565 endpoints (63 is 57 + 2/3 of the step to 66). An approximate BC1 block encoder didn't help either.
- One isotropic sample at the mip of the footprint's long axis blurs the oblique three-quarter views (fireproof-v2 front34 p95 22/14/2 against 7/8/2 filtered as a GPU does). Filtering as a GPU does (the short axis's mip, taps along the long axis) brings them in line with the rest.

## Fitting from the preview views

From a fit of the McLaren's capture (`kit fit`, 20261020-100748):

- From the right side view the side panels span 1378 × 260 px. The left side view's, matched texel for mirrored texel, span 1379 × 258 px, to a median 0.9 and p90 2.3 px on 2-texel cells.
- At frame 2917 a grass bank hides the left side below about 0.2 of its height (y 660 of 992) from the left side view. The front three-quarter view sees it.
- The top view's screen gives the side panels' u to a p90 of 0.0025 of the car's length, through an affine map on texture cells both see (4% dropped as outliers).
  - Parts further from the side camera than its panels (the roof's edge) sit at another scale in the side view's perspective: p90 0.007 and more.
- Where the top islands curve down, their texture y isn't one smooth function of the tilted top view's screen. A quadratic misses the roof's edges by up to 0.1 of w (p90 0.045).
- Maps fitted on the capture put paint a p90 of 1.5–1.6 px from where the preview side views show its car point on the doors. Maps fitted from car-set cameras at the same pose, which frame the car about 40 px higher, miss by 7.5–8.4 px.

## Dead ends

| Tried | Why not |
|---|---|
| Decrypting the car's own `.dat` (376 MB for the McLaren) | about 22% is plain zstd (textures, sound, animation, window meshes); the rest, body mesh included, is encrypted, and decrypting it would be circumvention |
| TV cameras | a camera's aim depends on the view before, even on the exact frame, so shots don't repeat |
| Session-time seeks | they drift by up to 12 frames |
| One colour model for the whole car (output = B + A·h(albedo), A and B from white and black paints per material) | p95 about 20 overall and 36–41 on metallic |
| Extra camera names in the car set | rejected, and the pointer cleared |
| Levelling car-locked side shots on the sill | the sill is already level to within its own noise (about 0.5°); a fitted line adds that noise as a tilt, so the two sides disagree by 6–14 px instead of 0.6–1.5 |
| The customer ID in the replay file's header | it has no fixed offset |

## Not yet measured

- Whether Trading Paints Downloader overwrites `car_team_<TeamID>.tga` when a session loads.
- Whether a lower frame-rate cap helps design sessions. `rendererDX11Monitor.ini` caps the sim at 239 fps, on external power only; it is the owner's setting, so no tool changes it.
