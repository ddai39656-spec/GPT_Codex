"""Generate the US-market and event payload used by the static trading terminal.

The generator intentionally separates observable facts from trading conclusions.
It uses only Python's standard library so GitHub Actions does not depend on an
unofficial package release. Yahoo endpoints are treated as a best-effort market
feed; SEC EDGAR is the authoritative source for filings.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


OUT = Path("data/market.json")
NOW = datetime.now(timezone.utc)
USER_AGENT = "GPT_Codex personal trading dashboard github.com/ddai39656-spec/GPT_Codex"

STOCKS: dict[str, tuple[str, str]] = {
    "NVDA": ("NVIDIA", "半导体"), "TSLA": ("Tesla", "汽车"),
    "AAPL": ("Apple", "大型科技"), "MSFT": ("Microsoft", "大型科技"),
    "AMZN": ("Amazon", "互联网"), "META": ("Meta", "互联网"),
    "GOOGL": ("Alphabet", "互联网"), "AMD": ("AMD", "半导体"),
    "AVGO": ("Broadcom", "半导体"), "PLTR": ("Palantir", "软件"),
    "COIN": ("Coinbase", "加密金融"), "MSTR": ("Strategy", "加密金融"),
    "HOOD": ("Robinhood", "金融科技"), "SMCI": ("Super Micro Computer", "AI基础设施"),
    "CRWD": ("CrowdStrike", "网络安全"), "NFLX": ("Netflix", "互联网"),
    "MARA": ("MARA Holdings", "加密矿业"), "RIOT": ("Riot Platforms", "加密矿业"),
    "IREN": ("IREN", "AI与矿业"), "SOFI": ("SoFi", "金融科技"),
    "ARM": ("Arm Holdings", "半导体"), "MU": ("Micron", "半导体"),
    "MRVL": ("Marvell", "半导体"), "TSM": ("TSMC", "半导体"),
    "ASML": ("ASML", "半导体"), "QCOM": ("Qualcomm", "半导体"),
    "ORCL": ("Oracle", "软件"), "CRM": ("Salesforce", "软件"),
    "NOW": ("ServiceNow", "软件"), "PANW": ("Palo Alto Networks", "网络安全"),
    "NET": ("Cloudflare", "网络安全"), "DDOG": ("Datadog", "软件"),
    "SNOW": ("Snowflake", "软件"), "SHOP": ("Shopify", "互联网"),
    "UBER": ("Uber", "互联网"), "ABNB": ("Airbnb", "互联网"),
    "JPM": ("JPMorgan", "金融"), "GS": ("Goldman Sachs", "金融"),
    "V": ("Visa", "金融"), "MA": ("Mastercard", "金融"),
    "LLY": ("Eli Lilly", "医疗"), "UNH": ("UnitedHealth", "医疗"),
    "XOM": ("Exxon Mobil", "能源"), "CVX": ("Chevron", "能源"),
    "BA": ("Boeing", "工业"), "CAT": ("Caterpillar", "工业"),
    "RBLX": ("Roblox", "互联网"), "RKLB": ("Rocket Lab", "航天"),
}

BENCHMARKS = ["SPY", "QQQ", "IWM", "^VIX", "^TNX"]

POSITIVE = {
    "beat", "beats", "raises", "raised", "upgrade", "upgraded", "approval",
    "approved", "buyback", "record", "wins", "contract", "partnership",
    "outperform", "profit", "surge", "soar", "launch", "growth",
}
NEGATIVE = {
    "miss", "misses", "cuts", "cut", "downgrade", "downgraded", "probe",
    "investigation", "lawsuit", "recall", "hack", "breach", "offering",
    "dilution", "warning", "fraud", "layoff", "default", "subpoena",
}

EVENT_PATTERNS = [
    ("财报/指引", ("earnings", "guidance", "revenue", "eps"), "高", "1–5个交易日"),
    ("监管/调查", ("sec ", "doj", "probe", "investigation", "subpoena"), "高", "1–10个交易日"),
    ("融资/稀释", ("offering", "dilution", "s-3", "424b5"), "高", "1–5个交易日"),
    ("并购/合作", ("acquisition", "merger", "partnership", "contract"), "高", "1–10个交易日"),
    ("产品/审批", ("launch", "approval", "approved", "fda"), "高", "1–10个交易日"),
    ("评级变化", ("upgrade", "downgrade", "price target", "outperform"), "中", "1–3个交易日"),
    ("网络安全", ("hack", "breach", "cyberattack", "outage"), "高", "分钟–5个交易日"),
]


def request_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20, attempts: int = 2) -> Any:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {last_error}")


def number(value: Any, digits: int = 2) -> float | None:
    try:
        value = float(value)
        if not math.isfinite(value):
            return None
        return round(value, digits)
    except (TypeError, ValueError):
        return None


def change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return number((new / old - 1) * 100, 2)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
    gains = mean([max(x, 0) for x in deltas]) or 0
    losses = mean([max(-x, 0) for x in deltas]) or 0
    if losses == 0:
        return 100.0
    return number(100 - 100 / (1 + gains / losses), 1)


def atr14(highs: list[float], lows: list[float], closes: list[float]) -> float | None:
    if min(len(highs), len(lows), len(closes)) < 15:
        return None
    values: list[float] = []
    start = len(closes) - 14
    for idx in range(start, len(closes)):
        values.append(max(highs[idx] - lows[idx], abs(highs[idx] - closes[idx - 1]), abs(lows[idx] - closes[idx - 1])))
    return number(mean(values), 4)


def yahoo_chart(symbol: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=3mo&interval=1d&includePrePost=true&events=div%2Csplits"
    payload = request_json(url)
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"no chart data for {symbol}")
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    timestamps = result.get("timestamp") or []
    rows = []
    for idx, ts in enumerate(timestamps):
        try:
            row = {
                "time": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "open": float(quote["open"][idx]), "high": float(quote["high"][idx]),
                "low": float(quote["low"][idx]), "close": float(quote["close"][idx]),
                "volume": float(quote["volume"][idx] or 0),
            }
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if all(math.isfinite(row[key]) for key in ("open", "high", "low", "close")):
            rows.append(row)
    if len(rows) < 6:
        raise RuntimeError(f"insufficient chart data for {symbol}")
    return {"symbol": symbol, "rows": rows, "meta": result.get("meta") or {}}


def compact_history(rows: list[dict[str, Any]], limit: int = 45) -> list[dict[str, Any]]:
    return [{"time": x["time"], "close": number(x["close"], 4), "volume": number(x["volume"], 0)} for x in rows[-limit:]]


def market_regime(charts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = []
    score = 0
    for symbol in BENCHMARKS:
        chart = charts.get(symbol)
        if not chart:
            continue
        closes = [x["close"] for x in chart["rows"]]
        price = closes[-1]
        ma20 = mean(closes[-20:])
        d5 = change(price, closes[-6])
        above = bool(ma20 and price > ma20)
        items.append({"ticker": symbol, "price": number(price, 4), "d5": d5, "ma20": number(ma20, 4), "above_ma20": above})
        if symbol in ("SPY", "QQQ", "IWM"):
            score += 1 if above else -1
        elif symbol == "^VIX":
            if price >= 25 or (d5 or 0) >= 12:
                score -= 2
            elif price <= 18:
                score += 1
    if score >= 3:
        label, tone, note = "风险偏好", "positive", "主要指数趋势向上，允许筛选顺势机会。"
    elif score <= -2:
        label, tone, note = "风险规避", "negative", "市场环境不支持追涨，所有进场条件自动收紧。"
    else:
        label, tone, note = "混合震荡", "neutral", "指数信号分化，仅考虑高质量个股结构。"
    return {"label": label, "tone": tone, "score": score, "note": note, "benchmarks": items}


def check(label: str, passed: bool, detail: str, critical: bool = False) -> dict[str, Any]:
    return {"label": label, "status": "pass" if passed else "fail", "detail": detail, "critical": critical}


def stock_record(symbol: str, chart: dict[str, Any], spy_chart: dict[str, Any] | None, regime: dict[str, Any]) -> dict[str, Any]:
    rows = chart["rows"]
    closes = [x["close"] for x in rows]
    highs = [x["high"] for x in rows]
    lows = [x["low"] for x in rows]
    opens = [x["open"] for x in rows]
    volumes = [x["volume"] for x in rows]
    price = closes[-1]
    ma20 = mean(closes[-20:])
    ma50 = mean(closes[-50:]) if len(closes) >= 50 else mean(closes)
    d1 = change(price, closes[-2])
    d5 = change(price, closes[-6])
    d20 = change(price, closes[-21]) if len(closes) >= 21 else None
    gap = change(opens[-1], closes[-2])
    atr = atr14(highs, lows, closes)
    atr_pct = number(atr / price * 100, 2) if atr and price else None
    volume_base = mean(volumes[-21:-1]) if len(volumes) >= 21 else mean(volumes[:-1])
    volume_ratio = number(volumes[-1] / volume_base, 2) if volume_base else None
    rs5 = None
    rs20 = None
    if spy_chart:
        spy_closes = [x["close"] for x in spy_chart["rows"]]
        rs5 = number((d5 or 0) - (change(spy_closes[-1], spy_closes[-6]) or 0), 2)
        if len(spy_closes) >= 21 and d20 is not None:
            rs20 = number(d20 - (change(spy_closes[-1], spy_closes[-21]) or 0), 2)
    rsi = rsi14(closes)
    extension = number((price / ma20 - 1) * 100, 2) if ma20 else None
    trend = bool(ma20 and ma50 and price > ma20 > ma50)
    not_overheated = bool(rsi is not None and 48 <= rsi <= 74 and extension is not None and extension <= 9)
    relative_strength = bool(rs5 is not None and rs5 > 0)
    participation = bool(volume_ratio is not None and volume_ratio >= 1.1)
    positive_day = bool(d1 is not None and d1 > 0)
    market_ok = regime["label"] != "风险规避"
    checks = [
        check("多头趋势", trend, f"价格 {'>' if price > (ma20 or price) else '≤'} MA20，MA20 {'>' if (ma20 or 0) > (ma50 or 0) else '≤'} MA50"),
        check("相对强度", relative_strength, f"5日相对SPY {rs5 if rs5 is not None else '-'}%"),
        check("成交参与", participation, f"量比 {volume_ratio if volume_ratio is not None else '-'}"),
        check("未过度延伸", not_overheated, f"RSI {rsi if rsi is not None else '-'}，偏离MA20 {extension if extension is not None else '-'}%"),
        check("当日确认", positive_day, f"1日 {d1 if d1 is not None else '-'}%"),
        check("市场环境", market_ok, regime["label"], critical=True),
    ]
    passed = sum(x["status"] == "pass" for x in checks)
    trade_ready = all(x["status"] == "pass" for x in checks)
    if price < (ma20 or price) and (d5 or 0) < 0:
        setup = "弱势回避"
    elif (d1 or 0) >= 3 and (volume_ratio or 0) >= 1.5:
        setup = "放量异动"
    elif trend and relative_strength and not_overheated:
        setup = "趋势延续候选"
    elif ma20 and price >= ma20 and extension is not None and extension <= 3 and (d5 or 0) > 0:
        setup = "多头回踩候选"
    else:
        setup = "等待结构"
    risk = atr * 1.5 if atr else price * 0.04
    entry = number(max(price, highs[-1]), 4)
    stop = number(entry - risk, 4) if entry else None
    target1 = number(entry + risk * 2, 4) if entry else None
    target2 = number(entry + risk * 3, 4) if entry else None
    directional_score = (
        (d1 or 0) * 0.8 + (d5 or 0) * 0.45 + (rs5 or 0) * 0.7
        + min(volume_ratio or 0, 3) * 1.5 + (2 if trend else -1)
        - max((extension or 0) - 8, 0) * 0.6
    )
    name, sector = STOCKS[symbol]
    return {
        "ticker": symbol, "name": name, "sector": sector,
        "price": number(price, 4), "d1": d1, "d5": d5, "d20": d20,
        "gap": gap, "volume": number(volumes[-1], 0), "volume_ratio": volume_ratio,
        "rsi14": rsi, "ma20": number(ma20, 4), "ma50": number(ma50, 4),
        "atr14": atr, "atr_pct": atr_pct, "extension_ma20": extension,
        "rs5_spy": rs5, "rs20_spy": rs20, "setup": setup,
        "decision": "强势候选" if trade_ready else ("回避" if setup == "弱势回避" else "等待触发"),
        "checks": checks, "passed_checks": passed, "total_checks": len(checks),
        "trade_ready": trade_ready, "score": number(directional_score, 2),
        "plan": {"trigger": entry, "stop": stop, "target1": target1, "target2": target2, "rr_target1": 2.0, "note": "需盘中价格与成交确认后执行"},
        "history": compact_history(rows),
    }


def classify_headline(title: str) -> dict[str, str]:
    lowered = title.lower()
    pos_hits = sorted(word for word in POSITIVE if word in lowered)
    neg_hits = sorted(word for word in NEGATIVE if word in lowered)
    if len(pos_hits) > len(neg_hits):
        bias = "偏利多"
    elif len(neg_hits) > len(pos_hits):
        bias = "偏利空"
    else:
        bias = "中性待确认"
    event_type, impact, horizon = "一般新闻", "中", "1–3个交易日"
    for candidate, words, candidate_impact, candidate_horizon in EVENT_PATTERNS:
        if any(word in lowered for word in words):
            event_type, impact, horizon = candidate, candidate_impact, candidate_horizon
            break
    hits = pos_hits if bias == "偏利多" else neg_hits
    reason = f"标题包含方向词：{', '.join(hits[:4])}" if hits else "标题方向不明确，必须结合正文和价格反应"
    return {"bias": bias, "impact": impact, "horizon": horizon, "event_type": event_type, "reason": reason}


def source_credibility(source: str) -> str:
    value = (source or "").lower()
    if any(name in value for name in ("sec", "reuters", "associated press", "fda", "business wire", "globenewswire")):
        return "高"
    if any(name in value for name in ("bloomberg", "cnbc", "marketwatch", "yahoo", "investor's business daily")):
        return "中高"
    return "中"


def yahoo_news(symbol: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"q": symbol, "quotesCount": 0, "newsCount": 6})
    payload = request_json(f"https://query1.finance.yahoo.com/v1/finance/search?{query}")
    events = []
    for item in (payload or {}).get("news") or []:
        title = item.get("title")
        if not title:
            continue
        classified = classify_headline(title)
        timestamp = item.get("providerPublishTime")
        published = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if isinstance(timestamp, (int, float)) else None
        source = item.get("publisher") or "Yahoo Finance"
        events.append({
            "market": "美股", "symbol": symbol, "title": title,
            **classified, "time": published, "source": source,
            "credibility": source_credibility(source), "novelty": "新近",
            "priced_in": "待价格验证", "url": item.get("link"),
            "analysis_limit": "基于标题分类，打开原文后再确认",
        })
    return events


def sec_events(symbols: list[str]) -> tuple[list[dict[str, Any]], bool]:
    try:
        mapping_payload = request_json("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": USER_AGENT})
    except Exception:
        return [], False
    ticker_to_cik = {
        str(item.get("ticker", "")).upper(): int(item["cik_str"])
        for item in (mapping_payload or {}).values() if item.get("ticker") and item.get("cik_str")
    }
    important = {"8-K", "10-Q", "10-K", "S-3", "S-3ASR", "424B5", "SC 13D", "SC 13G", "4"}
    events: list[dict[str, Any]] = []
    cutoff = (NOW - timedelta(days=10)).date()
    for symbol in symbols[:12]:
        cik = ticker_to_cik.get(symbol)
        if not cik:
            continue
        try:
            payload = request_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers={"User-Agent": USER_AGENT})
        except Exception:
            continue
        recent = (((payload or {}).get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        for idx, form in enumerate(forms[:30]):
            if form not in important:
                continue
            filed = (recent.get("filingDate") or [None] * len(forms))[idx]
            try:
                filed_date = datetime.fromisoformat(filed).date()
            except (TypeError, ValueError):
                continue
            if filed_date < cutoff:
                continue
            accession = (recent.get("accessionNumber") or [None] * len(forms))[idx]
            document = (recent.get("primaryDocument") or [None] * len(forms))[idx]
            if not accession or not document:
                continue
            compact_accession = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact_accession}/{document}"
            negative = form in {"S-3", "S-3ASR", "424B5"}
            label = "潜在融资/稀释文件" if negative else "公司提交重要监管文件"
            events.append({
                "market": "美股", "symbol": symbol, "title": f"{symbol} 提交 {form}：{label}",
                "bias": "偏利空" if negative else "中性待确认", "impact": "高",
                "horizon": "1–10个交易日", "event_type": "SEC公告", "time": f"{filed}T00:00:00Z",
                "source": "SEC EDGAR", "credibility": "高", "novelty": "新近",
                "priced_in": "待价格验证", "url": url,
                "reason": "来自SEC原始申报；需阅读文件正文判断具体影响",
                "analysis_limit": "原始监管文件，方向为规则初筛",
            })
        time.sleep(0.12)
    return events, True


def dedupe_events(events: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for event in sorted(events, key=lambda x: x.get("time") or "", reverse=True):
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (event.get("title") or "").lower())[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(event)
        if len(output) >= limit:
            break
    return output


def main() -> None:
    symbols = list(STOCKS) + BENCHMARKS
    charts: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(yahoo_chart, symbol): symbol for symbol in symbols}
        for future in concurrent.futures.as_completed(future_map):
            symbol = future_map[future]
            try:
                charts[symbol] = future.result()
            except Exception:
                failures.append(symbol)
    regime = market_regime(charts)
    rows = []
    for symbol in STOCKS:
        chart = charts.get(symbol)
        if chart:
            rows.append(stock_record(symbol, chart, charts.get("SPY"), regime))
    rows.sort(key=lambda row: row.get("score") or -999, reverse=True)

    focus = []
    for row in rows[:16] + [row for row in rows if row["ticker"] in {"NVDA", "TSLA", "COIN", "MSTR", "AAPL", "MSFT"}]:
        if row["ticker"] not in focus:
            focus.append(row["ticker"])
    news_events: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(yahoo_news, symbol): symbol for symbol in focus[:18]}
        for future in concurrent.futures.as_completed(future_map):
            try:
                news_events.extend(future.result())
            except Exception:
                continue
    filing_events, sec_ok = sec_events(focus)
    events = dedupe_events(filing_events + news_events)

    strong = sum(row["decision"] == "强势候选" for row in rows)
    weak = sum(row["decision"] == "回避" for row in rows)
    high_events = sum(event.get("impact") == "高" for event in events)
    source_status = [
        {"name": "Yahoo Market Data", "status": "ok" if rows else "error", "detail": f"{len(rows)}/{len(STOCKS)}只股票"},
        {"name": "Yahoo News", "status": "ok" if news_events else "degraded", "detail": f"{len(news_events)}条标题"},
        {"name": "SEC EDGAR", "status": "ok" if sec_ok else "degraded", "detail": f"{len(filing_events)}条原始申报"},
    ]
    payload = {
        "schema_version": 2, "updated_at": NOW.isoformat(),
        "freshness": {"expected_minutes": 20, "status": "fresh", "note": "超过30分钟将禁止显示为实时结论"},
        "sources": source_status, "regime": regime,
        "summary": {"stocks": len(rows), "strong": strong, "weak": weak, "events": len(events), "high_impact_events": high_events, "failed_symbols": failures},
        "us": rows, "events": events,
        "disclaimer": "仅供研究与决策辅助，不构成收益保证；数据缺失、延迟或冲突时不得执行交易。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"market: {len(rows)} stocks, {len(events)} events, {len(failures)} failures")


if __name__ == "__main__":
    main()

