"""Shape check for gene parameters: identifiers from other namespaces are not symbols.

This corpus is keyed by HGNC gene symbol only and carries no Ensembl/HGNC-id/NCBI
cross-references, so a well-formed foreign identifier cannot be resolved here. It is
answered as ``invalid_input`` naming the converter, never searched or fuzzy-matched as a
symbol (genefoundry-router #343).
"""

from __future__ import annotations

import re

from genereview_link.api.errors import FieldError, StructuredHTTPException

# ASCII [0-9] throughout, never \d: the value is copied into converter calls.
# Forms hgnc_resolve_symbol converts. No HGNC symbol has these shapes. UniProt accessions
# are deliberately absent: their shape collides with real symbols (e.g. P2RY12).
_HGNC_CONVERTIBLE = re.compile(
    r"ENSG[0-9]{11}(?:\.[0-9]+)?|HGNC:[0-9]+|N[MRP]_[0-9]+(?:\.[0-9]+)?", re.IGNORECASE
)
# Ensembl transcript/protein or non-human ids: hgnc_resolve_symbol converts none of them.
_ENSEMBL_OTHER = re.compile(r"ENS[A-Z]*[GTP][0-9]{11}(?:\.[0-9]+)?", re.IGNORECASE)
# HGNC followed by a non-letter (HGNC6407, HGNC:abc): a malformed HGNC id, not a symbol.
_HGNC_MALFORMED = re.compile(r"HGNC[^A-Za-z].*", re.IGNORECASE | re.DOTALL)
_NCBI_GENE = re.compile(r"(NCBIGene:)?([0-9]+)", re.IGNORECASE)

# Path-parameter pattern for gene symbols that also admits the two CURIE forms, so they
# reach the shape check instead of dying as an unexplained pattern mismatch.
GENE_PATH_PATTERN = (
    r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*"
    r"|[Hh][Gg][Nn][Cc]:[0-9]+|[Nn][Cc][Bb][Ii][Gg][Ee][Nn][Ee]:[0-9]+)$"
)


def reject_foreign_gene_identifier(field: str, value: str) -> None:
    """Raise ``unsupported_gene_identifier`` when *value* is an identifier, not a symbol."""
    value = value.strip()
    converter: dict[str, object] | None = None
    kind = "an identifier from another namespace"
    hint = f"Pass the gene's HGNC symbol as {field}."
    if _HGNC_CONVERTIBLE.fullmatch(value):
        converter = {"tool": "hgnc_resolve_symbol", "arguments": {"query": value}}
    elif m := _NCBI_GENE.fullmatch(value):
        converter = {
            "tool": "hgnc_resolve_gene_by_xref",
            "arguments": {"source": "entrez_id", "value": m.group(2)},
        }
        if not m.group(1):
            kind = "a bare number, read here as an NCBI Gene id"
    elif _ENSEMBL_OTHER.fullmatch(value):
        kind = "an Ensembl transcript, protein or non-human id, not a human gene id"
        hint += " No converter call is offered for this form."
    elif _HGNC_MALFORMED.fullmatch(value):
        kind = "a malformed HGNC id"
        hint += " An HGNC id is written HGNC:<n>."
    else:
        return
    if converter:
        hint = (
            f"Convert the identifier to its HGNC symbol with {converter['tool']} "
            f"(see next_commands), then call again with {field}=<symbol>."
        )
        if kind.startswith("a bare number"):
            hint += " If it is an HGNC id, send it as HGNC:<n>."
    raise StructuredHTTPException(
        status_code=400,
        code="unsupported_gene_identifier",
        # Fixed, server-authored: the caller's value is never echoed into prose.
        message=(
            f"{field} takes an HGNC gene symbol; the value is {kind}, which this corpus "
            "cannot resolve. Nothing was searched."
        ),
        recovery_hint=hint,
        field_errors=[
            FieldError(
                field=field,
                reason="identifier from an unsupported namespace, not a gene symbol",
                valid_values=["<HGNC gene symbol>, e.g. KRAS"],
            )
        ],
        next_commands=[converter] if converter else [],
    )
