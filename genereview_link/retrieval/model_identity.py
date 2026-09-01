"""Identity of the dense embedding model the corpus was built with.

This lives OUTSIDE `genereview_link.corpus`: the serving image ships no ingest pipeline,
and the fleet OCI content policy denies every path with a `corpus` component, so the
server cannot read these constants from `corpus.tokenizer`.
"""

from __future__ import annotations

BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_MODEL_FILE = "model.safetensors"
BGE_MODEL_FILE_SHA256 = "3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad"
BGE_DIM = 384  # output embedding dimension for bge-small-en-v1.5

__all__ = [
    "BGE_DIM",
    "BGE_MODEL_FILE",
    "BGE_MODEL_FILE_SHA256",
    "BGE_MODEL_NAME",
    "BGE_MODEL_REVISION",
]
