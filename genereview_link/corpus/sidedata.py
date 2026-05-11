"""Parse the three NBK side-data files into in-memory dicts.

Source: https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

GENESYM_FILE = "NBKid_shortname_genesymbol.txt"
OMIM_FILE = "NBKid_shortname_OMIM.txt"
TITLE_FILE = "GRtitle_shortname_NBKid.txt"


@dataclass(frozen=True, slots=True)
class SideData:
    """In-memory join tables keyed by NBK id."""

    gene_symbols: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    omim_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    short_name_by_nbk: Mapping[str, str] = field(default_factory=dict)


def load_sidedata(directory: Path) -> SideData:
    """Load and parse the three GeneReviews side-data files.

    Each is tab-separated. Multi-value rows are aggregated into tuples.
    """
    gene_symbols: dict[str, list[str]] = defaultdict(list)
    omim_ids: dict[str, list[str]] = defaultdict(list)
    short_name_by_nbk: dict[str, str] = {}

    gs_path = directory / GENESYM_FILE
    if gs_path.exists():
        for row in _rows(gs_path):
            if len(row) >= 3:
                nbk, short, gene = row[0], row[1], row[2]
                if gene and gene not in gene_symbols[nbk]:
                    gene_symbols[nbk].append(gene)
                if short:
                    short_name_by_nbk.setdefault(nbk, short)

    om_path = directory / OMIM_FILE
    if om_path.exists():
        for row in _rows(om_path):
            if len(row) >= 3:
                nbk, _short, omim = row[0], row[1], row[2]
                if omim and omim not in omim_ids[nbk]:
                    omim_ids[nbk].append(omim)

    title_path = directory / TITLE_FILE
    if title_path.exists():
        for row in _rows(title_path):
            if len(row) >= 3:
                _title, short, nbk = row[0], row[1], row[2]
                short_name_by_nbk.setdefault(nbk, short)

    return SideData(
        gene_symbols={k: tuple(v) for k, v in gene_symbols.items()},
        omim_ids={k: tuple(v) for k, v in omim_ids.items()},
        short_name_by_nbk=short_name_by_nbk,
    )


def _rows(path: Path) -> list[list[str]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.append(line.split("\t"))
    return result
