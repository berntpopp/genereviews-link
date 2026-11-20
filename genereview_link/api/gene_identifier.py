"""Shape check for gene parameters: identifiers from other namespaces are not symbols.

This corpus is keyed by HGNC gene symbol only and carries no Ensembl/HGNC-id/NCBI
cross-references, so a well-formed foreign identifier cannot be resolved here. It is
answered as ``invalid_input`` naming the converter, never searched or fuzzy-matched as a
symbol (genefoundry-router #343).
"""

from __future__ import annotations

import re

from genereview_link.api.errors import FieldError, StructuredHTTPException

# Forms hgnc_resolve_symbol converts. No HGNC symbol has these shapes. UniProt accessions
# are deliberately absent: their shape collides with real symbols (e.g. P2RY12).
_HGNC_CONVERTIBLE = re.compile(r"ENSG\d{11}(?:\.\d+)?|HGNC:\d+|N[MRP]_\d+(?:\.\d+)?", re.IGNORECASE)
# A transcript, not a gene: hgnc_resolve_symbol does not convert it, so no converter is named.
_TRANSCRIPT = re.compile(r"ENST\d{11}(?:\.\d+)?", re.IGNORECASE)
# A bare number is never a gene symbol; it is read as an NCBI Gene id.
_NCBI_GENE = re.compile(r"(?:NCBIGene:)?(\d+)", re.IGNORECASE)

# Path-parameter pattern for gene symbols that also admits the two CURIE forms, so they
# reach the shape check instead of dying as an unexplained pattern mismatch.
GENE_PATH_PATTERN = (
    r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*|[Hh][Gg][Nn][Cc]:\d+|[Nn][Cc][Bb][Ii][Gg][Ee][Nn][Ee]:\d+)$"
)


def reject_foreign_gene_identifier(field: str, value: str) -> None:
    """Raise ``unsupported_gene_identifier`` when *value* is an identifier, not a symbol."""
    value = value.strip()
    converter: dict[str, object] | None = None
    kind = "an identifier from another namespace"
    hint = (
        f"Pass the gene's HGNC symbol as {field}; no converter call is offered for a transcript id."
    )
    if _HGNC_CONVERTIBLE.fullmatch(value):
        converter = {"tool": "hgnc_resolve_symbol", "arguments": {"query": value}}
    elif m := _NCBI_GENE.fullmatch(value):
        converter = {
            "tool": "hgnc_resolve_gene_by_xref",
            "arguments": {"source": "entrez_id", "value": m.group(1)},
        }
    elif _TRANSCRIPT.fullmatch(value):
        kind = "an Ensembl transcript id, and this tool takes a gene"
    else:
        return
    if converter:
        hint = (
            f"Convert the identifier to its HGNC symbol with {converter['tool']} "
            f"(see next_commands), then call again with {field}=<symbol>."
        )
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
