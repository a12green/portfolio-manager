const $ = (sel) => document.querySelector(sel);
const statusEl = $("#status");

let currentUser = null;
let meta = { owners: [], markets: [], currencies: [], is_admin: true };
let funds = [];

function setStatus(msg) {
  statusEl.textContent = msg;
}

function showLogin(msg = "") {
  $("#auth-overlay").classList.add("show");
  $("#logout-btn").hidden = true;
  $("#change-pwd-btn").hidden = true;
  $("#account-btn").hidden = true;
  $("#user-label").textContent = "";
  const err = $("#login-error");
  if (msg) {
    err.textContent = msg;
    err.hidden = false;
  } else {
    err.hidden = true;
    err.textContent = "";
  }
}

function hideLogin() {
  $("#auth-overlay").classList.remove("show");
  $("#login-error").hidden = true;
}

function showChangePassword() {
  $("#pwd-current").value = "";
  $("#pwd-new").value = "";
  $("#pwd-confirm").value = "";
  $("#pwd-error").hidden = true;
  $("#pwd-success").hidden = true;
  $("#pwd-overlay").classList.add("show");
}

function hideChangePassword() {
  $("#pwd-overlay").classList.remove("show");
}

async function changePassword() {
  const current_password = $("#pwd-current").value;
  const new_password = $("#pwd-new").value;
  const confirm = $("#pwd-confirm").value;
  const err = $("#pwd-error");
  const ok = $("#pwd-success");
  err.hidden = true;
  ok.hidden = true;
  if (!current_password || !new_password) {
    err.textContent = "Enter your current and new password.";
    err.hidden = false;
    return;
  }
  if (new_password.length < 8) {
    err.textContent = "New password must be at least 8 characters.";
    err.hidden = false;
    return;
  }
  if (new_password !== confirm) {
    err.textContent = "New password and confirmation don't match.";
    err.hidden = false;
    return;
  }
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    });
    $("#pwd-current").value = "";
    $("#pwd-new").value = "";
    $("#pwd-confirm").value = "";
    ok.textContent = "Password updated.";
    ok.hidden = false;
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: {
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) {
    currentUser = null;
    showLogin();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : res.statusText;
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmtMoney(v, loc = "") {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  const sym = String(loc || "").toUpperCase() === "AUS" ? "A$" : "$";
  return `${sign}${sym}${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${Number(v).toFixed(2)}%`;
}

function plClass(v) {
  if (v == null) return "";
  return v >= 0 ? "good" : "bad";
}

const SPARK_SCALE_PCT = 3.0;

function sparkPct(price, baseline) {
  if (!baseline || baseline <= 0) return 0;
  return (price / baseline - 1) * 100;
}

function sparkY(pct, h, scale = SPARK_SCALE_PCT) {
  const clamped = Math.max(-scale, Math.min(scale, pct));
  const t = (scale - clamped) / (2 * scale);
  return 1 + (h - 2) * t;
}

function drawSpark(canvas, prices, baseline) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!prices || prices.length < 2) {
    ctx.strokeStyle = "#6e7681";
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    return;
  }
  const base = baseline && baseline > 0 ? baseline : prices[0];
  if (!base || base <= 0) {
    ctx.strokeStyle = "#6e7681";
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    return;
  }
  const pcts = prices.map((p) => sparkPct(p, base));
  const baseY = sparkY(0, h);
  ctx.setLineDash([2, 2]);
  ctx.strokeStyle = "#6e7681";
  ctx.beginPath();
  ctx.moveTo(0, baseY);
  ctx.lineTo(w, baseY);
  ctx.stroke();
  ctx.setLineDash([]);
  for (let i = 0; i < prices.length - 1; i++) {
    const x0 = (i / (prices.length - 1)) * (w - 2) + 1;
    const x1 = ((i + 1) / (prices.length - 1)) * (w - 2) + 1;
    const y0 = sparkY(pcts[i], h);
    const y1 = sparkY(pcts[i + 1], h);
    const midPct = (pcts[i] + pcts[i + 1]) / 2;
    ctx.strokeStyle = midPct >= 0 ? "#3fb950" : "#f85149";
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
  }
}

const PORT_COLUMNS = [
  {
    label: "Day",
    append(tr, h) {
      const td = document.createElement("td");
      const canvas = document.createElement("canvas");
      canvas.className = "spark";
      canvas.width = 72;
      canvas.height = 28;
      drawSpark(canvas, h.sparkline, h.previous_close);
      td.appendChild(canvas);
      tr.appendChild(td);
    },
  },
  {
    label: "Name",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td class="ticker">${h.name}</td>`);
    },
  },
  {
    label: "Qty",
    append(tr, _h, ctx) {
      tr.insertAdjacentHTML("beforeend", `<td>${ctx.qty}</td>`);
    },
  },
  {
    label: "% Port",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${h.pct_port.toFixed(1)}%</td>`);
    },
  },
  {
    label: "Open",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.day_open, h.loc)}</td>`);
    },
  },
  {
    label: "Mark",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.last, h.loc)}</td>`);
    },
  },
  {
    label: "vs Cost",
    append(tr, h) {
      tr.insertAdjacentHTML(
        "beforeend",
        `<td class="${plClass(h.price_diff)}">${fmtMoney(h.price_diff, h.loc)}</td>`
      );
    },
  },
  {
    label: "Day $",
    append(tr, h) {
      tr.insertAdjacentHTML(
        "beforeend",
        `<td class="${plClass(h.day_change)}">${fmtMoney(h.day_change, h.loc)}</td>`
      );
    },
  },
  {
    label: "Day %",
    append(tr, h) {
      tr.insertAdjacentHTML(
        "beforeend",
        `<td class="${plClass(h.day_change_pct)}">${fmtPct(h.day_change_pct)}</td>`
      );
    },
  },
  {
    label: "Day Hi",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.day_high, h.loc)}</td>`);
    },
  },
  {
    label: "Day Lo",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.day_low, h.loc)}</td>`);
    },
  },
  {
    label: "52W Hi",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.w52_high, h.loc)}</td>`);
    },
  },
  {
    label: "52W Lo",
    append(tr, h) {
      tr.insertAdjacentHTML("beforeend", `<td>${fmtMoney(h.w52_low, h.loc)}</td>`);
    },
  },
  {
    label: "P/L",
    append(tr, h) {
      tr.insertAdjacentHTML(
        "beforeend",
        `<td class="${plClass(h.pl_d)}">${fmtMoney(h.pl_d, h.loc)}</td>`
      );
    },
  },
  {
    label: "P/L %",
    append(tr, h) {
      tr.insertAdjacentHTML(
        "beforeend",
        `<td class="${plClass(h.pl_p)}">${fmtPct(h.pl_p)}</td>`
      );
    },
  },
];

function initPortTableHead() {
  const tr = document.querySelector("#port-table thead tr");
  if (!tr) return;
  tr.innerHTML = PORT_COLUMNS.map((c) => `<th>${c.label}</th>`).join("");
}

function fillSelect(el, values, selected) {
  el.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
  if (selected && values.includes(selected)) el.value = selected;
}

function fundsForOwner(owner) {
  if (owner === "All") return ["All", ...funds.map((f) => f.name)];
  return ["All", ...funds.filter((f) => f.owner === owner).map((f) => f.name)];
}

function applyOwnerScopeUI() {
  const admin = meta.is_admin;
  const defaultOwner = meta.owners[0];
  const ownerSelects = ["#port-owner", "#hm-owner", "#fund-owner", "#bal-owner", "#trends-owner", "#nw-owner"];
  ownerSelects.forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    fillSelect(el, meta.owners, el.value && meta.owners.includes(el.value) ? el.value : defaultOwner);
    if (!admin) {
      el.disabled = true;
      el.title = "Owner is fixed for your account";
    } else {
      el.disabled = false;
      el.title = "";
    }
  });
  syncFundSelects();
}

function syncFundSelects() {
  const owner = $("#port-owner").value;
  const names = fundsForOwner(owner);
  const prev = $("#port-fund").value;
  fillSelect($("#port-fund"), names, names.includes(prev) ? prev : "All");
  const tradeFunds = funds.map((f) => f.name);
  fillSelect($("#trade-fund"), tradeFunds, $("#trade-fund").value);
  const hmOwner = $("#hm-owner").value || meta.owners[0];
  fillSelect($("#hm-fund"), fundsForOwner(hmOwner), $("#hm-fund").value);
}

async function loadMeta() {
  meta = await api("/api/meta");
  funds = (await api("/api/funds")).funds;
  fillSelect($("#port-market"), meta.markets, "All");
  fillSelect($("#fund-currency"), meta.currencies);
  applyOwnerScopeUI();
}

let portfolioLoadId = 0;

async function loadPortfolio(live = false) {
  const loadId = ++portfolioLoadId;
  setStatus(live ? "Fetching live prices…" : "Loading portfolio…");
  const owner = $("#port-owner").value;
  const fund = $("#port-fund").value;
  const market = $("#port-market").value;
  let data;
  try {
    data = await api(
      `/api/portfolio?owner=${encodeURIComponent(owner)}&fund=${encodeURIComponent(fund)}&market=${encodeURIComponent(market)}&live=${live}`
    );
  } catch (e) {
    if (loadId === portfolioLoadId) setStatus(e.message);
    throw e;
  }
  if (loadId !== portfolioLoadId) return;
  $("#port-summary").textContent = data.summary;
  const tbody = $("#port-table tbody");
  tbody.innerHTML = "";
  data.holdings.forEach((h, i) => {
    const tr = document.createElement("tr");
    if (h.discrepancy) tr.classList.add("disc");
    else if (i % 2) tr.classList.add("stripe");
    const isOpt = h.instrument_type === "option";
    const qty = isOpt ? `${h.shares} ct` : String(h.shares);
    const ctx = { isOpt, qty, dash: "—" };
    for (const col of PORT_COLUMNS) col.append(tr, h, ctx);
    tbody.appendChild(tr);
  });
  setStatus(
    live
      ? `Live prices updated · ${new Date().toLocaleTimeString()}`
      : `Portfolio updated · ${new Date().toLocaleTimeString()}`
  );
}

/** Cached portfolio first, then live prices in the background (tab open). */
function openPortfolioTab() {
  loadPortfolio(false)
    .then(() => {
      if (!$("#panel-portfolio").classList.contains("active")) return;
      loadPortfolio(true);
    })
    .catch(() => {});
}

function syncTradeInstrumentUI() {
  const isOption = $("#trade-instrument").value === "option";
  $("#trade-option-fields").hidden = !isOption;
  $("#trade-qty-label").firstChild.textContent = isOption ? "Contracts " : "Shares ";
  $("#trade-price-label").firstChild.textContent = isOption ? "Premium " : "Price ";
  $("#trade-ticker-label").firstChild.textContent = isOption ? "Ticker / OCC " : "Ticker ";
}

async function loadTrades() {
  const fund = $("#trade-fund").value;
  if (!fund) return;
  const data = await api(`/api/funds/${encodeURIComponent(fund)}/trades`);
  const tbody = $("#trade-table tbody");
  tbody.innerHTML = data.trades
    .map(
      (t) =>
        `<tr><td>${t.id}</td><td>${t.trade_date}</td><td>${t.instrument_type}</td><td>${t.side}</td><td>${t.display_name || t.ticker}</td><td>${t.shares}</td><td>${t.price.toFixed(2)}</td><td>${t.fees.toFixed(2)}</td></tr>`
    )
    .join("");
}

async function loadLedger() {
  setStatus("Loading ledger…");
  const owner = $("#port-owner").value;
  const fund = $("#port-fund").value;
  const data = await api(
    `/api/ledger?owner=${encodeURIComponent(owner)}&fund=${encodeURIComponent(fund)}&live=false`
  );
  $("#ledger-summary").textContent = data.summaries.join(" · ");
  $("#ledger-table tbody").innerHTML = data.positions
    .map(
      (p) =>
        `<tr><td class="ticker">${p.display_name || p.ticker}</td><td>${p.shares}</td><td>${fmtMoney(p.avg_cost)}</td><td>${fmtMoney(p.mark_price)}</td><td>${fmtMoney(p.market_value)}</td><td class="${plClass(p.unrealized_pl)}">${fmtMoney(p.unrealized_pl)}</td><td>${p.realized_pl != null ? fmtMoney(p.realized_pl) : "—"}</td></tr>`
    )
    .join("");
  $("#disc-table tbody").innerHTML = data.discrepancies.length
    ? data.discrepancies
        .map(
          (d) =>
            `<tr class="disc"><td>${d.ticker}</td><td>${d.ledger_shares}</td><td>${d.sheet_shares}</td><td>${d.delta}</td><td>${d.scope}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="5">No discrepancies</td></tr>`;
  setStatus("Ledger updated");
}

function heatColor(pct) {
  const clamped = Math.max(-5, Math.min(5, pct || 0));
  if (clamped >= 0) {
    const t = clamped / 5;
    const r = Math.round(35 + (35 - 35) * t);
    const g = Math.round(134 + (198 - 134) * t);
    return `rgb(${r},${g},54)`;
  }
  const t = -clamped / 5;
  return `rgb(${Math.round(218 - 50 * t)},${Math.round(54 + 30 * t)},${Math.round(51 + 20 * t)})`;
}

async function loadHeatmap() {
  setStatus("Loading heat map…");
  const owner = $("#hm-owner").value;
  const fund = $("#hm-fund").value;
  const color = $("#hm-color").value;
  const data = await api(
    `/api/heatmap?owner=${encodeURIComponent(owner)}&fund=${encodeURIComponent(fund)}&color_by=${color}&live=false`
  );
  $("#hm-summary").textContent = `${data.tiles.length} tile(s) · total $${Math.round(data.total_usd).toLocaleString()} USD`;
  const root = $("#heatmap");
  root.innerHTML = "";
  const maxVal = Math.max(...data.tiles.map((t) => t.value), 1);
  data.tiles.forEach((t) => {
    const el = document.createElement("div");
    el.className = "heat-tile";
    const flex = Math.max(1, Math.round((t.value / maxVal) * 8));
    el.style.flex = `${flex} 1 120px`;
    el.style.background = heatColor(t.pl_pct);
    el.innerHTML = `<strong>${t.ticker}</strong><br>${t.fund_name}<br>${fmtPct(t.pl_pct)}`;
    root.appendChild(el);
  });
  setStatus("Heat map updated");
}

async function loadMovers() {
  setStatus("Loading market movers…");
  const market = $("#movers-market").value;
  const data = await api(`/api/market-movers?market=${encodeURIComponent(market)}`);
  const row = (m) =>
    `<tr><td>${m.rank}</td><td class="ticker">${m.symbol}</td><td>${m.name}</td><td>${fmtMoney(m.price)}</td><td class="${plClass(m.change_pct)}">${fmtPct(m.change_pct)}</td></tr>`;
  if (data.error) {
    const msg = `<tr><td colspan="5">Market movers unavailable (blocked network access to the quote provider)</td></tr>`;
    $("#gainers-table tbody").innerHTML = msg;
    $("#losers-table tbody").innerHTML = msg;
    setStatus("Market movers unavailable");
    return;
  }
  $("#gainers-table tbody").innerHTML = data.gainers.map(row).join("");
  $("#losers-table tbody").innerHTML = data.losers.map(row).join("");
  setStatus("Market movers updated");
}

async function loadFundBalances() {
  setStatus("Loading fund balances…");
  const owner = $("#bal-owner").value;
  const data = await api(`/api/fund-balances?owner=${encodeURIComponent(owner)}`);
  
  const rows = data.balances.map((b) =>
    `<tr><td>${b.fund_name}</td><td>${b.currency}</td><td>${fmtMoney(b.amount, b.currency)}</td><td>${fmtMoney(b.amount_usd)}</td><td>${b.as_of}</td><td>${b.source}</td></tr>`
  ).join("");
  $("#bal-table tbody").innerHTML = rows;
  
  const total = data.total_usd;
  $("#bal-summary").textContent = `Total fund value: ${fmtMoney(total)} · Funds: ${data.balances.length}`;
  
  // Sync manual balance fund select
  const fundSelect = $("#manual-fund");
  fundSelect.innerHTML = data.balances.map((b) => `<option value="${b.fund_name}">${b.fund_name}</option>`).join("");
  setStatus("Fund balances updated");
}

let trendsRawSeries = [];
let trendsLastKey = null;

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function trendsDataBounds(series) {
  const times = series.flatMap((s) => s.dates.map((d) => new Date(d).getTime()));
  if (!times.length) return null;
  return { min: new Date(Math.min(...times)), max: new Date(Math.max(...times)) };
}

// Mirrors the desktop app's RangeSlider default: last 365 days, or the full
// range if there's less data than that.
function setTrendsRangeDefault(bounds) {
  let fromD = new Date(bounds.max.getTime() - 365 * 24 * 3600 * 1000);
  if (fromD < bounds.min) fromD = bounds.min;
  $("#trends-from").value = isoDate(fromD);
  $("#trends-to").value = isoDate(bounds.max);
}

function setTrendsRangeBounds(bounds) {
  $("#trends-from").min = isoDate(bounds.min);
  $("#trends-from").max = isoDate(bounds.max);
  $("#trends-to").min = isoDate(bounds.min);
  $("#trends-to").max = isoDate(bounds.max);
}

function filterSeriesByRange(series, fromStr, toStr) {
  const fromT = fromStr ? new Date(fromStr).getTime() : -Infinity;
  const toT = toStr ? new Date(toStr).getTime() + 24 * 3600 * 1000 - 1 : Infinity; // inclusive end of day
  return series
    .map((s) => {
      const idxs = s.dates
        .map((_, i) => i)
        .filter((i) => {
          const t = new Date(s.dates[i]).getTime();
          return t >= fromT && t <= toT;
        });
      return {
        ...s,
        dates: idxs.map((i) => s.dates[i]),
        nav_local: idxs.map((i) => s.nav_local[i]),
        nav_usd: idxs.map((i) => s.nav_usd[i]),
      };
    })
    .filter((s) => s.dates.length);
}

function redrawTrendsFromCache() {
  const from = $("#trends-from").value;
  const to = $("#trends-to").value;
  const filtered = filterSeriesByRange(trendsRawSeries, from, to);
  if (!filtered.length) {
    $("#trends-chart").style.display = "none";
    $("#trends-no-data").style.display = "block";
    $("#trends-summary").textContent = "No trend data in the selected date range";
    return;
  }
  $("#trends-no-data").style.display = "none";
  $("#trends-chart").style.display = "block";
  const summary = filtered.map((s) => `${s.fund_name} (${s.owner})`).join(", ");
  const rangeLabel = from && to ? ` — ${from} to ${to}` : "";
  $("#trends-summary").textContent = `Showing trends for: ${summary}${rangeLabel}`;
  drawFundTrendsChart(filtered);
}

async function loadFundTrends() {
  setStatus("Loading fund trends…");
  const owner = $("#trends-owner").value;
  const fund = $("#trends-fund").value || null;
  const key = `${owner}||${fund || ""}`;
  const data = await api(`/api/fund-trends?owner=${encodeURIComponent(owner)}${fund ? `&fund=${encodeURIComponent(fund)}` : ""}`);

  trendsRawSeries = data.series || [];
  if (!trendsRawSeries.length) {
    $("#trends-chart").style.display = "none";
    $("#trends-no-data").style.display = "block";
    $("#trends-summary").textContent = "No trend data available for selected fund";
    setStatus("No trend data");
    return;
  }

  const bounds = trendsDataBounds(trendsRawSeries);
  setTrendsRangeBounds(bounds);
  // Reset to the default window when switching owner/fund; otherwise keep
  // whatever range the user picked across a plain Refresh click.
  if (key !== trendsLastKey || !$("#trends-from").value || !$("#trends-to").value) {
    setTrendsRangeDefault(bounds);
  }
  trendsLastKey = key;

  redrawTrendsFromCache();
  setStatus("Fund trends updated");
}

// Plain dollar amount, no forced "+" sign (fmtMoney adds one, meant for P&L deltas).
function fmtDollar(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Matches desktop's fund_balance_chart._format_axis_amount so both apps read the same way.
function fmtAxisMoney(value) {
  const sign = value < 0 ? "-" : "";
  const amount = Math.abs(value);
  if (amount >= 1_000_000) return `${sign}$${(amount / 1_000_000).toFixed(1)}M`;
  if (amount >= 10_000) return `${sign}$${Math.round(amount / 1_000)}K`;
  if (amount >= 1_000) return `${sign}$${(amount / 1_000).toFixed(1)}K`;
  return `${sign}$${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

// "Nice" round step for axis ticks (1/2/5 * power of 10), like matplotlib's locator.
function niceStep(roughStep) {
  const mag = Math.pow(10, Math.floor(Math.log10(roughStep || 1)));
  const norm = roughStep / mag;
  const step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
  return step * mag;
}

const TRENDS_LINE_COLORS = [
  "#4a7fd4", "#3fb950", "#f85149", "#d2a8ff", "#ffa657", "#79c0ff", "#ff7b72", "#a5d6ff",
];

function drawFundTrendsChart(series) {
  const container = $("#trends-chart");
  container.innerHTML = "";

  const usable = series.filter((s) => s.dates.length && s.nav_usd.length);

  const width = container.offsetWidth || 800;
  const height = 400;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  if (!usable.length) {
    container.appendChild(svg);
    return;
  }

  const padLeft = 68;
  const padRight = 16;
  const padTop = 16;
  const padBottom = 40;
  const chartWidth = width - padLeft - padRight;
  const chartHeight = height - padTop - padBottom;

  // Real time scale (shared across series) instead of index position, so funds
  // with different numbers of data points still line up by actual date.
  const allTimes = usable.flatMap((s) => s.dates.map((d) => new Date(d).getTime()));
  const allValues = usable.flatMap((s) => s.nav_usd);
  const minTime = Math.min(...allTimes);
  const maxTime = Math.max(...allTimes);
  const timeRange = maxTime - minTime || 1;

  let minValue = Math.min(0, Math.min(...allValues));
  let maxValue = Math.max(...allValues);
  if (maxValue === minValue) maxValue = minValue + 1;
  const valuePad = (maxValue - minValue) * 0.06;
  minValue -= valuePad;
  maxValue += valuePad;
  const valueRange = maxValue - minValue || 1;

  const xForTime = (t) => padLeft + ((t - minTime) / timeRange) * chartWidth;
  const yForValue = (v) => padTop + chartHeight - ((v - minValue) / valueRange) * chartHeight;

  const svgNS = "http://www.w3.org/2000/svg";
  const gridGroup = document.createElementNS(svgNS, "g");
  svg.appendChild(gridGroup);

  // Y-axis gridlines + $ labels (K/M formatted, like the desktop chart's axis).
  const step = niceStep((maxValue - minValue) / 6);
  const firstTick = Math.ceil(minValue / step) * step;
  for (let v = firstTick; v <= maxValue; v += step) {
    const y = yForValue(v);
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", padLeft);
    line.setAttribute("y1", y);
    line.setAttribute("x2", width - padRight);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "var(--border)");
    line.setAttribute("stroke-width", "1");
    line.setAttribute("opacity", "0.5");
    gridGroup.appendChild(line);

    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", padLeft - 8);
    label.setAttribute("y", y + 4);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("font-size", "11");
    label.setAttribute("fill", "var(--muted)");
    label.textContent = fmtAxisMoney(v);
    gridGroup.appendChild(label);
  }

  // X-axis date ticks, evenly spaced across the real date range.
  const tickCount = Math.min(6, usable.reduce((n, s) => Math.max(n, s.dates.length), 2));
  for (let i = 0; i < tickCount; i++) {
    const t = minTime + (i / (tickCount - 1 || 1)) * timeRange;
    const x = xForTime(t);
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", x);
    line.setAttribute("y1", padTop);
    line.setAttribute("x2", x);
    line.setAttribute("y2", height - padBottom);
    line.setAttribute("stroke", "var(--border)");
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "3,3");
    line.setAttribute("opacity", "0.4");
    gridGroup.appendChild(line);

    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", x);
    label.setAttribute("y", height - padBottom + 16);
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("font-size", "11");
    label.setAttribute("fill", "var(--muted)");
    label.textContent = new Date(t).toLocaleDateString(undefined, {
      month: "short",
      year: "numeric",
    });
    gridGroup.appendChild(label);
  }

  // Axis lines on top of the gridlines.
  const xAxis = document.createElementNS(svgNS, "line");
  xAxis.setAttribute("x1", padLeft);
  xAxis.setAttribute("y1", height - padBottom);
  xAxis.setAttribute("x2", width - padRight);
  xAxis.setAttribute("y2", height - padBottom);
  xAxis.setAttribute("stroke", "var(--muted)");
  svg.appendChild(xAxis);

  const yAxis = document.createElementNS(svgNS, "line");
  yAxis.setAttribute("x1", padLeft);
  yAxis.setAttribute("y1", padTop);
  yAxis.setAttribute("x2", padLeft);
  yAxis.setAttribute("y2", height - padBottom);
  yAxis.setAttribute("stroke", "var(--muted)");
  svg.appendChild(yAxis);

  // Y-axis title.
  const yTitle = document.createElementNS(svgNS, "text");
  yTitle.setAttribute("x", 14);
  yTitle.setAttribute("y", padTop - 2);
  yTitle.setAttribute("font-size", "11");
  yTitle.setAttribute("fill", "var(--muted)");
  yTitle.textContent = "Fund value (NAV), USD";
  svg.appendChild(yTitle);

  // Series lines + point markers.
  usable.forEach((s, idx) => {
    const color = TRENDS_LINE_COLORS[idx % TRENDS_LINE_COLORS.length];
    const pts = s.dates.map((d, i) => ({
      x: xForTime(new Date(d).getTime()),
      y: yForValue(s.nav_usd[i]),
    }));

    const polyline = document.createElementNS(svgNS, "polyline");
    polyline.setAttribute("points", pts.map((p) => `${p.x},${p.y}`).join(" "));
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", color);
    polyline.setAttribute("stroke-width", "2");
    svg.appendChild(polyline);

    pts.forEach((p, i) => {
      const dot = document.createElementNS(svgNS, "circle");
      dot.setAttribute("cx", p.x);
      dot.setAttribute("cy", p.y);
      dot.setAttribute("r", pts.length <= 12 ? 3 : 1.8);
      dot.setAttribute("fill", color);
      const title = document.createElementNS(svgNS, "title");
      title.textContent = `${s.fund_name}: ${fmtDollar(s.nav_usd[i])} on ${s.dates[i]}`;
      dot.appendChild(title);
      svg.appendChild(dot);
    });
  });

  container.appendChild(svg);

  // HTML legend below the chart (more robust than SVG text for many funds / narrow widths).
  const legend = document.createElement("div");
  legend.className = "trends-legend";
  legend.innerHTML = usable
    .map((s, idx) => {
      const color = TRENDS_LINE_COLORS[idx % TRENDS_LINE_COLORS.length];
      const latest = s.nav_usd[s.nav_usd.length - 1];
      return `<span class="trends-legend-item"><span class="trends-swatch" style="background:${color}"></span>${s.fund_name} (${s.owner}) — ${fmtDollar(latest)}</span>`;
    })
    .join("");
  container.appendChild(legend);
}

async function loadNetWorth() {
  setStatus("Loading net worth…");
  const owner = $("#nw-owner").value;
  const data = await api(`/api/net-worth?owner=${encodeURIComponent(owner)}`);
  
  const rows = data.lines.map((line) =>
    `<tr><td>${line.label}</td><td>${line.currency}</td><td>${fmtMoney(line.amount_local, line.currency)}</td><td>${fmtMoney(line.amount_usd)}</td><td>${line.source}</td></tr>`
  ).join("");
  $("#nw-table tbody").innerHTML = rows;
  
  $("#nw-summary").textContent = `Total net worth: ${fmtMoney(data.total_usd)} · FX rates: ${Object.entries(data.fx_rates).map(([c, r]) => `${c}=${r.toFixed(2)}`).join(", ")}`;
  
  // Load other assets
  await loadOtherAssets();
  setStatus("Net worth updated");
}

async function loadOtherAssets() {
  const owner = $("#nw-owner").value;
  const data = await api(`/api/other-assets?owner=${encodeURIComponent(owner)}`);
  
  const rows = data.assets.map((a) =>
    `<tr><td>${a.asset_type}</td><td>${fmtMoney(a.amount, a.currency)}</td><td>${a.currency}</td><td><button class="ghost" onclick="deleteOtherAsset('${a.asset_type}')">Delete</button></td></tr>`
  ).join("");
  $("#assets-table tbody").innerHTML = rows;
}

async function deleteOtherAsset(assetType) {
  if (!confirm("Delete this asset?")) return;
  try {
    const owner = $("#nw-owner").value;
    await api(`/api/other-assets/${encodeURIComponent(assetType)}?owner=${encodeURIComponent(owner)}`, { method: "DELETE" });
    await loadNetWorth();
    setStatus("Asset deleted");
  } catch (e) {
    setStatus("Error: " + e.message);
  }
}

function setupTabs() {
  $("#tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#panel-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "portfolio") openPortfolioTab();
    if (btn.dataset.tab === "ledger") loadLedger();
    if (btn.dataset.tab === "heatmap") loadHeatmap();
    if (btn.dataset.tab === "movers") loadMovers();
    if (btn.dataset.tab === "trades") loadTrades();
    if (btn.dataset.tab === "balances") loadFundBalances();
    if (btn.dataset.tab === "trends") loadFundTrends();
    if (btn.dataset.tab === "net-worth") loadNetWorth();
  });
}

function setupHandlers() {
  $("#port-owner").addEventListener("change", () => {
    syncFundSelects();
    loadPortfolio(false);
  });
  $("#port-fund").addEventListener("change", () => loadPortfolio(false));
  $("#port-market").addEventListener("change", () => loadPortfolio(false));
  $("#port-refresh").addEventListener("click", () => loadPortfolio(false));
  $("#port-live").addEventListener("click", () => loadPortfolio(true));
  $("#ledger-refresh").addEventListener("click", () => loadLedger());
  $("#hm-refresh").addEventListener("click", () => loadHeatmap());
  $("#hm-owner").addEventListener("change", syncFundSelects);
  $("#movers-refresh").addEventListener("click", () => loadMovers());
  $("#trade-fund").addEventListener("change", loadTrades);
  $("#trade-instrument").addEventListener("change", syncTradeInstrumentUI);
  syncTradeInstrumentUI();

  $("#fund-create").addEventListener("click", async () => {
    try {
      await api("/api/funds", {
        method: "POST",
        body: JSON.stringify({
          name: $("#fund-name").value.trim(),
          owner: $("#fund-owner").value,
          opening_cash: parseFloat($("#fund-cash").value) || 0,
          currency: $("#fund-currency").value,
        }),
      });
      await loadMeta();
      setStatus("Fund created");
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("#trade-save").addEventListener("click", async () => {
    try {
      const dateVal = $("#trade-date").value;
      const instrument = $("#trade-instrument").value;
      const body = {
        fund_name: $("#trade-fund").value,
        ticker: $("#trade-ticker").value.trim(),
        side: $("#trade-side").value,
        shares: parseFloat($("#trade-shares").value),
        price: parseFloat($("#trade-price").value),
        trade_date: dateVal || null,
        fees: parseFloat($("#trade-fees").value) || 0,
        notes: $("#trade-notes").value,
        instrument_type: instrument,
      };
      if (instrument === "option") {
        body.expiry = $("#trade-expiry").value || null;
        body.strike = $("#trade-strike").value ? parseFloat($("#trade-strike").value) : null;
        body.put_call = $("#trade-put-call").value;
      }
      await api("/api/trades", {
        method: "POST",
        body: JSON.stringify(body),
      });
      await loadTrades();
      setStatus("Trade saved");
    } catch (e) {
      setStatus(e.message);
    }
  });

  // Fund balances tab
  $("#bal-owner").addEventListener("change", () => loadFundBalances());
  $("#bal-refresh").addEventListener("click", () => loadFundBalances());
  $("#manual-save").addEventListener("click", async () => {
    try {
      const fund = $("#manual-fund").value;
      const nav = parseFloat($("#manual-nav").value);
      const date = $("#manual-date").value || new Date().toISOString().split("T")[0];
      if (!fund || !nav) {
        setStatus("Please fill in fund and NAV");
        return;
      }
      await api(`/api/funds/${encodeURIComponent(fund)}/balance`, {
        method: "PUT",
        body: JSON.stringify({
          nav: nav,
          balance_date: date,
        }),
      });
      await loadFundBalances();
      setStatus("Balance updated");
    } catch (e) {
      setStatus(e.message);
    }
  });

  // Fund trends tab
  $("#trends-owner").addEventListener("change", () => loadFundTrends());
  $("#trends-fund").addEventListener("change", () => loadFundTrends());
  $("#trends-refresh").addEventListener("click", () => loadFundTrends());
  $("#trends-from").addEventListener("change", () => redrawTrendsFromCache());
  $("#trends-to").addEventListener("change", () => redrawTrendsFromCache());
  $("#trends-full-range").addEventListener("click", () => {
    const bounds = trendsDataBounds(trendsRawSeries);
    if (!bounds) return;
    $("#trends-from").value = isoDate(bounds.min);
    $("#trends-to").value = isoDate(bounds.max);
    redrawTrendsFromCache();
  });

  // Net worth tab
  $("#nw-owner").addEventListener("change", () => loadNetWorth());
  $("#nw-refresh").addEventListener("click", () => loadNetWorth());
  
  // Other assets
  $("#asset-add").addEventListener("click", async () => {
    try {
      const type = $("#asset-type").value.trim();
      const amount = parseFloat($("#asset-amount").value);
      const currency = $("#asset-currency").value;
      if (!type || !amount) {
        setStatus("Please fill in type and amount");
        return;
      }
      const owner = $("#nw-owner").value;
      await api(`/api/other-assets?owner=${encodeURIComponent(owner)}`, {
        method: "POST",
        body: JSON.stringify({
          asset_type: type,
          amount: amount,
          currency: currency,
        }),
      });
      $("#asset-type").value = "";
      $("#asset-amount").value = "";
      await loadNetWorth();
      setStatus("Asset added");
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("#login-submit").addEventListener("click", () => login());
  $("#login-pass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") login();
  });
  $("#logout-btn").addEventListener("click", () => logout());
  $("#change-pwd-btn").addEventListener("click", () => showChangePassword());
  $("#pwd-cancel").addEventListener("click", () => hideChangePassword());
  $("#pwd-submit").addEventListener("click", () => changePassword());
  $("#pwd-confirm").addEventListener("keydown", (e) => {
    if (e.key === "Enter") changePassword();
  });

  $("#forgot-open").addEventListener("click", () => showForgot());
  $("#forgot-submit").addEventListener("click", () => submitForgot());
  $("#forgot-cancel").addEventListener("click", () => closeDialog("#forgot-overlay"));
  $("#forgot-email").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitForgot();
  });
  $("#reset-submit").addEventListener("click", () => submitReset());
  $("#reset-cancel").addEventListener("click", () => {
    resetToken = "";
    closeDialog("#reset-overlay");
  });
  $("#reset-confirm").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitReset();
  });
  $("#account-btn").addEventListener("click", () => showAccount());
  $("#account-save").addEventListener("click", () => saveAccount());
  $("#account-cancel").addEventListener("click", () => closeDialog("#account-overlay"));
}

function openDialog(sel) {
  $(sel).classList.add("show");
}

function closeDialog(sel) {
  $(sel).classList.remove("show");
}

function showMessage(sel, text) {
  const el = $(sel);
  el.textContent = text;
  el.hidden = !text;
}

// Forgot password -----------------------------------------------------------

function showForgot() {
  $("#forgot-email").value = "";
  showMessage("#forgot-error", "");
  showMessage("#forgot-success", "");
  openDialog("#forgot-overlay");
}

async function submitForgot() {
  showMessage("#forgot-error", "");
  showMessage("#forgot-success", "");
  const email = $("#forgot-email").value.trim();
  if (!email) {
    showMessage("#forgot-error", "Enter your email address.");
    return;
  }
  try {
    const data = await api("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    showMessage("#forgot-success", data.message);
  } catch (e) {
    showMessage("#forgot-error", e.message);
  }
}

// Reset password (opened from the emailed link: /#reset=<token>) ------------

let resetToken = "";

function openResetFromLink() {
  // The token lives in the fragment so it never reaches the server; clear it from the address bar.
  const m = location.hash.match(/^#reset=([A-Za-z0-9_-]{10,200})$/);
  if (!m) return;
  resetToken = m[1];
  history.replaceState(null, "", location.pathname + location.search);
  $("#reset-new").value = "";
  $("#reset-confirm").value = "";
  showMessage("#reset-error", "");
  showMessage("#reset-success", "");
  openDialog("#reset-overlay");
}

async function submitReset() {
  showMessage("#reset-error", "");
  const pw = $("#reset-new").value;
  const confirm = $("#reset-confirm").value;
  if (pw.length < 8) {
    showMessage("#reset-error", "New password must be at least 8 characters.");
    return;
  }
  if (pw !== confirm) {
    showMessage("#reset-error", "New password and confirmation don't match.");
    return;
  }
  try {
    await api("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token: resetToken, new_password: pw }),
    });
    resetToken = "";
    closeDialog("#reset-overlay");
    showLogin();
    setStatus("Password reset. Sign in with your new password.");
  } catch (e) {
    showMessage("#reset-error", e.message);
  }
}

// Account (profile + email for resets) --------------------------------------

async function showAccount() {
  try {
    const p = await api("/api/auth/profile");
    $("#account-username").textContent = `Username: ${p.username}`;
    $("#account-name").value = p.display_name || "";
    $("#account-email").value = p.email || "";
    $("#account-password").value = "";
    showMessage("#account-error", "");
    showMessage("#account-success", "");
    openDialog("#account-overlay");
  } catch (e) {
    setStatus(e.message);
  }
}

async function saveAccount() {
  showMessage("#account-error", "");
  showMessage("#account-success", "");
  const password = $("#account-password").value;
  try {
    const p = await api("/api/auth/profile", {
      method: "PUT",
      body: JSON.stringify({
        display_name: $("#account-name").value.trim(),
        email: $("#account-email").value.trim(),
        current_password: password || null,
      }),
    });
    $("#account-email").value = p.email || "";
    $("#account-password").value = "";
    showMessage("#account-success", "Saved.");
  } catch (e) {
    showMessage("#account-error", e.message);
  }
}

async function login() {
  const username = $("#login-user").value.trim();
  const password = $("#login-pass").value;
  if (!username || !password) {
    showLogin("Enter username and password.");
    return;
  }
  try {
    currentUser = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    $("#login-pass").value = "";
    hideLogin();
    $("#logout-btn").hidden = false;
    $("#change-pwd-btn").hidden = false;
    $("#account-btn").hidden = false;
    $("#user-label").textContent = currentUser.is_admin
      ? `Signed in as ${currentUser.username} (all owners)`
      : `Signed in as ${currentUser.username}`;
    await boot();
  } catch (e) {
    showLogin(e.message === "Unauthorized" ? "Invalid username or password." : e.message);
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) {
    /* ignore */
  }
  currentUser = null;
  showLogin();
  setStatus("Signed out");
}

async function boot() {
  openResetFromLink();
  try {
    currentUser = await api("/api/auth/me");
    hideLogin();
    $("#logout-btn").hidden = false;
    $("#change-pwd-btn").hidden = false;
    $("#user-label").textContent = currentUser.is_admin
      ? `Signed in as ${currentUser.username} (all owners)`
      : `Signed in as ${currentUser.username}`;
    await loadMeta();
    openPortfolioTab();
    if ($("#trade-fund").value) await loadTrades();
  } catch (e) {
    if (e.message !== "Unauthorized") setStatus(e.message);
    else showLogin();
  }
}

setupTabs();
initPortTableHead();
setupHandlers();
boot();
