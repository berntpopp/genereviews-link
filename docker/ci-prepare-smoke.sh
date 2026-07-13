#!/usr/bin/env bash
# Pre-seed the reviewed, immutable corpus artifact for the container smoke stack.
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
digest="$(jq -er '.data.digest' "$config")"
expected="${digest#sha256:}"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
  echo "container-release.json data.digest is not a sha256 hex digest" >&2
  exit 1
}

seed_dir="$GF_SMOKE_FIXTURE_DIR/corpus-seed"
mkdir -p "$seed_dir"
bundle="$seed_dir/corpus-bundle.tar.gz"

curl -fsSL --proto '=https' --tlsv1.2 --max-time 900 -o "$bundle" \
  "https://github.com/${repository}/releases/download/${release_tag}/corpus-bundle.tar.gz"

# Authenticity: the artifact must be exactly the one this commit reviewed.
echo "${expected}  ${bundle}" | sha256sum -c -

{
  echo "CORPUS_SEED_DIR=${seed_dir}"
  echo "CORPUS_BUNDLE_SHA256=${expected}"
  echo "POSTGRES_PASSWORD=smoke-only-not-a-secret"
} >> "$GF_SMOKE_ENV_FILE"

echo "prepared ${release_tag} corpus artifact at ${bundle}"
