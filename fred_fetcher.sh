#!/bin/bash
# FRED Data Fetcher — runs independently of the Python server.
# Periodically downloads FRED CSV data via curl and saves to fred_cache/.
# Avoids HTTP/2 issues by retrying with different protocols.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="$SCRIPT_DIR/fred_cache"
INTERVAL=300  # 5 minutes

# All FRED series used by the dashboard: series_id:years
SERIES=(
    "DFII10:3"
    "T10Y2Y:3"
    "DFF:2"
    "SOFR:1"
    "BAMLH0A0HYM2:2"
    "UNRATE:5"
    "CPIAUCSL:5"
    "PCEPILFE:5"
)

mkdir -p "$CACHE_DIR"

# Cross-platform date offset: returns YYYY-MM-DD for N days ago
date_ago() {
    if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
        date -v-${1}d +%Y-%m-%d  # macOS
    else
        date -d "$1 days ago" +%Y-%m-%d  # Linux
    fi
}

fetch_one() {
    local sid="$1" days="$2"
    local start_date end_date url tmp out

    start_date=$(date_ago "$days")
    end_date=$(date +%Y-%m-%d)
    url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=${sid}&cosd=${start_date}&coed=${end_date}"
    tmp="$CACHE_DIR/${sid}.csv.tmp"
    out="$CACHE_DIR/${sid}.csv"

    # Try default (HTTP/2), then HTTP/1.1 as fallback, with retries
    local attempt proto
    for attempt in 1 2 3 4 5; do
        if [ "$attempt" -le 3 ]; then
            proto=""  # default (HTTP/2)
        else
            proto="--http1.1"  # fallback
        fi

        curl -s --max-time 20 $proto \
             -H "User-Agent: Mozilla/5.0" "$url" > "$tmp" 2>/dev/null

        if [ -s "$tmp" ] && head -1 "$tmp" | grep -q "observation_date"; then
            mv "$tmp" "$out"
            return 0
        fi
        rm -f "$tmp"
        sleep 1
    done
    return 1
}

fetch_all() {
    local ok=0 fail=0
    for entry in "${SERIES[@]}"; do
        local sid="${entry%%:*}"
        local years="${entry##*:}"
        local days=$(( years * 365 + 30 ))

        if fetch_one "$sid" "$days"; then
            ok=$((ok + 1))
        else
            fail=$((fail + 1))
        fi
    done
    echo "  [fred_fetcher] Fetched: ${ok} ok, ${fail} failed ($(date +%H:%M:%S))"
}

# Initial fetch
echo "  [fred_fetcher] Initial fetch..."
fetch_all

# Loop
while true; do
    sleep "$INTERVAL"
    fetch_all
done
