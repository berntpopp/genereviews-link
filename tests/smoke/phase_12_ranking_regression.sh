#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8765}"
FIXTURE="${FIXTURE:-tests/fixtures/ranking_baseline.json}"

failures=0

while IFS=$'\t' read -r status expected query; do
  actual="$(
    curl -fsS --get "$BASE_URL/passages/search" \
      --data-urlencode "q=$query" \
      --data-urlencode "mode=full" \
      --data-urlencode "limit=1" \
      | jq -r '.results[0].passage_id // ""'
  )"

  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS\t%s\t%s\t%s\n' "$status" "$expected" "$query"
  else
    printf 'FAIL\t%s\texpected=%s\tactual=%s\t%s\n' "$status" "$expected" "$actual" "$query"
    failures=$((failures + 1))
  fi
done < <(
  jq -r '.[] | [.status, .expected_top1_passage_id, .query] | @tsv' "$FIXTURE"
)

if (( failures > 0 )); then
  printf '%d ranking regression checks failed\n' "$failures" >&2
  exit 1
fi

printf 'All ranking regression checks passed\n'
