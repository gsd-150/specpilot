"""Loads a local embedding model, only when a measurement actually needs it.

Kept apart from `throughput.py` on purpose. The timing harness and the cache key
must stay importable — and testable — on a machine with no model runtime
installed, which is every machine running the unit suite. The heavy import lives
here and happens inside a function, so `import specpilot.embedding.throughput`
never pulls in two gigabytes of tensors.

The model is loaded from a local directory, never a model hub. A measurement
that quietly downloaded a different revision would be a measurement of something
else, and the weight hash it reports would describe bytes that arrived after the
run started.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# The excerpt cap, not the model's 8192 limit. Clauses are bounded below this by
# construction, so a longer window would measure padding the corpus never has.
MAX_TOKENS = 512


class EmbeddingRuntimeUnavailable(RuntimeError):
    """The optional local embedding runtime is not installed."""


def load_encoder(
    model_dir: Path, device: str, *, max_tokens: int = MAX_TOKENS
) -> Callable[[Sequence[str]], Any]:
    """Return a callable that dense-encodes a batch on `device`."""
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as error:  # pragma: no cover - exercised by absence
        raise EmbeddingRuntimeUnavailable(
            "install the 'embedding' extra to measure throughput"
        ) from error

    if device == "mps" and not torch.backends.mps.is_available():
        raise EmbeddingRuntimeUnavailable("this machine has no MPS device")

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModel.from_pretrained(str(model_dir), local_files_only=True)
    model = model.to(device)
    model.eval()

    def encode(batch: Sequence[str]) -> Any:
        tokens = tokenizer(
            list(batch),
            padding=True,
            truncation=True,
            max_length=max_tokens,
            return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            hidden = model(**tokens).last_hidden_state
        # BGE-M3's dense vector is the normalized CLS token.
        dense = torch.nn.functional.normalize(hidden[:, 0], p=2, dim=1)
        if device == "mps":
            # MPS dispatches asynchronously; without this the timer would
            # measure how fast Python can enqueue work, not how fast the GPU
            # can finish it.
            torch.mps.synchronize()
        return dense

    return encode
