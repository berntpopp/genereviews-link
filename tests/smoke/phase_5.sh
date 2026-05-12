#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 5 smoke checks ==="

# 1. score_breakdown absent by default
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1")
echo "$out" | jq -e '.results[0] | has("score_breakdown") | not' >/dev/null \
  || { echo "FAIL: score_breakdown should be absent by default"; exit 1; }
echo "OK: score_breakdown absent by default"

# 2. include=score_breakdown returns non-null rrf_score
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1&include=score_breakdown&rerank=rrf")
echo "$out" | jq -e '.results[0].score_breakdown.rrf_score != null' >/dev/null \
  || { echo "FAIL: rrf_score should be non-null with include=score_breakdown"; exit 1; }
echo "OK: rrf_score populated"

# 3. include=score_breakdown returns non-null dense_rank
echo "$out" | jq -e '.results[0].score_breakdown.dense_rank != null' >/dev/null \
  || { echo "FAIL: dense_rank should be non-null"; exit 1; }
echo "OK: dense_rank populated"

# 4. exclude=score_breakdown is a no-op (still absent)
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1&exclude=score_breakdown")
echo "$out" | jq -e '.results[0] | has("score_breakdown") | not' >/dev/null \
  || { echo "FAIL: exclude=score_breakdown should remain absent"; exit 1; }
echo "OK: exclude=score_breakdown no-op"

echo "=== All Phase 5 smoke checks passed ==="
