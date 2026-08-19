"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const STORAGE = {
  watchCrypto: "edge-watch-crypto-v2",
  watchStocks: "edge-watch-stocks-v2",
  settings: "edge-risk-settings-v2",
  journal: "edge-journal-v2",
};

const state = {
  market: null,
  crypto: null,
  selectedCrypto: null,
  selectedStock: null,
  activeTab: "overview",
  fresh: false,
  loading: false,
  watchCrypto: readStorage(STORAGE.watchCrypto, []),
  watchStocks: readStorage(STORAGE.watchStocks, []),
  settings: readStorage(STORAGE.settings, { capital: 10000, riskPercent: 0.5, portfolioRisk: 2 }),
  journal: readStorage(STORAGE.journal, []),
};

function readStorage(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const absolute = Math.abs(number);
  if (absolute >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
  if (absolute >= 1e6) return `$${(number / 1e6).toFixed(2)}M`;
  if (absolute >= 1e3) return `$${(number / 1e3).toFixed(1)}K`;
  return `$${number.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (Math.abs(number) >= 1000) return `$${number.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (Math.abs(number) >= 1) return `$${number.toFixed(2)}`;
  if (Math.abs(number) >= 0.01) return `$${number.toFixed(4)}`;
  return `$${number.toPrecision(5)}`;
}

function formatPercent(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function formatAge(minutes) {
  const value = Number(minutes);
  if (!Number.isFinite(value)) return "未知";
  if (value < 60) return `${Math.max(0, Math.round(value))}分钟`;
  if (value < 1440) return `${(value / 60).toFixed(1)}小时`;
  return `${(value / 1440).toFixed(1)}天`;
}

function toneByNumber(value) {
  const number = asNumber(value);
  return number > 0 ? "tone-positive" : number < 0 ? "tone-negative" : "tone-neutral";
}

function toneForDecision(value) {
  const text = String(value || "");
  if (/必须买|强势候选|可执行/.test(text)) return "positive";
  if (/接近|等待/.test(text)) return "warning";
  if (/风险|回避|禁止/.test(text)) return "negative";
  return "neutral";
}

function statusIcon(status) {
  return status === "pass" ? "✓" : status === "fail" ? "×" : "?";
}

function formatDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function timeAgo(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}小时前`;
  return `${Math.floor(minutes / 1440)}天前`;
}

function truncateAddress(value, start = 8, end = 6) {
  const text = String(value || "");
  return text.length > start + end + 3 ? `${text.slice(0, start)}…${text.slice(-end)}` : text;
}

function toast(message, type = "info") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show ${type === "error" ? "error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2600);
}

async function copyText(value, label = "内容") {
  try {
    await navigator.clipboard.writeText(String(value || ""));
    toast(`${label}已复制`);
  } catch {
    toast("复制失败，请手动复制", "error");
  }
}

function setActiveTab(tab) {
  state.activeTab = tab;
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.tab === tab));
  $$(".view").forEach(view => view.classList.toggle("active", view.dataset.view === tab));
  if (tab === "crypto") renderCrypto();
  if (tab === "stocks") renderStocks();
  if (tab === "events") renderEvents();
  if (tab === "journal") renderJournal();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function combinedUpdatedAt() {
  const values = [state.market?.updated_at, state.crypto?.updated_at].filter(Boolean).map(value => new Date(value).getTime()).filter(Number.isFinite);
  return values.length ? Math.min(...values) : null;
}

function updateFreshness() {
  const timestamp = combinedUpdatedAt();
  const badge = $("#freshnessBadge");
  if (!timestamp) {
    state.fresh = false;
    badge.className = "freshness error";
    badge.querySelector("span").textContent = "数据不可用";
    $("#updatedAt").textContent = "尚未取得有效数据";
    return;
  }
  const ageMinutes = (Date.now() - timestamp) / 60000;
  state.fresh = ageMinutes <= 30 && ageMinutes >= -2;
  badge.className = `freshness ${state.fresh ? "fresh" : "stale"}`;
  badge.querySelector("span").textContent = state.fresh ? "数据新鲜" : "数据过期 · 禁止执行";
  $("#updatedAt").textContent = `最旧数据 ${timeAgo(timestamp)}更新`;
}

async function loadData(showMessage = false) {
  if (state.loading) return;
  state.loading = true;
  const button = $("#refreshAll");
  button.disabled = true;
  button.textContent = "刷新中…";
  const stamp = Date.now();
  const [marketResult, cryptoResult] = await Promise.allSettled([
    fetch(`data/market.json?v=${stamp}`, { cache: "no-store" }).then(response => {
      if (!response.ok) throw new Error(`market ${response.status}`);
      return response.json();
    }),
    fetch(`data/crypto.json?v=${stamp}`, { cache: "no-store" }).then(response => {
      if (!response.ok) throw new Error(`crypto ${response.status}`);
      return response.json();
    }),
  ]);
  if (marketResult.status === "fulfilled") state.market = marketResult.value;
  if (cryptoResult.status === "fulfilled") state.crypto = cryptoResult.value;
  state.loading = false;
  button.disabled = false;
  button.textContent = "立即刷新";
  updateFreshness();
  renderAll();
  if (marketResult.status === "rejected" || cryptoResult.status === "rejected") {
    toast("部分数据源加载失败，相关信号已禁止", "error");
  } else if (showMessage) {
    toast("数据已刷新并重新核验");
  }
}

function metricCard(label, value, caption, tone = "info") {
  return `<article class="metric-card ${tone}"><span class="label">${escapeHtml(label)}</span><b class="value">${escapeHtml(value)}</b><div class="caption">${escapeHtml(caption)}</div></article>`;
}

function renderOverview() {
  const crypto = state.crypto?.summary || {};
  const market = state.market?.summary || {};
  const effectiveActionable = state.fresh ? asNumber(crypto.actionable) : 0;
  $("#overviewMetrics").innerHTML = [
    metricCard("必须买｜可执行", effectiveActionable, state.fresh ? "仅统计全部硬条件通过" : "数据过期，强制归零", "positive"),
    metricCard("接近触发", asNumber(crypto.near_trigger), "网页可见，不发送提醒", "warning"),
    metricCard("高风险否决", asNumber(crypto.risk_denied), "明确风险或安全字段失败", "negative"),
    metricCard("美股强势候选", asNumber(market.strong), "仍需盘中价格与成交确认", "info"),
    metricCard("高影响事件", asNumber(market.high_impact_events) + highCryptoEvents(), "新闻不会直接升级信号", "warning"),
  ].join("");

  const regime = state.market?.regime;
  const label = $("#regimeLabel");
  label.textContent = regime?.label || "数据不足";
  label.className = `state-pill ${regime?.tone || "neutral"}`;
  $("#regimeNote").textContent = regime?.note || "市场环境数据不可用，禁止依据该模块交易。";
  $("#benchmarkGrid").innerHTML = (regime?.benchmarks || []).map(item => `
    <div class="benchmark">
      <b>${escapeHtml(item.ticker)}</b>
      <strong>${formatPrice(item.price)}</strong>
      <small class="${toneByNumber(item.d5)}">5D ${formatPercent(item.d5)}</small>
    </div>`).join("") || `<div class="empty-state"><span>暂无指数数据</span></div>`;

  const sources = [...(state.market?.sources || []), ...(state.crypto?.sources || [])];
  const healthy = sources.filter(item => item.status === "ok").length;
  $("#healthSummary").textContent = `${healthy}/${sources.length || 0} 正常`;
  $("#healthSummary").className = `state-pill ${healthy === sources.length && sources.length ? "positive" : "warning"}`;
  $("#sourceHealth").innerHTML = sources.map(item => `
    <div class="health-item ${escapeHtml(item.status)}"><i></i><b>${escapeHtml(item.name)}</b><span>${escapeHtml(item.detail)}</span></div>`).join("") || `<div class="empty-state"><span>暂无数据源状态</span></div>`;

  const candidates = [...(state.crypto?.candidates || [])]
    .filter(item => item.decision !== "高风险否决")
    .sort((a, b) => asNumber(b.score) - asNumber(a.score))
    .slice(0, 6);
  $("#opportunityQueue").innerHTML = candidates.map(item => `
    <div class="compact-row" data-overview-kind="crypto" data-id="${escapeHtml(item.id)}">
      <div><b>${escapeHtml(item.symbol)} · ${escapeHtml(item.chain)}</b><span>${escapeHtml(item.decision)} · ${escapeHtml((item.blockers || []).slice(0, 2).join("、") || "等待更多证据")}</span></div>
      <div class="right"><b class="${toneByNumber(item.price_change?.h1)}">${formatPercent(item.price_change?.h1)}</b><span>${formatMoney(item.liquidity_usd)} LP</span></div>
    </div>`).join("") || `<div class="empty-state"><span>暂无候选</span></div>`;

  const events = combinedEvents().filter(item => item.impact === "高").slice(0, 6);
  $("#highImpactEvents").innerHTML = events.map(item => `
    <div class="compact-row" data-overview-kind="event">
      <div><b>${escapeHtml(item.symbol || "市场")} · ${escapeHtml(item.title)}</b><span>${escapeHtml(item.source || "未知来源")} · ${formatDate(item.time)}</span></div>
      <div class="right"><span class="tag ${toneForBias(item.bias)}">${escapeHtml(item.bias)}</span></div>
    </div>`).join("") || `<div class="empty-state"><span>暂无高影响事件</span></div>`;
}

function highCryptoEvents() {
  return (state.crypto?.events || []).filter(item => item.impact === "高").length;
}

function toggleWatch(type, id) {
  const key = type === "crypto" ? "watchCrypto" : "watchStocks";
  const storageKey = type === "crypto" ? STORAGE.watchCrypto : STORAGE.watchStocks;
  const list = state[key];
  const index = list.indexOf(id);
  if (index >= 0) list.splice(index, 1);
  else list.push(id);
  writeStorage(storageKey, list);
  if (type === "crypto") renderCrypto();
  else renderStocks();
}

function cryptoFiltered() {
  let items = [...(state.crypto?.candidates || [])];
  const query = $("#cryptoSearch").value.trim().toLowerCase();
  const chain = $("#chainFilter").value;
  const status = $("#cryptoStateFilter").value;
  const minimum = asNumber($("#cryptoLiquidity").value);
  const sort = $("#cryptoSort").value;
  if (query) items = items.filter(item => [item.symbol, item.name, item.ca, item.pool].some(value => String(value || "").toLowerCase().includes(query)));
  if (chain !== "all") items = items.filter(item => item.chain === chain);
  if (status === "actionable") items = items.filter(item => item.actionable);
  if (status === "near") items = items.filter(item => item.decision === "接近触发｜保持静默");
  if (status === "risk") items = items.filter(item => item.decision === "高风险否决");
  if (status === "watch") items = items.filter(item => state.watchCrypto.includes(item.id));
  items = items.filter(item => asNumber(item.liquidity_usd) >= minimum);
  items.sort((a, b) => {
    if (sort === "liquidity") return asNumber(b.liquidity_usd) - asNumber(a.liquidity_usd);
    if (sort === "volume") return asNumber(b.volume?.h1) - asNumber(a.volume?.h1);
    if (sort === "change") return asNumber(b.price_change?.h1) - asNumber(a.price_change?.h1);
    if (sort === "age") return asNumber(a.age_minutes, Infinity) - asNumber(b.age_minutes, Infinity);
    return asNumber(b.score) - asNumber(a.score);
  });
  return items;
}

function renderCrypto() {
  const items = cryptoFiltered();
  $("#cryptoCount").textContent = items.length;
  $("#cryptoList").innerHTML = items.map(item => {
    const watched = state.watchCrypto.includes(item.id);
    const active = state.selectedCrypto === item.id;
    const tone = toneForDecision(item.decision);
    return `<div class="scan-item ${active ? "active" : ""}" data-crypto-id="${escapeHtml(item.id)}">
      <button class="star-button ${watched ? "on" : ""}" data-watch-crypto="${escapeHtml(item.id)}" type="button" aria-label="${watched ? "移除" : "加入"}自选">★</button>
      <div class="scan-main"><div class="scan-title"><b>${escapeHtml(item.symbol)}</b><span class="tag ${tone}">${escapeHtml(item.chain)}</span></div><div class="scan-sub">${escapeHtml(item.setup)} · ${escapeHtml(item.decision)}</div></div>
      <div class="scan-right"><b class="${toneByNumber(item.price_change?.h1)}">${formatPercent(item.price_change?.h1)}</b><span>${formatMoney(item.liquidity_usd)}</span></div>
    </div>`;
  }).join("") || `<div class="empty-state"><b>当前筛选无结果</b><span>降低流动性门槛或切换状态。</span></div>`;
  const selected = (state.crypto?.candidates || []).find(item => item.id === state.selectedCrypto);
  if (selected) renderCryptoDetail(selected);
}

function relatedEvents(market, symbol, ca) {
  return combinedEvents().filter(event => event.market === market && ((symbol && event.symbol === symbol) || (ca && event.ca === ca))).slice(0, 5);
}

function renderCryptoDetail(item) {
  const watched = state.watchCrypto.includes(item.id);
  const events = relatedEvents("加密币", item.symbol, item.ca);
  $("#cryptoEvidence").innerHTML = `
    <div class="asset-head">
      <div class="asset-id"><span class="panel-kicker">${escapeHtml(item.chain)} · ${escapeHtml(item.dex || "DEX")}</span><h2>${escapeHtml(item.symbol)} <span class="tag ${toneForDecision(item.decision)}">${escapeHtml(item.setup)}</span></h2><p>${escapeHtml(item.name)} · ${escapeHtml(item.ca)}</p></div>
      <div class="asset-actions">
        <button class="mini-button" id="cryptoWatchButton" type="button">${watched ? "★ 已自选" : "☆ 加入自选"}</button>
        <button class="mini-button" id="copyCa" type="button">复制 CA</button>
        <button class="mini-button" id="livePairRefresh" type="button">刷新池数据</button>
        <a class="mini-button" href="${safeUrl(item.url)}" target="_blank" rel="noopener">打开 DEX ↗</a>
      </div>
    </div>
    <div class="stats-grid">
      ${stat("价格", formatPrice(item.price_usd))}${stat("流动性", formatMoney(item.liquidity_usd))}${stat("FDV", formatMoney(item.fdv))}
      ${stat("5m", formatPercent(item.price_change?.m5), toneByNumber(item.price_change?.m5))}${stat("1h", formatPercent(item.price_change?.h1), toneByNumber(item.price_change?.h1))}${stat("池龄", formatAge(item.age_minutes))}
    </div>
    <div class="chart-wrap"><div class="chart-head"><h3>价格快照</h3><span>${(item.history || []).length < 2 ? "等待下一次后台快照形成趋势" : `${item.history.length}个连续快照`}</span></div><canvas class="chart-canvas" id="cryptoChart" aria-label="加密币价格快照图"></canvas></div>
    <div class="evidence-section">
      <div class="section-row"><h3>身份与主池</h3><span class="tag info">完整地址</span></div>
      <div class="address-grid">
        ${addressRow("CA", item.ca, "copyCaInline")}${addressRow("主池", item.pool, "copyPool")}
      </div>
    </div>
    <div class="evidence-section">
      <div class="section-row"><h3>资金与成交观察</h3><span class="tag warning">聚合数据 ≠ 美元净买</span></div>
      <div class="flow-grid">
        ${flowCard("5m成交", formatMoney(item.volume?.m5), `买/卖 ${item.txns?.m5?.buys || 0}/${item.txns?.m5?.sells || 0}`)}
        ${flowCard("1h成交", formatMoney(item.volume?.h1), `买/卖 ${item.txns?.h1?.buys || 0}/${item.txns?.h1?.sells || 0}`)}
        ${flowCard("6h成交", formatMoney(item.volume?.h6), `买/卖 ${item.txns?.h6?.buys || 0}/${item.txns?.h6?.sells || 0}`)}
        ${flowCard("24h成交", formatMoney(item.volume?.h24), `买/卖 ${item.txns?.h24?.buys || 0}/${item.txns?.h24?.sells || 0}`)}
      </div>
    </div>
    <div class="evidence-section">
      <div class="section-row"><h3>相关事件</h3><span>${events.length} 条</span></div>
      <div class="event-mini-list">${events.length ? events.map(event => `<div class="event-mini"><b>${escapeHtml(event.title)}</b><span>${escapeHtml(event.source)} · ${formatDate(event.time)}</span></div>`).join("") : `<p>当前没有结构化事件；这不会被当作利空或利多。</p>`}</div>
    </div>`;
  renderCryptoDecision(item);
  requestAnimationFrame(() => drawChart($("#cryptoChart"), (item.history || []).map(point => point.price), "crypto"));
  $("#cryptoWatchButton").onclick = () => toggleWatch("crypto", item.id);
  $("#copyCa").onclick = () => copyText(item.ca, "CA");
  $("#copyCaInline").onclick = () => copyText(item.ca, "CA");
  $("#copyPool").onclick = () => copyText(item.pool, "主池地址");
  $("#livePairRefresh").onclick = () => refreshSelectedPair(item);
}

function renderCryptoDecision(item) {
  const freshActionable = Boolean(item.actionable && state.fresh);
  const title = !state.fresh ? "数据过期｜禁止执行" : item.decision;
  const tone = !state.fresh ? "negative" : toneForDecision(item.decision);
  const checks = item.checks || [];
  const passed = checks.filter(check => check.status === "pass").length;
  const blockers = !state.fresh ? ["数据新鲜度"] : (item.blockers || []);
  $("#cryptoDecision").innerHTML = `
    <div class="decision-hero">
      <div class="decision-label">STRICT AND DECISION</div>
      <div class="decision-title ${tone}">${escapeHtml(title)}</div>
      <p>${freshActionable ? "全部关键条件通过，可以进入仓位计算。" : "关键条件存在失败或未知，通知保持静默。"}</p>
      <div class="progress"><span style="width:${checks.length ? Math.round(passed / checks.length * 100) : 0}%"></span></div>
      <div class="progress-meta"><span>证据通过 ${passed}/${checks.length}</span><span>未知同样否决</span></div>
      ${blockers.length ? `<div class="blocker-box ${tone === "negative" ? "negative" : ""}"><b>当前阻断：</b>${escapeHtml(blockers.slice(0, 5).join("、"))}</div>` : ""}
    </div>
    <div class="checks">${checks.map(check => checkHtml(check)).join("")}</div>
    <div class="plan-box">
      <h3>执行计划</h3>
      <div class="plan-grid">
        ${planCell("买入触发", freshActionable ? formatPrice(item.execution?.entry) : "禁止生成")}
        ${planCell("计划仓位", freshActionable ? formatMoney(item.execution?.position_usdt) : "0")}
        ${planCell("硬止损", freshActionable ? formatPrice(item.execution?.stop) : "—")}
        ${planCell("分批止盈", freshActionable ? (item.execution?.targets || []).map(formatPrice).join(" / ") : "—")}
      </div>
      <div class="plan-note">${escapeHtml(item.execution?.reason || "没有完整证据，不提供伪精确价格。")}</div>
      <button class="button full" type="button" ${freshActionable ? "" : "disabled"}>记录可执行计划</button>
    </div>`;
}

async function refreshSelectedPair(item) {
  const button = $("#livePairRefresh");
  button.disabled = true;
  button.textContent = "刷新中…";
  try {
    const response = await fetch(`https://api.dexscreener.com/latest/dex/pairs/${encodeURIComponent(item.chain)}/${encodeURIComponent(item.pool)}`);
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    const pair = (payload.pairs || [])[0];
    if (!pair) throw new Error("empty");
    item.price_usd = asNumber(pair.priceUsd, item.price_usd);
    item.liquidity_usd = asNumber(pair.liquidity?.usd, item.liquidity_usd);
    item.price_change = { ...item.price_change, ...(pair.priceChange || {}) };
    item.volume = { ...item.volume, ...(pair.volume || {}) };
    item.txns = { ...item.txns, ...(pair.txns || {}) };
    renderCrypto();
    toast("池数据已刷新；安全结论未改变");
  } catch {
    toast("DEX实时刷新失败，保留后台快照", "error");
    button.disabled = false;
    button.textContent = "刷新池数据";
  }
}

function stat(label, value, className = "") {
  return `<div class="stat"><span>${escapeHtml(label)}</span><b class="${className}">${escapeHtml(value)}</b></div>`;
}

function addressRow(label, value, buttonId) {
  return `<div class="address-row"><span>${escapeHtml(label)}</span><code title="${escapeHtml(value)}">${escapeHtml(value || "未知")}</code><button class="mini-button" id="${buttonId}" type="button">复制</button></div>`;
}

function flowCard(label, value, detail) {
  return `<div class="flow-card"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b><small>${escapeHtml(detail)}</small></div>`;
}

function checkHtml(check) {
  return `<div class="check-item ${escapeHtml(check.status)}"><div class="check-icon">${statusIcon(check.status)}</div><div class="check-copy"><b>${escapeHtml(check.label)}</b><span>${escapeHtml(check.detail)}</span>${check.evidence ? `<small>证据：${escapeHtml(check.evidence)}</small>` : ""}</div></div>`;
}

function planCell(label, value) {
  return `<div class="plan-cell"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`;
}

function stockFiltered() {
  let items = [...(state.market?.us || [])];
  const query = $("#stockSearch").value.trim().toLowerCase();
  const setup = $("#stockSetupFilter").value;
  const sort = $("#stockSort").value;
  if (query) items = items.filter(item => [item.ticker, item.name, item.sector].some(value => String(value || "").toLowerCase().includes(query)));
  if (setup === "watch") items = items.filter(item => state.watchStocks.includes(item.ticker));
  else if (setup !== "all") items = items.filter(item => item.decision === setup);
  items.sort((a, b) => {
    if (sort === "d1") return asNumber(b.d1) - asNumber(a.d1);
    if (sort === "d5") return asNumber(b.d5) - asNumber(a.d5);
    if (sort === "volume") return asNumber(b.volume_ratio) - asNumber(a.volume_ratio);
    if (sort === "rs") return asNumber(b.rs5_spy) - asNumber(a.rs5_spy);
    return asNumber(b.score) - asNumber(a.score);
  });
  return items;
}

function renderStocks() {
  const items = stockFiltered();
  $("#stockCount").textContent = items.length;
  $("#stockList").innerHTML = items.map(item => {
    const watched = state.watchStocks.includes(item.ticker);
    const active = state.selectedStock === item.ticker;
    return `<div class="scan-item ${active ? "active" : ""}" data-stock-id="${escapeHtml(item.ticker)}">
      <button class="star-button ${watched ? "on" : ""}" data-watch-stock="${escapeHtml(item.ticker)}" type="button" aria-label="${watched ? "移除" : "加入"}自选">★</button>
      <div class="scan-main"><div class="scan-title"><b>${escapeHtml(item.ticker)}</b><span class="tag ${toneForDecision(item.decision)}">${escapeHtml(item.sector)}</span></div><div class="scan-sub">${escapeHtml(item.setup)} · ${escapeHtml(item.decision)}</div></div>
      <div class="scan-right"><b class="${toneByNumber(item.d1)}">${formatPercent(item.d1)}</b><span>量比 ${item.volume_ratio ?? "—"}</span></div>
    </div>`;
  }).join("") || `<div class="empty-state"><b>当前筛选无结果</b><span>切换结构或自选筛选。</span></div>`;
  const selected = (state.market?.us || []).find(item => item.ticker === state.selectedStock);
  if (selected) renderStockDetail(selected);
}

function renderStockDetail(item) {
  const watched = state.watchStocks.includes(item.ticker);
  const events = relatedEvents("美股", item.ticker);
  $("#stockEvidence").innerHTML = `
    <div class="asset-head">
      <div class="asset-id"><span class="panel-kicker">${escapeHtml(item.sector)} · US EQUITY</span><h2>${escapeHtml(item.ticker)} <span class="tag ${toneForDecision(item.decision)}">${escapeHtml(item.setup)}</span></h2><p>${escapeHtml(item.name)}</p></div>
      <div class="asset-actions"><button class="mini-button" id="stockWatchButton" type="button">${watched ? "★ 已自选" : "☆ 加入自选"}</button><a class="mini-button" href="https://finance.yahoo.com/quote/${encodeURIComponent(item.ticker)}" target="_blank" rel="noopener">查看行情 ↗</a></div>
    </div>
    <div class="stats-grid">
      ${stat("价格", formatPrice(item.price))}${stat("1日", formatPercent(item.d1), toneByNumber(item.d1))}${stat("5日", formatPercent(item.d5), toneByNumber(item.d5))}
      ${stat("量比", item.volume_ratio ?? "—")}${stat("RSI14", item.rsi14 ?? "—")}${stat("相对SPY", formatPercent(item.rs5_spy), toneByNumber(item.rs5_spy))}
    </div>
    <div class="chart-wrap"><div class="chart-head"><h3>45个交易日价格</h3><span>日线结构 · 非盘中触发</span></div><canvas class="chart-canvas" id="stockChart" aria-label="股票价格历史图"></canvas></div>
    <div class="evidence-section">
      <div class="section-row"><h3>趋势与波动</h3><span class="tag info">相对强度优先</span></div>
      <div class="flow-grid">
        ${flowCard("MA20", formatPrice(item.ma20), `偏离 ${formatPercent(item.extension_ma20)}`)}
        ${flowCard("MA50", formatPrice(item.ma50), item.price > item.ma50 ? "价格在上方" : "价格在下方")}
        ${flowCard("ATR14", formatPrice(item.atr14), `约 ${formatPercent(item.atr_pct)}`)}
        ${flowCard("20日表现", formatPercent(item.d20), `相对SPY ${formatPercent(item.rs20_spy)}`)}
      </div>
    </div>
    <div class="evidence-section">
      <div class="section-row"><h3>近期相关事件</h3><span>${events.length} 条</span></div>
      <div class="event-mini-list">${events.length ? events.map(event => `<div class="event-mini"><b>${escapeHtml(event.title)}</b><span>${escapeHtml(event.source)} · ${formatDate(event.time)} · ${escapeHtml(event.bias)}</span></div>`).join("") : `<p>当前没有抓取到直接相关事件。</p>`}</div>
    </div>`;
  renderStockDecision(item);
  requestAnimationFrame(() => drawChart($("#stockChart"), (item.history || []).map(point => point.close), "stock"));
  $("#stockWatchButton").onclick = () => toggleWatch("stock", item.ticker);
}

function positionForStock(item) {
  const trigger = asNumber(item.plan?.trigger, NaN);
  const stop = asNumber(item.plan?.stop, NaN);
  const perShare = trigger - stop;
  const riskAmount = asNumber(state.settings.capital) * asNumber(state.settings.riskPercent) / 100;
  if (![trigger, stop, perShare, riskAmount].every(Number.isFinite) || perShare <= 0 || riskAmount <= 0) return null;
  const shares = Math.max(0, Math.floor(riskAmount / perShare));
  return { shares, value: shares * trigger, riskAmount, perShare };
}

function renderStockDecision(item) {
  const effectiveReady = Boolean(item.trade_ready && state.fresh);
  const title = !state.fresh ? "数据过期｜禁止执行" : item.decision;
  const checks = item.checks || [];
  const passed = checks.filter(check => check.status === "pass").length;
  const position = positionForStock(item);
  $("#stockDecision").innerHTML = `
    <div class="decision-hero">
      <div class="decision-label">STRUCTURE DECISION</div><div class="decision-title ${toneForDecision(title)}">${escapeHtml(title)}</div>
      <p>${effectiveReady ? "日线结构全部通过，仍需盘中触发与成交确认。" : "当前结构不完整，不应因为单日上涨追高。"}</p>
      <div class="progress"><span style="width:${checks.length ? Math.round(passed / checks.length * 100) : 0}%"></span></div>
      <div class="progress-meta"><span>证据通过 ${passed}/${checks.length}</span><span>${escapeHtml(item.setup)}</span></div>
    </div>
    <div class="checks">${checks.map(check => checkHtml(check)).join("")}</div>
    <div class="plan-box">
      <h3>风险定义后的计划</h3>
      <div class="plan-grid">
        ${planCell("触发价", effectiveReady ? formatPrice(item.plan?.trigger) : "等待确认")}
        ${planCell("止损", effectiveReady ? formatPrice(item.plan?.stop) : "—")}
        ${planCell("目标1", effectiveReady ? formatPrice(item.plan?.target1) : "—")}
        ${planCell("目标2", effectiveReady ? formatPrice(item.plan?.target2) : "—")}
        ${planCell("建议股数", effectiveReady && position ? `${position.shares}股` : "0")}
        ${planCell("名义仓位", effectiveReady && position ? formatMoney(position.value) : "$0")}
      </div>
      <div class="plan-note">风险预算 ${formatMoney(position?.riskAmount || 0)}。${escapeHtml(item.plan?.note || "未确认前不执行。")}</div>
      <button class="button full" id="logStockPlan" type="button" ${effectiveReady ? "" : "disabled"}>记录这笔计划</button>
    </div>`;
  const button = $("#logStockPlan");
  if (button && effectiveReady) button.onclick = () => logStockPlan(item, position);
}

function combinedEvents() {
  return [...(state.market?.events || []), ...(state.crypto?.events || [])].sort((a, b) => new Date(b.time || 0) - new Date(a.time || 0));
}

function toneForBias(bias) {
  return bias === "偏利多" ? "positive" : bias === "偏利空" ? "negative" : "warning";
}

function renderEvents() {
  let items = combinedEvents();
  const query = $("#eventSearch").value.trim().toLowerCase();
  const market = $("#eventMarket").value;
  const bias = $("#eventBias").value;
  const impact = $("#eventImpact").value;
  if (query) items = items.filter(item => [item.symbol, item.title, item.source, item.event_type].some(value => String(value || "").toLowerCase().includes(query)));
  if (market !== "all") items = items.filter(item => item.market === market);
  if (bias !== "all") items = items.filter(item => item.bias === bias);
  if (impact !== "all") items = items.filter(item => item.impact === impact);
  $("#eventGrid").innerHTML = items.map(item => `
    <article class="event-card">
      <div class="event-top"><span class="event-symbol">${escapeHtml(item.symbol || "MARKET")}</span><div><span class="tag ${toneForBias(item.bias)}">${escapeHtml(item.bias || "待确认")}</span> <span class="tag ${item.impact === "高" ? "negative" : "info"}">${escapeHtml(item.impact || "中")}影响</span></div></div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.reason || "需要结合原文和价格反应确认。")}</p>
      <div class="event-meta"><span>${escapeHtml(item.event_type || "事件")} · ${formatDate(item.time)}<br>${escapeHtml(item.source || "未知来源")} · 可信度${escapeHtml(item.credibility || "中")}</span>${safeUrl(item.url) !== "#" ? `<a href="${safeUrl(item.url)}" target="_blank" rel="noopener">原文 ↗</a>` : ""}</div>
    </article>`).join("") || `<div class="empty-state"><b>当前筛选没有事件</b><span>事件为空不会被解释成利多或利空。</span></div>`;
}

function logStockPlan(item, position) {
  state.journal.unshift({
    id: `stock-${item.ticker}-${Date.now()}`, type: "美股", symbol: item.ticker,
    createdAt: new Date().toISOString(), status: "计划", resultR: null,
    entry: item.plan?.trigger, stop: item.plan?.stop, target: item.plan?.target1,
    size: position?.shares || 0, riskAmount: position?.riskAmount || 0,
  });
  writeStorage(STORAGE.journal, state.journal);
  toast(`${item.ticker} 计划已写入复盘区`);
}

function renderJournal() {
  $("#accountCapital").value = state.settings.capital;
  $("#riskPercent").value = state.settings.riskPercent;
  $("#portfolioRisk").value = state.settings.portfolioRisk;
  const perTrade = asNumber(state.settings.capital) * asNumber(state.settings.riskPercent) / 100;
  const portfolio = asNumber(state.settings.capital) * asNumber(state.settings.portfolioRisk) / 100;
  $("#riskRuleText").innerHTML = `单笔最多承担 <b>${formatMoney(perTrade)}</b>；全部持仓合计风险不超过 <b>${formatMoney(portfolio)}</b>。没有有效止损的交易，仓位自动为0。`;
  $("#journalList").innerHTML = state.journal.map(entry => `
    <div class="journal-entry">
      <div><b>${escapeHtml(entry.symbol)} · ${escapeHtml(entry.type)} <span class="tag ${entry.resultR > 0 ? "positive" : entry.resultR < 0 ? "negative" : "info"}">${entry.resultR == null ? escapeHtml(entry.status) : `${entry.resultR > 0 ? "+" : ""}${entry.resultR}R`}</span></b><span>${formatDate(entry.createdAt)} · 触发 ${formatPrice(entry.entry)} · 止损 ${formatPrice(entry.stop)} · 风险 ${formatMoney(entry.riskAmount)}</span></div>
      <div><button class="mini-button" data-result="2" data-journal-id="${escapeHtml(entry.id)}" type="button">+2R</button> <button class="mini-button" data-result="-1" data-journal-id="${escapeHtml(entry.id)}" type="button">-1R</button></div>
    </div>`).join("") || `<div class="empty-state"><b>还没有交易记录</b><span>只有满足执行条件的计划才能一键写入。</span></div>`;
}

function saveSettings() {
  const capital = asNumber($("#accountCapital").value);
  const riskPercent = asNumber($("#riskPercent").value);
  const portfolioRisk = asNumber($("#portfolioRisk").value);
  if (capital <= 0 || riskPercent < 0.1 || riskPercent > 5 || portfolioRisk < riskPercent) {
    toast("风险参数无效：资金需大于0，组合风险不能小于单笔风险", "error");
    return;
  }
  state.settings = { capital, riskPercent, portfolioRisk };
  writeStorage(STORAGE.settings, state.settings);
  renderJournal();
  if (state.selectedStock) renderStocks();
  toast("风险参数已保存在当前浏览器");
}

function exportJournal() {
  const payload = JSON.stringify({ exportedAt: new Date().toISOString(), settings: state.settings, journal: state.journal }, null, 2);
  const blob = new Blob([payload], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `edge-terminal-journal-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function drawChart(canvas, rawValues, kind) {
  if (!canvas) return;
  const values = rawValues.map(Number).filter(Number.isFinite);
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = "rgba(127,147,167,.11)";
  context.lineWidth = 1;
  for (let index = 1; index < 4; index++) {
    const y = height * index / 4;
    context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
  }
  if (!values.length) {
    context.fillStyle = "#7f93a7"; context.font = "11px system-ui"; context.fillText("暂无价格快照", 14, 26); return;
  }
  if (values.length === 1) {
    context.fillStyle = "#58e7d6"; context.beginPath(); context.arc(width / 2, height / 2, 3, 0, Math.PI * 2); context.fill();
    context.fillStyle = "#7f93a7"; context.font = "11px system-ui"; context.fillText("等待下一次快照形成趋势", 14, 24); return;
  }
  let minimum = Math.min(...values), maximum = Math.max(...values);
  if (minimum === maximum) { minimum *= .995; maximum *= 1.005; }
  const padX = 8, padY = 12;
  const x = index => padX + index / (values.length - 1) * (width - padX * 2);
  const y = value => padY + (maximum - value) / (maximum - minimum) * (height - padY * 2);
  const positive = values.at(-1) >= values[0];
  const color = positive ? "#42d392" : "#ff667d";
  const gradient = context.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, positive ? "rgba(66,211,146,.22)" : "rgba(255,102,125,.2)");
  gradient.addColorStop(1, "rgba(7,16,25,0)");
  context.beginPath(); context.moveTo(x(0), height - padY);
  values.forEach((value, index) => context.lineTo(x(index), y(value)));
  context.lineTo(x(values.length - 1), height - padY); context.closePath(); context.fillStyle = gradient; context.fill();
  context.beginPath(); values.forEach((value, index) => index ? context.lineTo(x(index), y(value)) : context.moveTo(x(index), y(value)));
  context.strokeStyle = color; context.lineWidth = kind === "stock" ? 2 : 1.8; context.stroke();
  context.fillStyle = color; context.beginPath(); context.arc(x(values.length - 1), y(values.at(-1)), 3, 0, Math.PI * 2); context.fill();
}

function renderAll() {
  renderOverview();
  renderCrypto();
  renderStocks();
  renderEvents();
  renderJournal();
}

function bindEvents() {
  $$(".nav-item").forEach(item => item.addEventListener("click", () => setActiveTab(item.dataset.tab)));
  $$('[data-go]').forEach(item => item.addEventListener("click", () => setActiveTab(item.dataset.go)));
  $("#refreshAll").addEventListener("click", () => loadData(true));
  ["cryptoSearch", "chainFilter", "cryptoStateFilter", "cryptoLiquidity", "cryptoSort"].forEach(id => $("#" + id).addEventListener("input", renderCrypto));
  ["stockSearch", "stockSetupFilter", "stockSort"].forEach(id => $("#" + id).addEventListener("input", renderStocks));
  ["eventSearch", "eventMarket", "eventBias", "eventImpact"].forEach(id => $("#" + id).addEventListener("input", renderEvents));
  $("#saveSettings").addEventListener("click", saveSettings);
  $("#exportJournal").addEventListener("click", exportJournal);

  $("#cryptoList").addEventListener("click", event => {
    const watch = event.target.closest("[data-watch-crypto]");
    if (watch) { event.stopPropagation(); toggleWatch("crypto", watch.dataset.watchCrypto); return; }
    const row = event.target.closest("[data-crypto-id]");
    if (row) { state.selectedCrypto = row.dataset.cryptoId; renderCrypto(); }
  });
  $("#stockList").addEventListener("click", event => {
    const watch = event.target.closest("[data-watch-stock]");
    if (watch) { event.stopPropagation(); toggleWatch("stock", watch.dataset.watchStock); return; }
    const row = event.target.closest("[data-stock-id]");
    if (row) { state.selectedStock = row.dataset.stockId; renderStocks(); }
  });
  $("#opportunityQueue").addEventListener("click", event => {
    const row = event.target.closest("[data-id]");
    if (!row) return;
    state.selectedCrypto = row.dataset.id;
    setActiveTab("crypto");
  });
  $("#highImpactEvents").addEventListener("click", () => setActiveTab("events"));
  $("#journalList").addEventListener("click", event => {
    const button = event.target.closest("[data-journal-id]");
    if (!button) return;
    const entry = state.journal.find(item => item.id === button.dataset.journalId);
    if (!entry) return;
    entry.resultR = asNumber(button.dataset.result);
    entry.status = "已复盘";
    writeStorage(STORAGE.journal, state.journal);
    renderJournal();
  });
  window.addEventListener("resize", () => {
    const crypto = (state.crypto?.candidates || []).find(item => item.id === state.selectedCrypto);
    const stock = (state.market?.us || []).find(item => item.ticker === state.selectedStock);
    if (crypto) drawChart($("#cryptoChart"), (crypto.history || []).map(point => point.price), "crypto");
    if (stock) drawChart($("#stockChart"), (stock.history || []).map(point => point.close), "stock");
  });
}

bindEvents();
renderJournal();
loadData();
setInterval(updateFreshness, 60_000);
setInterval(() => loadData(false), 5 * 60_000);

