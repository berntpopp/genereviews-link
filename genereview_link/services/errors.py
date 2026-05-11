"""Domain exceptions for GeneReview Link services."""

from __future__ import annotations


class NotYetIndexedError(Exception):
    """Raised when a chapter/gene isn't in the active corpus and fresh=False."""

    def __init__(
        self,
        *,
        gene_symbol: str | None = None,
        nbk_id: str | None = None,
        pubmed_id: str | None = None,
        corpus_version: str | None = None,
    ) -> None:
        super().__init__("not_yet_indexed")
        self.gene_symbol = gene_symbol
        self.nbk_id = nbk_id
        self.pubmed_id = pubmed_id
        self.corpus_version = corpus_version
