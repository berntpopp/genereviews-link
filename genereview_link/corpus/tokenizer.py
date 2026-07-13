"""Cached BGE tokenizer for chunk boundary calculation and encoding.

The same tokenizer instance is used by chunking.py (window boundaries) and
retrieval/embeddings.py (query encoding) so chunk size guarantees match
encoder input size.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

# Re-exported so the ingest pipeline keeps its historical import path. The constants
# themselves live in retrieval.model_identity, which the serving image actually ships.
from genereview_link.retrieval.model_identity import BGE_DIM, BGE_MODEL_NAME

__all__ = ["BGE_DIM", "BGE_MODEL_NAME", *globals().get("__all__", [])]
BGE_MAX_TOKENS = 512  # model context
BGE_RESERVED_SPECIAL_TOKENS = 2  # [CLS], [SEP]
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
    return str(tok.decode(token_ids, skip_special_tokens=True))


def encode_with_offsets(text: str) -> tuple[list[int], list[tuple[int, int]]]:
    """Return token ids and their character-span offsets in *text*.

    Each offset tuple ``(start, end)`` is the half-open character range of the
    corresponding token in the original *text* string.  Special tokens are
    excluded (``add_special_tokens=False``).

    Use this instead of ``encode_to_token_ids`` + ``decode_tokens`` when you
    need to recover the original text for a token window: slice
    ``text[offsets[i][0]:offsets[j][1]]`` rather than calling
    ``decode_tokens``, which would lossily lowercase and insert spaces around
    punctuation.
    """
    tok = bge_tokenizer()
    encoding = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    token_ids: list[int] = list(encoding["input_ids"])
    offsets: list[tuple[int, int]] = [
        (int(start), int(end)) for start, end in encoding["offset_mapping"]
    ]
    return token_ids, offsets
