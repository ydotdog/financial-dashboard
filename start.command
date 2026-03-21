#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  金融看板启动中..."
echo "  http://localhost:8888"
echo "  按 Ctrl+C 停止"
echo ""

# Start FRED data fetcher in background (bypasses MacPacket HTTP/2 proxy issue)
./fred_fetcher.sh &
FETCHER_PID=$!

# Wait for initial FRED data fetch to complete
while [ ! -f fred_cache/DFF.csv ]; do sleep 0.5; done

# Open browser and start server
open http://localhost:8888
python3 server.py

# Clean up fetcher on exit
kill $FETCHER_PID 2>/dev/null
