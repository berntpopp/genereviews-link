---
name: ncbi-scraper-change
description: Use when modifying NCBI E-utilities or NCBI Bookshelf scraping logic in eutils_client.py.
---

# NCBI Scraper Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect `genereview_link/api/eutils_client.py` for the affected scraper
   method (`_extract_title`, `_extract_authors`, `_find_main_content`, etc.).
2. Never bypass the rate limiter — keep the existing 0.11s/0.34s delays
   and the `RATE_LIMIT_STATE_FILE` coordination path intact.
3. Always parse XML via `defusedxml.ElementTree`. Never import
   `xml.etree.ElementTree` directly.
4. For HTML scraping, prefer existing BeautifulSoup selectors over new ones.
   Add a fixture in `tests/fixtures/` that captures the current NCBI page
   structure and write a parser test against it.
5. Run `pytest tests/test_scraper_parsers.py tests/test_scraper_integration.py`
   before claiming the change is complete.
6. If selectors had to change, document the trigger (page structure change,
   new section, etc.) in the commit message body.
7. Run `make ci-local` before handoff.
