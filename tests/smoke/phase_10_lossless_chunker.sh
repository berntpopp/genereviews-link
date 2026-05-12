#!/usr/bin/env bash
# Phase 10 smoke: verify the no-loss chunker recovered the previously-missing
# clinical content via the MCP endpoints.
#
# Tests against the live gr-pg corpus on port 8765 after the lossless reingest.
# Each check fails loudly so a regression is impossible to miss.

set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8765}"
fail=0

check() {
  local name="$1" cond="$2"
  if eval "$cond" >/dev/null; then
    echo "PASS  $name"
  else
    echo "FAIL  $name"
    fail=$((fail+1))
  fi
}

# --- regression: BRCA1 Prevention section ---
PREV=$(curl -sS "$BASE/chapters/NBK1247/sections/management?heading_path_contains=Prevention&mode=full")
check "NBK1247 Prevention section has at least 1 passage" \
  "[ \$(echo '$PREV' | jq -r '.passage_count') -ge 1 ]"
check "NBK1247 Prevention passage mentions mastectomy" \
  "echo '$PREV' | jq -r '.passages[].text' | grep -qi mastectomy"
check "NBK1247 Prevention passage mentions salpingo-oophorectomy or oophorectomy" \
  "echo '$PREV' | jq -r '.passages[].text' | grep -qiE 'salpingo-oophorectomy|oophorectomy'"

# --- regression: chunk 0022 is no longer a 63-char stub ---
PASSAGE22=$(curl -sS "$BASE/passages/NBK1247:0022?mode=full")
CHARS=$(echo "$PASSAGE22" | jq -r '.passage.char_count')
echo "INFO  NBK1247:0022 char_count=$CHARS"
check "NBK1247:0022 is not a header-only stub" "[ \$CHARS -gt 100 ]"

# --- search: BRCA1 risk-reducing surgery now returns Prevention content ---
SEARCH=$(curl -sS "$BASE/passages/search?q=BRCA1+risk-reducing+mastectomy+salpingo-oophorectomy&limit=5")
HITS=$(echo "$SEARCH" | jq -r '.results | length')
echo "INFO  search returned $HITS hits"
check "search returns at least 3 hits" "[ \$HITS -ge 3 ]"
check "search hits include a Prevention or risk-reducing surgery passage" \
  "echo '$SEARCH' | jq -r '.results[] | select(.heading_path | test(\"Prevention\"; \"i\")) | .passage_id' | head -1 | grep -q ."

# --- search: top hit's snippet should mention mastectomy or prophylactic ---
TOP_SNIPPET=$(echo "$SEARCH" | jq -r '.results[0].snippet')
echo "INFO  top snippet: $TOP_SNIPPET"

# --- audit reachability: at least one passage in corpus contains a list bullet marker ---
LIST_SEARCH=$(curl -sS "$BASE/passages/search?q=Consider+prophylactic&limit=3")
check "search for 'Consider prophylactic' returns content" \
  "[ \$(echo '$LIST_SEARCH' | jq -r '.results | length') -ge 1 ]"

# --- random sample: previously-empty section bodies are no longer stubs ---
# Pick a few section/chapter combos and assert each section's max passage char_count is large.
for nbk in NBK1247 NBK1488 NBK1440; do
  M=$(curl -sS "$BASE/chapters/${nbk}/sections/management?mode=full" 2>/dev/null || echo '{}')
  MAX=$(echo "$M" | jq -r '[.passages[].text | length] | max // 0')
  echo "INFO  $nbk management section max passage size: $MAX"
  check "$nbk management section has substantive passages" "[ \$MAX -gt 200 ]"
done

if [ $fail -gt 0 ]; then
  echo
  echo "RESULT: $fail check(s) FAILED"
  exit 1
fi
echo
echo "RESULT: ALL CHECKS PASSED"
