"""Token-window chunker that never crosses section boundaries.

Used by corpus/nxml.py to split each <sec> body into BGE-compatible windows.
"""

from __future__ import annotations

from dataclasses import dataclass

from genereview_link.corpus.tokenizer import (
    BGE_NET_CHUNK_TOKENS,
    decode_tokens,
    encode_to_token_ids,
)

DEFAULT_OVERLAP_TOKENS = 50


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One section-bounded chunk."""

    chunk_index: int
    text: str
    token_count: int


# T2.2: text-normalization leak audit (2026-05-12)
#
# The encode -> slice -> decode round-trip performed by chunk_section_text for
# multi-window chunks destroys text fidelity in two ways:
#
#   1. Case loss: BAAI/bge-small-en-v1.5 uses an uncased WordPiece vocabulary.
#      tok.decode() reconstructs tokens in their lowercased canonical form, so
#      "Lynch syndrome (CRC)" becomes "lynch syndrome ( crc )".
#
#   2. Whitespace artifacts around punctuation: WordPiece tokenizes "(" and ")"
#      as standalone sub-word tokens.  tok.decode() inserts spaces between every
#      token, producing "( crc )" instead of "(CRC)" and "low - density" instead
#      of "low-density".
#
# Only the multi-window path (len(token_ids) > max_tokens) is affected; the
# fast-path at the early-return above returns the original string verbatim.
# Because GeneReviews chapter sections are routinely longer than 510 tokens,
# most stored passages are mangled.
#
# Evidence (probe run 2026-05-12):
#   input:  'Lynch syndrome (CRC) and low-density lipoprotein cholesterol (LDL-C).'
#   output: 'lynch syndrome ( crc ) and low - density lipoprotein cholesterol ( ldl - c ).'
#
# Fix: replace decode_tokens(window) with a character-span slice of the
# original text, using the token offsets returned by the tokenizer.  This
# eliminates the decode round-trip entirely.  Planned for next commit (Task 27).
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

    token_ids = encode_to_token_ids(text)
    if len(token_ids) <= max_tokens:
        return [TextChunk(chunk_index=0, text=text, token_count=len(token_ids))]

    stride = max_tokens - overlap_tokens
    if stride <= 0:
        raise ValueError(f"overlap_tokens ({overlap_tokens}) must be < max_tokens ({max_tokens})")

    chunks: list[TextChunk] = []
    start = 0
    index = 0
    while start < len(token_ids):
        window = token_ids[start : start + max_tokens]
        chunks.append(
            TextChunk(
                chunk_index=index,
                text=decode_tokens(window),
                token_count=len(window),
            )
        )
        if start + max_tokens >= len(token_ids):
            break
        start += stride
        index += 1
    return chunks
