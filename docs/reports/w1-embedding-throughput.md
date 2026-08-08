# W1 Embedding Throughput Measurement

**Date:** 2026-08-08
**Machine:** Apple Silicon (arm64), macOS 25.6, Python 3.14, torch 2.13.0,
transformers 5.14.1
**Model:** BAAI/bge-m3, dense vector only (normalized CLS), loaded from a local
directory with `local_files_only=True`
**Weights SHA-256:** `1c8e4c9b024d81ce9c563c93962bbd26c6c6eb8661b4ce62ca340057ca532a1d`
**Pipeline version:** `clause/v1`
**Corpus:** the two frozen RFCs — 9110 (1400 clauses, 56,604 words) and 9112
(312 clauses, 12,000 words), 1712 clauses and 68,604 words in total

This report exists because product plan §7 forbids writing a full-corpus
encoding time down anywhere before one has been measured. Everything below was
run; nothing was extrapolated from a vendor figure.

> **Revised 2026-08-08.** An earlier version of this report measured 1474
> clauses. Building the normative index found 17 BCP 14 keywords sitting in
> `<li>` and `<dd>` elements the source had not wrapped in `<t>`, which the
> clause model was skipping — 4143 words of RFC 9110 and 1139 of RFC 9112 that
> were in no clause at all. Fixing that changed the corpus, so every figure
> here was re-measured. The batch-size recommendation was also withdrawn; see
> below.

## Headline: the corpus encodes in well under two minutes

The whole-corpus rows are **not** extrapolations from a sample. Both documents
were encoded end to end — `--sample` set to each document's full clause count —
so the wall clock is the wall clock. Three runs per device.

| Device | Batch order | Batch | Whole corpus | Rate |
|---|---|---|---|---|
| MPS | length | 32 | **39.8–47.6 s** (median 40.5) | ~42 clause/s |
| CPU | length | 32 | 77.1–80.4 s (median 80.2) | ~21 clause/s |

**The defensible claim for W2 planning: a full re-index of the current corpus
costs between 40 and 80 seconds on this machine.** Quoting the best run as if it
were the number is how a measured figure turns back into a guess.

MPS is roughly twice CPU and much less stable. Across the whole session MPS
ranged from 37.5 s to 81.7 s for identical work, degrading steadily under
back-to-back runs and returning to its fast end after four minutes idle.
`pmset -g therm` recorded no thermal or performance warning throughout, so this
is not OS-level thermal throttling being reported; the mechanism is
unidentified and only the effect is claimed. CPU held to a 1.5–4% spread in the
same windows.

For W2 this means a one-off index build on a rested machine lands at the fast
end and one following other GPU work lands at the slow end. Both are under two
minutes, so neither changes a decision.

## Batch order dominates. Batch size does not.

Every batch pads to its longest member, so grouping clauses of similar length
before batching is worth a large, reproducible factor.

RFC 9110, 200-clause sample, clauses per second, one run each:

| Device | Order | bs=8 | bs=16 | bs=32 |
|---|---|---|---|---|
| MPS | document | 22.37 | 22.62 | 19.93 |
| MPS | length | **41.83** | 39.18 | 31.47 |
| CPU | document | 14.28 | 11.65 | 10.23 |
| CPU | length | **19.92** | 19.35 | 18.36 |

Length ordering wins in every cell, on both devices, in every run taken this
session. The padding accounting says why — same sample, 12,048 real tokens:

| Order | Batch | Padded tokens | Wasted |
|---|---|---|---|
| document | 8 | 23,696 | 49.2% |
| document | 16 | 27,144 | 55.6% |
| document | 32 | 29,944 | 59.8% |
| length | 8 | 12,680 | **5.0%** |
| length | 16 | 13,264 | 9.2% |
| length | 32 | 14,480 | 16.8% |

Sixty percent of the work in the naive configuration is padding.

**The batch-size column is noise.** A single-run sweep is what produced the
table above, and it appears to show a trend. Interleaving four rounds so that
machine drift cannot line up with batch size dissolves it:

| Config | Four runs | Median | Spread |
|---|---|---|---|
| mps length bs=8 | 28.49 34.28 30.31 27.33 | 29.40 | 6.95 |
| mps length bs=16 | 31.32 31.23 27.93 25.03 | 29.58 | 6.29 |
| mps length bs=32 | 29.19 27.41 24.39 23.05 | 25.90 | 6.14 |

Within-config spread is as large as the between-config difference, and every
config drifts downward across rounds — the machine, not the batch size. An
earlier draft of this report recommended batch size 32 on the strength of the
single-run sweep. **That recommendation is withdrawn.**

**Recommendation for W2: group batches by length. Batch size anywhere from 8 to
32 is indistinguishable on this machine, so pick it for memory, not speed.**
The length-ordering win has to be revisited if chunking changes, since it comes
from the length distribution rather than from any particular number.

## Sampling

`evenly_spaced` takes the sample by stride across the whole document rather than
from the front, because an RFC opens with its abstract and introduction, which
run shorter than its average clause. The stride sample tracks the corpus: 40.4
words per clause in the 200-clause sample against 40.4 across all 1400.

The whole-corpus figures make this moot for the headline number, but the sampled
command stays the cheap way to re-check a configuration — as long as it is run
interleaved, which is the lesson of the table above.

## A correction worth keeping

RFC 9112 first measured 33% slower than RFC 9110, which read as a property of
the document. Interleaved re-runs put the two within 4% of each other, matching
their padding waste. Token density is identical between them — 1.49 and 1.50
tokens per word — so the tokenizer was not the difference either. The first
reading was the machine.

Both of this report's retractions have the same shape: a single run, read as a
result. The measurements that survived are the ones taken interleaved and
repeated.

## What did not enter Git

- **No model weights.** They live under `data/cache/models/bge-m3`, which
  `.gitignore` excludes; the repository holds the hash, not the checkpoint.
- **No vectors.** Throughput needs the forward pass, not its output.
- **No clause text.** `ThroughputMeasurement` has no field that can hold it, and
  a test asserts the serialized record does not contain sampled text.
- **No new default dependency.** `torch` and `transformers` are the optional
  `embedding` extra, imported lazily inside `specpilot embedding measure`. The
  unit suite runs, and `throughput.py` imports, on a machine with neither.

## Reproducing

```bash
specpilot embedding measure \
  --manifest af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691 \
  --manifest-dir manifests/local/r0/source \
  --xml artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml \
  --model-dir data/cache/models/bge-m3 \
  --device mps --batch-order length --batch-size 32 --sample 1400
```

The manifest is checked before the model loads: the supplied file is hashed and
refused with `document_hash_mismatch` unless it is the frozen document. A
throughput figure measured against a document other than the corpus would not be
a figure about this project.

## What this does not establish

No quality metric was produced, and none belongs in W1. This measures cost, not
whether BGE-M3 retrieves the right clause — that is W2's question, against gold
the author has not yet written. A fast encoder that retrieves badly would
produce exactly these numbers.
