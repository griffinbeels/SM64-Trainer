# SM64 Trainer website design prototype

Launch decision: publish alongside the ghost/community stable release. This full static prototype is pending Griffin's design review. The preview notice identifies the public-download mismatch; never deploy this preview as the community launch.

## Preview

Serve this folder with a static HTTP server or open index.html. No build, dependencies, analytics, site account or service worker. The download link resolves the latest non-prerelease, non-withdrawn GitHub release, validates the exact uploaded installer and checksum, and displays its version, size and asset upload date. API failure and JavaScript absence retain the official GitHub releases page. Prototype data is runtime fetched; production should use static generated metadata so downloads do not depend on GitHub API availability or visitor rate limits.

## Approved engineering next

1. Select the stable community release after it passes existing release gates. Capture human design approval and remove the private preview banner only for that launch.
2. Generate HTML/JSON download metadata in GitHub Actions using the existing release contract. Validate all six incremental release assets, exact tag URLs, uploaded state, checksums, and the withdrawal marker. Fail deployment without changing the last good Pages version.
3. Trigger on production release publication/promotion, relevant release edits/withdrawals, site changes, and manual reconciliation. A reconciliation schedule can be added explicitly if desired.
4. Deploy website/ via Cloudflare Pages Direct Upload. Store a scoped Pages Edit token and account ID only in GitHub Actions secrets. Choose the Pages project name during actual setup.
5. Attach sm64trainer.app to Pages; configure a www-only redirect preserving path/query. Preserve auth.sm64trainer.app, OAuth registrations and localhost callback behavior.
6. Verify public desktop/mobile behavior, no-JS download, stale/failure metadata, current version/date, and auth boundaries.

Cloudflare setup: https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/
Custom domain: https://developers.cloudflare.com/pages/configuration/custom-domains/
www: https://developers.cloudflare.com/pages/how-to/www-redirect/

## Evidence and copy

Navy/gold tokens match src/sm64_events/ui/index.html. Logo, star and Bob-omb Battlefield art reuse existing project assets. Two practice images are authentic private-free project captures (the gold view is from the community development build). No synthetic gameplay, security certification, player counts or testimonials. The nine actual tier names and five divisions follow caps.js/scoring.py. Requirements and privacy/plugin answers follow main README and ghost-overlay community protocol/policies. Free forever and optional Ko-fi are Griffin's explicit commitment.

Product copy: Library examples are available community submissions; the page does not claim every possible strategy has a video. PB automation is scoped to eligible attempts. Sharing is disclosed/enabled during signup; stopping future uploads and withdrawing existing ghosts are distinct actions. No promise of zero plugin performance effect or universal speedrun rules compatibility.

Primary source GitHub: https://github.com/griffinbeels/SM64-Trainer
Privacy and sharing: https://auth.sm64trainer.app/policies
