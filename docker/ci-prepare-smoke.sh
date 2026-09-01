#!/usr/bin/env bash
# Pre-seed the reviewed, immutable corpus release assets for the container smoke stack.
#
# `genereview-corpus-restore` runs on an internal-only network so it can reach PostgreSQL
# and nothing else -- it can never fetch the corpus itself. This hook is the ONLY place the
# stack touches the network, and it proves the bytes against the digest committed in
# container-release.json BEFORE they are placed where the sidecar can read them.
#
# The committed digest is the trust root. The download host is not trusted: a tampered
# artifact fails this check and never reaches the stack.
#
# Contract (set by the router's reusable container-ci workflow):
#   GF_SMOKE_FIXTURE_DIR  directory to write fixtures into
#   GF_SMOKE_ENV_FILE     file to append bounded KEY=VALUE assignments to
set -euo pipefail

: "${GF_SMOKE_FIXTURE_DIR:?GF_SMOKE_FIXTURE_DIR is required}"
: "${GF_SMOKE_ENV_FILE:?GF_SMOKE_ENV_FILE is required}"

repository="${GITHUB_REPOSITORY:-berntpopp/genereviews-link}"
config="$(dirname "$0")/../container-release.json"

release_tag="$(jq -er '.data.release_tag' "$config")"
asset_name="$(jq -er '.data.asset_name // "corpus-bundle.tar.gz"' "$config")"
data_digest="$(jq -er '.data.digest' "$config")"
expected_data="${data_digest#sha256:}"
[[ "$expected_data" =~ ^[0-9a-f]{64}$ ]] || {
  echo "container-release.json data.digest is not a sha256 hex digest" >&2
  exit 1
}

seed_dir="$GF_SMOKE_FIXTURE_DIR/corpus-seed"
mkdir -p "$seed_dir"

case "$asset_name" in
  corpus-bundle.tar.gz)
    bundle="$seed_dir/corpus-bundle.tar.gz"
    curl -fsSL --proto '=https' --tlsv1.2 --max-time 900 --max-filesize 4294967296 \
      -o "$bundle" \
      "https://github.com/${repository}/releases/download/${release_tag}/corpus-bundle.tar.gz"
    echo "${expected_data}  ${bundle}" | sha256sum -c -
    {
      echo "CORPUS_SEED_DIR=${seed_dir}"
      echo "CORPUS_SEED_PATH=/seed/corpus-bundle.tar.gz"
      echo "CORPUS_BUNDLE_SHA256=${expected_data}"
      echo "POSTGRES_PASSWORD=smoke-only-not-a-secret"
    } >> "$GF_SMOKE_ENV_FILE"
    ;;
  corpus.dump)
    manifest_digest="$(jq -er '.data.manifest_digest' "$config")"
    checksums_digest="$(jq -er '.data.checksums_digest' "$config")"
    expected_manifest="${manifest_digest#sha256:}"
    expected_checksums="${checksums_digest#sha256:}"
    for expected in "$expected_manifest" "$expected_checksums"; do
      [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
        echo "container-release.json direct asset digest is not a sha256 hex digest" >&2
        exit 1
      }
    done
    curl -fsSL --proto '=https' --tlsv1.2 --max-time 900 --max-filesize 4294967296 \
      -o "$seed_dir/corpus.dump" \
      "https://github.com/${repository}/releases/download/${release_tag}/corpus.dump"
    for asset in manifest.json SHA256SUMS; do
      curl -fsSL --proto '=https' --tlsv1.2 --max-time 900 --max-filesize 1048576 \
        -o "$seed_dir/$asset" \
        "https://github.com/${repository}/releases/download/${release_tag}/${asset}"
    done
    echo "${expected_data}  $seed_dir/corpus.dump" | sha256sum -c -
    echo "${expected_manifest}  $seed_dir/manifest.json" | sha256sum -c -
    echo "${expected_checksums}  $seed_dir/SHA256SUMS" | sha256sum -c -
    grep -Fxq "${expected_data}  corpus.dump" "$seed_dir/SHA256SUMS" || {
      echo "SHA256SUMS does not bind corpus.dump" >&2
      exit 1
    }
    grep -Fxq "${expected_manifest}  manifest.json" "$seed_dir/SHA256SUMS" || {
      echo "SHA256SUMS does not bind manifest.json" >&2
      exit 1
    }
    {
      echo "CORPUS_SEED_DIR=${seed_dir}"
      echo "CORPUS_SEED_PATH=/seed"
      echo "CORPUS_DUMP_SHA256=${expected_data}"
      echo "CORPUS_MANIFEST_SHA256=${expected_manifest}"
      echo "CORPUS_CHECKSUMS_SHA256=${expected_checksums}"
      echo "POSTGRES_PASSWORD=smoke-only-not-a-secret"
    } >> "$GF_SMOKE_ENV_FILE"
    ;;
  *)
    echo "container-release.json data.asset_name is not a supported restore asset" >&2
    exit 1
    ;;
esac

echo "prepared ${release_tag} ${asset_name} at ${seed_dir}"
