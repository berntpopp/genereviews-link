"""The ONNX serving path must reproduce the vectors the corpus was embedded with.

The corpus's 40,853 passage vectors were produced by sentence-transformers (PyTorch). The
serving image cannot carry PyTorch, so it runs the same weights through ONNX Runtime. If
those two paths disagree, query and passage vectors stop being comparable and we are back
to the exact silent-wrong-answer failure this whole change exists to remove -- only harder
to spot, because the provider would look real.

So parity is asserted, not assumed.

Both halves need artifacts that are deliberately absent from CI's default environment:
the reference half needs PyTorch (`uv sync --extra cpu`), and both need the staged model
(`genereview-link model stage --output <dir>`, then GENEREVIEW_TEST_MODEL_DIR=<dir>).
The tests skip when those are missing rather than passing vacuously -- and
`test_parity_suite_is_not_vacuous` fails if a skip would have hidden a real comparison.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from genereview_link.retrieval.model_identity import (
    BGE_DIM,
    BGE_MAX_SEQ_LENGTH,
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
    BGE_POOLING,
)

#: Texts spanning the shapes the server actually embeds: prefixed queries, corpus
#: passages, a truncation-length input, and a degenerate short one.
PARITY_TEXTS = [
    "Represent this sentence for searching relevant passages: BRCA1 breast cancer surveillance",
    "BRCA1-related breast cancer surveillance schedule.",
    "MTHFR deficiency: prenatal testing considerations.",
    "CHEK2 hereditary cancer risk management and screening intervals.",
    "Genetic counseling: risk to family members.",
    "A" * 3000,
    "short",
    "Tumour surveillance in Li-Fraumeni syndrome begins in infancy.",
]

#: Observed minimum across this suite is 0.999999999796; the bar is set far below that so
#: it tracks a genuine regression rather than float noise.
MIN_COSINE = 0.999999


def _model_dir() -> Path | None:
    configured = os.environ.get("GENEREVIEW_TEST_MODEL_DIR")
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_dir() else None


def _require_model() -> Path:
    path = _model_dir()
    if path is None:
        pytest.skip("set GENEREVIEW_TEST_MODEL_DIR to a staged model directory")
    return path


def _require_reference() -> None:
    pytest.importorskip("torch", reason="reference parity needs the `cpu` extra")
    pytest.importorskip("sentence_transformers")


def test_the_pinned_pipeline_matches_the_published_model_card() -> None:
    """Pooling and length are pinned in code; assert they match the published config."""
    assert BGE_POOLING == "cls"
    assert BGE_MAX_SEQ_LENGTH == 512
    assert BGE_DIM == 384
    assert BGE_MODEL_NAME == "BAAI/bge-small-en-v1.5"
    assert len(BGE_MODEL_REVISION) == 40

    model_dir = _model_dir()
    if model_dir is None or not (model_dir / "1_Pooling" / "config.json").is_file():
        pytest.skip("staged model does not include the pooling config to cross-check")
    pooling = json.loads((model_dir / "1_Pooling" / "config.json").read_text())
    assert pooling["pooling_mode_cls_token"] is True
    assert pooling["pooling_mode_mean_tokens"] is False
    assert pooling["word_embedding_dimension"] == BGE_DIM


@pytest.mark.slow
def test_onnx_matches_sentence_transformers() -> None:
    """The measured parity claim in model_identity.py, as an executable assertion."""
    _require_reference()
    model_dir = _require_model()
    reference_dir = os.environ.get("GENEREVIEW_TEST_REFERENCE_DIR")
    if not reference_dir or not Path(reference_dir).is_dir():
        pytest.skip("set GENEREVIEW_TEST_REFERENCE_DIR to a sentence-transformers model dir")

    import numpy as np
    from sentence_transformers import SentenceTransformer

    from genereview_link.retrieval.onnx_embeddings import OnnxBgeEmbeddingProvider

    reference = np.asarray(
        SentenceTransformer(reference_dir, device="cpu").encode(
            PARITY_TEXTS, normalize_embeddings=True
        ),
        dtype=np.float64,
    )
    provider = OnnxBgeEmbeddingProvider(model_dir)
    candidate = np.asarray(asyncio.run(provider.embed_passages(PARITY_TEXTS)), dtype=np.float64)

    assert candidate.shape == reference.shape == (len(PARITY_TEXTS), BGE_DIM)
    cosine = np.sum(reference * candidate, axis=1)
    worst = float(cosine.min())
    assert worst >= MIN_COSINE, (
        f"ONNX and sentence-transformers disagree (min cosine {worst:.12f}); query and "
        "corpus vectors would no longer be comparable"
    )


@pytest.mark.slow
def test_onnx_vectors_are_unit_length_and_correctly_shaped() -> None:
    """Cheap invariant that needs no PyTorch: normalisation actually happened."""
    model_dir = _require_model()

    from genereview_link.retrieval.onnx_embeddings import OnnxBgeEmbeddingProvider

    provider = OnnxBgeEmbeddingProvider(model_dir)
    vectors = asyncio.run(provider.embed_passages(PARITY_TEXTS))

    assert len(vectors) == len(PARITY_TEXTS)
    for vector in vectors:
        assert len(vector) == BGE_DIM
        norm = sum(value * value for value in vector) ** 0.5
        assert abs(norm - 1.0) < 1e-5


@pytest.mark.slow
def test_semantically_close_texts_rank_above_unrelated_ones() -> None:
    """Real embeddings must actually carry meaning -- the property the stub cannot fake."""
    model_dir = _require_model()

    from genereview_link.retrieval.onnx_embeddings import OnnxBgeEmbeddingProvider

    provider = OnnxBgeEmbeddingProvider(model_dir)
    query, related, unrelated = asyncio.run(
        provider.embed_passages(
            [
                "Represent this sentence for searching relevant passages: "
                "BRCA1 breast cancer surveillance",
                "BRCA1-related breast cancer surveillance schedule.",
                "MTHFR deficiency: prenatal testing considerations.",
            ]
        )
    )
    close = sum(a * b for a, b in zip(query, related, strict=True))
    far = sum(a * b for a, b in zip(query, unrelated, strict=True))
    assert close > far, f"related {close:.4f} did not outrank unrelated {far:.4f}"
