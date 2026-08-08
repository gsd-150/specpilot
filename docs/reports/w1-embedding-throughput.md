# W1 Embedding Throughput Measurement

**Date:** 2026-08-08
**Machine:** Apple Silicon (arm64), macOS 25.6, Python 3.14, torch 2.13.0,
transformers 5.14.1
**Model:** BAAI/bge-m3, dense vector only (normalized CLS), loaded from a local
directory with `local_files_only=True`
**Weights SHA-256:** `1c8e4c9b024d81ce9c563c93962bbd26c6c6eb8661b4ce62ca340057ca532a1d`
**Pipeline version:** `clause/v1`
**Corpus:** the two frozen RFCs — 9110 (1559 clauses, 58,732 words, 90,666
tokens) and 9112 (350 clauses, 12,769 words, 20,231 tokens), 1909 clauses and
110,897 tokens in total

This report exists because product plan §7 forbids writing a full-corpus
encoding time down anywhere before one has been measured. Everything below was
run; nothing was extrapolated from a vendor figure.

## Headline: the corpus encodes in well under two minutes

Not extrapolations from a sample. Both documents were encoded end to end —
`--sample` set to each document's full clause count — so the wall clock is the
wall clock. Three runs per device, batches ordered by token count.

| Device | Whole corpus | Median | Rate |
|---|---|---|---|
| MPS | 37.9–91.7 s | 54.1 s | ~35 clause/s |
| CPU | 97.7–102.0 s | 101.7 s | ~19 clause/s |

**The defensible claim for W2 planning: a full re-index costs between 40 and 100
seconds on this machine.** Quoting the best run as if it were the number is how
a measured figure turns back into a guess.

CPU is the stable one — 97.7, 101.7, 102.0 across three runs. MPS is roughly
twice as fast and far less predictable: across this session it ranged from 37.5 s
to 105 s for identical work, degrading under back-to-back runs and returning to
its fast end after a few minutes idle. `pmset -g therm` recorded no thermal or
performance warning throughout, so this is not OS-level throttling being
reported; the mechanism is unidentified and only the effect is claimed.

Either end is under two minutes, so neither changes a W2 decision.

## What to batch by, and what not to

Every batch pads to its longest member, so how clauses are grouped is worth more
than how many go in a batch. Two separate choices matter, and one of them broke
silently partway through this work.

Padding accounting on RFC 9110's 1559 clauses, 90,666 real tokens, batch 32:

| Grouping | Padded tokens | Wasted |
|---|---|---|
| document order | 235,953 | 61.6% |
| by **word** count | 137,141 | 33.9% |
| by **token** count | 94,692 | **4.3%** |

**Order by length, not by document order.** Reproducible in every run this
session, on both devices.

**Measure that length in tokens, not words.** This is the one that broke. While
the corpus was all prose at a steady 1.50 tokens per word, word count was an
exact proxy and word-ordering reached about 16% waste. Admitting ABNF blocks at
2.90 tokens per word introduced a second population: a 50-word grammar block is
145 tokens where a 50-word paragraph is 75, so word-ordering now batches them
together and pads both to the longer. It kept working — just badly — and nothing
announced the change. Switching the key to tokens cut median CPU time from
136.2 s to 101.7 s on the same corpus.

`ThroughputMeasurement` therefore records `length_metric` beside `device` and
`batch_order`. An unlabelled sort key is ambiguous by more than the precision
the rate is reported to.

**Batch size, between 8 and 32, is noise.** A single-run sweep appeared to show
batch 32 winning on MPS. Interleaving four rounds so machine drift cannot line
up with batch size dissolves it:

| Config | Four runs | Median | Spread |
|---|---|---|---|
| bs=8 | 28.49 34.28 30.31 27.33 | 29.40 | 6.95 |
| bs=16 | 31.32 31.23 27.93 25.03 | 29.58 | 6.29 |
| bs=32 | 29.19 27.41 24.39 23.05 | 25.90 | 6.14 |

Within-config spread is as large as the difference between configs, and every
config drifts downward across rounds. **Pick batch size for memory, not speed.**

## Sampling

`evenly_spaced` takes the sample by stride across the whole document rather than
from the front, because an RFC opens with its abstract and introduction, which
run shorter than its average clause. The whole-corpus figures make this moot for
the headline, but the sampled command stays the cheap way to re-check a
configuration — run interleaved, which is the lesson of the table above.

## Three retractions, one shape

Each of these was a single run or a single assumption, read as a result.

1. **RFC 9112 is not slower than RFC 9110.** It first measured 33% slower, which
   read as a property of the document. Interleaved re-runs put the two within
   4%, matching their padding waste. Token density is identical — 1.49 and 1.50
   tokens per word.
2. **Batch size 32 is not the right choice on MPS.** Withdrawn above; the effect
   does not survive interleaving.
3. **Word count is not a stand-in for token count.** True of the corpus it was
   measured on, false of the corpus a week later.

The measurements that survived are the ones taken interleaved and repeated. The
corpus itself was re-measured four times as parsing defects were found and
fixed; the figures above are from the current corpus at commit `80cfb4a`.

## What did not enter Git

- **No model weights.** They live under `data/cache/models/bge-m3`, which
  `.gitignore` excludes; the repository holds the hash, not the checkpoint.
- **No vectors.** Throughput needs the forward pass, not its output.
- **No clause text.** `ThroughputMeasurement` has no field that can hold it, and
  a test asserts the serialized record does not contain sampled text.
- **No new default dependency.** `torch` and `transformers` are the optional
  `embedding` extra, imported lazily. The unit suite runs, and `throughput.py`
  imports, on a machine with neither.

## Reproducing

```bash
specpilot embedding measure \
  --manifest af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691 \
  --manifest-dir manifests/local/r0/source \
  --xml artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml \
  --model-dir data/cache/models/bge-m3 \
  --device mps --batch-order length --batch-size 32 --sample 1559
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
