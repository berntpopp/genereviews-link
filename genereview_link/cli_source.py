"""Source-side CLI commands: acquire an offline source set, then ingest it.

Split out of ``cli.py`` to keep both modules inside the per-file line budget;
they are registered on the single Typer app by ``cli.py`` so the command surface
is unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer


def ingest_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Download + parse only; do not write to DB."),
    ] = False,
    archive: Annotated[Path | None, typer.Option("--archive")] = None,
    side_data_dir: Annotated[Path | None, typer.Option("--side-data-dir")] = None,
    source_metadata: Annotated[Path | None, typer.Option("--source-metadata")] = None,
    prior_manifest: Annotated[Path | None, typer.Option("--prior-manifest")] = None,
    genesis: Annotated[
        bool,
        typer.Option(
            "--genesis",
            help=(
                "First build of the chain: no prior manifest. The emitted "
                "manifest is marked as the genesis of the chain."
            ),
        ),
    ] = False,
) -> None:
    """Run the full ingest pipeline against DATABASE_URL.

    Two mutually exclusive modes, both from a retained offline source set:
    chained (``--prior-manifest``, the ``manifest.json`` published by the
    previous release, proven against it) and ``--genesis`` (no prior; only valid
    for the first build of a chain). Omitting both is still refused.
    """
    import asyncio

    from genereview_link.corpus.pipeline import run_full_ingest
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            if dry_run:
                typer.echo("dry-run not yet implemented; aborting")
                raise typer.Exit(2)
            result = await run_full_ingest(
                pool,
                archive=archive,
                side_data_dir=side_data_dir,
                source_metadata=source_metadata,
                prior_manifest=prior_manifest,
                genesis=genesis,
            )
            typer.echo(
                f"ingested {result.chapter_count} chapters / "
                f"{result.passage_count} passages "
                f"as corpus_version={result.corpus_version}"
            )
        finally:
            await pool.close()

    asyncio.run(run())


def snapshot_cmd(
    dest: Annotated[
        Path, typer.Option("--dest", help="Directory to assemble the offline source set in.")
    ],
    acknowledge_terms: Annotated[
        bool,
        typer.Option(
            "--acknowledge-terms",
            help=(
                "Affirm the GeneReviews terms (noncommercial research use only, "
                "retain the copyright notice and Usage Disclaimer, no further "
                "modifications) before any source byte is fetched."
            ),
        ),
    ] = False,
    genesis: Annotated[
        bool,
        typer.Option(
            "--genesis", help="First build of the chain: assemble without a prior manifest."
        ),
    ] = False,
    prior_manifest: Annotated[Path | None, typer.Option("--prior-manifest")] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-download every file even if it still matches.")
    ] = False,
    min_interval: Annotated[
        float | None,
        typer.Option("--min-interval", help="Seconds between upstream requests."),
    ] = None,
    verify: Annotated[
        bool,
        typer.Option("--verify/--no-verify", help="Prove the result through ingest's own reader."),
    ] = True,
) -> None:
    """Fetch the upstream GeneReviews source set that `ingest` consumes.

    Acquisition only: no database, no embeddings, no publication. The output is
    exactly the layout `ingest` reads (`--archive`, `--side-data-dir`,
    `--source-metadata`), plus a `snapshot-manifest.json` recording what was
    fetched. Publication stays rights-gated and separate.
    """
    import os

    from genereview_link.corpus.source_fetch import (
        PoliteRateLimiter,
        SourceFetchError,
        default_min_interval,
        fetch_source_snapshot,
    )

    api_key = os.getenv("NCBI_API_KEY") or None
    interval = default_min_interval(api_key) if min_interval is None else min_interval
    try:
        result = asyncio.run(
            fetch_source_snapshot(
                dest,
                genesis=genesis,
                acknowledge_terms=acknowledge_terms,
                prior_manifest=prior_manifest,
                rate_limiter=PoliteRateLimiter(interval),
                api_key=api_key,
                refresh=refresh,
                verify=verify,
            )
        )
    except SourceFetchError as error:
        typer.echo(f"snapshot refused: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(
        f"snapshot ready: {result.destination} "
        f"({len(result.chapter_ids)} chapters, upstream {result.listing.last_updated})"
    )
    typer.echo(f"fetched: {', '.join(result.fetched) or 'nothing'}")
    typer.echo(f"reused: {', '.join(result.reused) or 'nothing'}")
    chain = (
        "--genesis"
        if result.genesis
        else f"--prior-manifest {result.destination}/prior-manifest.json"
    )
    typer.echo(
        "next: genereview-link ingest "
        f"--archive {result.archive} --side-data-dir {result.destination} "
        f"--source-metadata {result.source_metadata} {chain}"
    )


__all__ = ["ingest_cmd", "snapshot_cmd"]
