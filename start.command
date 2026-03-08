#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  金融看板启动中..."
echo "  http://localhost:8888"
echo "  按 Ctrl+C 停止"
echo ""
open http://localhost:8888
python3 server.py
