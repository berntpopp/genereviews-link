"""No placeholder SHA-256 may ever stand in for a corpus bundle identity.

Production ran for months with ``CORPUS_BUNDLE_SHA256`` set to 64 zeroes, inherited from
a compose default (``${CORPUS_BUNDLE_SHA256:-000...0}``). That value is a syntactically
valid SHA-256, so every gate downstream reported "identity configured" and passed: the
restore sidecar exited 0 on each deploy while pinning nothing at all.

These tests cover both halves of that failure:

* the code paths must refuse a placeholder digest wherever a bundle identity is accepted;
* no tracked file may ship one as a corpus digest again -- fixing the symptom without
  fixing the source would just let the next deployment inherit it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from genereview_link.db import direct_seed
from genereview_link.db.restore import (
    PLACEHOLDER_DIGESTS,
    ArchivePolicyError,
    is_placeholder_digest,
    normalize_corpus_digest,
    seed_identity_mode,
)

ROOT = Path(__file__).resolve().parents[2]

#: A well-formed digest that is not a placeholder, for the "valid" side of each case.
REAL = "4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc"

#: Files that carry, or could carry, a corpus digest into a deployment.
CONFIG_FILES = (
    "docker/docker-compose.yml",
    "docker/docker-compose.prod.yml",
    "docker/docker-compose.npm.yml",
    "docker/docker-compose.dev.yml",
    "docker/docker-compose.override.gr-pg.yml",
    "docker/ci-prepare-smoke.sh",
    ".env.docker.example",
    "container-release.json",
)

#: `KEY=value`, `KEY: value`, or `"key": "value"` for any corpus digest key, plus the
#: data-release digests in container-release.json. Quotes and `sha256:` are optional.
_DIGEST_ASSIGNMENT = re.compile(
    r"""(?P<key>CORPUS_[A-Z0-9_]*SHA256
        |EXPECTED_BUNDLE_SHA256
        |"(?:data_)?(?:digest|manifest_digest|checksums_digest)")
        \s*[:=]\s*
        ["']?(?:\$\{[^}]*?[:-]+)?\s*(?:sha256:)?(?P<digest>[0-9a-fA-F]{64})""",
    re.VERBOSE,
)


def test_the_all_zero_digest_is_a_placeholder() -> None:
    assert is_placeholder_digest("0" * 64)
    assert is_placeholder_digest("sha256:" + "0" * 64)
    assert is_placeholder_digest("0" * 64) and "0" * 64 in PLACEHOLDER_DIGESTS


def test_the_empty_file_digest_is_a_placeholder() -> None:
    """Checksumming nothing must not count as checksumming something."""
    assert is_placeholder_digest(hashlib.sha256(b"").hexdigest())


def test_a_real_digest_is_not_a_placeholder() -> None:
    assert not is_placeholder_digest(REAL)
    assert normalize_corpus_digest(f"sha256:{REAL.upper()}", label="test digest") == REAL


@pytest.mark.parametrize("value", sorted(PLACEHOLDER_DIGESTS))
def test_normalize_refuses_every_placeholder(value: str) -> None:
    with pytest.raises(ArchivePolicyError, match="placeholder"):
        normalize_corpus_digest(value, label="corpus bundle SHA-256")


@pytest.mark.parametrize("value", ["", "   ", "not-a-digest", "0" * 63, "g" * 64])
def test_normalize_refuses_malformed_identities(value: str) -> None:
    with pytest.raises(ArchivePolicyError, match="exact 64-character"):
        normalize_corpus_digest(value, label="corpus bundle SHA-256")


def test_seed_identity_mode_refuses_the_placeholder_legacy_identity() -> None:
    """The exact production configuration must now fail closed."""
    with pytest.raises(ArchivePolicyError, match="placeholder"):
        seed_identity_mode("0" * 64, "", "", "")


def test_seed_identity_mode_refuses_a_placeholder_direct_identity() -> None:
    with pytest.raises(ArchivePolicyError, match="placeholder"):
        seed_identity_mode("", "0" * 64, REAL, REAL)


def test_seed_identity_mode_still_accepts_real_identities() -> None:
    assert seed_identity_mode(REAL, "", "", "") == "legacy"
    assert seed_identity_mode("", REAL, REAL, REAL) == "direct"


def test_direct_seed_anchors_refuse_placeholders(tmp_path: Path) -> None:
    with pytest.raises(direct_seed.DirectSeedError, match="placeholder"):
        direct_seed.extract_direct_seed(
            tmp_path,
            tmp_path / "out",
            expected_dump_sha256="0" * 64,
            expected_manifest_sha256=REAL,
            expected_checksums_sha256=REAL,
        )


def test_direct_seed_placeholder_set_matches_restore() -> None:
    """The two modules duplicate the constant to avoid an import cycle; keep them equal."""
    assert direct_seed._PLACEHOLDER_DIGESTS == PLACEHOLDER_DIGESTS


@pytest.mark.parametrize("relative", CONFIG_FILES)
def test_no_tracked_config_ships_a_placeholder_corpus_digest(relative: str) -> None:
    """Fix the source, not the symptom: nothing may hand a placeholder to a deployment."""
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(f"{relative} is not present in this checkout")
    offenders = [
        f"{match.group('key')} = {match.group('digest')}"
        for match in _DIGEST_ASSIGNMENT.finditer(path.read_text(encoding="utf-8"))
        if is_placeholder_digest(match.group("digest"))
    ]
    assert not offenders, f"{relative} ships placeholder corpus digest(s): {offenders}"


def test_the_scanner_would_have_caught_the_original_defect(tmp_path: Path) -> None:
    """A regression test that cannot fail on the original bug is not a regression test."""
    sample = tmp_path / "docker-compose.yml"
    sample.write_text(
        '      CORPUS_BUNDLE_SHA256: "${CORPUS_BUNDLE_SHA256:-' + "0" * 64 + '}"\n',
        encoding="utf-8",
    )
    matches = list(_DIGEST_ASSIGNMENT.finditer(sample.read_text(encoding="utf-8")))
    assert len(matches) == 1
    assert is_placeholder_digest(matches[0].group("digest"))
