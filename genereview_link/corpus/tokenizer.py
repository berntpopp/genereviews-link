"""Cached BGE tokenizer for chunk boundary calculation and encoding.

The same tokenizer instance is used by chunking.py (window boundaries) and
retrieval/embeddings.py (query encoding) so chunk size guarantees match
encoder input size.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_MAX_TOKENS = 512                  # model context
BGE_RESERVED_SPECIAL_TOKENS = 2       # [CLS], [SEP]
BGE_NET_CHUNK_TOKENS = BGE_MAX_TOKENS - BGE_RESERVED_SPECIAL_TOKENS  # 510


@lru_cache(maxsize=1)
def bge_tokenizer() -> Any:
    """Load BGE WordPiece tokenizer once per process."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(BGE_MODEL_NAME, use_fast=True)


def count_tokens(text: str) -> int:
    """Return the BGE token count for *text* (excluding special tokens)."""
    tok = bge_tokenizer()
    return len(tok.encode(text, add_special_tokens=False))


def encode_to_token_ids(text: str) -> list[int]:
    """Return the BGE token id sequence (no special tokens)."""
    tok = bge_tokenizer()
    return list(tok.encode(text, add_special_tokens=False))


def decode_tokens(token_ids: list[int]) -> str:
    """Inverse of encode_to_token_ids."""
    tok = bge_tokenizer()
    return tok.decode(token_ids, skip_special_tokens=True)
