#!/bin/bash
# FRED Data Fetcher — runs independently of the Python server.
# Periodically downloads FRED CSV data via curl and saves to fred_cache/.
# This bypasses the MacPacket VPN/proxy HTTP/2 issue that affects Python processes.

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

fetch_all() {
    local end_date=$(date +%Y-%m-%d)
    for entry in "${SERIES[@]}"; do
        local sid="${entry%%:*}"
        local years="${entry##*:}"
        local days=$(( years * 365 + 30 ))

        # macOS date: use -v flag for date arithmetic
        local start_date=$(date -v-${days}d +%Y-%m-%d)

        local url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=${sid}&cosd=${start_date}&coed=${end_date}"
        local tmp="$CACHE_DIR/${sid}.csv.tmp"
        local out="$CACHE_DIR/${sid}.csv"

        curl -s --max-time 15 --retry 3 --retry-all-errors --retry-delay 2 \
             -H "User-Agent: Mozilla/5.0" "$url" -o "$tmp" 2>/dev/null

        if [ $? -eq 0 ] && [ -s "$tmp" ] && head -1 "$tmp" | grep -q "observation_date"; then
            mv "$tmp" "$out"
        else
            rm -f "$tmp"
            # Keep existing cache if fetch failed
        fi
    done
}

# Initial fetch
echo "  [fred_fetcher] Initial fetch..."
fetch_all
echo "  [fred_fetcher] Done. Refreshing every ${INTERVAL}s."

# Loop
while true; do
    sleep "$INTERVAL"
    fetch_all
done
