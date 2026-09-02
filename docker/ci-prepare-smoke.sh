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

# The fleet contract (the router's ReleaseConfig, extra keys forbidden) owns
# container-release.json's `data` block: mode, release_tag, digest, schema
# compatibility, image allowlist. Which release ASSET carries that digest, and the
# digests of the two control files a direct (manifest-v3) release ships beside it,
# are this repository's own concern and live in corpus-release.json. When that file
# is present it must name the same release and the same digest as the contract pin.
seed_config="$(dirname "$0")/../corpus-release.json"

release_tag="$(jq -er '.data.release_tag' "$config")"
data_digest="$(jq -er '.data.digest' "$config")"
asset_name="corpus-bundle.tar.gz"
if [ -f "$seed_config" ]; then
  asset_name="$(jq -er '.asset_name' "$seed_config")"
  [ "$(jq -er '.release_tag' "$seed_config")" = "$release_tag" ] || {
    echo "corpus-release.json release_tag differs from container-release.json data.release_tag" >&2
    exit 1
  }
  [ "$(jq -er '.digest' "$seed_config")" = "$data_digest" ] || {
    echo "corpus-release.json digest differs from container-release.json data.digest" >&2
    exit 1
  }
fi
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
    manifest_digest="$(jq -er '.manifest_digest' "$seed_config")"
    checksums_digest="$(jq -er '.checksums_digest' "$seed_config")"
    expected_manifest="${manifest_digest#sha256:}"
    expected_checksums="${checksums_digest#sha256:}"
    for expected in "$expected_manifest" "$expected_checksums"; do
      [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
        echo "corpus-release.json direct asset digest is not a sha256 hex digest" >&2
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
      echo "CORPUS_RELEASE_TAG=${release_tag}"
      echo "POSTGRES_PASSWORD=smoke-only-not-a-secret"
    } >> "$GF_SMOKE_ENV_FILE"
    ;;
  *)
    echo "corpus-release.json asset_name is not a supported restore asset" >&2
    exit 1
    ;;
esac

# The embedding model is staged into the SAME seed directory: the deployment gate grants
# exactly one bind mount, so both artifacts reach the sidecar through it. Its bytes are
# proven against the digests committed in genereview_link/retrieval/model_identity.py --
# the download host is not trusted here either.
model_dir="$seed_dir/model"
mkdir -p "$model_dir"
identity="$(dirname "$0")/../genereview_link/retrieval/model_identity.py"
# stdlib only: the runner has python3 but need not have this project installed.
eval "$(python3 - "$identity" <<'PYEOF'
import ast, sys, shlex
tree = ast.parse(open(sys.argv[1]).read())
const = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
        const[node.targets[0].id] = node.value.value
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
        const[node.targets[0].id] = {k.value: v.value for k, v in zip(node.value.keys, node.value.values)
                                     if isinstance(v, ast.Constant)}
for name in ("BGE_MODEL_NAME", "BGE_MODEL_REVISION", "BGE_ONNX_FILE_SHA256"):
    print(f"{name}={shlex.quote(str(const[name]))}")
print(f"BGE_TOKENIZER_FILE_SHA256={shlex.quote(const['BGE_MODEL_FILES']['tokenizer.json'])}")
PYEOF
)"
for spec in "onnx/model.onnx:model.onnx:$BGE_ONNX_FILE_SHA256" \
            "tokenizer.json:tokenizer.json:$BGE_TOKENIZER_FILE_SHA256"; do
  src="${spec%%:*}"; rest="${spec#*:}"; name="${rest%%:*}"; want="${rest#*:}"
  [[ "$want" =~ ^[0-9a-f]{64}$ ]] || { echo "model identity pin is not a sha256" >&2; exit 1; }
  curl -fsSL --proto '=https' --tlsv1.2 --max-time 900 --max-filesize 536870912 \
    -o "$model_dir/$name" \
    "https://huggingface.co/${BGE_MODEL_NAME}/resolve/${BGE_MODEL_REVISION}/${src}"
  echo "${want}  ${model_dir}/${name}" | sha256sum -c -
done
{
  echo "MODEL_SEED_PATH=/seed/model"
  echo "GENEREVIEW_EMBEDDING_PROVIDER=onnx"
} >> "$GF_SMOKE_ENV_FILE"

echo "prepared ${release_tag} ${asset_name} at ${seed_dir} (model staged at ${model_dir})"
