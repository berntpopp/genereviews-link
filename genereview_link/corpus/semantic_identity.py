"""Database-independent logical identity for one GeneReviews corpus."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any


def _json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical(value: object) -> bytes:
    return (
        json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def _digest_rows(rows: Iterable[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical(dict(row)))
    return digest.hexdigest()


def compute_content_identity(
    *,
    chapters: list[Mapping[str, object]],
    passages: list[Mapping[str, object]],
    side_mapping_ids: set[str],
    source_capture: Mapping[str, object],
) -> dict[str, object]:
    """Hash logical rows in canonical key order and compare every prior chapter."""
    ordered_chapters = sorted(chapters, key=lambda row: str(row["nbk_id"]))
    ordered_passages = sorted(
        passages, key=lambda row: (str(row["nbk_id"]), str(row["passage_id"]))
    )
    chapter_ids = [str(row["nbk_id"]) for row in ordered_chapters]
    if chapter_ids != sorted(side_mapping_ids) or chapter_ids != source_capture.get("chapter_ids"):
        raise ValueError("database, side mapping, and capture chapter IDs differ")
    by_chapter: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for passage in ordered_passages:
        by_chapter[str(passage["nbk_id"])].append(passage)
    chapter_digests = {
        str(chapter["nbk_id"]): hashlib.sha256(
            _canonical(
                {
                    "chapter": dict(chapter),
                    "passages": [dict(row) for row in by_chapter[str(chapter["nbk_id"])]],
                }
            )
        ).hexdigest()
        for chapter in ordered_chapters
    }
    prior = source_capture.get("prior_artifact")
    if not isinstance(prior, Mapping) or not isinstance(prior.get("chapter_digests"), Mapping):
        raise ValueError("source capture lacks prior per-chapter identity")
    prior_ids = {str(value) for value in prior.get("chapter_ids", [])}
    current_ids = set(chapter_ids)
    prior_digests = prior["chapter_digests"]
    changed = sorted(
        chapter_id
        for chapter_id in current_ids & prior_ids
        if prior_digests.get(chapter_id) != chapter_digests[chapter_id]
    )
    archive = source_capture.get("archive")
    if not isinstance(archive, Mapping):
        raise ValueError("source capture lacks archive identity")
    return {
        "chapter_ids": chapter_ids,
        "chapter_ids_sha256": hashlib.sha256(_canonical(chapter_ids)).hexdigest(),
        "side_mapping_ids_sha256": hashlib.sha256(_canonical(sorted(side_mapping_ids))).hexdigest(),
        "chapters_sha256": _digest_rows(ordered_chapters),
        "passages_sha256": _digest_rows(ordered_passages),
        "chapter_digests": chapter_digests,
        "source_archive": {
            "members_sha256": archive.get("members_sha256"),
            "expanded_sha256": archive.get("expanded_sha256"),
        },
        "delta_from_prior": {
            "object_id": prior.get("object_id"),
            "prior_chapter_count": prior.get("chapter_count"),
            "added": sorted(current_ids - prior_ids),
            "removed": sorted(prior_ids - current_ids),
            "changed": changed,
            "chapters_sha256": {
                "prior": prior.get("chapters_sha256"),
                "current": _digest_rows(ordered_chapters),
            },
            "passages_sha256": {
                "prior": prior.get("passages_sha256"),
                "current": _digest_rows(ordered_passages),
            },
        },
    }


async def collect_content_identity(connection: Any) -> dict[str, object]:
    """Recompute logical identity from a caller-owned database snapshot."""
    chapters = await connection.fetch(
        """
        select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids, authors,
               initial_pub_date, last_updated_date, corpus_version, nxml_relpath,
               raw_metadata, primary_gene_symbols
          from genereview.genereview_chapters order by nbk_id
        """
    )
    passages = await connection.fetch(
        """
        select nbk_id, passage_id, chapter_section, heading_path, section_level,
               chunk_index, text, text_hash, char_count, token_estimate,
               corpus_version, passage_type, table_id, table_data, passage_role
          from genereview.genereview_passages order by nbk_id, passage_id
        """
    )
    capture = await connection.fetchval(
        "select source_capture from public.genereview_corpus_version where is_active"
    )
    if not isinstance(capture, Mapping):
        raise ValueError("active corpus lacks its retained source capture")
    mapping_ids = capture.get("chapter_ids")
    if not isinstance(mapping_ids, list):
        raise ValueError("source capture lacks sorted mapping chapter IDs")
    return compute_content_identity(
        chapters=[dict(row) for row in chapters],
        passages=[dict(row) for row in passages],
        side_mapping_ids={str(value) for value in mapping_ids},
        source_capture=capture,
    )


__all__ = ["collect_content_identity", "compute_content_identity"]
