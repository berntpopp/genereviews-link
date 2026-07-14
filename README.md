# genereviews-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/genereviews-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/genereviews-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/genereviews-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/genereviews-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An **MCP server** (Streamable HTTP and stdio) over
[GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1116/) — the expert-authored,
peer-reviewed gene–disease chapters on NCBI Bookshelf. It serves them as a searchable
corpus of individually citable passages, and exposes the same service layer as a REST API.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

A GeneReviews chapter is a book chapter. NCBI E-utilities return its abstract and its
links, but the body exists only as Bookshelf HTML — there is no API that returns a
chapter's sections, its tables, or a fragment small enough to quote. An agent must
therefore scrape an entire chapter (tens of thousands of tokens for one gene) or do
without.

This server ingests the corpus into Postgres/pgvector and answers with **passages**:
hybrid lexical + dense search returns a handful of hits, each carrying a stable
`passage_id` (`NBK1247:0042`), its section, its chapter's last-updated date, and a
verbatim `recommended_citation`. A claim becomes attributable, and the answer fits in a
context window. Chapters outside the corpus can still be scraped on demand — capped, and
stamped `_meta.truncated` when the cap bites.

## Quick start

The server is hosted behind the [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router):

```bash
claude mcp add --transport http genereviews https://genereviews-link.genefoundry.org/mcp
```

To run it locally (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
uv sync --group dev
cp .env.example .env      # NCBI_API_KEY is optional but lifts the NCBI rate limit
make dev                  # REST on :8000, MCP at /mcp
claude mcp add --transport http genereview-link http://127.0.0.1:8000/mcp
```

The live-NCBI tools (`search_genereviews`, `get_abstract`, `get_links`, `get_fulltext`)
work immediately with no corpus. **Corpus-backed passage search returns HTTP 503 until a
corpus is loaded** — restore a published bundle, build one locally, or point at a
populated Postgres. See [Data & corpus](docs/data.md) for the three loading modes.

## Tools

| Tool | Purpose |
|------|---------|
| `search_passages` | Hybrid lexical + dense search across all chapter passages |
| `search_passages_batch` | Run up to 5 independent passage searches in one call |
| `search_genereviews` | Find GeneReviews chapters for a gene symbol (live NCBI) |
| `get_chapter_metadata` | Chapter outline: sections, passage counts, and table IDs |
| `get_chapter_section` | Every passage in one named section of a chapter |
| `get_passage` | One passage by `passage_id`, with an optional neighbour window |
| `get_passages_batch` | Up to 20 passages by id in a single request |
| `get_table` | One GeneReviews table as structured rows |
| `get_abstract` | PubMed abstract and metadata for a chapter's PMID (live NCBI) |
| `get_links` | Bookshelf, PMC and external links for a PMID (live NCBI) |
| `get_fulltext` | Scrape a chapter from NCBI Bookshelf into hierarchical sections |
| `get_genereview_summary` | Gene-keyed convenience workflow: abstract + links, fulltext opt-in |
| `get_license` | GeneReviews attribution and citation terms |

Leaf names are **unprefixed**, per Tool-Naming Standard v1 — the gateway owns the
namespace. Behind `genefoundry-router` these surface as `genereviews_<tool>` (e.g.
`genereviews_search_passages`). `serverInfo.name` is `GeneReview Link Tool`; the canonical
namespace token is `genereviews`. Static references are also served as MCP resources
(`genereview://usage`, `genereview://license`).

## Data & provenance

- **Upstream:** [GeneReviews on NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK1116/),
  reached through NCBI E-utilities and Bookshelf HTML.
- **Refresh model:** the passage corpus is an immutable, digest-pinned data release,
  restored once into Postgres; the live-NCBI tools always hit NCBI and stamp their
  response version as `live:<timestamp>`. See [Data & corpus](docs/data.md).
- **Rate limits:** NCBI allows 3 req/sec without an API key and 10 req/sec with one;
  `NCBI_API_KEY` is optional but recommended. Scraping is throttled harder still. The
  client enforces this — do not bypass it.
- **Citation:** every search hit and passage carries a `recommended_citation` — paste it
  verbatim. Cite the `passage_id`, the chapter's NBK id, and `chapter_last_updated`.
- **Data licence:** GeneReviews® content is **© 1993–present University of Washington**
  (SPDX `LicenseRef-GeneReviews`) — copyrighted, *not* an open licence. Attribute the
  University of Washington when redistributing; full terms at
  [NBK138602](https://www.ncbi.nlm.nih.gov/books/NBK138602/), and via the `get_license`
  tool.

## Documentation

- [Data & corpus](docs/data.md) — the three corpus-loading modes, the ingest pipeline, and the maintainer bundle-publish flow.
- [Configuration](docs/configuration.md) — every environment variable, plus the Host/Origin and CORS semantics.
- [Deployment](docs/deployment.md) — Docker and Nginx Proxy Manager, the no-egress restore sidecar, memory budget, and transports.
- [Architecture](docs/architecture.md) — the dual REST + MCP surface, the REST endpoint reference, caching, and rate limiting.
- [Security policy](SECURITY.md) — vulnerability reporting and the operator-only repository settings.
- [Changelog](CHANGELOG.md) · [`AGENTS.md`](AGENTS.md) — engineering conventions for humans and agents.

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions, and use the GitHub issue tracker
for bugs and questions. `make ci-local` is the definition-of-done gate: format, lint, line
budget, README standard, mypy, and tests.

## License

Code: [MIT](LICENSE) © Bernt Popp. Data: GeneReviews® content © 1993–present University of
Washington, sourced from NCBI Bookshelf and used under its
[terms](https://www.ncbi.nlm.nih.gov/books/NBK138602/) — it is copyrighted, and
redistribution must attribute the University of Washington.
