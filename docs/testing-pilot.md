# Browser-free testing pilot

The first migration keeps inexpensive logic checks in pytest/Node and adds a
component lane using Vitest, jsdom and Preact Testing Library. It changes test
execution only. Real browser workflow and responsive checks remain intact.

## Cost inventory

The inventory started from main at `acec9b14` on September 5, 2026. It reads
source and saved timing output, without importing tests, collecting pytest or
launching a browser. There are 340 `test_*.py` files. Source references identify
68 files mentioning browser harnesses, and 34 additional files directly
launching Node. These are migration hints, not disjoint counts of individual
test cases: files mix static, logic, API and rendered checks, and indirect
helpers can hide additional subprocesses. Parameterization also makes a source
function count different from pytest's reported test count.

The saved integration run passed 9,307 tests with 12 skips in 435.48 seconds.
Its slowest-15 report identifies these priorities:

| Saved case | Observed duration | Appropriate next investigation |
| --- | ---: | --- |
| Full imported-row Scorecard parity | 99.52s | Profile repeated server/store work; it already runs without a browser. Preserve all-row coverage. |
| Rapid route switching with several clients | 27.53s | Retain as a concurrency integration contract; extract deterministic state checks only where they prove a separate behavior. |
| Library click during initial loading | 24.70s | Component race checks with controlled promises may shorten local feedback; retain actual page wiring coverage. |
| Overall-owner-note fixture setup | 23.02s | Separate setup cost from assertions before changing test coverage. |
| Recorder burst reaching the rendered row | 21.28s | Preserve the end-to-end latency claim; DOM emulation cannot certify paint timing. |
| Several individual responsive viewports | 19.06–19.79s each | Select affected surfaces during edits; examine fixture/browser lifecycle before pruning the integration matrix. |

These durations overlap under parallel workers. They must not be summed into
wall time or treated as CPU measurements. The saved output lists only the
slowest cases; it cannot establish each lane's total share of the suite.

## What the pilot established

All local pilot checks ran serially through the shared runner with
`--reserve 30`, admitting two of this machine's 32 logical CPUs. The first
baseline waited 22.1 seconds for admission; queue delay is excluded below.

| Check | Result | Scope of the measurement |
| --- | --- | --- |
| Original formatting module | 8 passed, 0.67s pytest | Eleven short-lived Node launches |
| Batched formatting module | Same 8 passed, 0.17s pytest | One Node launch, same frame cases and complete seeded cutoff set |
| Initial component pilot | 3 passed; 15.83s Vitest, 17.12s pytest | First environment load was expensive |
| Warm pilot, including a trial JS formatting port | 11 passed; 1.72s Vitest, 2.19s pytest | Startup amortized, but pure logic still had a cheaper path |

These are single observations, not a benchmark distribution or a prediction
for the full suite. The Vitest formatting port was removed after the comparison;
its assertions remain in the faster batched Python/Node path. The component
pilot now also covers clearing a time versus explicitly entering zero.

The TimeFields checks mount the real component with a parent that echoes each
commit. They assert untouched fields remain blank, successive edits retain
earlier entries, external values replace the fields, and clearing differs from
zero. Removing the component's self-commit guard reproduced the blank-field
regression and failed two component cases. Introducing frame quantization into
the seconds splitter failed four formatting cases. Both temporary source
mutations were restored byte-for-byte.

The final focused run passed all eight formatting cases and four component
cases; the component bridge took 1.73 seconds. Including the scoped documentation
checks, pytest reported 783 passed and six skipped in 7.18 seconds. Changed-line
lint passed. No matching pilot Python, Node or Chrome processes remained after
completion. Vite warned about absent source maps in the shipped vendored runtime;
the runtime itself loaded and its assertions ran.

The browser import workflow still proves a typed time becomes the card's PB
and a log row. Component checks do not replace that integration claim, and
the existing browser assertion about untouched fields remains in place.

## Next migration boundaries

1. Batch repeated Node startups in other arithmetic modules, preserving their
   inputs, assertions and individual pytest cases.
2. Move standalone field/dialog/state contracts into component checks when
   their failures do not require browser layout or platform behavior. Prove
   the known failure is still caught before removing an old check.
3. Keep browser work behind the machine budget. Reusing a browser with fresh
   contexts needs a separate lifecycle change in the owning harness; this
   pilot does not alter that dependency.
4. Coordinate full integration checks against the combined candidate and reuse
   only matching code/dependency/configuration/test-input evidence. The current
   lock prevents overlapping cooperative runs; it does not deduplicate queued
   requests. A central integration queue and remote CI remain future work.

The component environment has no rendering engine. This pilot establishes a
cheaper feedback path for named contracts, not a fix for desktop stutter or
permission to replace visual verification with DOM assertions.

References: [Vitest environments](https://vitest.dev/guide/environment),
[Preact Testing Library](https://testing-library.com/docs/preact-testing-library/intro/),
[jsdom's rendering limits](https://github.com/jsdom/jsdom#pretending-to-be-a-visual-browser),
and [Playwright context isolation](https://playwright.dev/python/docs/browser-contexts).
