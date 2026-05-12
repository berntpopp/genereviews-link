"""Token-window chunker that never crosses section boundaries.

Used by corpus/nxml.py to split each <sec> body into BGE-compatible windows.
"""

from __future__ import annotations

from dataclasses import dataclass

from genereview_link.corpus.tokenizer import (
    BGE_NET_CHUNK_TOKENS,
    encode_with_offsets,
)

DEFAULT_OVERLAP_TOKENS = 50


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One section-bounded chunk."""

    chunk_index: int
    text: str
    token_count: int


# Earlier versions used decode_tokens(window) to recover text from each token
# window, which lossily lowercased everything and inserted spaces around
# punctuation tokens (e.g. "Lynch syndrome (CRC)" became
# "lynch syndrome ( crc )").  We now slice the original text by character
# offsets returned by the tokenizer, eliminating the decode round-trip.
def chunk_section_text(
    text: str,
    *,
    max_tokens: int = BGE_NET_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[TextChunk]:
    """Split *text* into overlapping token windows.

    The full *text* must come from within a single <sec>; this function never
    looks for paragraph boundaries to split — that decoupling happens in nxml.py.
    """
    if not text.strip():
        return []

    token_ids, offsets = encode_with_offsets(text)
    if len(token_ids) <= max_tokens:
        return [TextChunk(chunk_index=0, text=text, token_count=len(token_ids))]

    stride = max_tokens - overlap_tokens
    if stride <= 0:
        raise ValueError(f"overlap_tokens ({overlap_tokens}) must be < max_tokens ({max_tokens})")

    chunks: list[TextChunk] = []
    start = 0
    index = 0
    while start < len(token_ids):
        end = min(start + max_tokens, len(token_ids))
        window_len = end - start
        char_start = offsets[start][0]
        char_end = offsets[end - 1][1]
        chunks.append(
            TextChunk(
                chunk_index=index,
                text=text[char_start:char_end],
                token_count=window_len,
            )
        )
        if end >= len(token_ids):
            break
        start += stride
        index += 1
    return chunks
