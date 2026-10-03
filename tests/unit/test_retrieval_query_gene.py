"""Conservative recognition of explicitly written GeneReview gene symbols."""

from dataclasses import replace

import pytest

from genereview_link.retrieval.lexical_ranking import (
    augment_primary_gene_candidates,
    rank_lexical_candidates,
)
from genereview_link.retrieval.query_gene import (
    apply_query_gene_context,
    candidate_query_gene_symbol,
    infer_query_gene_symbol,
)
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow
from genereview_link.retrieval.rerank import rerank_with_embeddings


def test_infers_one_exact_primary_symbol_from_query() -> None:
    assert infer_query_gene_symbol("BRCA1 risk-reducing surgery", {"BRCA1", "BRCA2"}) == "BRCA1"


def test_does_not_infer_lowercase_common_word_as_gene() -> None:
    assert infer_query_gene_symbol("met the diagnostic criteria", {"MET"}) is None


def test_ambiguous_uppercase_symbol_needs_genetic_context() -> None:
    assert infer_query_gene_symbol("KIT", {"KIT"}) is None
    assert infer_query_gene_symbol("KIT mutation testing", {"KIT"}) == "KIT"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("BRCA1 DNA repair", "BRCA1"),
        ("HFE C282Y allele frequency", "HFE"),
        ("BRCA1 versus BRCA2 management", None),
    ],
)
def test_symbol_candidate_ignores_acronyms_and_variant_tokens(
    query: str, expected: str | None
) -> None:
    assert candidate_query_gene_symbol(query) == expected


def test_does_not_choose_one_gene_for_a_multi_gene_query() -> None:
    assert infer_query_gene_symbol("BRCA1 versus BRCA2 management", {"BRCA1", "BRCA2"}) is None


def test_unrecognized_or_nonprimary_symbol_is_not_inferred() -> None:
    assert infer_query_gene_symbol("BRCA1 risk-reducing surgery", {"BRCA2"}) is None
    assert infer_query_gene_symbol("BRCA1 risk-reducing surgery", set()) is None


def test_primary_symbol_boosts_without_removing_other_chapters() -> None:
    target = _row("NBK1247:0024", 0.45, ("BRCA1", "BRCA2"))
    cross_gene = _row("NBK1211:0033", 0.50, ())

    contextual = apply_query_gene_context([cross_gene, target], "BRCA1 risk-reducing surgery")
    ranked, _diag = rerank_with_embeddings(contextual, dense_scores={})

    assert [row.passage.nbk_id for row in ranked] == ["NBK1247", "NBK1211"]
    assert ranked[0].primary_gene_match is True
    assert ranked[1].primary_gene_match is False


def test_shared_lexical_ranker_applies_same_primary_symbol_boost() -> None:
    rows = [
        _row("NBK1211:0033", 0.50, ()),
        _row("NBK1247:0024", 0.45, ("BRCA1", "BRCA2")),
    ]

    ranked = rank_lexical_candidates(rows, "BRCA1 risk-reducing surgery")

    assert [row.passage.nbk_id for row in ranked] == ["NBK1247", "NBK1211"]


async def test_primary_gene_lookup_recovers_candidates_outside_lexical_top_k() -> None:
    target = _row("NBK501979:0005", 0.2, ("GRIN2B",))
    base = [_row("NBK385627:0003", 0.9, ())]

    class Repository:
        async def search_passages(self, query: str, **kwargs: object) -> list[LexicalPassageRow]:
            assert query == "GRIN2B-related neurodevelopmental disorder phenotype spectrum"
            assert kwargs["gene_symbol"] == "GRIN2B"
            assert kwargs["gene_role"] == "any"
            return [target]

    candidates = await augment_primary_gene_candidates(
        Repository(),  # type: ignore[arg-type]
        base,
        "GRIN2B-related neurodevelopmental disorder phenotype spectrum",
    )
    ranked = rank_lexical_candidates(
        candidates, "GRIN2B-related neurodevelopmental disorder phenotype spectrum"
    )

    assert {row.passage.passage_id for row in candidates} == {
        "NBK385627:0003",
        "NBK501979:0005",
    }
    target_result = next(row for row in ranked if row.passage.passage_id == "NBK501979:0005")
    assert target_result.primary_gene_match is True


@pytest.mark.asyncio
async def test_single_gene_chapter_is_defining_without_primary_array() -> None:
    target = _row("NBK1250:0032", 0.3, ())
    target = replace(
        target,
        passage=replace(
            target.passage,
            gene_symbols=("CFTR",),
            primary_gene_symbols=(),
            chapter_title="Cystic Fibrosis",
        ),
    )

    class Repository:
        async def search_passages(
            self, *_args: object, **_kwargs: object
        ) -> list[LexicalPassageRow]:
            return [target]

    candidates = await augment_primary_gene_candidates(
        Repository(),  # type: ignore[arg-type]
        [],
        "CFTR F508del modulator therapy",
    )
    ranked = rank_lexical_candidates(candidates, "CFTR F508del modulator therapy")

    assert len(ranked) == 1
    assert ranked[0].primary_gene_match is True


@pytest.mark.asyncio
async def test_primary_gene_lookup_skips_multigene_and_explicit_filter_queries() -> None:
    class Repository:
        async def search_passages(self, *_args: object, **_kwargs: object) -> list[object]:
            raise AssertionError("ambiguous or explicit-filter query must not trigger lookup")

    rows = [_row("NBK1247:0024", 0.5, ("BRCA1", "BRCA2"))]
    for query, explicit_gene in [
        ("BRCA1 versus BRCA2 risk-reducing surgery", None),
        ("BRCA1 risk-reducing surgery", "BRCA2"),
    ]:
        assert (
            await augment_primary_gene_candidates(
                Repository(),
                rows,
                query,
                explicit_gene=explicit_gene,  # type: ignore[arg-type]
            )
            == rows
        )


def test_explicit_gene_filter_context_overrides_conflicting_query_symbol() -> None:
    brca = _row("NBK1247:0024", 0.5, ("BRCA1", "BRCA2"))
    lynch = _row("NBK1211:0033", 0.4, ("MSH2",))

    contextual = apply_query_gene_context(
        [brca, lynch], "BRCA1 versus Lynch syndrome MSH2 mutation", explicit_gene="MSH2"
    )

    assert [row.primary_gene_match for row in contextual] == [False, True]


def test_multi_gene_comparison_keeps_both_chapters_unboosted() -> None:
    rows = [
        _row("NBK1247:0024", 0.5, ("BRCA1", "BRCA2")),
        _row("NBK1211:0033", 0.4, ("MSH2",)),
    ]

    contextual = apply_query_gene_context(rows, "BRCA1 versus MSH2 risk-reducing surgery")

    assert [row.primary_gene_match for row in contextual] == [False, False]


def _row(
    passage_id: str, lexical_rank: float, primary_symbols: tuple[str, ...]
) -> LexicalPassageRow:
    nbk_id = passage_id.split(":", maxsplit=1)[0]
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id=nbk_id,
            passage_id=passage_id,
            chapter_section="management",
            heading_path="Management > Surveillance",
            section_level=2,
            chunk_index=0,
            text=passage_id,
            gene_symbols=primary_symbols,
            primary_gene_symbols=primary_symbols,
        ),
        phrase_rank=lexical_rank,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=1,
        lexical_rank=lexical_rank,
    )
