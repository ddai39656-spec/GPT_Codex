"""Build a transparent crypto discovery and risk-gate payload.

DEX Screener is used for discovery and aggregate pool observations. Aggregate
buy/sell counts are never presented as true USD net flow. Missing safety,
sell-simulation, LP-lock, or flow evidence remains UNKNOWN and blocks the
strict executable signal by design.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUT = Path("data/crypto.json")
NOW = datetime.now(timezone.utc)
CHAINS = {"solana", "base", "ethereum"}
USER_AGENT = "GPT_Codex personal trading dashboard github.com/ddai39656-spec/GPT_Codex"
DEX_BASE = "https://api.dexscreener.com"
GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"
CHAIN_IDS = {"ethereum": "1", "base": "8453"}
LIQUIDITY_FLOOR = {"solana": 80_000, "base": 120_000, "ethereum": 150_000}


def request_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20, attempts: int = 2) -> Any:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {last_error}")


def number(value: Any, digits: int = 2) -> float | None:
    try:
        value = float(value)
        if not math.isfinite(value):
            return None
        return round(value, digits)
    except (TypeError, ValueError):
        return None


def nested(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and payload.get("tokenAddress"):
        return [payload]
    return []


def add_discovery(target: dict[str, dict[str, Any]], payload: Any, source: str) -> None:
    for item in as_items(payload):
        chain = item.get("chainId")
        address = item.get("tokenAddress")
        if chain not in CHAINS or not address:
            continue
        key = f"{chain}:{address}"
        row = target.setdefault(key, {"chain": chain, "address": address, "source_tags": []})
        if source not in row["source_tags"]:
            row["source_tags"].append(source)
        row["profile"] = item


def discover() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    endpoints = {
        "最新资料": f"{DEX_BASE}/token-profiles/latest/v1",
        "最新Boost": f"{DEX_BASE}/token-boosts/latest/v1",
        "热门Boost": f"{DEX_BASE}/token-boosts/top/v1",
        "社区接管": f"{DEX_BASE}/community-takeovers/latest/v1",
    }
    discovered: dict[str, dict[str, Any]] = {}
    statuses: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(request_json, url): name for name, url in endpoints.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                payload = future.result()
                before = len(discovered)
                add_discovery(discovered, payload, name)
                statuses.append({"name": f"DEX {name}", "status": "ok", "detail": f"新增{len(discovered) - before}个地址"})
            except Exception as exc:
                statuses.append({"name": f"DEX {name}", "status": "degraded", "detail": str(exc)[:120]})
    return discovered, statuses


def batches(values: list[str], size: int = 30) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def fetch_pairs(discovered: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    for chain in sorted(CHAINS):
        addresses = [row["address"] for row in discovered.values() if row["chain"] == chain][:90]
        for group in batches(addresses):
            encoded = urllib.parse.quote(",".join(group), safe=",")
            tasks.append((chain, group, f"{DEX_BASE}/tokens/v1/{chain}/{encoded}"))
    pairs: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(request_json, url): (chain, group) for chain, group, url in tasks}
        for future in concurrent.futures.as_completed(futures):
            try:
                payload = future.result()
                if isinstance(payload, list):
                    pairs.extend(item for item in payload if isinstance(item, dict))
            except Exception:
                continue
    return pairs


def choose_main_pairs(discovered: dict[str, dict[str, Any]], pairs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_token: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        chain = pair.get("chainId")
        for side in ("baseToken", "quoteToken"):
            address = nested(pair, side, "address")
            if chain and address:
                by_token.setdefault(f"{chain}:{address}", []).append(pair)
    selected = []
    for key, discovery in discovered.items():
        options = by_token.get(key) or []
        if not options:
            continue
        options.sort(key=lambda pair: number(nested(pair, "liquidity", "usd")) or 0, reverse=True)
        selected.append((discovery, options[0]))
    selected.sort(key=lambda item: number(nested(item[1], "liquidity", "usd")) or 0, reverse=True)
    return selected[:100]


def fetch_goplus(selected: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    results: dict[str, dict[str, Any]] = {}
    statuses: list[dict[str, Any]] = []
    tasks: list[tuple[str, list[str], str]] = []
    for chain, chain_id in CHAIN_IDS.items():
        addresses = [item[0]["address"] for item in selected if item[0]["chain"] == chain]
        for group in batches(addresses, 20):
            query = urllib.parse.urlencode({"contract_addresses": ",".join(group)})
            tasks.append((chain, group, f"{GOPLUS_BASE}/token_security/{chain_id}?{query}"))
    solana = [item[0]["address"] for item in selected if item[0]["chain"] == "solana"]
    for group in batches(solana, 20):
        query = urllib.parse.urlencode({"contract_addresses": ",".join(group)})
        tasks.append(("solana", group, f"{GOPLUS_BASE}/solana/token_security?{query}"))
    success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(request_json, url): (chain, group) for chain, group, url in tasks}
        for future in concurrent.futures.as_completed(futures):
            chain, group = futures[future]
            try:
                payload = future.result()
                data = (payload or {}).get("result") or {}
                if isinstance(data, dict):
                    for address, value in data.items():
                        if isinstance(value, dict):
                            results[f"{chain}:{address}"] = value
                            results[f"{chain}:{address.lower()}"] = value
                    success += 1
            except Exception:
                continue
    statuses.append({
        "name": "GoPlus Security", "status": "ok" if success else "degraded",
        "detail": f"{len(results)}条安全记录；未覆盖项目保持未知并禁止升级",
    })
    return results, statuses


def status(label: str, state: str, detail: str, *, critical: bool = True, evidence: str = "") -> dict[str, Any]:
    return {"label": label, "status": state, "detail": detail, "critical": critical, "evidence": evidence}


def binary_check(label: str, raw: dict[str, Any], negative_keys: tuple[str, ...], detail: str) -> dict[str, Any]:
    values = [str(raw.get(key)) for key in negative_keys if raw.get(key) is not None]
    if not values:
        return status(label, "unknown", "数据源未返回该字段", evidence="GoPlus")
    if any(value == "1" for value in values):
        return status(label, "fail", detail, evidence="GoPlus")
    if all(value == "0" for value in values):
        return status(label, "pass", "未发现该危险权限", evidence="GoPlus")
    return status(label, "unknown", "返回值无法确定", evidence="GoPlus")


def security_checks(chain: str, raw: dict[str, Any] | None, history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not raw:
        checks = [
            status("可卖出与Honeypot", "unknown", "安全数据缺失，禁止执行"),
            status("增发与冻结权限", "unknown", "安全数据缺失，禁止执行"),
            status("黑名单与余额修改", "unknown", "安全数据缺失，禁止执行"),
            status("LP锁定/烧毁", "unknown", "没有可靠锁仓证据"),
        ]
        return checks, {"coverage": "none", "provider": None}

    checks: list[dict[str, Any]] = []
    if chain in ("ethereum", "base"):
        honeypot = raw.get("is_honeypot")
        sell_tax = number(raw.get("sell_tax"), 4)
        buy_tax = number(raw.get("buy_tax"), 4)
        if str(honeypot) == "1":
            checks.append(status("可卖出与Honeypot", "fail", "安全接口标记为Honeypot", evidence="GoPlus"))
        elif str(honeypot) == "0" and (sell_tax is None or sell_tax <= 0.1):
            checks.append(status("可卖出与Honeypot", "unknown", "接口未发现Honeypot，但缺少独立卖出模拟", evidence="GoPlus单源"))
        else:
            checks.append(status("可卖出与Honeypot", "unknown", "无法完成双源卖出确认", evidence="GoPlus"))
        if sell_tax is not None and sell_tax > 0.1:
            checks.append(status("异常税率", "fail", f"卖出税约{sell_tax * 100:.1f}%", evidence="GoPlus"))
        elif buy_tax is not None or sell_tax is not None:
            checks.append(status("异常税率", "pass", f"买/卖税 {number((buy_tax or 0) * 100, 2)}% / {number((sell_tax or 0) * 100, 2)}%", evidence="GoPlus"))
        else:
            checks.append(status("异常税率", "unknown", "税率字段缺失", evidence="GoPlus"))
        checks.append(binary_check("任意增发", raw, ("is_mintable",), "合约可能任意增发"))
        checks.append(binary_check("黑名单/冻结", raw, ("is_blacklisted", "transfer_pausable"), "存在黑名单或暂停转账权限"))
        checks.append(binary_check("余额修改/隐藏权限", raw, ("owner_change_balance", "hidden_owner", "can_take_back_ownership"), "存在余额或隐藏所有者风险"))
    else:
        checks.append(status("可卖出与Honeypot", "unknown", "尚未完成独立Solana卖出模拟", evidence="GoPlus单源"))
        mint_flag = raw.get("is_mintable") if raw.get("is_mintable") is not None else raw.get("mintable")
        freeze_flag = raw.get("is_freezable") if raw.get("is_freezable") is not None else raw.get("freezable")
        if mint_flag is None and freeze_flag is None:
            checks.append(status("Mint/Freeze Authority", "unknown", "权限字段缺失，禁止执行", evidence="GoPlus"))
        elif str(mint_flag) == "1" or str(freeze_flag) == "1":
            checks.append(status("Mint/Freeze Authority", "fail", "仍存在增发或冻结权限", evidence="GoPlus"))
        elif str(mint_flag) == "0" and str(freeze_flag) == "0":
            checks.append(status("Mint/Freeze Authority", "pass", "接口未发现增发或冻结权限", evidence="GoPlus"))
        else:
            checks.append(status("Mint/Freeze Authority", "unknown", "权限状态不完整", evidence="GoPlus"))

    locked = 0.0
    for holder in raw.get("lp_holders") or []:
        if str(holder.get("is_locked")) == "1":
            locked += number(holder.get("percent"), 6) or 0
    if locked >= 0.8:
        checks.append(status("LP锁定/烧毁", "pass", f"安全接口显示约{locked * 100:.1f}% LP锁定", evidence="GoPlus"))
    elif locked > 0:
        checks.append(status("LP锁定/烧毁", "fail", f"仅约{locked * 100:.1f}% LP被标记锁定", evidence="GoPlus"))
    else:
        checks.append(status("LP锁定/烧毁", "unknown", "未获得充分锁定/烧毁证据", evidence="GoPlus"))

    if len(history) >= 2:
        old_liq = number(history[0].get("liquidity_usd")) or 0
        new_liq = number(history[-1].get("liquidity_usd")) or 0
        delta = number((new_liq / old_liq - 1) * 100, 2) if old_liq else None
        if delta is not None and delta <= -12:
            checks.append(status("LP短期稳定", "fail", f"观察期流动性下降{abs(delta):.1f}%", evidence="本站历史快照"))
        else:
            checks.append(status("LP短期稳定", "pass", f"观察期变化{delta if delta is not None else 0:+.1f}%", evidence="本站历史快照"))
    else:
        checks.append(status("LP短期稳定", "unknown", "历史快照不足", evidence="本站历史快照"))
    return checks, {"coverage": "partial", "provider": "GoPlus", "raw_fields": sorted(raw.keys())[:40]}


def previous_history() -> dict[str, list[dict[str, Any]]]:
    if not OUT.exists():
        return {}
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        history = payload.get("history") or {}
        return {str(key): value for key, value in history.items() if isinstance(value, list)}
    except Exception:
        return {}


def build_candidate(discovery: dict[str, Any], pair: dict[str, Any], security_raw: dict[str, Any] | None, histories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    chain = discovery["chain"]
    address = discovery["address"]
    key = f"{chain}:{address}"
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    token_is_base = str(base.get("address", "")).lower() == address.lower()
    token = base if token_is_base else quote
    price = number(pair.get("priceUsd"), 12)
    liquidity = number(nested(pair, "liquidity", "usd"), 2) or 0
    fdv = number(pair.get("fdv"), 2)
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    created = number(pair.get("pairCreatedAt"), 0)
    age_minutes = number((NOW.timestamp() * 1000 - created) / 60_000, 1) if created else None
    snapshot = {
        "time": NOW.isoformat(), "price": price, "liquidity_usd": liquidity,
        "volume_m5": number(volume.get("m5"), 2) or 0,
        "volume_h1": number(volume.get("h1"), 2) or 0,
        "buys_m5": int(nested(txns, "m5", "buys", default=0) or 0),
        "sells_m5": int(nested(txns, "m5", "sells", default=0) or 0),
        "buys_h1": int(nested(txns, "h1", "buys", default=0) or 0),
        "sells_h1": int(nested(txns, "h1", "sells", default=0) or 0),
    }
    history = (histories.get(key) or [])[-23:]
    if not history or history[-1].get("time") != snapshot["time"]:
        history.append(snapshot)

    floor = LIQUIDITY_FLOOR[chain]
    identity = status("CA与真实主池", "pass" if address and pair.get("pairAddress") else "fail", "DEX返回完整CA和池地址", evidence="DEX Screener")
    liquid = status("执行流动性", "pass" if liquidity >= floor else "fail", f"${liquidity:,.0f} / 门槛${floor:,.0f}", critical=False, evidence="DEX Screener")
    security, security_meta = security_checks(chain, security_raw, history)

    buys_m5, sells_m5 = snapshot["buys_m5"], snapshot["sells_m5"]
    buys_h1, sells_h1 = snapshot["buys_h1"], snapshot["sells_h1"]
    m5_change = number(changes.get("m5"), 2) or 0
    h1_change = number(changes.get("h1"), 2) or 0
    volume_m5 = snapshot["volume_m5"]
    volume_h1 = snapshot["volume_h1"]
    acceleration = bool(volume_h1 > 0 and volume_m5 >= volume_h1 / 12 * 1.25 and volume_m5 >= 2_000)
    structure_ok = m5_change >= 0 and h1_change >= 0 and buys_m5 > sells_m5 and buys_h1 >= sells_h1
    structure_fail = m5_change <= -5 or h1_change <= -12 or sells_m5 > buys_m5 * 1.5
    flow_checks = [
        status("5m真实美元净买", "unknown", "DEX仅提供聚合笔数与成交量，不能冒充美元净买", evidence="数据能力限制"),
        status("15m真实美元净买", "unknown", "需要逐笔Swap数据或专业数据源", evidence="数据能力限制"),
        status("1h资金结构改善", "unknown", f"聚合买/卖笔数 {buys_h1}/{sells_h1}，仅供观察", evidence="DEX聚合数据"),
        status("成交重新加速", "pass" if acceleration else "fail", f"5m ${volume_m5:,.0f}；1h ${volume_h1:,.0f}", critical=False, evidence="DEX Screener"),
        status("价格结构确认", "fail" if structure_fail else ("pass" if structure_ok else "unknown"), f"5m {m5_change:+.2f}%；1h {h1_change:+.2f}%", evidence="DEX聚合数据"),
        status("合理2×空间", "unknown", "缺少筹码成本、历史压力和可比估值证据", evidence="待补充"),
    ]
    checks = [identity, liquid] + security + flow_checks
    critical = [item for item in checks if item.get("critical")]
    actionable = bool(critical and all(item["status"] == "pass" for item in critical))
    blockers = [item["label"] for item in critical if item["status"] != "pass"]
    explicit_risk = any(item["status"] == "fail" and item.get("critical") for item in checks)
    observed_passes = sum(item["status"] == "pass" for item in checks)
    observed_score = (
        min(liquidity / max(floor, 1), 3) * 12
        + max(min(h1_change, 30), -30) * 0.7
        + (12 if buys_m5 > sells_m5 else -8)
        + (10 if acceleration else 0)
        + len(discovery.get("source_tags") or []) * 2
        - (25 if explicit_risk else 0)
    )
    if explicit_risk:
        decision, tone = "高风险否决", "negative"
    elif actionable:
        decision, tone = "必须买｜可立即执行", "positive"
    elif liquidity >= floor and structure_ok and acceleration:
        decision, tone = "接近触发｜保持静默", "warning"
    else:
        decision, tone = "观察｜保持静默", "neutral"
    setup = "新池/早期" if age_minutes is not None and age_minutes <= 180 else ("资金异动" if acceleration else "趋势观察")
    return {
        "id": key, "chain": chain, "ca": address, "pool": pair.get("pairAddress"),
        "dex": pair.get("dexId"), "url": pair.get("url"), "name": token.get("name") or "Unknown",
        "symbol": token.get("symbol") or "?", "quote_symbol": quote.get("symbol") if token_is_base else base.get("symbol"),
        "price_usd": price, "liquidity_usd": liquidity, "fdv": fdv,
        "pair_created_at": datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
        "age_minutes": age_minutes, "price_change": {key: number(changes.get(key), 2) for key in ("m5", "h1", "h6", "h24")},
        "volume": {key: number(volume.get(key), 2) or 0 for key in ("m5", "h1", "h6", "h24")},
        "txns": {key: {"buys": int(nested(txns, key, "buys", default=0) or 0), "sells": int(nested(txns, key, "sells", default=0) or 0)} for key in ("m5", "h1", "h6", "h24")},
        "source_tags": discovery.get("source_tags") or [], "setup": setup,
        "decision": decision, "tone": tone, "actionable": actionable,
        "score": number(observed_score, 2), "checks": checks,
        "passed_checks": observed_passes, "total_checks": len(checks), "blockers": blockers,
        "security": security_meta,
        "execution": {
            "entry": None, "position_usdt": None, "stop": None, "targets": [],
            "reason": "硬条件未全部通过，禁止生成伪精确执行价格" if not actionable else "全部硬条件通过",
        },
        "history": history,
    }


def crypto_events(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for item in candidates:
        h1 = nested(item, "price_change", "h1", default=0) or 0
        volume_h1 = nested(item, "volume", "h1", default=0) or 0
        buys = nested(item, "txns", "h1", "buys", default=0) or 0
        sells = nested(item, "txns", "h1", "sells", default=0) or 0
        if abs(h1) < 8 and volume_h1 < 250_000 and item["decision"] not in ("接近触发｜保持静默", "高风险否决"):
            continue
        bias = "偏利多" if h1 > 3 and buys > sells else ("偏利空" if h1 < -3 and sells > buys else "中性待确认")
        events.append({
            "market": "加密币", "symbol": item["symbol"], "chain": item["chain"],
            "ca": item["ca"], "pool": item["pool"],
            "title": f"{item['symbol']} 资金结构观察：1h {h1:+.1f}%，成交 ${volume_h1:,.0f}",
            "bias": bias, "impact": "高" if abs(h1) >= 20 or volume_h1 >= 750_000 else "中",
            "horizon": "分钟–数小时", "event_type": "链上资金异动", "time": NOW.isoformat(),
            "source": "DEX Screener aggregate", "credibility": "中", "novelty": "实时观察",
            "priced_in": "需结合回踩与净买确认", "url": item.get("url"),
            "reason": f"1h聚合买/卖笔数 {buys}/{sells}；这不是实际美元净买，也不会直接升级为买入信号。",
        })
        if len(events) >= 24:
            break
    return events


def main() -> None:
    histories = previous_history()
    discovered, source_status = discover()
    pairs = fetch_pairs(discovered)
    selected = choose_main_pairs(discovered, pairs)
    security_map, security_status = fetch_goplus(selected)
    candidates = []
    current_history: dict[str, list[dict[str, Any]]] = {}
    for discovery, pair in selected:
        key = f"{discovery['chain']}:{discovery['address']}"
        raw = security_map.get(key) or security_map.get(f"{discovery['chain']}:{discovery['address'].lower()}")
        candidate = build_candidate(discovery, pair, raw, histories)
        candidates.append(candidate)
        current_history[key] = candidate["history"]
    candidates.sort(key=lambda item: item.get("score") or -999, reverse=True)
    actionable = sum(item["actionable"] for item in candidates)
    near = sum(item["decision"] == "接近触发｜保持静默" for item in candidates)
    denied = sum(item["decision"] == "高风险否决" for item in candidates)
    payload = {
        "schema_version": 2, "updated_at": NOW.isoformat(),
        "freshness": {"expected_minutes": 20, "status": "fresh", "note": "DEX前端可刷新价格；安全和历史快照按后台周期更新"},
        "sources": source_status + security_status,
        "summary": {"discovered": len(discovered), "pools": len(candidates), "actionable": actionable, "near_trigger": near, "risk_denied": denied},
        "policy": {
            "mode": "strict_and", "notify_only_actionable": True,
            "statement": "任何关键字段未知或失败，都不会产生【必须买｜可立即执行】。",
            "aggregate_flow_warning": "DEX聚合买卖笔数不是实际美元净买。",
        },
        "candidates": candidates, "events": crypto_events(candidates), "history": current_history,
        "disclaimer": "高风险资产研究工具；不保证盈利，未知安全字段等同于禁止执行。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"crypto: {len(discovered)} discovered, {len(candidates)} pools, {actionable} actionable")


if __name__ == "__main__":
    main()

