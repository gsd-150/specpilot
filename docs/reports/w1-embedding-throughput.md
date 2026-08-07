# W1 Embedding Throughput Measurement

**Commit:** `767641a`
**Date:** 2026-08-08
**Machine:** Apple Silicon (arm64), macOS 25.6, Python 3.14, torch 2.13.0,
transformers 5.14.1
**Model:** BAAI/bge-m3, dense vector only (normalized CLS), loaded from a local
directory with `local_files_only=True`
**Weights SHA-256:** `1c8e4c9b024d81ce9c563c93962bbd26c6c6eb8661b4ce62ca340057ca532a1d`
**Pipeline version:** `clause/v1`
**Corpus:** the two frozen RFCs — 9110 (1222 clauses, 53,575 words) and 9112
(252 clauses, 11,049 words), 1474 clauses and 64,624 words in total

This report exists because product plan §7 forbids writing a full-corpus
encoding time down anywhere before one has been measured. Everything below was
run; nothing was extrapolated from a vendor figure.

## Headline: the corpus encodes in well under two minutes

The whole-corpus rows are **not** extrapolations from a sample. Both documents
were encoded end to end — `--sample` set to each document's full clause count —
so the wall clock is the wall clock.

| Device | Batch order | Batch | Machine state | Whole corpus | Rate |
|---|---|---|---|---|---|
| MPS | length | 32 | rested | **37.5 s** | 39.3 clause/s |
| MPS | length | 32 | after sustained runs | 67.4 s | 21.9 clause/s |
| CPU | length | 32 | either | 86.0 s | 17.1 clause/s |

**The defensible claim for W2 planning: a full re-index of the current corpus
costs between 40 and 90 seconds on this machine.** Not "about 38 seconds" — the
spread below is real and load-dependent, and quoting the best run as if it were
the number is how a measured figure turns back into a guess.

CPU was the stable one: 85.3 s, 86.0 s, 86.6 s across three runs, a 1.5% spread.
MPS ranged from 37.5 s to 81.7 s across eight runs of the identical command.

## The spread is sustained load, not the corpus

The first RFC 9112 measurement came out at 26.4 clause/s against RFC 9110's
39.2, which looked like a property of the document. It was not. Re-run
interleaved, three rounds each:

| Round | RFC 9112 | RFC 9110 |
|---|---|---|
| 1 | 37.06 | 39.11 |
| 2 | 37.25 | 38.64 |
| 3 | 37.01 | 38.35 |

The 4% residual gap matches these documents' padding waste (14.6% against
15.9%); the 33% gap in the first reading was the machine, not the text. Token
density is identical between the two — 1.49 and 1.50 tokens per word — so the
tokenizer is not the difference either.

MPS then degraded across the session, from 37.5 s to 67.4 s for the same work.
Idling the machine for four minutes restored it to 37.5 s exactly. `pmset -g
therm` recorded no thermal or performance warning throughout, so this is not OS-
level thermal throttling being reported; the mechanism is unidentified and only
the effect is claimed here. CPU throughput was unaffected in the same window.

For W2 this means a one-off index build on a rested machine lands at the fast
end, and a build that follows other GPU work lands at the slow end. Both are
under two minutes, so neither changes any decision.

## Batch order dominates batch size

Every batch pads to its longest member. Grouping clauses of similar length
before batching is worth more than any batch-size choice, and it also reverses
what batch size appears to do.

RFC 9110, 200-clause sample, clauses per second:

| Device | Order | bs=8 | bs=16 | bs=32 |
|---|---|---|---|---|
| MPS | document | 24.12 | 22.21 | 20.33 |
| MPS | length | 37.26 | 38.77 | **39.23** |
| CPU | document | 14.75 | 13.31 | 11.83 |
| CPU | length | **24.33** | 23.76 | 21.71 |

In document order, smaller batches look faster on both devices. That is entirely
a padding artefact — a smaller batch has less length spread inside it, so it
wastes less. Reading it as a batch-size result would send W2's tuning in exactly
the wrong direction. Once lengths are grouped, MPS behaves the way a GPU should
and prefers larger batches.

The padding accounting, over the same sample (12,985 real tokens):

| Document | Order | Batch | Padded tokens | Wasted |
|---|---|---|---|---|
| 9110 | document | 8 | 25,888 | 49.8% |
| 9110 | document | 32 | 32,632 | 60.2% |
| 9110 | length | 8 | 13,744 | 5.5% |
| 9110 | length | 32 | 15,440 | 15.9% |

Sixty percent of the work in the naive configuration is padding.

**Recommendation for W2: length-grouped batches, size 32 on MPS.** This has to
be revisited if chunking changes, since the win comes from the length
distribution and not from the number 32.

## Sampling

`evenly_spaced` takes the sample by stride across the whole document rather than
from the front, because an RFC opens with its abstract and introduction, which
run shorter than its average clause. The stride sample tracks the corpus
closely: 43.7 words per clause in the 200-clause sample against 43.8 across all
1222. Sampling from the front would have measured the abstract and reported it
as the corpus.

The whole-corpus figures above make this moot for the headline number, but the
sampled command stays the cheap way to re-check a configuration.

## What did not enter Git

- **No model weights.** They live under `data/cache/models/bge-m3`, which
  `.gitignore` excludes; the repository holds the hash, not the checkpoint.
- **No vectors.** Throughput needs the forward pass, not its output, so nothing
  is retained.
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
  --device mps --batch-order length --batch-size 32 --sample 1222
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
