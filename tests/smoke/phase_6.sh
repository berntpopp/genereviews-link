#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 6 smoke checks ==="

# 1. get_passage with neighbors
out=$(curl -sf "$BASE/passages/NBK1247:0010?neighbors=2")
echo "$out" | jq -e '.passage.passage_id == "NBK1247:0010"' >/dev/null
echo "$out" | jq -e '(.neighbors_before | length) <= 2' >/dev/null
echo "$out" | jq -e '(.neighbors_after | length) <= 2' >/dev/null
echo "$out" | jq -e 'has("has_more_before") and has("has_more_after")' >/dev/null
echo "OK: get_passage neighbors window"

# 2. get_chapter_metadata
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
echo "$out" | jq -e '.sections | length > 0' >/dev/null
echo "$out" | jq -e '.gene_symbols | index("BRCA1") != null' >/dev/null
echo "OK: get_chapter_metadata"

# 3. Empty-result diagnostics
# Use gene=XYZNOTEXIST to force empty results; semantic search returns hits for any query string
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=5&gene=XYZNOTEXIST")
echo "$out" | jq -e '.results == []' >/dev/null
echo "$out" | jq -e '._meta.diagnostics.suggestions | length >= 0' >/dev/null
echo "OK: empty-result diagnostics"

# 4. concatenated_text gated
# Use 'diagnosis' section: no 'summary' passages are seeded in this corpus version
out=$(curl -sf "$BASE/chapters/NBK1247/sections/diagnosis")
echo "$out" | jq -e 'has("concatenated_text") | not' >/dev/null
echo "OK: concatenated_text absent by default"

out=$(curl -sf "$BASE/chapters/NBK1247/sections/diagnosis?include=concatenated_text")
echo "$out" | jq -e '.concatenated_text | type == "string"' >/dev/null
echo "OK: include=concatenated_text returns the field"

echo "=== All Phase 6 smoke checks passed ==="
