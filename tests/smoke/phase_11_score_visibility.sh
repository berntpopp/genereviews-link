#!/usr/bin/env bash
# Phase 11 smoke: verify score visibility, diagnostics, query aliasing, and
# heading-path filtered empty-result diagnostics after rebuild.

set -euo pipefail

BASE_URL="${BASE_URL:-${BASE:-http://127.0.0.1:8765}}"

echo "=== Phase 11 score visibility smoke checks ==="
echo "INFO: BASE_URL=$BASE_URL"

fetch() {
  local label="$1"
  local path="$2"
  local url

  if [[ "$path" == http://* || "$path" == https://* ]]; then
    url="$path"
  else
    url="$BASE_URL$path"
  fi

  if ! curl -fsS "$url"; then
    echo "FAIL: $label" >&2
    echo "URL: $url" >&2
    return 1
  fi
}

check_jq() {
  local name="$1"
  local json="$2"
  local filter="$3"

  if printf '%s\n' "$json" | jq -e "$filter" >/dev/null; then
    echo "OK: $name"
  else
    echo "FAIL: $name"
    echo "jq filter: $filter"
    printf '%s\n' "$json" | jq . || printf '%s\n' "$json"
    exit 1
  fi
}

# 1. RRF search exposes rank fields and diagnostics.
out=$(fetch "RRF score visibility search" "/passages/search?q=BRCA1+risk-reducing+mastectomy+salpingo-oophorectomy&limit=5")
check_jq "RRF search returns at least one result" \
  "$out" \
  '.results | length >= 1'
check_jq "RRF search rows expose non-null score and rank fields" \
  "$out" \
  'all(.results[]; .rrf_score != null and .lexical_score != null and .lexical_rank_position != null)'
check_jq "RRF diagnostics report rrf rerank and candidates" \
  "$out" \
  '._meta.diagnostics.rerank_used == "rrf" and
   ._meta.diagnostics.lexical_candidate_count >= 1 and
   ._meta.diagnostics.dense_candidate_count >= 1'

# 2. query alias resolves and carries rank fields.
out=$(fetch "query alias search" "/passages/search?query=BRCA1&limit=1")
check_jq "query alias returns exactly one ranked result" \
  "$out" \
  '.results | length == 1'
check_jq "query alias result exposes non-null score and rank fields" \
  "$out" \
  '.results[0].rrf_score != null and
   .results[0].lexical_score != null and
   .results[0].lexical_rank_position != null'

# 3. heading_path_contains filters search hits case-insensitively.
out=$(fetch "heading_path_contains search" "/passages/search?q=mastectomy&heading_path_contains=Prevention&limit=3")
check_jq "heading_path_contains returns at least one Prevention result" \
  "$out" \
  '.results | length >= 1'
check_jq "heading_path_contains limits all rows to Prevention heading paths" \
  "$out" \
  'all(.results[]; (.heading_path // "" | test("Prevention"; "i")))'

# 4. ids_only still carries diagnostics and lexical rank position.
out=$(fetch "ids_only search" "/passages/search?q=mastectomy&mode=ids_only&limit=3")
check_jq "ids_only returns diagnostics" \
  "$out" \
  '._meta.diagnostics != null'
check_jq "ids_only rows include lexical_rank_position" \
  "$out" \
  '(.results | length >= 1) and all(.results[]; has("lexical_rank_position"))'

# 5. Empty filtered branch reports unfiltered lexical count.
out=$(fetch "empty filtered search" "/passages/search?q=zzzxqvnonexistenttoken&sections=management&limit=3")
check_jq "empty filtered search returns no results" \
  "$out" \
  '.results | length == 0'
check_jq "empty filtered search reports unfiltered_lexical_count value" \
  "$out" \
  '._meta.diagnostics | has("unfiltered_lexical_count") and (.unfiltered_lexical_count | type == "number")'

echo "=== All Phase 11 score visibility smoke checks passed ==="
