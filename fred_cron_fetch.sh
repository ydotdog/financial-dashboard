#!/bin/bash
# FRED Cron Fetcher — designed to run via cron on the VPS.
# Usage: crontab entry: */5 * * * * /root/dashboard_source/fred_cron_fetch.sh
# Fetches all FRED series and saves to fred_cache/ directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="$SCRIPT_DIR/fred_cache"
mkdir -p "$CACHE_DIR"

# Cross-platform date offset
date_ago() {
    if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
        date -v-${1}d +%Y-%m-%d  # macOS
    else
        date -d "$1 days ago" +%Y-%m-%d  # Linux
    fi
}

end=$(date +%Y-%m-%d)

fetch() {
    local sid="$1" days="$2"
    local start tmp out
    start=$(date_ago "$days")
    tmp="$CACHE_DIR/${sid}.csv.tmp"
    out="$CACHE_DIR/${sid}.csv"

    curl -s --max-time 20 \
         -H "User-Agent: Mozilla/5.0" \
         "https://fred.stlouisfed.org/graph/fredgraph.csv?id=${sid}&cosd=${start}&coed=${end}" \
         > "$tmp" 2>/dev/null

    if [ -s "$tmp" ] && head -1 "$tmp" | grep -q "observation_date"; then
        mv "$tmp" "$out"
    else
        rm -f "$tmp"
    fi
}

fetch DFII10 1125
fetch T10Y2Y 1125
fetch DFF 760
fetch SOFR 395
fetch BAMLH0A0HYM2 760
fetch UNRATE 1855
fetch CPIAUCSL 1855
fetch PCEPILFE 1855
