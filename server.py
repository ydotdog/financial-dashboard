#!/usr/bin/env python3
"""Financial Dashboard - Local Data Server
Data source: FRED (Federal Reserve Economic Data)
"""

import http.server
import urllib.request
import urllib.parse
import subprocess
import json
import os
import time
from datetime import datetime, timedelta

_cache = {}
CACHE_TTL = 300  # 5 min


def get_fred_csv(series_id, years=1):
    """Fetch FRED series as JSON array [{time, value}, ...]"""
    cache_key = f"{series_id}_{years}"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["ts"] < CACHE_TTL:
        return _cache[cache_key]["data"]

    end = datetime.now()
    start = end - timedelta(days=365 * years + 30)
    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={series_id}"
        f"&cosd={start.strftime('%Y-%m-%d')}"
        f"&coed={end.strftime('%Y-%m-%d')}"
    )

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        csv_text = resp.read().decode("utf-8")

    data = []
    for line in csv_text.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) >= 2:
            val = parts[1].strip()
            if val and val != ".":
                try:
                    data.append({"time": parts[0].strip(), "value": float(val)})
                except ValueError:
                    continue

    _cache[cache_key] = {"ts": now, "data": data}
    return data


def curl_json(url):
    """Fetch URL via curl subprocess (avoids Python SSL issues)"""
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)


def get_cnbc_chart(symbol, years=1):
    """Fetch CNBC daily bars as [{time, value}, ...] + current quote"""
    cache_key = f"cnbc_{symbol}_{years}"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["ts"] < CACHE_TTL:
        return _cache[cache_key]["data"]

    end = datetime.now()
    start = end - timedelta(days=365 * years + 30)
    sfmt = start.strftime("%Y%m%d") + "000000"
    efmt = end.strftime("%Y%m%d") + "235959"

    # Historical bars (via curl)
    bars_url = (
        f"https://ts-api.cnbc.com/harmony/app/bars/"
        f"{symbol}/1D/{sfmt}/{efmt}/adjusted/USD.json"
    )
    bars_data = curl_json(bars_url)
    bars = bars_data.get("barData", {}).get("priceBars", [])
    data = []
    for b in bars:
        ts = int(b["tradeTimeinMills"]) / 1000
        d = datetime.fromtimestamp(ts)
        data.append({
            "time": d.strftime("%Y-%m-%d"),
            "value": float(b["close"]),
        })

    # Current quote (via curl)
    quote_url = (
        f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        f"?symbols={symbol}&requestMethod=itv&partnerId=2&fund=1&exthrs=1&output=json"
    )
    quote = {}
    try:
        qdata = curl_json(quote_url)
        q = qdata["FormattedQuoteResult"]["FormattedQuote"][0]
        # Strip %, $, etc. from numeric fields (bonds return "4.138%")
        def parse_num(v):
            return float(str(v).replace("%", "").replace("$", "").replace(",", "").strip())
        last_time = q.get("last_time", "")
        if "T" in last_time:
            last_time = last_time.split("T")[0]
        quote = {
            "last": parse_num(q["last"]),
            "change": parse_num(q["change"]),
            "change_pct": q.get("change_pct", ""),
            "previous_close": parse_num(q.get("previous_day_closing", 0)),
            "name": q.get("shortName", symbol),
            "time": last_time,
        }
    except Exception:
        pass

    result = {"bars": data, "quote": quote}
    _cache[cache_key] = {"ts": now, "data": result}
    return result


def get_cnbc_batch_quotes(symbols):
    """Fetch multiple CNBC quotes at once. symbols = list of strings."""
    cache_key = "batch_" + "|".join(sorted(symbols))
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["ts"] < 30:  # 30s cache
        return _cache[cache_key]["data"]

    sym_str = urllib.parse.quote("|".join(symbols))
    url = (
        f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        f"?symbols={sym_str}&requestMethod=itv&partnerId=2&fund=1&exthrs=1&output=json"
    )
    raw = curl_json(url)
    quotes = raw.get("FormattedQuoteResult", {}).get("FormattedQuote", [])
    if not isinstance(quotes, list):
        quotes = [quotes]

    def parse_num(v):
        try:
            return float(str(v).replace("%", "").replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            return 0

    result = []
    for q in quotes:
        result.append({
            "symbol": q.get("symbol", ""),
            "name": q.get("shortName", q.get("symbol", "")),
            "last": parse_num(q.get("last", 0)),
            "change": parse_num(q.get("change", 0)),
            "change_pct": q.get("change_pct", "0%"),
        })

    _cache[cache_key] = {"ts": now, "data": result}
    return result


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/fred/"):
            self.handle_fred()
        elif self.path == "/api/cnbc/quotes" or self.path.startswith("/api/cnbc/quotes?"):
            self.handle_cnbc_batch()
        elif self.path.startswith("/api/cnbc/"):
            self.handle_cnbc()
        else:
            super().do_GET()

    def handle_fred(self):
        parts = self.path.split("?")
        series_id = parts[0].replace("/api/fred/", "").strip("/")
        years = 1
        if len(parts) > 1:
            params = urllib.parse.parse_qs(parts[1])
            years = int(params.get("years", ["1"])[0])

        try:
            data = get_fred_csv(series_id, years)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "max-age=300")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_cnbc(self):
        parts = self.path.split("?")
        symbol = parts[0].replace("/api/cnbc/", "").strip("/")
        years = 1
        if len(parts) > 1:
            params = urllib.parse.parse_qs(parts[1])
            years = int(params.get("years", ["1"])[0])
        try:
            data = get_cnbc_chart(symbol, years)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "max-age=300")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_cnbc_batch(self):
        parts = self.path.split("?")
        symbols = ["US10Y", ".DXY", ".VIX", ".SPX", "@GC.1", "EUR=", "JPY=", "CNH="]
        if len(parts) > 1:
            params = urllib.parse.parse_qs(parts[1])
            if "symbols" in params:
                symbols = params["symbols"][0].split(",")
        try:
            data = get_cnbc_batch_quotes(symbols)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "max-age=30")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, fmt, *args):
        # Only log API requests
        if args and "/api/" in str(args[0]):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    PORT = 8888
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    http.server.HTTPServer.allow_reuse_address = True
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    print(f"\n  Financial Dashboard")
    print(f"  http://localhost:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped")
        server.server_close()
