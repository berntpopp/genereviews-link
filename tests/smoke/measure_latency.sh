#!/usr/bin/env bash
# tests/smoke/measure_latency.sh
#
# Measure p50 latency (ms) for each MCP-exposed tool over 20 samples.
# Warm-up: one untimed call before the timed loop to prime the connection.
#
# Usage:
#   BASE=http://127.0.0.1:8001 tests/smoke/measure_latency.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8001}"

measure() {
    local label="$1"
    local url="$2"
    local n=20
    local times=()

    # Warm-up call (untimed)
    curl -sf -o /dev/null "$url" || true

    for _ in $(seq 1 $n); do
        t=$(curl -sf -o /dev/null -w "%{time_total}\n" "$url")
        times+=("$t")
    done
    p50=$(printf "%s\n" "${times[@]}" | sort -n | awk -v n="$n" 'NR==int(n/2)+1{printf "%.0f", $1 * 1000}')
    echo "$label: ~${p50}ms p50"
}

echo "=== GeneReview-Link tool latency measurements ==="

measure "search_passages rrf"     "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=rrf&limit=5"
measure "search_passages lexical" "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=lexical&limit=5"
measure "search_passages off"     "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=off&limit=5"
measure "get_passage"             "$BASE/passages/NBK1247:0010"
measure "get_passage neighbors=3" "$BASE/passages/NBK1247:0010?neighbors=3"
# NBK1247 summary section is empty; use 'diagnosis' instead
measure "get_chapter_section"     "$BASE/chapters/NBK1247/sections/diagnosis"
measure "get_chapter_metadata"    "$BASE/chapters/NBK1247/metadata"
# NBK1247 table IDs use long NXML names; use the first available one
measure "get_table"               "$BASE/chapters/NBK1247/tables/brca1.molgen.TA"

echo "=== Done ==="
