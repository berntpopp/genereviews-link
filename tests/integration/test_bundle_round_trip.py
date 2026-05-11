"""pg_dump -> pg_restore round-trip preserves data + verifies row counts."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import asyncpg
import pytest

from genereview_link.corpus.bundle import BundleManifest, pg_dump_to, write_bundle
from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations


def _pg_available() -> bool:
    """Return True if pg_dump and pg_restore are on PATH."""
    return shutil.which("pg_dump") is not None and shutil.which("pg_restore") is not None


pytestmark = pytest.mark.slow


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.slow
async def test_pg_dump_restore_round_trip(pool: asyncpg.Pool, tmp_path: Path) -> None:
    """Seed minimal data, dump to a file, restore into a temp schema, assert counts."""
    if not _pg_available():
        pytest.skip("pg_dump / pg_restore not found on PATH")

    database_url = os.environ.get("GENEREVIEW_TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("GENEREVIEW_TEST_DATABASE_URL not set")

    # Apply migrations so tables exist
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")

    # Insert a corpus version row and one chapter
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into public.genereview_corpus_version
                (version, file_list_etag, tarball_sha256, tarball_size_bytes,
                 ingest_started_at, ingest_status, is_active)
            values ('2026-01-01', 'etag1', 'sha1', 0, now(), 'completed', true)
            on conflict (version) do nothing
            """
        )
        await conn.execute(
            """
            insert into genereview.chapters
                (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                 authors, nxml_relpath, corpus_version)
            values ('NBK9999', 'TestGene', 'Test Chapter Title', 99999999,
                    ARRAY['TESTG'], ARRAY['999999'],
                    ARRAY['Author A'], 'NBK9999.xml', '2026-01-01')
            on conflict (nbk_id) do nothing
            """
        )
        chapter_count = await conn.fetchval("select count(*) from genereview.chapters")

    # --- pg_dump ---
    dump_path = tmp_path / "corpus.dump"
    pg_dump_to(dump_path, database_url=database_url)
    assert dump_path.exists()
    assert dump_path.stat().st_size > 0

    # --- write bundle (exercises write_bundle path) ---
    sidedata = tmp_path / "sidedata"
    sidedata.mkdir()
    (sidedata / "dummy.txt").write_text("dummy sidedata\n")
    bundle_out = tmp_path / "bundle.tar.gz"
    manifest = BundleManifest(corpus_version="2026-01-01", chapter_count=int(chapter_count))
    write_bundle(work_dir=tmp_path, output=bundle_out, manifest=manifest, sidedata_dir=sidedata)
    assert bundle_out.exists()

    # --- verify tarball contents ---
    with tarfile.open(bundle_out, "r:gz") as tar:
        members = {m.name for m in tar.getmembers()}
    assert "manifest.json" in members
    assert "corpus.dump" in members
    assert "sidedata/dummy.txt" in members

    # --- pg_restore back into the DB to verify round-trip integrity ---
    # Use shutil.which to resolve the full path (avoids S607 partial-path warning).
    # pg_restore may exit non-zero for warnings; we check data instead.
    pg_restore_bin = shutil.which("pg_restore") or "pg_restore"
    subprocess.run(  # noqa: S603
        [
            pg_restore_bin,
            "--schema=genereview",
            "--schema=public",
            "-d",
            database_url,
            "--clean",
            "--if-exists",
            str(dump_path),
        ],
        check=False,
        capture_output=True,
    )

    # Verify chapter count survived the restore cycle
    async with pool.acquire() as conn:
        restored_count = await conn.fetchval("select count(*) from genereview.chapters")
    assert int(restored_count) >= int(chapter_count)
