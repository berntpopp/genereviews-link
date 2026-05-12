#!/usr/bin/env bash
# tests/smoke/phase_8.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 8 smoke checks ==="

# 1. License resource via REST still resolvable
# LicenseNotice shape: copyright, terms_url, data_source, data_source_url, notes
out=$(curl -sf "$BASE/license")
echo "$out" | jq -e '.copyright' >/dev/null || { echo "FAIL: license route"; exit 1; }
echo "OK: REST /license still works"

# 2. Unknown gene returns structured 400
out=$(curl -s "$BASE/passages/search?q=x&gene=BRCA9999")
code=$(echo "$out" | jq -r '.detail.code // empty')
[[ "$code" == "gene_not_indexed" ]] || { echo "FAIL: expected gene_not_indexed, got $code"; exit 1; }
echo "OK: gene_not_indexed structured 400"

# 3. dedupe param accepted
out=$(curl -sf "$BASE/chapters/NBK1247/sections/management?include=concatenated_text&dedupe=true")
echo "$out" | jq -e '.concatenated_text != null' >/dev/null
echo "OK: dedupe param works"

echo "=== All Phase 8 smoke checks passed ==="
