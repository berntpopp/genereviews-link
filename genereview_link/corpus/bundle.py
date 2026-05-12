"""Build a release bundle from a populated Postgres."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


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
    schemas: tuple[str, ...] = ("public", "genereview"),
    extensions: tuple[str, ...] = ("vector",),
) -> None:
    cmd = ["pg_dump", "-Fc", "--no-owner", "-f", str(dump_path)]
    for extension in extensions:
        cmd.extend(["--extension", extension])
    for schema in schemas:
        cmd.extend(["--schema", schema])
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
