"""Build a release bundle from a populated Postgres."""

from __future__ import annotations

import hashlib
import importlib.resources as resources
import json
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


def _reviewed_migration_digests() -> dict[str, dict[str, str]]:
    """Digest the exact SQL files shipped by this source revision."""
    from genereview_link.db.migrations import control as control_pkg
    from genereview_link.db.migrations import data as data_pkg

    result: dict[str, dict[str, str]] = {"control": {}, "data": {}}
    for namespace, package, prefix in (
        ("control", control_pkg, ""),
        ("data", data_pkg, "genereview:"),
    ):
        for entry in resources.files(package).iterdir():
            if entry.is_file() and entry.name.endswith(".sql"):
                version = entry.name.removesuffix(".sql")
                result[namespace][f"{prefix}{version}"] = hashlib.sha256(
                    entry.read_bytes()
                ).hexdigest()
    return result


@dataclass
class BundleManifest:
    manifest_version: str = "1"
    bundle_format: str = "tar.gz"
    corpus_release_id: str = ""
    corpus_version: str = ""
    tarball_source_sha256: str = ""
    tarball_last_updated: str = ""
    chapter_count: int = 0
    passage_count: int = 0
    embedding: dict[str, object] = field(
        default_factory=lambda: {
            "model_name": "BAAI/bge-small-en-v1.5",
            "dimension": 384,
            "distance_metric": "cosine",
            "active_table": "genereview_embeddings_bge384",
        }
    )
    postgres: dict[str, object] = field(
        default_factory=lambda: {
            "major_version": "18",
            "pgvector_version": "0.8.2",
        }
    )
    schema_migrations: dict[str, list[str]] = field(
        default_factory=lambda: {"control": [], "data": []}
    )
    migration_file_sha256: dict[str, dict[str, str]] = field(
        default_factory=_reviewed_migration_digests
    )
    app_git_sha: str = ""
    app_version: str = ""
    genereview_link_version: str = ""
    hnsw: dict[str, object] = field(
        default_factory=lambda: {
            "index_name": "genereview_embeddings_bge384_hnsw_cosine",
            "exists": False,
        }
    )
    source: dict[str, object] = field(default_factory=dict)
    validation: dict[str, object] = field(
        default_factory=lambda: {"status": "not_run", "smoke_queries": []}
    )
    evaluation: dict[str, object] = field(
        default_factory=lambda: {
            "status": "not_run",
            "suite": "tests/eval/genereviews_queries.jsonl",
            "suite_sha256": "",
            "model_name": "BAAI/bge-small-en-v1.5",
            "results": {},
            "result_sha256": "",
        }
    )
    computation: dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_by: str = "manual"
    license: dict[str, object] = field(
        default_factory=lambda: {
            "copyright": "(c) 1993-2026 University of Washington",
            "terms_url": "https://www.ncbi.nlm.nih.gov/books/NBK138602/",
        }
    )
    checksums: dict[str, str] = field(default_factory=dict)


def pg_dump_to(
    dump_path: Path,
    *,
    database_url: str,
    snapshot: str | None = None,
    tables: tuple[str, ...] = (
        "genereview.genereview_chapters",
        "genereview.genereview_embeddings_bge384",
        "genereview.genereview_passages",
        "public.genereview_corpus_version",
    ),
) -> None:
    cmd = [
        "pg_dump",
        "-Fc",
        "--data-only",
        "--no-owner",
        "--no-privileges",
        "-f",
        str(dump_path),
    ]
    for table in tables:
        cmd.extend(["--table", table])
    if snapshot is not None:
        cmd.extend(["--snapshot", snapshot])
    cmd.append(database_url)
    subprocess.run(  # noqa: S603
        cmd,
        check=True,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_data_only_bundle(*, work_dir: Path, output: Path, manifest: BundleManifest) -> Path:
    """Write the canonical, data-only release directory without overwriting it.

    The release contract is deliberately small: a PostgreSQL custom-format data
    dump, stable JSON metadata, and checksums for precisely those two files.
    Operational timestamps are excluded because they are not artifact identity.
    """
    dump = work_dir / "corpus.dump"
    if not dump.is_file() or dump.is_symlink():
        raise ValueError("work_dir must contain a regular corpus.dump")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite bundle output: {output}")
    output.mkdir(mode=0o700, parents=True)
    target_dump = output / "corpus.dump"
    shutil.copyfile(dump, target_dump)
    payload = asdict(manifest)
    payload.pop("created_at", None)
    payload["manifest_version"] = "3"
    payload["bundle_format"] = "postgresql-custom-data-only"
    payload["checksums"] = {"corpus.dump": sha256_file(target_dump)}
    metadata = output / "manifest.json"
    metadata.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    sums = output / "SHA256SUMS"
    sums.write_text(
        f"{payload['checksums']['corpus.dump']}  corpus.dump\n"
        f"{sha256_file(metadata)}  manifest.json\n"
    )
    return output


def write_bundle(
    *,
    work_dir: Path,
    output: Path,
    manifest: BundleManifest,
    sidedata_dir: Path,
) -> Path:
    """Pack manifest + corpus.dump + sidedata/ into a single .tar.gz."""
    dump = work_dir / "corpus.dump"
    manifest.checksums["corpus.dump"] = sha256_file(dump)
    for f in sidedata_dir.iterdir():
        if f.is_file():
            manifest.checksums[f"sidedata/{f.name}"] = sha256_file(f)

    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2))

    with tarfile.open(output, "w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")
        tar.add(dump, arcname="corpus.dump")
        for f in sidedata_dir.iterdir():
            if f.is_file():
                tar.add(f, arcname=f"sidedata/{f.name}")

    sha_sibling = output.with_suffix(output.suffix + ".sha256")
    sha_sibling.write_text(sha256_file(output) + "  " + output.name + "\n")
    return output
