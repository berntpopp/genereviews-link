"""Identity of the dense embedding model the corpus was built with.

This lives OUTSIDE `genereview_link.corpus`: the serving image ships no ingest pipeline,
and the fleet OCI content policy denies every path with a `corpus` component, so the
server cannot read these constants from `corpus.tokenizer`.
"""

from __future__ import annotations

BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_DIM = 384  # output embedding dimension for bge-small-en-v1.5

__all__ = ["BGE_DIM", "BGE_MODEL_NAME"]
