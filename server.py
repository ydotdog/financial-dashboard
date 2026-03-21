#!/usr/bin/env python3
"""Financial Dashboard - Local Data Server
Data source: FRED (Federal Reserve Economic Data)
"""

import http.server
import urllib.parse
import subprocess
import json
import os
import time
from datetime import datetime, timedelta

_cache = {}
CACHE_TTL = 300  # 5 min


FRED_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fred_cache")


def get_fred_csv(series_id, years=1):
    """Read FRED series from cached CSV files (written by fred_fetcher.sh)."""
    cache_key = f"{series_id}_{years}"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["ts"] < CACHE_TTL:
        return _cache[cache_key]["data"]

    csv_path = os.path.join(FRED_CACHE_DIR, f"{series_id}.csv")
    if not os.path.exists(csv_path):
        raise RuntimeError(f"FRED cache not found: {csv_path} (is fred_fetcher.sh running?)")

    with open(csv_path, "r") as f:
        csv_text = f.read()

    # Filter data to requested year range
    cutoff = (datetime.now() - timedelta(days=365 * years + 30)).strftime("%Y-%m-%d")
    data = []
    for line in csv_text.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) >= 2:
            date_str = parts[0].strip()
            val = parts[1].strip()
            if val and val != "." and date_str >= cutoff:
                try:
                    data.append({"time": date_str, "value": float(val)})
                except ValueError:
                    continue

    _cache[cache_key] = {"ts": now, "data": data}
    return data


def curl_json(url):
    """Fetch URL via curl subprocess (avoids Python SSL issues)"""
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15",
         "--retry", "3", "--retry-all-errors", "--retry-delay", "2",
         "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"curl failed (exit {r.returncode}): {r.stderr.strip()}")
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


CLAUDE_CLI = os.path.expanduser("~/.local/bin/claude")
ANALYSIS_CACHE_TTL = 1800  # 30 min


def gather_dashboard_data():
    """Collect latest values from all indicators for AI analysis."""
    summary = {}
    # CNBC real-time
    try:
        quotes = get_cnbc_batch_quotes(["US10Y", ".DXY", ".VIX", ".SPX", "@GC.1", "EUR=", "JPY="])
        for q in quotes:
            summary[q["symbol"]] = {"last": q["last"], "change": q["change"], "change_pct": q["change_pct"]}
    except Exception:
        pass
    # FRED daily
    fred_series = {
        "DFII10": "10Y TIPS Real Yield",
        "T10Y2Y": "2s10s Yield Curve Spread",
        "DFF": "Fed Funds Rate",
        "SOFR": "SOFR",
        "BAMLH0A0HYM2": "HY Credit Spread OAS",
        "UNRATE": "Unemployment Rate",
    }
    for sid, label in fred_series.items():
        try:
            data = get_fred_csv(sid, 1)
            if data:
                last = data[-1]
                summary[label] = {"value": last["value"], "date": last["time"]}
        except Exception:
            pass
    return summary


def generate_ai_analysis():
    """Call claude CLI to produce a concise macro analysis."""
    cache_key = "ai_analysis"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["ts"] < ANALYSIS_CACHE_TTL:
        return _cache[cache_key]["data"]

    data = gather_dashboard_data()
    if not data:
        return {"error": "No data available"}

    data_text = json.dumps(data, ensure_ascii=False, indent=2)

    prompt = f"""你是一位宏观经济分析师。根据以下实时金融数据，用中文写一段简洁的市场分析（200-300字）。

要求：
- 总结当前宏观环境（紧缩/宽松、风险偏好）
- 指出最值得关注的1-2个信号
- 用通俗语言，让非专业人士也能理解
- 不要用markdown格式，纯文本
- 末尾标注数据时间

当前数据：
{data_text}"""

    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        r = subprocess.run(
            [CLAUDE_CLI, "-p", "--model", "claude-opus-4-6", prompt],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if r.returncode != 0:
            return {"error": r.stderr.strip() or "claude CLI failed"}
        analysis = r.stdout.strip()
        result = {"text": analysis, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        _cache[cache_key] = {"ts": now, "data": result}
        return result
    except subprocess.TimeoutExpired:
        return {"error": "Analysis generation timed out"}
    except FileNotFoundError:
        return {"error": "claude CLI not found"}
    except Exception as e:
        return {"error": str(e)}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/fred/"):
            self.handle_fred()
        elif self.path == "/api/cnbc/quotes" or self.path.startswith("/api/cnbc/quotes?"):
            self.handle_cnbc_batch()
        elif self.path.startswith("/api/cnbc/"):
            self.handle_cnbc()
        elif self.path == "/api/analysis" or self.path.startswith("/api/analysis?"):
            self.handle_analysis()
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

    def handle_analysis(self):
        try:
            static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.json")
            if "refresh=1" in self.path and os.path.exists(CLAUDE_CLI):
                _cache.pop("ai_analysis", None)
                data = generate_ai_analysis()
                # Save locally and push to VPS
                with open(static_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                # Push to VPS in background
                subprocess.Popen(
                    ["scp", "-P", "22222", static_path, "root@152.32.235.14:/root/dashboard_source/analysis.json"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif os.path.exists(static_path):
                with open(static_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = generate_ai_analysis()
                with open(static_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "max-age=600")
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
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"\n  Financial Dashboard")
    print(f"  http://localhost:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped")
        server.server_close()
