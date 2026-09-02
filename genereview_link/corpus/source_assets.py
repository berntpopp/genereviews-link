"""The exact inventory of files that make up one offline GeneReviews source capture.

``snapshot`` writes this set and ``ingest`` consumes it.  A genesis build has no prior
release to chain from, so its inventory is the same minus exactly the one prior-artifact
file -- that difference is the only thing that distinguishes the two shapes.
"""

from __future__ import annotations

SOURCE_ASSETS = frozenset(
    {
        "source-capture.json",
        "file_list.csv",
        "prior-manifest.json",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    }
)
PRIOR_ASSETS = frozenset({"prior-manifest.json"})
GENESIS_SOURCE_ASSETS = SOURCE_ASSETS - PRIOR_ASSETS

__all__ = ["GENESIS_SOURCE_ASSETS", "PRIOR_ASSETS", "SOURCE_ASSETS"]
