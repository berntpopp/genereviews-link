#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 9 smoke checks ==="

# 1. mode=ids_only returns lean shape
out=$(curl -sf "$BASE/passages/search?q=BRCA1&mode=ids_only&limit=2")
echo "$out" | jq -e '.results[0] | keys == ["chapter_section", "passage_id", "rrf_score"]' >/dev/null \
  || { echo "FAIL: ids_only shape unexpected"; echo "$out"; exit 1; }
echo "OK: ids_only lean shape"

# 2. snippet_chars accepted and reduces snippet size
big=$(curl -sf "$BASE/passages/search?q=BRCA1&snippet_chars=800&limit=1")
small=$(curl -sf "$BASE/passages/search?q=BRCA1&snippet_chars=80&limit=1")
big_len=$(echo "$big" | jq -r '.results[0].snippet | length')
small_len=$(echo "$small" | jq -r '.results[0].snippet | length')
[[ "$small_len" -lt "$big_len" ]] || { echo "FAIL: snippet_chars no effect ($small_len vs $big_len)"; exit 1; }
echo "OK: snippet_chars shrinks snippet ($small_len < $big_len chars)"

# 3. recommended_citation present and formatted
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1")
echo "$out" | jq -e '.results[0].recommended_citation | startswith("BRCA")' >/dev/null \
  || { echo "FAIL: recommended_citation missing or malformed"; echo "$out"; exit 1; }
echo "OK: recommended_citation present"

# 4. _meta.license_summary present on every envelope
echo "$out" | jq -e '._meta.license_summary | contains("genereview://license")' >/dev/null \
  || { echo "FAIL: _meta.license_summary missing"; exit 1; }
echo "OK: _meta.license_summary present"

# 5. get_chapter_metadata returns tables list
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
echo "$out" | jq -e '.tables | length > 0' >/dev/null \
  || { echo "FAIL: tables list empty on NBK1247"; exit 1; }
table_id=$(echo "$out" | jq -r '.tables[0].table_id')
echo "OK: tables[0].table_id = $table_id"

# 6. Per-section total_char_count populated
echo "$out" | jq -e '.sections[] | select(.passage_count > 0).total_char_count > 0' >/dev/null \
  || { echo "FAIL: total_char_count not populated for non-empty sections"; exit 1; }
echo "OK: per-section total_char_count"

# 7. SectionSummary.note on systematically-unscraped sections
echo "$out" | jq -e '.sections[] | select(.section == "summary") | .note | length > 0' >/dev/null \
  || { echo "FAIL: summary section has no note"; exit 1; }
echo "OK: SectionSummary.note for unscraped summary"

# 8. get_chapter_section returns passage_count + concatenated_char_count
out=$(curl -sf "$BASE/chapters/NBK1247/sections/management?include=concatenated_text")
pc=$(echo "$out" | jq -r '.passage_count')
cc=$(echo "$out" | jq -r '.concatenated_char_count')
ct=$(echo "$out" | jq -r '.concatenated_text | length')
[[ "$pc" -gt 0 ]] && [[ "$cc" -eq "$ct" ]] \
  || { echo "FAIL: passage_count/concatenated_char_count mismatch ($pc, $cc vs $ct)"; exit 1; }
echo "OK: section metadata fields ($pc passages, $cc chars)"

# 9. POST /passages/batch with 2 ids returns 2 passages
out=$(curl -sf -X POST "$BASE/passages/batch" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["NBK1247:0001", "NBK1247:0002"]}')
echo "$out" | jq -e '.passages | length == 2' >/dev/null \
  || { echo "FAIL: batch fetch returned wrong count"; echo "$out"; exit 1; }
echo "OK: POST /passages/batch (2 found)"

# 10. POST /passages/batch with oversize returns 413
out=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/passages/batch" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json; print(json.dumps({"ids":[f"NBK1247:{i:04d}" for i in range(25)]}))')")
[[ "$out" == "413" ]] || { echo "FAIL: oversize batch returned $out, expected 413"; exit 1; }
echo "OK: oversize batch returns 413"

# 11. License resource has SPDX + attribution_text
out=$(curl -sf "$BASE/license")
echo "$out" | jq -e '.license_spdx == "LicenseRef-GeneReviews"' >/dev/null \
  || { echo "FAIL: license_spdx wrong or missing"; exit 1; }
echo "$out" | jq -e '.attribution_text | startswith("GeneReviews")' >/dev/null \
  || { echo "FAIL: attribution_text wrong or missing"; exit 1; }
echo "OK: license SPDX + attribution_text"

# 12. table_id surfaced on table-type search hits (best-effort)
out=$(curl -sf "$BASE/passages/search?q=targeted+therapies&limit=5")
echo "$out" | jq -e '[.results[] | select(.passage_type == "table") | .table_id] | length > 0' >/dev/null \
  || { echo "WARN: no table-type hits in this query (may be fine; check manually)"; }

# 13. include=heading_path_array opts in
out=$(curl -sf "$BASE/passages/search?q=BRCA1&include=heading_path_array&limit=1")
echo "$out" | jq -e '.results[0].heading_path_array | type == "array"' >/dev/null \
  || { echo "FAIL: heading_path_array opt-in didn't take"; exit 1; }
echo "OK: include=heading_path_array opt-in"

# 14. include=score_breakdown surfaces dense_model_id + embedding_dim under _meta
out=$(curl -sf "$BASE/passages/search?q=BRCA1&include=score_breakdown&limit=1")
echo "$out" | jq -e '._meta.dense_model_id != null and ._meta.embedding_dim != null' >/dev/null \
  || { echo "FAIL: _meta.dense_model_id/embedding_dim absent on include=score_breakdown"; exit 1; }
echo "OK: _meta model fields under score_breakdown"

echo "=== All Phase 9 smoke checks passed ==="
