# SM64 Trainer website

This is the public download and information page for SM64 Trainer. It uses static
HTML/CSS and a small browser script. The approved navy/gold design keeps the
download action in the first screen, explains practice feedback and community
ghosts, and makes optional support easy to find.

**Start with an offline preview below.** Production builds require a complete
eligible stable or public beta release. Hosting, credentials, DNS, and the dated
readiness snapshot belong to the [deployment runbook](../docs/website-deployment.md).
The app's live recorder is independent of website work.

## Where to change what

| Change | Owner |
| --- | --- |
| Copy, feature sections, requirements, FAQ, navigation, image descriptions, social metadata | [index.html](index.html) |
| Palette, spacing, typography, layout, responsive behavior, focus and motion | [styles.css](styles.css) |
| Localized upload time and opening a linked FAQ answer | [release.js](release.js) |
| Screenshots and existing project art | [assets/](assets/) and the asset table below |
| Repository, canonical URL, minimum advertised app version | [release-policy.json](release-policy.json) |
| Browser permissions and response caching | [_headers](_headers) |
| Search engine discovery | [robots.txt](robots.txt), [sitemap.xml](sitemap.xml) |
| Selecting a real release and filling the production template | [build_website.py](../tools/build_website.py) |
| Safe sample states and local preview server | [preview_website.py](../tools/preview_website.py) |
| Release/preview contract checks | [test_build_website.py](../tests/test_build_website.py), [test_preview_website.py](../tests/test_preview_website.py) |
| CI artifacts and publishing | [website.yml](../.github/workflows/website.yml), [deployment runbook](../docs/website-deployment.md) |

Keep this separation when adding a feature. The site does not need the trainer
server, app dependencies, a database, or a frontend framework to render. Add a
helper only when an observed repeated task needs it.

One [index.html](index.html) template renders both `/` and `/beta/`. The builder's
`data-site-copy` hooks supply beta title, introduction, update notice and signup
copy. Assets, styles and scripts use site-root paths. Keep one shared design.
The enduring public beta link is **https://sm64trainer.app/beta/**; it resolves to
the current validated, versioned public installer without an invitation token.

## Preview without a public release

Run from the **root of the checkout containing your changes**. The helper uses
the standard library and sample release data; it makes no GitHub request and
needs no token. The command selects Python 3.13 without loading app dependencies.

On Windows:

```powershell
$websitePreview = Join-Path $env:TEMP ('sm64trainer-preview-' + [guid]::NewGuid().ToString('N'))
uv run --no-project --python 3.13 python tools/preview_website.py --output $websitePreview --state ready --serve --port 8787
```

Open the local URL printed by the helper, normally
`http://127.0.0.1:8787/`. It does not open a browser for you. Stop it with
**Ctrl+C** when finished. If that port is occupied, choose another port; leave
unrelated processes alone.

A preview has a visible sample-data banner, `release.json` status `preview`,
and three disabled download actions. **It is review output, never a Cloudflare
publishing candidate.** The production builder and release policy stay unchanged.

To inspect the unavailable state, stop the first server and use a fresh directory:

```powershell
$websiteUnavailable = Join-Path $env:TEMP ('sm64trainer-unavailable-' + [guid]::NewGuid().ToString('N'))
uv run --no-project --python 3.13 python tools/preview_website.py --output $websiteUnavailable --state unavailable --serve --port 8787
```

Omit `--serve` to build files only. Every output must be a **new directory**;
pick a new name instead of overwriting an old preview. Repository/source roots
and the system temporary root are protected. Keep outputs outside `website/`.

Use `--state beta-only` for the launch state before stable 1.10.0: the root
explains that community stable is pending and links to `/beta/`. The beta route
has the normal ready layout. `ready` renders both channels ready; `unavailable`
renders both without direct downloads. Each state includes `/beta/` and its own
`/beta/release.json`; all preview download actions remain disabled.

Use this server for CSP review: it applies the checked-in global and exact-path
rules from `_headers`. Opening the file directly, or using a plain static
server, does not exercise those response headers. The local server does not
simulate Cloudflare certificates, TLS, or CDN cache behavior.

## Contracts to preserve

### HTML hooks and browser behavior

The builder currently requires **three** `.download-link` anchors, each with a
`.download-label` span, and **one** `#release-meta`. All three production
downloads resolve to the same validated, versioned installer URL. Adding or
removing a download action requires changing the builder's count and its tests
together; a copy edit should keep these hooks intact. The builder also writes
CTA labels: changing a template span alone will not change production wording.
Update the builder and its checks when deliberately changing generated labels.

The builder removes `.preview-note`, fills release metadata, and emits a
`<time data-release-upload datetime="…">` with a UTC value and readable fallback.
JavaScript localizes that time in the visitor's browser and retains UTC in the
tooltip. Downloads, version, installer size, and upload time work without JS.

Keep native `<details>/<summary>` FAQ controls and unique IDs. Links such as
`#ghost-sharing` and `#rank-answer` depend on them. The script opens a linked
answer on initial load and hash changes; other section links scroll normally.

Preserve the skip link, keyboard focus treatment, heading order, informative
image alt text, and empty alt text for decoration. The Ko-fi action remains
visibly optional on mobile as well as desktop.

### CSP and release output

`_headers` is the source of truth. It permits local scripts/styles and the
official Ko-fi image host, with no browser connections to release APIs. Inline
styles and scripts are blocked. Tier colors therefore use CSS classes. A new
font, video, embed, or external image needs a deliberate CSP change and browser
verification; do not loosen the whole policy to silence an error.

The production builder copies public root files and supported asset types,
then emits `release.json`. README, agent instructions, policy, private notes,
and hidden assets are excluded; linked files/directories are rejected.
New public root files require an explicit builder allowlist update. Do not put
private content in the public asset directory.

Stable downloads require a complete public stable release at or above the
policy floor, currently **1.10.0**. `/beta/` independently selects a published
`v1.10.0-beta` or later beta core, with GitHub prerelease enabled. Beta ordering
uses the installer's shared SemVer contract (`beta.10` follows `beta.9`).
Both channels require all six public assets and exact versioned repository URLs.
The highest eligible SemVer wins, independent of publication date. Incomplete newer releases block publication. Withdrawn
releases can select an earlier eligible release or, in an explicit withdrawal
refresh, produce unavailable output. Network failures leave the existing site
alone. Detailed exit codes and operator recovery live in the runbook.

## Change copy or add a feature section

1. Establish what the downloaded app version actually supports. Use the
   [app README](../README.md), relevant implementation/docs, and the published
   [privacy policy](https://auth.sm64trainer.app/policies). If a new claim needs
   a newer app release, coordinate copy and the minimum version deliberately.
2. Edit `index.html` using the existing semantic sections. Give new sections
   unique IDs and useful headings; keep download hooks and established links.
   Describe what the feature does before introducing an internal term.
3. Preview ready and unavailable states. Inspect longer copy and narrow screens,
   including the first screen's download action and optional support label.
4. Run the relevant checks below and retain before/after evidence for visible
   changes. Update this guide only when a maintained contract changes.

Keep the established claims precise: the Library contains available community
examples, eligible attempts receive automatic PB handling, and sharing during
signup is disclosed. Stopping new uploads and withdrawing existing ghosts are
different actions. Free forever and optional Ko-fi are Griffin's commitment.
Checksums establish integrity; the page makes no security certification, player
count, testimonial, zero-performance-impact, or universal run-rules promise.

The rank names and divisions come from [caps.js](../src/sm64_events/ui/components/caps.js)
and [scoring.py](../src/sm64_events/ranks/scoring.py). Requirements and supported
ROM/emulator behavior come from the app's own documentation. Recheck these
sources when the product changes instead of extending old marketing copy.

## Replace a screenshot or visual

| Asset | Actual dimensions | Provenance and role |
| --- | --- | --- |
| [logo.png](assets/logo.png) | 256 × 256 | Repository [logo master](../assets/sm64_tracker.png), reduced from 1024 × 1024 as RGBA with Lanczos; canonical project mark |
| [star.png](assets/star.png) | 256 × 256 | Identical to [star_1.png](../src/sm64_events/ui/assets/star_1.png); decoration |
| [bob.webp](assets/bob.webp) | 621 × 621 | Identical to the [Bob-omb Battlefield course art](../src/sm64_events/ui/assets/course_icons/bob.webp); illustration, not ghost gameplay |
| [practice-gold.png](assets/practice-gold.png) | 1072 × 613 | Authentic community development-build Bomb Clip GOLD capture; hero and social image |
| [trainer-app.png](assets/trainer-app.png) | 1500 × 1100 | Authentic Trainer Practice/analysis capture with the earlier full interface layout |

The screenshot originals stay in private session evidence. The checked-in
images are the public references; do not add links to ignored capture folders
or a developer's filesystem. The design and these captures were approved on
October 13, 2026.

For a replacement:

1. Capture the actual product at the relevant state with representative data.
   Use its isolated UI fixture or a capture Griffin provides; protect the live
   recorder. Remove private account details, tokens, paths, and unrelated windows.
   Never present generated gameplay or a drawing as an actual product capture.
2. Keep the useful subject legible at the site's display size. Preserve aspect
   ratio and compress appropriately; retain a higher-resolution original privately.
3. Update the image file, HTML `width`/`height`, accurate alt text, and caption.
   A hero replacement also updates OpenGraph image URL, alt, width, and height.
   Use a development-build label when the image shows unreleased behavior.
4. Update the table's dimensions/provenance and compare mobile/desktop previews.
   Keep decorative stars and course art out of the accessibility reading order.

The current raster assets total roughly 434 KB. Treat that as a measured
baseline, not a universal budget. Keep the hero image prioritized, retain lazy
loading below the fold, and avoid introducing a heavy animation or embed for a
small explanatory benefit. Reuse authoritative art for canonical marks.

## Review and verification

Run the website's stdlib tests without an app environment:

```powershell
uv run --no-project --python 3.13 python -m unittest discover -s tests -p "test_*website.py" -v
```

Project-wide verification still uses the repository's configured
`uv run --offline --frozen` workflow. Test the change's blast radius locally;
the integration and full-CI gates remain owned by the project workflow.

For visible edits, review the actual source through the safe preview helper.
CI also builds ready/unavailable/beta-only sample artifacts separately from production.
A green production launch hold does not verify the rendered design.

- Start with mobile 390 × 844 and desktop 1440 × 900; check 320px for narrow copy.
  For responsive changes, inspect both sides of the affected 760px or 1100px
  breakpoint. Use 1600px or wider when changing the wide-screen rules.
- Inspect the first screen and the changed section, not only a scaled full-page
  image. The primary action should remain easy to find and content legible.
- Check ready and unavailable layouts, localized upload time with JS, UTC with
  JS disabled, and FAQ deep links such as `/#ghost-sharing`.
- Use the keyboard to reach links and FAQ controls. Check reduced motion when
  changing transitions and keep motion in both directions.
- Check console/CSP errors, broken images/links, long text, and horizontal
  overflow. Confirm each sample download is disabled. Production link validity
  belongs to the release-builder checks, not a sample click.

Website viewports differ from the Trainer UI's 850px minimum. Website work
should not start, restart, or attach the app's live recording server.

## Troubleshooting

| Symptom | Next check |
| --- | --- |
| Production exits 3 with `launch_hold` | Expected before either eligible release channel exists; use the preview helper for editing, preserve the floor |
| Output already exists or is protected | Choose a new leaf directory outside source and protected roots |
| Local port is busy | Stop your own preview or choose another port; do not kill an unrelated listener |
| Source downloads differ from generated output | `index.html` is a template; inspect the generated release metadata |
| Styling/script/image blocked | Inspect the exact console violation against `_headers`; serve through the preview helper |
| Image looks stretched or social preview is stale | Compare file dimensions with HTML and OpenGraph metadata |
| Website does not update after a release | Use the runbook's CI result, settings, complete-asset, and withdrawal checks |
| Domain pending or auth problem | Use the runbook; preserve `auth.sm64trainer.app` and its existing service |

## Leave a useful handoff

Record the intended change, owned checkout/commit, source-backed copy decisions,
asset provenance, preview state/viewport, before/after evidence, check results,
and remaining work. Distinguish sample preview, validated publishing candidate,
and a deployed public page. Keep raw conversations and capture originals private.
Future agents should start here and use the operator runbook only for deployment
or domain work.
