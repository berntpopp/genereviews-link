# Task B1 findings: chapter_last_updated semantics

**Date:** 2026-05-12
**Outcome:** (a) — parser picks wrong date-type

## Method

Probed NBK1440 + 5 randomly-selected chapters' NXML `<pub-history>` and
`<pub-date>` elements.  NXMLs were extracted from the live NCBI litarch
tarball (`ca/84/gene_NBK1116.tar.gz`, version 2026-05-10) by streaming and
extracting the target files without downloading the full ~607 MB archive.
Stored `last_updated_date` values were read from
`genereview.genereview_chapters` (gr-pg corpus DB, port 5436).

All six NXMLs use the production `<pub-history>` shape (no `<pub-date>`
elements present in any chapter probed).  The `<pub-history>` can contain
any subset of three date-type values: `created`, `updated`, and `revised`.

## Per-chapter findings

```json
{
  "NBK1440": {
    "nxml_relpath": "gene_NBK1116/hemochromatosis.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2000-04-03",
    "pub-history date-type=updated": "2024-04-11",
    "pub-history date-type=revised": "2005-07-13",
    "parser_picked_date_type": "revised",
    "parser_last_updated_date": "2005-07-13",
    "stored_last_updated_date": "2005-07-13",
    "correct_last_updated_date": "2024-04-11",
    "newest_reference_year_in_text": 2024
  },
  "NBK23758": {
    "nxml_relpath": "gene_NBK1116/tar.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2009-12-08",
    "pub-history date-type=updated": "2022-08-25",
    "pub-history date-type=revised": "2023-11-02",
    "parser_picked_date_type": "revised",
    "parser_last_updated_date": "2023-11-02",
    "stored_last_updated_date": "2023-11-02",
    "correct_last_updated_date": "2023-11-02",
    "note": "revised > updated here so stored value happens to be correct",
    "newest_reference_year_in_text": 2023
  },
  "NBK475670": {
    "nxml_relpath": "gene_NBK1116/rab18-def.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2018-01-04",
    "pub-history date-type=updated": null,
    "pub-history date-type=revised": null,
    "parser_picked_date_type": null,
    "parser_last_updated_date": null,
    "stored_last_updated_date": null,
    "correct_last_updated_date": null,
    "newest_reference_year_in_text": 2022
  },
  "NBK619577": {
    "nxml_relpath": "gene_NBK1116/usp7-ndd.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2025-12-04",
    "pub-history date-type=updated": null,
    "pub-history date-type=revised": null,
    "parser_picked_date_type": null,
    "parser_last_updated_date": null,
    "stored_last_updated_date": null,
    "correct_last_updated_date": null,
    "newest_reference_year_in_text": 2024
  },
  "NBK114806": {
    "nxml_relpath": "gene_NBK1116/kat6b-dis.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2012-12-13",
    "pub-history date-type=updated": "2020-01-02",
    "pub-history date-type=revised": "2013-01-10",
    "parser_picked_date_type": "revised",
    "parser_last_updated_date": "2013-01-10",
    "stored_last_updated_date": "2013-01-10",
    "correct_last_updated_date": "2020-01-02",
    "newest_reference_year_in_text": 2020
  },
  "NBK92947": {
    "nxml_relpath": "gene_NBK1116/mpv17-mtdep.nxml",
    "pub-date pub-type=initial": null,
    "pub-date pub-type=updated": null,
    "pub-history date-type=created": "2012-05-17",
    "pub-history date-type=updated": "2018-05-17",
    "pub-history date-type=revised": null,
    "parser_picked_date_type": "updated",
    "parser_last_updated_date": "2018-05-17",
    "stored_last_updated_date": "2018-05-17",
    "correct_last_updated_date": "2018-05-17",
    "note": "no revised entry; parser falls through to updated — correct",
    "newest_reference_year_in_text": 2018
  }
}
```

## Semantics of revised vs updated

Based on the probed chapters, the two date-type values carry distinct semantics:

- **`revised`**: appears to capture a metadata or structural-revision timestamp
  (e.g., when the NXML schema was updated by NCBI tooling), not a content
  update visible to readers.  It is always an *older* date than `updated`
  in chapters where both are present (except NBK23758 where revised=2023-11
  and updated=2022-08).
- **`updated`**: reflects the most-recent content/editorial update as shown on
  the GeneReviews web page.  When present, it is the date that should be
  stored as `last_updated_date`.

## Conclusion

**Outcome (a).**  The parser in `genereview_link/corpus/nxml.py` uses a
preference ordering of `revised` first, then `updated` (line 73):

```python
_rev = _ph.find("date[@date-type='revised']") or _ph.find("date[@date-type='updated']")
```

This is wrong whenever a chapter has *both* `revised` and `updated` entries
and `revised` predates `updated` (the common case).  For NBK1440, the parser
picks `revised`=2005-07-13 and stores it as `last_updated_date`, ignoring
`updated`=2024-04-11.  The reviewer's report was correct: the stored date is
nearly 19 years stale.

NBK114806 has the same problem (stored 2013-01-10 instead of 2020-01-02).
NBK23758 happens to be stored correctly because `revised` > `updated` there.
Chapters with only `created` (NBK475670, NBK619577) are correctly stored as
NULL.

The fix is to invert the preference: prefer `updated`, fall back to `revised`.
Because `updated` is the content-editorial timestamp and `revised` is a
metadata-schema timestamp, the corrected parser should read:

```python
_rev = _ph.find("date[@date-type='updated']") or _ph.find("date[@date-type='revised']")
```

This one-line change is the entirety of the code correction.  A
chapters-only metadata reingest is then required to backfill the stale dates.

## Implication for Task B2 (= Task 16 in plan)

Task 16 should:

1. Apply the one-line fix to `genereview_link/corpus/nxml.py`: swap the
   `'revised'` / `'updated'` priority in the `_ph` branch (line 73).
2. Update the corresponding unit-test fixture so the test exercises a chapter
   with *both* date-types and asserts that `updated` wins.
3. Run a chapters-only metadata reingest (or targeted UPDATE from re-parsed
   NXMLs) to correct `last_updated_date` for all chapters in the live corpus.
4. Add a brief note to the MCP usage resource / `get_chapter` docstring
   explaining that `chapter_last_updated` reflects the GeneReviews editorial
   update date (NCBI `date-type=updated`), not a schema-revision timestamp.
