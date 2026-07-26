# Art credits

## Star split icons — `src/sm64_events/ui/assets/star_icons/`

The community split-icon pack: 100×100 PNGs, `{course}{1..7}.png`.

Original: **https://www.furaffinity.net/view/7557800/**

**Folder version: https://www.reddit.com/r/speedrun/comments/27fvua/for\_sm64\_runners\_in\_search\_of\_split\_icons/**

## Course portraits — `src/sm64_events/ui/assets/course_icons/`

Screenshots taken from Super Mario 64 itself — the course paintings and entry
areas. Nintendo's artwork, used here in a free, non-commercial practice tool.

Tick Tock Clock and Rainbow Ride were re-captured at 600×600 (2026-07-25)
because the originals were 64×64 and 100×100 thumbnails that blurred in the
picker; the size floor in `tests/test_star_icons.py` keeps that from recurring.
Four main courses — HMC, SSL, DDD and SL — have no portrait at all, because
they are not entered through a painting; they fall back to their star-1 icon.

## Empty-state cast — `src/sm64_events/ui/assets/empty/`

Six character renders — two Boo, two Ukiki, two Toad — drawn dimmed behind the
Practice tab's "nothing here yet" panels (`ui/components/emptystate.js`). Model
renders from Super Mario 64: Nintendo's artwork, used here in a free,
non-commercial practice tool, as with the course portraits above.

Supplied at 455×420 and installed at 360px wide, alpha-trimmed to each
character's own silhouette (so they have different aspect ratios and are sized
by HEIGHT in the CSS, which is what makes them read as one set). Registry ↔
folder coverage is pinned both ways by `tests/test_ui_empty_states.py` — a
renamed file would otherwise 404 a broken-image icon into the exact panel the
art exists to make look deliberate.

## Rank cap sprites — `src/sm64_events/ui/assets/hat/`

The Mario-cap rank badges derive from `assets/hat_rank.psd` (Griffin's own
Photoshop artwork) via `tools/build_hat_assets.py`, which turns the raw
`assets/hat_raw/*.png` exports into the tintable white masters the renderer
composes: the cap's HSV value channel becomes its white shading, the Capless
outline is a dilated-then-subtracted ring, and each wing export is split into
left/right halves so a flap can rotate them in opposite directions.
Re-running the script after a re-export is the only way these files change.

The division-digit glyph on each sign uses **Super Mario 256**
(`src/sm64_events/ui/assets/fonts/SuperMario256.ttf`), a fan-made font in the
style of the in-game HUD counter — not one of Nintendo's own font files —
used here in a free, non-commercial practice tool.
