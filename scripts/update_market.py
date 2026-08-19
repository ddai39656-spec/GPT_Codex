import json, math, os, re
from datetime import datetime, timezone
import requests
import pandas as pd
import yfinance as yf

OUT = "data/market.json"
SYMBOLS = [
    "NVDA","TSLA","AAPL","MSFT","AMZN","META","GOOGL","AMD","AVGO","PLTR",
    "COIN","MSTR","HOOD","SMCI","CRWD","NFLX","MARA","RIOT","IREN","SOFI"
]
NAMES = {
    "NVDA":"NVIDIA","TSLA":"Tesla","AAPL":"Apple","MSFT":"Microsoft","AMZN":"Amazon",
    "META":"Meta","GOOGL":"Alphabet","AMD":"AMD","AVGO":"Broadcom","PLTR":"Palantir",
    "COIN":"Coinbase","MSTR":"Strategy","HOOD":"Robinhood","SMCI":"Super Micro Computer",
    "CRWD":"CrowdStrike","NFLX":"Netflix","MARA":"MARA Holdings","RIOT":"Riot Platforms",
    "IREN":"IREN","SOFI":"SoFi"
}
POS = ["beat","beats","surge","soar","upgrade","raises","record","approval","approved","buyback","partnership","contract","wins","strong","growth","launch","outperform","profit"]
NEG = ["miss","misses","cut","cuts","downgrade","probe","investigation","lawsuit","recall","hack","breach","offering","dilution","warning","weak","decline","fall","layoff","fraud"]
HIGH = ["earnings","guidance","sec ","doj","fda","offering","acquisition","merger","buyback","lawsuit","investigation","hack","breach","approval"]

def fnum(x, nd=2):
    try:
        x=float(x)
        if math.isnan(x) or math.isinf(x): return None
        return round(x, nd)
    except Exception:
        return None

def rsi14(close):
    if len(close) < 15: return None
    d = close.diff().dropna()
    gains = d.clip(lower=0).tail(14).mean()
    losses = -d.clip(upper=0).tail(14).mean()
    if losses == 0: return 100.0
    rs = gains / losses
    return fnum(100 - 100/(1+rs), 1)

def pct(a,b):
    if not b: return None
    return fnum((a/b-1)*100,2)

def signal(price, ma20, rsi, d1, d5, vr):
    if price is None or ma20 is None: return "数据不足"
    if rsi is not None and rsi >= 75: return "过热，谨防回撤"
    if price > ma20 and (d5 or 0) > 3 and (vr or 0) >= 1.2: return "放量强势"
    if price > ma20 and (d1 or 0) >= 0: return "多头结构"
    if price < ma20 and (d1 or 0) < 0: return "弱势/回避"
    return "震荡观察"

def extract_series(frame, symbol, col):
    try:
        if isinstance(frame.columns, pd.MultiIndex):
            # group_by='ticker' => first level ticker
            if symbol in frame.columns.get_level_values(0):
                return frame[symbol][col].dropna()
            if col in frame.columns.get_level_values(0):
                return frame[col][symbol].dropna()
        return frame[col].dropna()
    except Exception:
        return pd.Series(dtype=float)

def stock_rows():
    rows=[]
    data = yf.download(SYMBOLS, period="1mo", interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True, timeout=20)
    for s in SYMBOLS:
        c = extract_series(data,s,"Close")
        v = extract_series(data,s,"Volume")
        if len(c) < 2: continue
        price=fnum(c.iloc[-1],4)
        d1=pct(c.iloc[-1], c.iloc[-2])
        d5=pct(c.iloc[-1], c.iloc[-6] if len(c)>=6 else c.iloc[0])
        ma20=fnum(c.tail(20).mean(),4) if len(c)>=5 else None
        vr=None
        if len(v)>=2:
            base=v.iloc[:-1].tail(20).mean()
            vr=fnum(v.iloc[-1]/base,2) if base and not math.isnan(base) else None
        rsi=rsi14(c)
        score=(abs(d1 or 0)*1.2)+(abs(d5 or 0)*0.25)+min(vr or 0,5)*1.5
        rows.append({
            "ticker":s,"name":NAMES.get(s,s),"price":price,"d1":d1,"d5":d5,
            "volume_ratio":vr,"rsi14":rsi,"ma20":ma20,
            "above_ma20": bool(price and ma20 and price>ma20),
            "signal":signal(price,ma20,rsi,d1,d5,vr),"score":fnum(score,2)
        })
    rows.sort(key=lambda x:x.get("score") or 0, reverse=True)
    return rows

def classify(title):
    t=(title or "").lower()
    p=sum(k in t for k in POS); n=sum(k in t for k in NEG)
    bias="偏利多" if p>n else "偏利空" if n>p else "中性待确认"
    impact="高" if any(k in t for k in HIGH) else "中"
    horizon="1–5个交易日" if impact=="高" else "1–3个交易日"
    why="标题包含明确的正向催化词" if bias=="偏利多" else "标题包含明确的风险/负向催化词" if bias=="偏利空" else "方向需要结合正文、价格与成交量确认"
    return bias,impact,horizon,why

def parse_news_item(item):
    c=item.get("content",{}) if isinstance(item,dict) else {}
    title = c.get("title") or item.get("title") if isinstance(item,dict) else None
    url = None; source=None; ts=None
    if c:
        url=(c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
        source=(c.get("provider") or {}).get("displayName")
        ts=c.get("pubDate") or c.get("displayTime")
    if isinstance(item,dict):
        url=url or item.get("link")
        source=source or item.get("publisher")
        ts=ts or item.get("providerPublishTime")
    if isinstance(ts,(int,float)):
        ts=datetime.fromtimestamp(ts,timezone.utc).isoformat()
    return title,url,source,ts

def stock_events(rows):
    events=[]
    # Focus on the most active names plus mega-cap anchors
    focus=[]
    for x in rows[:12] + [r for r in rows if r["ticker"] in ("NVDA","TSLA","COIN","MSTR","AAPL","MSFT")]:
        if x["ticker"] not in focus: focus.append(x["ticker"])
    for s in focus[:14]:
        try:
            news=yf.Ticker(s).get_news(count=4, tab="news") or []
        except Exception:
            news=[]
        for item in news[:3]:
            title,url,source,ts=parse_news_item(item)
            if not title: continue
            bias,impact,horizon,why=classify(title)
            events.append({
                "market":"美股","symbol":s,"title":title,"bias":bias,"impact":impact,
                "horizon":horizon,"time":ts,"source":source or "Yahoo Finance",
                "url":url,"reason":why
            })
    # de-duplicate titles
    seen=set(); out=[]
    for e in events:
        key=re.sub(r"\W+","",e["title"].lower())[:100]
        if key in seen: continue
        seen.add(key); out.append(e)
    return out[:40]

def crypto_events():
    out=[]; seen=set()
    try:
        boosts=requests.get("https://api.dexscreener.com/token-boosts/top/v1",timeout=15).json()
    except Exception:
        return out
    for b in boosts or []:
        chain=b.get("chainId"); ca=b.get("tokenAddress")
        if chain not in ("solana","base","ethereum") or not ca or ca in seen: continue
        seen.add(ca)
        try:
            pairs=requests.get(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{ca}",timeout=10).json()
            if not isinstance(pairs,list) or not pairs: continue
            p=max(pairs,key=lambda x:(x.get("liquidity") or {}).get("usd") or 0)
            sym=(p.get("baseToken") or {}).get("symbol") or "?"
            h1=(p.get("priceChange") or {}).get("h1") or 0
            m5=(p.get("priceChange") or {}).get("m5") or 0
            vol1=(p.get("volume") or {}).get("h1") or 0
            liq=(p.get("liquidity") or {}).get("usd") or 0
            tx=(p.get("txns") or {}).get("h1") or {}
            buys=tx.get("buys") or 0; sells=tx.get("sells") or 0
            impact="高" if abs(h1)>=15 or vol1>=500000 else "中"
            bias="偏利多" if h1>3 and buys>sells else "偏利空" if h1<-3 and sells>buys else "中性待确认"
            title=f"DEX活跃：{sym} 1h {h1:+.1f}% · 1h成交 ${vol1:,.0f}"
            reason=f"1h买/卖笔数 {buys}/{sells}，5m {m5:+.1f}%，流动性约 ${liq:,.0f}。这是资金/交易结构事件，不等同于安全核验。"
            out.append({"market":"加密币","symbol":sym,"chain":chain,"ca":ca,"pool":p.get("pairAddress"),
                        "title":title,"bias":bias,"impact":impact,"horizon":"分钟–数小时","time":datetime.now(timezone.utc).isoformat(),
                        "source":"DEX Screener","url":p.get("url"),"reason":reason})
        except Exception:
            continue
        if len(out)>=12: break
    return out

def main():
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    rows=stock_rows()
    events=stock_events(rows)+crypto_events()
    payload={"updated_at":datetime.now(timezone.utc).isoformat(),"us":rows,"events":events}
    with open(OUT,"w",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
    print(f"wrote {len(rows)} stocks, {len(events)} events")

if __name__=="__main__": main()
