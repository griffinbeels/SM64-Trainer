# Deferred input-history compression

`ReadTimes` retains every observed UTC microsecond and the existing zlib byte
representation. It buffers packed 64-bit values until the sampler seals a short
history or the buffer reaches 64 KiB. Discarded short states do no compression;
long holds stream bounded batches into the existing compressed temporary spool.
The sampler, capture identity, queries, timestamp conversion and media paths do
not change.

## Bounded measurement, 2026-09-08

The baseline was the unmodified module at `24346c44`, SHA-256
`17722c200acdcbb9cb91947a3115848c3d2420379a7eccd7282df4d11c7c932b`.
The candidate was the implemented module, SHA-256
`15a1720c0211e5ec9420029c5914bf5a48e686e7ce6deba1bf715c84ad9d74f8`.
Both ran in the same offline Python 3.13.12 process on Windows, through the
shared test runner. Each batch ran twice with reversed implementation order.
CPU timing used `process_time_ns` without allocation tracing; its Windows
resolution was about 15.6 ms. No live application process was changed.

| Batch, per round | Eager CPU, mean ms | Deferred CPU, mean ms | Reduction |
| --- | ---: | ---: | ---: |
| 65,000 sealed histories, eight reads each | 875.0 | 734.4 | 16.1% |
| 30,000 frames, two discarded four-read states then an eight-read final state | 742.2 | 515.6 | 30.5% |
| 30 sealed holds, 20,000 reads each | 960.9 | 703.1 | 26.8% |

This measures the history writer, not whole-application CPU or live frame rate.
The normal-history saving averaged about 2.2 microseconds per sealed history.
The short-history samples included repeated and backwards timestamps; long
holds used seeded nonmonotonic microseconds to exercise less-compressible data.

A separate `tracemalloc` measurement after eight pending reads, before sealing,
reported peak traced allocations of 301,872 bytes eager and 925 bytes deferred.
Sealing still allocates zlib state; this is not a claim about the finalization
peak. A frame with two rewrites changed from three compressor constructions,
16 streaming compress calls and one flush to one final one-shot compression.
Long histories retain bounded pending memory; their sealed output still grows
with the evidence retained.

## Acceptance evidence

An independent reference fed each packed timestamp separately into the former
streaming compressor. Compressed bytes, decoded values, ordering and extrema
matched exactly for short histories, either side of the 1 KiB and 64 KiB buffer
boundaries, and a 20,000-read history. The offline comparison also forced a real
temporary-file spill and checked closure.

The dedicated tests cover discarded-state cleanup, refusal after close and
actual compression creation, write, flush, read and seal failures through the
sampler. The failed state loses provenance, its spool closes, and the following
state remains capturable. Existing tests cover within-frame rewrites, reset,
capture ownership, precise membership queries, store reopen and long holds.

Validation: 90 focused input regressions passed in 4.56 s; after adding two more
failure cases, all 15 dedicated cases passed in 0.33 s. The final comparison
passed in 9.37 s. Pinned Python lint reported zero findings in both changed
Python files. The child worktree lacked the JavaScript/type-check dependencies
for the repository quick gate; full integration verification belongs to the
parent checkout.

Re-run the contract coverage with the project Python and an absolute
`PYTHONPATH` pointing at the checkout's `src` directory:

```text
python tools/run_tests.py tests/test_input_readtimes.py tests/test_input_readtimes_deferred.py tests/test_inputs_sampler.py tests/test_inputs_observation.py tests/test_inputs_provenance.py tests/test_inputs_wrap_failures.py tests/test_input_capture_boundaries.py tests/test_inputs_capture_identity.py -v
```

The frozen baseline, bounded comparison script and raw measurements are retained
in the worker's private `.iteration/input-history/` evidence directory. They are
session evidence rather than a performance threshold in the normal test suite.
