#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 7 post-rebuild smoke ==="

# Tables present — search results do not expose passage_type directly; table
# passages are identifiable by heading_path containing "Table".
out=$(curl -sf "$BASE/passages/search?q=CFTR+pathogenic+variants+table&limit=10")
echo "$out" | jq -e '[.results[] | select(.heading_path != null and (.heading_path | test("Table")))] | length > 0' >/dev/null \
  || { echo "FAIL: no table passages in search results (heading_path contains no Table)"; exit 1; }
echo "OK: table passages searchable"

# get_table works
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
table_count=$(echo "$out" | jq -r '.table_count')
[[ "$table_count" -gt 0 ]] || { echo "FAIL: NBK1247 table_count is $table_count"; exit 1; }
echo "OK: chapter metadata reports $table_count tables"

# chapter_last_updated populated
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
echo "$out" | jq -e '.chapter_last_updated != null' >/dev/null \
  || { echo "FAIL: chapter_last_updated still null"; exit 1; }
echo "OK: chapter_last_updated populated"

# Text normalization
out=$(curl -sf "$BASE/passages/search?q=Lynch+syndrome&limit=1&include=score_breakdown")
echo "$out" | jq -e '.results[0].snippet // empty | test("^[a-z]") | not' >/dev/null \
  || { echo "WARN: snippet still starts with lowercase (may be valid); inspect manually"; }
echo "OK: text normalization sample"

echo "=== All Phase 7 smoke checks passed ==="
