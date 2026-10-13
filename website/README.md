# SM64 Trainer website

The responsive design was approved on October 13, 2026. The site launches alongside the stable ghost/community release. `release-policy.json` holds the repository, canonical site URL, and initial stable version floor. Source `index.html` is a template; production output removes its private-preview notice and fills the download details.

## Preview

Build from the repository root with `python tools/build_website.py --output .website-build`, then serve `.website-build/` with a static server. No frontend framework, package installation, analytics, site account, or service worker. Downloads, exact version, size, and upload date are rendered into HTML and work without JavaScript. JavaScript only opens a linked FAQ answer. Visitors never call the GitHub API.

## Deployment

The authoritative setup and operator steps are in [docs/website-deployment.md](../docs/website-deployment.md). GitHub Actions validates the full public release asset set before deploying generated static files to Cloudflare Pages. The initial community launch floor prevents today's older public build being advertised as community-ready. Explicit withdrawal refresh can retire direct links or use an earlier eligible community release. Network/build failures do not replace a healthy site.

The site uses `sm64trainer.app`; sign-in stays on `auth.sm64trainer.app`. Only an exact `www` redirect should point to the website. CI needs a scoped Pages Edit token stored as an Actions secret, with production deployment disabled until launch setup is complete.

## Evidence and copy

Navy/gold tokens match src/sm64_events/ui/index.html. Logo, star and Bob-omb Battlefield art reuse existing project assets. Two practice images are authentic private-free project captures (the gold view is from the community development build). No synthetic gameplay, security certification, player counts or testimonials. The nine actual tier names and five divisions follow caps.js/scoring.py. Requirements and privacy/plugin answers follow main README and ghost-overlay community protocol/policies. Free forever and optional Ko-fi are Griffin's explicit commitment.

Product copy: Library examples are available community submissions; the page does not claim every possible strategy has a video. PB automation is scoped to eligible attempts. Sharing is disclosed/enabled during signup; stopping future uploads and withdrawing existing ghosts are distinct actions. No promise of zero plugin performance effect or universal speedrun rules compatibility.

Primary source GitHub: https://github.com/griffinbeels/SM64-Trainer
Privacy and sharing: https://auth.sm64trainer.app/policies
