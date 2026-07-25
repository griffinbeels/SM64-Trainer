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
