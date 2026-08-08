/* Stock Machine dashboard. Vanilla JS, hand-rolled SVG charts.
   Display-only: every analytical number comes precomputed from the API. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const COLORS = { blue: "#3987e5", orange: "#d95926", aqua: "#199e70" };
const state = { companies: [], view: "home", ticker: null };

/* ---------------- formatting ---------------- */
const fmtMoney = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return "$" + (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  return "$" + v.toFixed(2);
};
const fmtNum = (v, d = 1) => (v == null ? "—" : v.toFixed(d));
const fmtPct = (v, d = 1) => (v == null ? "—" : v.toFixed(d) + "%");
const fmtSignedPct = (v) =>
  v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

/* ---------------- tooltip ---------------- */
const tip = $("#tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(x + 14, window.innerWidth - r.width - 10) + "px";
  tip.style.top = Math.max(8, y - r.height - 12) + "px";
}
const hideTip = () => (tip.hidden = true);

/* ---------------- chart engine ---------------- */
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}
function niceScale(min, max) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span / 3)));
  const err = span / 3 / step;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  const s = step * mult;
  const lo = Math.floor(min / s) * s;
  const hi = Math.ceil(max / s) * s;
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += s) ticks.push(v);
  return { lo, hi, ticks };
}
const W = 520, H = 190, PAD = { t: 10, r: 12, b: 22, l: 46 };

function frame(container, title) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  if (title) {
    const h = document.createElement("h3");
    h.textContent = title;
    wrap.appendChild(h);
  }
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  wrap.appendChild(svg);
  container.appendChild(wrap);
  return { wrap, svg };
}
function grid(svg, scale, fmt) {
  for (const t of scale.ticks) {
    const y = yPos(t, scale);
    svg.appendChild(svgEl("line", {
      x1: PAD.l, x2: W - PAD.r, y1: y, y2: y,
      stroke: "#32322f", "stroke-width": 1,
    }));
    const lbl = svgEl("text", {
      x: PAD.l - 6, y: y + 3, "text-anchor": "end",
      fill: "#8a897f", "font-size": 10, "font-family": "ui-monospace, monospace",
    });
    lbl.textContent = fmt(t);
    svg.appendChild(lbl);
  }
}
const yPos = (v, s) =>
  PAD.t + (H - PAD.t - PAD.b) * (1 - (v - s.lo) / (s.hi - s.lo || 1));

function xLabels(svg, labels, xAt, every) {
  labels.forEach((l, i) => {
    if (i % every !== 0) return;
    const t = svgEl("text", {
      x: xAt(i), y: H - 6, "text-anchor": "middle",
      fill: "#8a897f", "font-size": 9.5, "font-family": "ui-monospace, monospace",
    });
    t.textContent = l;
    svg.appendChild(t);
  });
}

/* Bar chart: rounded data-end anchored to baseline, 2px surface gaps, hover. */
function barChart(container, { title, labels, values, fmt, color = COLORS.blue }) {
  const { svg } = frame(container, title);
  const present = values.filter((v) => v != null);
  if (!present.length) return;
  const scale = niceScale(Math.min(0, ...present), Math.max(0, ...present));
  grid(svg, scale, fmt);
  const n = values.length;
  const innerW = W - PAD.l - PAD.r;
  const bw = Math.max(3, innerW / n - 2);
  const y0 = yPos(Math.max(scale.lo, 0), scale);
  values.forEach((v, i) => {
    if (v == null) return;
    const x = PAD.l + (innerW / n) * i + 1;
    const y = yPos(v, scale);
    const top = Math.min(y, y0), hgt = Math.max(2, Math.abs(y0 - y));
    const r = Math.min(4, bw / 2);
    // rounded at the data end only, flat at the baseline
    const d = v >= 0
      ? `M${x},${top + hgt} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${top + hgt} Z`
      : `M${x},${top} V${top + hgt - r} Q${x},${top + hgt} ${x + r},${top + hgt} H${x + bw - r} Q${x + bw},${top + hgt} ${x + bw},${top + hgt - r} V${top} Z`;
    const bar = svgEl("path", { d, fill: color });
    const hit = svgEl("rect", {
      x: x - 1, y: PAD.t, width: bw + 2, height: H - PAD.t - PAD.b,
      fill: "transparent",
    });
    hit.addEventListener("mousemove", (e) =>
      showTip(`<div class="t">${labels[i]}</div>
               <div class="row"><span class="sw" style="background:${color}"></span>${fmt(v)}</div>`,
        e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(bar);
    svg.appendChild(hit);
  });
  xLabels(svg, labels, (i) => PAD.l + (innerW / n) * i + bw / 2, Math.ceil(n / 6));
}

/* Line chart: 2px lines, crosshair + shared tooltip, direct label at last point. */
function lineChart(container, { title, labels, series, fmt }) {
  const { wrap, svg } = frame(container, title);
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  if (!all.length) return;
  const scale = niceScale(Math.min(...all), Math.max(...all));
  grid(svg, scale, fmt);
  const n = labels.length;
  const innerW = W - PAD.l - PAD.r;
  const xAt = (i) => PAD.l + (n === 1 ? innerW / 2 : (innerW / (n - 1)) * i);
  for (const s of series) {
    let d = "";
    s.values.forEach((v, i) => {
      if (v == null) return;
      d += (d ? "L" : "M") + xAt(i).toFixed(1) + "," + yPos(v, scale).toFixed(1);
    });
    svg.appendChild(svgEl("path", {
      d, fill: "none", stroke: s.color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
    const lastIdx = s.values.length - 1 - [...s.values].reverse().findIndex((v) => v != null);
    const lv = s.values[lastIdx];
    if (lv != null) {
      const t = svgEl("text", {
        x: Math.min(xAt(lastIdx) + 4, W - 2), y: yPos(lv, scale) - 5,
        fill: "#c3c2b7", "font-size": 9.5, "text-anchor": "end",
        "font-family": "ui-monospace, monospace",
      });
      t.textContent = fmt(lv);
      svg.appendChild(t);
    }
  }
  xLabels(svg, labels, xAt, Math.ceil(n / 6));
  // crosshair
  const cross = svgEl("line", {
    y1: PAD.t, y2: H - PAD.b, stroke: "#52514e", "stroke-width": 1, "stroke-dasharray": "3,3",
  });
  cross.style.display = "none";
  svg.appendChild(cross);
  const overlay = svgEl("rect", {
    x: PAD.l, y: PAD.t, width: innerW, height: H - PAD.t - PAD.b, fill: "transparent",
  });
  overlay.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - PAD.l) / innerW) * (n - 1));
    if (i < 0 || i >= n) return;
    cross.setAttribute("x1", xAt(i)); cross.setAttribute("x2", xAt(i));
    cross.style.display = "";
    const rows = series
      .filter((s) => s.values[i] != null)
      .map((s) => `<div class="row"><span class="sw" style="background:${s.color}"></span>${s.name}: ${fmt(s.values[i])}</div>`)
      .join("");
    showTip(`<div class="t">${labels[i]}</div>${rows}`, e.clientX, e.clientY);
  });
  overlay.addEventListener("mouseleave", () => { cross.style.display = "none"; hideTip(); });
  svg.appendChild(overlay);
  if (series.length > 1) {
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = series
      .map((s) => `<span class="li"><span class="sw" style="background:${s.color}"></span>${s.name}</span>`)
      .join("");
    wrap.appendChild(legend);
  }
}

/* ---------------- data ---------------- */
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

/* ---------------- sidebar ---------------- */
function renderSidebar() {
  const nav = $("#nav-companies");
  nav.innerHTML = "";
  const home = document.createElement("div");
  home.className = "nav-item nav-home" + (state.view === "home" ? " active" : "");
  home.innerHTML = `<span class="tk">Coverage</span><span class="score-pill">${state.companies.length}</span>`;
  home.onclick = () => go("home");
  nav.appendChild(home);
  const pf = document.createElement("div");
  pf.className = "nav-item nav-home" + (state.view === "portfolio" ? " active" : "");
  pf.innerHTML = `<span class="tk">Paper portfolio</span><span class="score-pill">L/S</span>`;
  pf.onclick = () => go("portfolio");
  nav.appendChild(pf);
  const pred = document.createElement("div");
  pred.className = "nav-item nav-home" + (state.view === "predict" ? " active" : "");
  pred.innerHTML = `<span class="tk">Prediction lab</span><span class="score-pill">MC</span>`;
  pred.onclick = () => go("predict");
  nav.appendChild(pred);
  const sys = document.createElement("div");
  sys.className = "nav-item nav-home" + (state.view === "system" ? " active" : "");
  sys.innerHTML = `<span class="tk">System</span><span class="score-pill">KPI</span>`;
  sys.onclick = () => go("system");
  nav.appendChild(sys);
  for (const c of state.companies) {
    const el = document.createElement("div");
    el.className = "nav-item" + (state.ticker === c.ticker && state.view === "stock" ? " active" : "");
    el.innerHTML = `<span><div class="tk">${c.ticker}</div><div class="nm">${c.legal_name || ""}</div></span>
      <span class="score-pill" title="Fundamental quality score (0–100, sector-adjusted). Describes filed fundamentals — NOT predictive of returns (failed its backtest kill criterion). For opportunity, use the Signals column.">${fmtNum(c.composite_score, 0)}</span>`;
    el.onclick = () => go("stock", c.ticker);
    nav.appendChild(el);
  }
}

function go(view, ticker = null) {
  state.view = view;
  state.ticker = ticker;
  renderSidebar();
  if (view === "home") renderHome();
  else if (view === "system") renderSystem();
  else if (view === "portfolio") renderPortfolio();
  else if (view === "predict") renderPredict(ticker);
  else renderStock(ticker);
}

/* ---------------- prediction lab (fan chart + probabilities) ---------------- */
function fanChart(container, hist, fan, lastPrice) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const h = document.createElement("h3");
  h.textContent = "12-month price distribution (Monte Carlo fan)";
  wrap.appendChild(h);
  const FW = 640, FH = 240, P = { t: 10, r: 14, b: 22, l: 52 };
  const svg = svgEl("svg", { viewBox: `0 0 ${FW} ${FH}`, role: "img" });
  const histN = hist.length;
  const total = histN + fan.length;
  const all = [...hist.map((p) => p.close),
               ...fan.flatMap((f) => [f.p10, f.p90])];
  const scale = niceScale(Math.min(...all), Math.max(...all));
  for (const t of scale.ticks) {
    const y = P.t + (FH - P.t - P.b) * (1 - (t - scale.lo) / (scale.hi - scale.lo || 1));
    svg.appendChild(svgEl("line", { x1: P.l, x2: FW - P.r, y1: y, y2: y, stroke: "#32322f" }));
    const lbl = svgEl("text", { x: P.l - 6, y: y + 3, "text-anchor": "end", fill: "#8a897f", "font-size": 10, "font-family": "ui-monospace, monospace" });
    lbl.textContent = "$" + Math.round(t);
    svg.appendChild(lbl);
  }
  const xAt = (i) => P.l + ((FW - P.l - P.r) / (total - 1)) * i;
  const yAt = (v) => P.t + (FH - P.t - P.b) * (1 - (v - scale.lo) / (scale.hi - scale.lo || 1));
  const band = (loKey, hiKey, opacity) => {
    let d = `M${xAt(histN - 1)},${yAt(lastPrice)}`;
    fan.forEach((f, i) => { d += `L${xAt(histN + i)},${yAt(f[hiKey])}`; });
    [...fan].reverse().forEach((f, i) => { d += `L${xAt(histN + fan.length - 1 - i)},${yAt(f[loKey])}`; });
    d += "Z";
    svg.appendChild(svgEl("path", { d, fill: "#3987e5", opacity, stroke: "none" }));
  };
  band("p10", "p90", 0.16);
  band("p25", "p75", 0.26);
  let dHist = "";
  hist.forEach((p, i) => { dHist += (dHist ? "L" : "M") + xAt(i) + "," + yAt(p.close); });
  svg.appendChild(svgEl("path", { d: dHist, fill: "none", stroke: "#c3c2b7", "stroke-width": 1.6 }));
  let dMed = `M${xAt(histN - 1)},${yAt(lastPrice)}`;
  fan.forEach((f, i) => { dMed += `L${xAt(histN + i)},${yAt(f.p50)}`; });
  svg.appendChild(svgEl("path", { d: dMed, fill: "none", stroke: "#3987e5", "stroke-width": 2, "stroke-dasharray": "5,3" }));
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = `<span class="li"><span class="sw" style="background:#c3c2b7"></span>history (6m)</span>
    <span class="li"><span class="sw" style="background:#3987e5"></span>median path</span>
    <span class="li"><span class="sw" style="background:#3987e5;opacity:.26"></span>25–75%</span>
    <span class="li"><span class="sw" style="background:#3987e5;opacity:.16"></span>10–90%</span>`;
  wrap.appendChild(svg);
  wrap.appendChild(legend);
  container.appendChild(wrap);
}

async function renderPredict(ticker) {
  const m = $("#main");
  const sel = ticker || state.predictTicker || "AAPL";
  state.predictTicker = sel;
  const options = state.companies.map((c) =>
    `<option value="${c.ticker}" ${c.ticker === sel ? "selected" : ""}>${c.ticker}</option>`).join("");
  m.innerHTML = `
    <div class="page-head"><h1>Prediction lab</h1>
      <select id="pred-ticker" style="background:var(--surface-2);color:var(--text-1);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-family:var(--mono)">${options}</select>
      <span class="chip neutral">probabilistic · returns-based</span></div>
    <div class="page-sub">LSTM (torch) with a Gaussian head vs a block-bootstrap Monte Carlo baseline —
      whichever wins walk-forward validation leads the display. Price-history-only: it knows nothing
      about earnings or fundamentals. First run per ticker trains ~1–2 min. Not investment advice.</div>
    <div id="pred-body"><div class="loading">Training walk-forward folds for ${sel}… (~1–2 min on first run)</div></div>`;
  $("#pred-ticker").addEventListener("change", (e) => renderPredict(e.target.value));
  let r;
  try { r = await fetchJSON(`/api/predict/${sel}`); }
  catch (e) { $("#pred-body").innerHTML = `<div class="loading">Failed: ${e.message}</div>`; return; }
  if (r.status !== "OK") {
    $("#pred-body").innerHTML = `<div class="banner">No forecast: ${r.reason || r.status}</div>`;
    return;
  }
  const primary = r.models[r.primary_model];
  const other = r.primary_model === "lstm" ? "bootstrap" : "lstm";
  const v = r.validation;
  const probRow = (label, h) => {
    if (!h) return "";
    const dn = r.models.bootstrap_drift_neutral?.horizons?.[label];
    return `<tr><td>${label}</td>
    <td>$${h.p10}</td><td>$${h.p25}</td><td><b>$${h.p50}</b></td><td>$${h.p75}</td><td>$${h.p90}</td>
    <td class="${h.prob_positive >= 0.5 ? "pos" : "neg"}">${(h.prob_positive * 100).toFixed(0)}%</td>
    <td>${dn ? (dn.prob_positive * 100).toFixed(0) + "%" : "—"}</td>
    <td>${(h.prob_up_10pct * 100).toFixed(0)}%</td>
    <td>${(h.prob_down_10pct * 100).toFixed(0)}%</td>
    <td>${(h.prob_down_20pct * 100).toFixed(0)}%</td></tr>`;
  };
  const body = document.createElement("div");
  body.className = "grid";
  const chartPanel = document.createElement("div");
  chartPanel.className = "panel wide";
  fanChart(chartPanel, r.history_tail, primary.fan, r.last_price);
  chartPanel.innerHTML += `<div class="note">Primary model: <b>${r.primary_model}</b>
    (${v.verdict.lstm_beats_baseline ? "LSTM beat the bootstrap baseline in walk-forward validation" : "LSTM did NOT beat the block-bootstrap baseline — the baseline leads"}).
    Last price $${r.last_price} (${r.as_of}).</div>`;
  body.appendChild(chartPanel);
  const pt = document.createElement("div");
  pt.className = "panel wide";
  pt.innerHTML = `<h3>Probability of future moves — ${r.primary_model} (primary)</h3>
    <div class="table-wrap"><table class="scen-table"><thead><tr>
      <th>Horizon</th><th>P10</th><th>P25</th><th>Median</th><th>P75</th><th>P90</th>
      <th>P(up) drift</th><th>P(up) neutral</th><th>P(+10%)</th><th>P(−10%)</th><th>P(−20%)</th></tr></thead>
      <tbody>${Object.entries(primary.horizons).map(([k, h]) => probRow(k, h)).join("")}</tbody></table></div>
    ${r.drift_diagnostics ? `<div class="note"><b>Directional-bias warning</b> (pressure-tested):
      raw probabilities extrapolate historical drift of
      ${fmtSignedPct(r.drift_diagnostics.historical_drift_annualized_pct)}/yr from a
      survivorship-selected sample, and ran +6–8pt hot vs walk-forward reality across the universe.
      The drift-neutral column removes it — the honest number sits between the two.</div>` : ""}
    ${r.models[other] ? `<div class="mini" style="margin-top:8px">Comparison — ${other}: 12m P(up)
      ${(r.models[other].horizons["12m"].prob_positive * 100).toFixed(0)}%, median $${r.models[other].horizons["12m"].p50}</div>` : ""}`;
  body.appendChild(pt);
  const vp = document.createElement("div");
  vp.className = "panel wide";
  vp.innerHTML = `<h3>Validation — has this model earned trust?</h3>
    <div class="kv">
      <span class="k">Walk-forward folds (21-day, train strictly before cutoff)</span><span class="v">${v.n_folds}</span>
      <span class="k">LSTM direction hit rate</span><span class="v">${v.lstm ? (v.lstm.direction_hit_rate * 100).toFixed(0) + "%" : "n/a"}</span>
      <span class="k">Bootstrap direction hit rate</span><span class="v">${(v.bootstrap.direction_hit_rate * 100).toFixed(0)}%</span>
      <span class="k">LSTM 80%-interval coverage</span><span class="v">${v.lstm ? (v.lstm.interval_80_coverage * 100).toFixed(0) + "%" : "n/a"}</span>
      <span class="k">Bootstrap 80%-interval coverage</span><span class="v">${(v.bootstrap.interval_80_coverage * 100).toFixed(0)}%</span>
      <span class="k">Verdict</span><span class="v">${v.verdict.lstm_beats_baseline ? "LSTM leads" : "baseline leads"}</span>
    </div>
    <div class="note">${v.verdict.kill_criterion}. ${v.verdict.note}. ${r.methodology.leak_controls}.
      ${r.methodology.limitations}</div>`;
  body.appendChild(vp);
  $("#pred-body").innerHTML = "";
  $("#pred-body").appendChild(body);
}

/* ---------------- paper portfolio view ---------------- */
async function renderPortfolio() {
  const m = $("#main");
  m.innerHTML = '<div class="loading">Loading paper portfolio…</div>';
  let s;
  try { s = await fetchJSON("/api/paper"); }
  catch (e) { m.innerHTML = `<div class="loading">Failed: ${e.message}</div>`; return; }
  const latest = s.latest || {};
  const marks = latest.details || [];
  const longs = marks.filter((p) => p.direction === "long");
  const shorts = marks.filter((p) => p.direction === "short");
  const posRow = (p) => `<tr data-t="${p.ticker}" style="cursor:pointer">
    <td><span class="tk">${p.ticker}</span>${p.flagged ? ' <span class="chip warn" title="' + p.flagged + '">⚠ flagged</span>' : ""}</td>
    <td>${p.direction}</td><td>$${p.entry?.toFixed(2)}</td><td>$${p.price?.toFixed(2)}</td>
    <td class="${cls(p.position_ret_pct)}">${fmtSignedPct(p.position_ret_pct)}</td></tr>`;
  const navRows = (s.nav || []).slice(-14).reverse().map((n) => `
    <tr><td>${n.date}</td><td class="${cls(n.long_ret_pct)}">${fmtSignedPct(n.long_ret_pct)}</td>
    <td class="${cls(n.short_ret_pct)}">${fmtSignedPct(n.short_ret_pct)}</td>
    <td class="${cls(n.ls_ret_pct)}">${fmtSignedPct(n.ls_ret_pct)}</td>
    <td class="mini">${n.n_long}L / ${n.n_short}S</td></tr>`).join("");
  m.innerHTML = `
    <div class="page-head"><h1>Paper portfolio</h1>
      <span class="chip neutral">${longs.length} long · ${shorts.length} short</span>
      ${latest.ls_ret_pct != null ? `<span class="chip ${latest.ls_ret_pct >= 0 ? "good" : "bad"}">L/S avg since entry ${fmtSignedPct(latest.ls_ret_pct)}</span>` : ""}</div>
    <div class="page-sub">Mechanical book from classifications: long every ATTRACTIVE, short every
      UNATTRACTIVE, equal weight, adjusted-close marks. Positions flagged by invalidation monitoring
      require a deliberate analyst re-pass — nothing auto-closes. ${s.conventions || ""}
      Paper only — not investment advice.</div>
    <div class="grid">
      <div class="panel"><h3>Long book ${latest.long_ret_pct != null ? `· avg ${fmtSignedPct(latest.long_ret_pct)}` : ""}</h3>
        <div class="table-wrap"><table class="scen-table"><thead><tr><th>Ticker</th><th>Dir</th><th>Entry</th><th>Mark</th><th>P&L</th></tr></thead>
        <tbody>${longs.map(posRow).join("") || "<tr><td colspan=5>none</td></tr>"}</tbody></table></div></div>
      <div class="panel"><h3>Short book ${latest.short_ret_pct != null ? `· avg ${fmtSignedPct(latest.short_ret_pct)}` : ""}</h3>
        <div class="table-wrap"><table class="scen-table"><thead><tr><th>Ticker</th><th>Dir</th><th>Entry</th><th>Mark</th><th>P&L</th></tr></thead>
        <tbody>${shorts.map(posRow).join("") || "<tr><td colspan=5>none</td></tr>"}</tbody></table></div></div>
      <div class="panel wide"><h3>Daily marks (last 14)</h3>
        <div class="table-wrap"><table class="scen-table"><thead><tr><th>Date</th><th>Long avg</th><th>Short avg</th><th>L/S</th><th>Book</th></tr></thead>
        <tbody>${navRows || "<tr><td colspan=5>no marks yet</td></tr>"}</tbody></table></div></div>
      ${s.recent_closes?.length ? `<div class="panel wide"><h3>Recent closes</h3>
        <div class="table-wrap"><table class="scen-table"><thead><tr><th>Ticker</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th></tr></thead>
        <tbody>${s.recent_closes.map((c) => `<tr><td>${c.ticker}</td><td>${c.direction}</td>
          <td>$${c.entry_price?.toFixed(2)} (${c.entry_date})</td><td>$${c.exit_price?.toFixed(2)} (${c.exit_date})</td>
          <td class="mini">${c.exit_reason}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    </div>`;
  m.querySelectorAll("tr[data-t]").forEach((tr) =>
    tr.addEventListener("click", () => go("stock", tr.dataset.t)));
}

/* ---------------- system / KPI view ---------------- */
async function renderSystem() {
  const m = $("#main");
  m.innerHTML = `<div class="loading">Computing KPIs…</div>`;
  let data;
  try { data = await fetchJSON("/api/kpis"); }
  catch (e) { m.innerHTML = `<div class="loading">KPI load failed: ${e.message}</div>`; return; }
  const CATS = { data: "Data quality", forecast: "Forecast quality",
                 ai: "AI contribution", ops: "Operational" };
  const stCls = { PASS: "good", FAIL: "bad", PENDING: "neutral" };
  const s = data.summary;
  m.innerHTML = `
    <div class="page-head"><h1>System validation</h1>
      <span class="chip good">${s.pass} pass</span>
      <span class="chip bad">${s.fail} fail</span>
      <span class="chip neutral">${s.pending} pending</span></div>
    <div class="page-sub">${data.principle}</div>
    ${Object.entries(CATS).map(([cat, label]) => {
      const rows = data.kpis.filter((k) => k.category === cat);
      if (!rows.length) return "";
      return `<div class="panel wide" style="margin-bottom:14px"><h3>${label}</h3>
        <div class="table-wrap"><table class="coverage"><thead><tr>
          <th style="text-align:left">KPI</th><th>Current</th><th>Target</th><th>Status</th>
        </tr></thead><tbody>
        ${rows.map((k) => `<tr>
          <td style="text-align:left;white-space:normal;font-family:-apple-system,sans-serif">${k.kpi}
            ${k.detail ? `<div class="mini" style="white-space:normal">${k.detail}</div>` : ""}</td>
          <td>${k.value ?? "—"}</td>
          <td class="mini">${k.target ?? "—"}</td>
          <td><span class="chip ${stCls[k.status]}">${k.status}</span></td>
        </tr>`).join("")}
        </tbody></table></div></div>`;
    }).join("")}`;
}

/* ---------------- coverage view ---------------- */
const SIGNAL_META = {
  low_embedded_expectations: ["EXPECT↓", "Price-implied growth is BELOW what the company has already delivered — a low bar"],
  insider_buying: ["INSIDER+", "Discretionary open-market insider buying in the last 6 months"],
  favorable_base_rate: ["BASE+", "Historical setups with this growth/valuation/ROIC profile outperformed the universe"],
  beats_expectations: ["BEATS", "Consistent earnings beats (expectations score ≥ 70)"],
  cheap_vs_sector: ["VALUE", "P/E at or below the 40th percentile of its sector"],
};

function signalChips(c) {
  return Object.entries(SIGNAL_META)
    .filter(([k]) => c.signals && c.signals[k])
    .map(([k, [lbl, tip]]) => `<span class="chip good" title="${tip}" style="font-size:9.5px;padding:2px 6px">${lbl}</span>`)
    .join(" ");
}

function rangeCell(c) {
  const r = c.report_12m;
  if (!r || r.fair_value_low == null) return '<span class="mini">no report</span>';
  return `$${r.fair_value_low}–$${r.fair_value_high}`;
}

function renderHome() {
  const m = $("#main");
  const rows = [...state.companies].sort((a, b) =>
    (b.signal_count ?? 0) - (a.signal_count ?? 0)
    || (b.report_12m?.expected_return_pct ?? -999) - (a.report_12m?.expected_return_pct ?? -999)
    || (b.composite_score ?? -1) - (a.composite_score ?? -1));
  const top = rows.filter((c) => (c.signal_count ?? 0) >= 3).slice(0, 6);
  m.innerHTML = `
    <div class="page-head"><h1>Coverage</h1>
      <span class="chip neutral">${rows.length} companies</span></div>
    <div class="page-sub">Sorted by <b>evidence convergence</b> — a transparent checklist of
      independent favorable signals (embedded expectations, insiders, base rates, beat
      history, sector valuation), NOT a calibrated probability. Quality score describes
      the business; it is not predictive. Hover any chip for its meaning.</div>
    ${top.length ? `<div class="panel wide" style="margin-bottom:16px">
      <h3>Signal convergence — most independent favorable evidence</h3>
      <div class="tiles">${top.map((c) => `
        <div class="tile" style="cursor:pointer" data-t="${c.ticker}">
          <div class="lbl">${c.ticker} · ${c.signal_count}/5 signals</div>
          <div style="margin:4px 0">${signalChips(c)}</div>
          <div class="sub">${c.report_12m ? `12m E[r] ${fmtSignedPct(c.report_12m.expected_return_pct)} · range ${rangeCell(c)}` : "no analyst report yet"}</div>
          ${c.next_earnings_date ? `<div class="sub">earnings ${c.next_earnings_date}</div>` : ""}
        </div>`).join("")}
      </div>
      <div class="note">Convergence of independent evidence classes — inspect each signal on the stock page before acting. Not investment advice.</div>
    </div>` : ""}
    <div class="table-wrap"><table class="coverage">
      <thead><tr>
        <th>Company</th><th>Sector</th><th>Signals</th><th>Price</th>
        <th>12m E[r]</th><th>12m range (bear–bull)</th><th>12m past</th>
        <th>Rev YoY</th><th>FCF yield</th><th>P/E (ttm)</th><th>Quality</th><th></th>
      </tr></thead>
      <tbody>${rows.map((c) => `
        <tr data-t="${c.ticker}">
          <td><span class="tk">${c.ticker}</span> <span class="nm">${c.legal_name || ""}</span></td>
          <td><span class="nm">${c.sector || "—"}</span></td>
          <td style="text-align:left">${signalChips(c) || '<span class="mini">—</span>'}</td>
          <td>${c.price == null ? "—" : "$" + c.price.toFixed(2)}</td>
          <td class="${cls(c.report_12m?.expected_return_pct)}">${c.report_12m ? fmtSignedPct(c.report_12m.expected_return_pct) : "—"}</td>
          <td>${rangeCell(c)}</td>
          <td class="${cls(c.twelve_month_pct)}">${fmtSignedPct(c.twelve_month_pct)}</td>
          <td class="${cls(c.revenue_yoy_pct)}">${fmtSignedPct(c.revenue_yoy_pct)}</td>
          <td>${fmtPct(c.fcf_yield_pct)}</td>
          <td>${fmtNum(c.pe_ttm)}</td>
          <td><span class="scorebar" title="Fundamental quality — descriptive, not predictive"><span class="track"><span class="fill" style="width:${(c.composite_score ?? 0)}%"></span></span>${fmtNum(c.composite_score, 0)}</span></td>
          <td>${c.has_report ? `<span class="chip neutral">${(c.report_12m?.classification || "report").toLowerCase()}</span>` : ""}</td>
        </tr>`).join("")}
      </tbody></table></div>`;
  m.querySelectorAll("[data-t]").forEach((el) =>
    el.addEventListener("click", () => go("stock", el.dataset.t)));
}

/* ---------------- stock view ---------------- */
const SCORE_LABELS = {
  growth: "Growth", profitability: "Profitability",
  earnings_quality: "Earnings quality", financial_health: "Financial health",
  capital_allocation: "Capital allocation", expectations: "Expectations",
  valuation: "Valuation",
};

async function renderStock(ticker) {
  const m = $("#main");
  m.innerHTML = `<div class="loading">Loading ${ticker}…</div>`;
  let bundleData, priceData, reportData = null;
  try {
    [bundleData, priceData] = await Promise.all([
      fetchJSON(`/api/bundle/${ticker}`),
      fetchJSON(`/api/prices/${ticker}?days=756`),
    ]);
    try { reportData = await fetchJSON(`/api/report/${ticker}`); } catch (e) { /* none yet */ }
  } catch (e) {
    m.innerHTML = `<div class="loading">Failed to load ${ticker}: ${e.message}</div>`;
    return;
  }
  const b = bundleData;
  const d = b.derived_metrics;
  const ms = b.market_snapshot;
  const q = b.financial_history.quarterly_periods;
  const dq = b.data_quality;
  const statusCls = dq.status === "PASS" ? "good" : dq.status === "WARN" ? "warn" : "bad";

  m.innerHTML = `
    <div class="page-head">
      <h1>${b.company.legal_name}</h1>
      <span class="chip neutral">${ticker}</span>
      <span class="chip ${statusCls}">data ${dq.status}</span>
      ${reportData ? `<span class="chip neutral">analyzed ${reportData.as_of?.slice(0, 10) || ""}</span>` : ""}
    </div>
    <div class="page-sub">${b.company.industry_sic || ""} · CIK ${b.company.cik}
      · knowledge cutoff ${b.knowledge_cutoff.slice(0, 16).replace("T", " ")}</div>
    <div class="tiles" id="tiles"></div>
    <div class="grid" id="grid"></div>
    <div id="report-root"></div>
    <div style="margin-top:14px" id="qtable-root"></div>`;

  // tiles
  const tiles = [
    ["Price", ms.price == null ? "—" : "$" + ms.price.toFixed(2), ms.price_date],
    ["Market cap", fmtMoney(ms.market_cap), "EV " + fmtMoney(ms.enterprise_value)],
    ["12-month", fmtSignedPct(ms.price_change.twelve_month_pct), "3m " + fmtSignedPct(ms.price_change.three_month_pct)],
    ["P/E (ttm)", fmtNum(d.valuation.pe_ttm), d.valuation.pe_5y_percentile == null ? "" : "5y %ile " + fmtNum(d.valuation.pe_5y_percentile, 0)],
    ["FCF yield", fmtPct(d.valuation.fcf_yield_pct), "P/FCF " + fmtNum(d.valuation.price_to_fcf_ttm)],
    ["Rev growth YoY", fmtSignedPct(d.growth.revenue_yoy_pct), "3y CAGR " + fmtPct(d.growth.revenue_cagr_3y_pct)],
  ];
  $("#tiles").innerHTML = tiles
    .map(([l, v, s]) => `<div class="tile"><div class="lbl">${l}</div><div class="val">${v}</div><div class="sub">${s || ""}</div></div>`)
    .join("");

  const grid = $("#grid");

  // charts (left)
  const last16 = q.slice(-16);
  const qLabels = last16.map((p) => p.period_id || p.period_end);
  const chartPanel = document.createElement("div");
  chartPanel.className = "panel wide";
  chartPanel.innerHTML = "<h3>Fundamentals — last 16 quarters</h3>";
  const chartGrid = document.createElement("div");
  chartGrid.className = "grid";
  chartPanel.appendChild(chartGrid);
  grid.appendChild(chartPanel);

  const cell = () => { const el = document.createElement("div"); chartGrid.appendChild(el); return el; };
  barChart(cell(), {
    title: "Revenue", labels: qLabels,
    values: last16.map((p) => p.income_statement.revenue),
    fmt: (v) => fmtMoney(v),
  });
  lineChart(cell(), {
    title: "Margins (quarterly)", labels: qLabels,
    series: [
      { name: "Gross", color: COLORS.blue, values: last16.map((p) => ratio(p.income_statement.gross_profit, p.income_statement.revenue)) },
      { name: "Operating", color: COLORS.orange, values: last16.map((p) => ratio(p.income_statement.operating_income, p.income_statement.revenue)) },
      { name: "Net", color: COLORS.aqua, values: last16.map((p) => ratio(p.income_statement.net_income, p.income_statement.revenue)) },
    ],
    fmt: (v) => fmtPct(v, 0),
  });
  barChart(cell(), {
    title: "Free cash flow", labels: qLabels,
    values: last16.map((p) => p.cash_flow.free_cash_flow),
    fmt: (v) => fmtMoney(v), color: COLORS.aqua,
  });
  const priceLabels = priceData.map((r) => r.date.slice(2, 7));
  lineChart(cell(), {
    title: "Price — 3y (adjusted)", labels: priceLabels,
    series: [{ name: ticker, color: COLORS.blue, values: priceData.map((r) => r.adj_close) }],
    fmt: (v) => "$" + v.toFixed(0),
  });

  // scores (right column panels)
  const sc = b.fundamental_scores;
  const scorePanel = document.createElement("div");
  scorePanel.className = "panel";
  scorePanel.innerHTML = `<h3>Fundamental scores · composite ${fmtNum(sc.composite_score, 1)}</h3>
    <div class="score-rows">${Object.entries(SCORE_LABELS).map(([k, lbl]) => {
      const v = sc.components[k];
      if (v == null)
        return `<div class="score-row na"><span class="lbl">${lbl}</span><span class="na-note">n/a — no point-in-time consensus data</span><span class="num">—</span></div>`;
      return `<div class="score-row"><span class="lbl">${lbl}</span><span class="track"><span class="fill" style="width:${v}%"></span></span><span class="num">${fmtNum(v, 0)}</span></div>`;
    }).join("")}</div>
    <div class="note">Scoring profile: <b>${sc.scoring_profile?.profile || "general"}</b>${sc.scoring_profile?.sector_adjusted_metrics?.length ? ` (sector-adjusted: ${sc.scoring_profile.sector_adjusted_metrics.join(", ")})` : ""}. Missing components renormalize weights. Thresholds are documented conventions, unproven until backtested.</div>`;
  grid.appendChild(scorePanel);

  // key metrics panel
  const km = document.createElement("div");
  km.className = "panel";
  km.innerHTML = `<h3>Key metrics (TTM)</h3><div class="kv">${[
    ["Gross margin", fmtPct(d.profitability.gross_margin_pct)],
    ["Operating margin", fmtPct(d.profitability.operating_margin_pct)],
    ["Net margin", fmtPct(d.profitability.net_margin_pct)],
    ["FCF margin", fmtPct(d.profitability.fcf_margin_pct)],
    ["ROIC", fmtPct(d.profitability.roic_pct)],
    ["OCF / net income", fmtNum(d.earnings_quality.operating_cash_flow_to_net_income, 2)],
    ["Accrual ratio (% assets)", fmtPct(d.earnings_quality.accrual_ratio_pct_of_assets)],
    ["SBC / revenue", fmtPct(d.earnings_quality.stock_comp_to_revenue_pct)],
    ["Net debt", fmtMoney(d.financial_health.net_debt)],
    ["Current ratio", fmtNum(d.financial_health.current_ratio, 2)],
    ["Net shareholder yield", fmtPct(d.capital_allocation.net_shareholder_yield_pct)],
    ["Diluted shares YoY", fmtSignedPct(d.capital_allocation.diluted_share_change_yoy_pct)],
    ["EV / revenue", fmtNum(d.valuation.ev_to_revenue_ttm)],
    ["Earnings yield", fmtPct(d.valuation.earnings_yield_pct)],
  ].map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join("")}</div>`;
  grid.appendChild(km);

  // price-implied expectations + base rates + catalyst (Phase A)
  const pie = b.price_implied_expectations || {};
  const brr = b.base_rates || {};
  const cc2 = b.catalyst_calendar || {};
  const pa = document.createElement("div");
  pa.className = "panel wide";
  const gapCls = pie.gap_vs_achieved_pct == null ? "" : pie.gap_vs_achieved_pct > 5 ? "neg" : "pos";
  const brOk = brr.status === "OK";
  pa.innerHTML = `<h3>What the price assumes
      ${cc2.next_earnings_date ? `<span class="chip neutral" style="margin-left:8px">next earnings ${cc2.next_earnings_date} (${cc2.days_until}d)</span>` : ""}</h3>
    <div class="grid">
      <div><div class="mini" style="margin-bottom:6px">Reverse DCF — growth required to justify today's price</div>
        <div class="kv">
          <span class="k">Basis</span><span class="v">${(pie.basis || "—").replaceAll("_", " ")}</span>
          <span class="k">Price-implied 5y CAGR</span><span class="v">${pie.implied_cagr_5y_pct != null ? fmtSignedPct(pie.implied_cagr_5y_pct) : (pie.reverse_dcf?.note || "—")}</span>
          <span class="k">Achieved FCF CAGR (3y)</span><span class="v">${fmtSignedPct(pie.achieved_fcf_cagr_3y_pct)}</span>
          <span class="k">Achieved revenue CAGR (3y)</span><span class="v">${fmtSignedPct(pie.achieved_revenue_cagr_3y_pct)}</span>
          <span class="k">Consensus next-FY revenue growth</span><span class="v">${fmtSignedPct(pie.consensus_next_fy_revenue_growth_pct)}</span>
          <span class="k">Gap: implied − achieved</span><span class="v ${gapCls}">${fmtSignedPct(pie.gap_vs_achieved_pct)}</span>
        </div>
        <div class="note">${pie.reading || ""} Assumptions: ${pie.reverse_dcf?.assumptions ? `r=${pie.reverse_dcf.assumptions.discount_rate_pct}%, terminal g=${pie.reverse_dcf.assumptions.terminal_growth_pct}% — documented conventions.` : "—"}</div>
      </div>
      <div><div class="mini" style="margin-bottom:6px">Base rates — comparable historical setups (own panel)</div>
        ${brOk ? `<div class="kv">
          <span class="k">Setup bucket (growth / value / ROIC)</span><span class="v">${Object.values(brr.subject_buckets).join(" / ")}</span>
          <span class="k">Historical analogs</span><span class="v">${brr.n_analogs}</span>
          <span class="k">Outperformed universe</span><span class="v">${(brr.outperform_share * 100).toFixed(0)}%</span>
          <span class="k">Median 12m excess return</span><span class="v ${cls(brr.median_excess_12m_pct)}">${fmtSignedPct(brr.median_excess_12m_pct)}</span>
          <span class="k">P10 / P90 excess</span><span class="v">${fmtSignedPct(brr.p10_excess_12m_pct)} / ${fmtSignedPct(brr.p90_excess_12m_pct)}</span>
        </div><div class="note">${brr.methodology || ""}</div>`
        : `<div class="note">No base rate issued — ${brr.reason || brr.status || "unavailable"}. Abstention beats a made-up number.</div>`}
      </div>
    </div>`;
  grid.appendChild(pa);

  // sector peer comparison panel
  const pg = b.peer_group || {};
  const pp = document.createElement("div");
  pp.className = "panel wide";
  if (pg.available) {
    const rows = (pg.comparison || []).map((r) => {
      const pct = r.percentile;
      const good = pct != null &&
        (r.higher_is === "higher" ? pct >= 50 : pct <= 50);
      return `<tr><td>${r.label}${r.higher_is === "lower" ? ' <span class="mini">(lower better)</span>' : ""}</td>
        <td>${r.value != null ? (String(r.metric).includes("pct") || String(r.metric).includes("margin") || String(r.metric).includes("yield") ? fmtPct(r.value) : fmtNum(r.value)) : "—"}</td>
        <td>${r.sector_median != null ? (String(r.metric).includes("pct") || String(r.metric).includes("margin") || String(r.metric).includes("yield") ? fmtPct(r.sector_median) : fmtNum(r.sector_median)) : "—"}</td>
        <td class="${pct == null ? "" : good ? "pos" : "neg"}">${pct != null ? pct.toFixed(0) + "th" : "—"}</td>
        <td class="mini">${r.n}</td></tr>`;
    }).join("");
    pp.innerHTML = `<h3>Sector comparison — ${pg.sector} (${pg.peer_count} companies)</h3>
      <div class="table-wrap"><table class="scen-table"><thead><tr>
        <th>Metric</th><th>${ticker}</th><th>Sector median</th><th>Percentile</th><th>n</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
      <div class="note">Peers: ${(pg.peers || []).join(", ")}. ${pg.methodology || ""}</div>`;
  } else {
    pp.innerHTML = `<h3>Sector comparison</h3>
      <div class="note">Unavailable — ${pg.reason || "no peer data"}${pg.sector ? ` (sector: ${pg.sector})` : ""}.</div>`;
  }
  grid.appendChild(pp);

  // insider activity panel (Phase B)
  const ins = b.insider_activity || {};
  const ip = document.createElement("div");
  ip.className = "panel";
  const sigCls = { MULTIPLE_DISCRETIONARY_BUYERS: "good",
                   NET_DISCRETIONARY_BUYING: "good",
                   NET_DISCRETIONARY_SELLING: "warn",
                   ROUTINE_ONLY: "neutral", NO_DATA: "neutral" }[ins.signal] || "neutral";
  const insRows = (ins.recent_transactions || []).slice(0, 6).map((r) => `
    <tr><td>${r.date}</td><td style="text-align:left">${(r.owner || "").slice(0, 18)}</td>
    <td style="text-align:left" class="mini">${(r.classification || "").replaceAll("_", " ")}</td>
    <td>${r.value != null ? fmtMoney(r.value) : "—"}</td></tr>`).join("");
  ip.innerHTML = `<h3>Insider activity (6m) <span class="chip ${sigCls}">${(ins.signal || "—").replaceAll("_", " ")}</span></h3>
    <div class="kv">
      <span class="k">Discretionary purchases</span><span class="v">${ins.discretionary_purchases?.n ?? 0} (${fmtMoney(ins.discretionary_purchases?.total_value || 0)}, ${ins.discretionary_purchases?.owners ?? 0} insiders)</span>
      <span class="k">Discretionary sales</span><span class="v">${ins.discretionary_sales?.n ?? 0} (${fmtMoney(ins.discretionary_sales?.total_value || 0)})</span>
    </div>
    ${insRows ? `<div class="table-wrap" style="margin-top:8px"><table class="scen-table"><thead><tr><th>Date</th><th style="text-align:left">Insider</th><th style="text-align:left">Type</th><th>Value</th></tr></thead><tbody>${insRows}</tbody></table></div>` : ""}
    <div class="note">${ins.note || ""}</div>`;
  grid.appendChild(ip);

  // expectations / consensus panel
  const cons = b.consensus || {};
  const ep = document.createElement("div");
  ep.className = "panel wide";
  if (cons.available) {
    const fwd = (cons.forward_estimates || []).map((r) => `
      <tr><td>${r.forecast_period_end} <span class="mini">(${r.period_type})</span></td>
      <td>${r.eps_mean != null ? r.eps_mean.toFixed(2) : "—"}</td>
      <td>${fmtMoney(r.revenue_mean)}</td>
      <td>${r.analyst_count ?? "—"}</td></tr>`).join("");
    const sur = (cons.surprise_history || []).slice().reverse().map((s) => `
      <tr><td>${s.date}</td><td>${s.actual_eps?.toFixed(2) ?? "—"}</td>
      <td>${s.estimated_eps?.toFixed(2) ?? "—"}</td>
      <td class="${cls(s.surprise_pct)}">${fmtSignedPct(s.surprise_pct)}</td></tr>`).join("");
    ep.innerHTML = `<h3>Expectations · consensus vintage ${cons.snapshot_date || "—"}
      ${cons.forward_pe_next_fy != null ? `· fwd P/E ${cons.forward_pe_next_fy}` : ""}</h3>
      <div class="grid">
        <div><div class="mini" style="margin-bottom:6px">Forward estimates</div>
          <div class="table-wrap"><table class="scen-table"><thead><tr>
          <th>Period</th><th>EPS est.</th><th>Revenue est.</th><th>Analysts</th>
          </tr></thead><tbody>${fwd || "<tr><td colspan=4>none</td></tr>"}</tbody></table></div></div>
        <div><div class="mini" style="margin-bottom:6px">Surprise history (vendor-recorded)</div>
          <div class="table-wrap"><table class="scen-table"><thead><tr>
          <th>Date</th><th>Actual EPS</th><th>Est. EPS</th><th>Surprise</th>
          </tr></thead><tbody>${sur || "<tr><td colspan=4>none</td></tr>"}</tbody></table></div></div>
      </div>
      <div class="note">${cons.vintage_note || ""}</div>`;
  } else {
    ep.innerHTML = `<h3>Expectations</h3>
      <div class="note">No consensus dataset connected. Add FMP_API_KEY to .env
      and run the pipeline — forward estimates, surprise history and the
      expectations score unlock automatically; revision analysis unlocks after
      7 days of accumulated snapshots.</div>`;
  }
  grid.appendChild(ep);

  // data quality panel
  const dqp = document.createElement("div");
  dqp.className = "panel wide";
  dqp.innerHTML = `<h3>Data quality — ${dq.status}</h3>
    <div class="kv">
      <span class="k">Completeness (critical fields)</span><span class="v">${(dq.completeness_score * 100).toFixed(0)}%</span>
      <span class="k">Quarters / years normalized</span><span class="v">${b.financial_history.period_count.quarters} / ${b.financial_history.period_count.years}</span>
      <span class="k">Restatement events logged</span><span class="v">${dq.restatement_warnings.length}${dq.restatement_warnings.length >= 20 ? "+" : ""}</span>
      <span class="k">Missing datasets</span><span class="v">${(dq.missing_datasets || []).join(", ") || "none"}</span>
    </div>
    <div class="note">${(dq.known_limitations || []).map((l) => "· " + l).join("<br>")}</div>`;
  grid.appendChild(dqp);

  // price prediction panel — async so the page never blocks on training
  const predP = document.createElement("div");
  predP.className = "panel wide";
  predP.innerHTML = `<h3>Price prediction — Monte Carlo</h3>
    <div class="loading">Loading forecast… (first run per ticker trains ~30s)</div>`;
  grid.appendChild(predP);
  (async () => {
    let r;
    try { r = await fetchJSON(`/api/predict/${ticker}`); }
    catch (e) { predP.querySelector(".loading").textContent = "Forecast unavailable: " + e.message; return; }
    if (r.status !== "OK") {
      predP.querySelector(".loading").textContent = "No forecast: " + (r.reason || r.status);
      return;
    }
    const prim = r.models[r.primary_model];
    const v = r.validation;
    predP.innerHTML = `<h3>Price prediction — ${r.primary_model} (primary)
      <span class="chip ${v.verdict.lstm_beats_baseline ? "good" : "neutral"}" style="margin-left:8px">
        ${v.verdict.lstm_beats_baseline ? "LSTM beat baseline" : "baseline leads"}</span></h3>`;
    fanChart(predP, r.history_tail, prim.fan, r.last_price);
    predP.innerHTML += `
      <div class="table-wrap" style="margin-top:8px"><table class="scen-table"><thead><tr>
        <th>Horizon</th><th>P10</th><th>Median</th><th>P90</th>
        <th>P(up) drift</th><th>P(up) neutral</th><th>P(+10%)</th><th>P(−10%)</th><th>P(−20%)</th></tr></thead>
        <tbody>${Object.entries(prim.horizons).map(([k, h]) => {
          const dn = r.models.bootstrap_drift_neutral?.horizons?.[k];
          return `<tr><td>${k}</td><td>$${h.p10}</td><td><b>$${h.p50}</b></td><td>$${h.p90}</td>
          <td class="${h.prob_positive >= 0.5 ? "pos" : "neg"}">${(h.prob_positive * 100).toFixed(0)}%</td>
          <td>${dn ? (dn.prob_positive * 100).toFixed(0) + "%" : "—"}</td>
          <td>${(h.prob_up_10pct * 100).toFixed(0)}%</td>
          <td>${(h.prob_down_10pct * 100).toFixed(0)}%</td>
          <td>${(h.prob_down_20pct * 100).toFixed(0)}%</td></tr>`; }).join("")}
        </tbody></table></div>
      <div class="note">${r.drift_diagnostics ? `<b>Directional-bias warning:</b> raw P(up) extrapolates
        this stock's historical drift (${fmtSignedPct(r.drift_diagnostics.historical_drift_annualized_pct)}/yr);
        universe pressure-testing measured a +6–8pt upward bias vs walk-forward reality. The
        drift-neutral column removes historical drift — read the two as bracketing the honest answer. ` : ""}
        Price-history-only model — knows nothing about earnings, filings or fundamentals.
        Walk-forward direction hit: ${v.lstm ? "LSTM " + (v.lstm.direction_hit_rate * 100).toFixed(0) + "% vs " : ""}bootstrap
        ${(v.bootstrap.direction_hit_rate * 100).toFixed(0)}% (${v.n_folds} folds).
        Full detail in the Prediction lab. Not investment advice.</div>`;
  })();

  if (reportData) {
    const latestAvail = q.length ? q[q.length - 1].available_at : null;
    window.__breaches = b.invalidation_breaches || [];
    renderReport($("#report-root"), reportData, latestAvail);
  }
  renderQuarterlyTable($("#qtable-root"), q.slice(-12));
}

const ratio = (a, b) => (a == null || !b ? null : (a / b) * 100);

/* ---------------- analysis report ---------------- */
function renderReport(root, r, latestAvailableAt) {
  const stale = latestAvailableAt && r.as_of &&
    latestAvailableAt > r.as_of.slice(0, 10);
  const staleBanner = stale
    ? `<div class="banner">⚠ This analysis predates the newest filing
       (data available ${latestAvailableAt}, report as of ${r.as_of.slice(0, 10)}).
       Numbers in the narrative may no longer match the bundle — re-run the
       analyst pass before relying on it.</div>`
    : "";
  const breaches = window.__breaches || [];
  const breachBanner = breaches.length
    ? `<div class="banner" style="border-left-color:var(--status-bad)">⚠ INVALIDATION
       BREACH${breaches.length > 1 ? "ES" : ""}: ${breaches.map((b) =>
         `${b.description} (observed ${b.observed} vs ${b.threshold}, ${b.triggered_at})`).join(" · ")}
       — the thesis requires a deliberate analyst re-pass.</div>`
    : "";
  const con = r.conclusion || {};
  const concls = { ATTRACTIVE: "good", WATCH: "warn", UNATTRACTIVE: "bad", INSUFFICIENT_DATA: "neutral" }[con.classification] || "neutral";
  const t = r.investment_thesis || {};
  const adv = r.adversarial_review || {};
  const scen = r.scenarios || [];
  const fc = r.forecasts && r.forecasts.twelve_month;
  root.innerHTML = `
    <div class="panel wide report-block" style="margin-top:14px">
      ${breachBanner}${staleBanner}
      <div class="page-head" style="margin-bottom:8px">
        <h2 style="margin:0">Machine analysis</h2>
        <span class="chip ${concls}">${(con.classification || "").replace("_", " ")}</span>
        <span class="chip neutral">conviction ${con.conviction || "—"}</span>
        <span class="chip neutral">horizon ${(con.time_horizon || "").replace("_", " ").toLowerCase()}</span>
        <span class="mini">as of ${r.as_of?.slice(0, 10)} · analyst layer output, evidence-cited · not investment advice</span>
      </div>
      <p>${t.summary || ""}</p>
      ${section("Fundamental trend", `<p><b>${r.fundamental_trend?.direction || ""}</b> (${r.fundamental_trend?.strength || ""}) —
        drivers: ${(r.fundamental_trend?.primary_drivers || []).join("; ")}
        ${r.fundamental_trend?.primary_deteriorations?.length ? "· deteriorations: " + r.fundamental_trend.primary_deteriorations.join("; ") : ""}</p>`)}
      ${scen.length ? section("Scenarios (12-month)", scenTable(scen, fc)) : ""}
      ${r.forecasts ? section("Projected price by horizon", horizonTable(r.forecasts)) : ""}
      ${list("What is already priced in", t.what_is_already_priced_in)}
      ${list("Catalysts", t.catalysts)}
      ${list("Risks", t.risks)}
      ${list("Invalidation conditions", t.invalidation_conditions)}
      ${section("Adversarial review", `<p><b>Strongest bear case:</b> ${adv.strongest_bear_case || "—"}</p>
        ${list("Fragile assumptions", adv.fragile_assumptions, true)}
        ${list("Valuation concerns", adv.valuation_concerns, true)}
        ${list("Unresolved questions", adv.unresolved_questions, true)}`)}
      ${r.claims?.length ? section("Claims register", `<div class="claims">${r.claims.map((c) => `
        <div class="claim"><span class="chip ${{ FACT: "good", INFERENCE: "warn", FORECAST: "neutral" }[c.classification] || "neutral"}">${c.classification}</span>
        <span>${c.claim} <span class="src">${(c.source_ids || []).join(" ")}</span></span></div>`).join("")}</div>`) : ""}
    </div>`;
}
const section = (title, inner) => `<div class="report-block"><h3 style="color:var(--text-1);font-size:13px;margin-bottom:6px">${title}</h3>${inner}</div>`;
const list = (title, items, sub = false) =>
  items && items.length
    ? `${sub ? "" : `<h3 style="color:var(--text-1);font-size:13px;margin:10px 0 4px">${title}</h3>`}${sub ? `<p style="margin:6px 0 2px"><b>${title}:</b></p>` : ""}<ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul>`
    : "";
function horizonTable(fcs) {
  const HORIZONS = [["three_month", "3 months"], ["six_month", "6 months"],
                    ["twelve_month", "12 months"]];
  const rows = HORIZONS.filter(([k]) => fcs[k]).map(([k, lbl]) => {
    const f = fcs[k];
    return `<tr><td>${lbl}</td>
      <td>$${f.expected_price ?? f.fair_value_base}</td>
      <td class="${cls(f.expected_return_pct)}">${fmtSignedPct(f.expected_return_pct)}</td>
      <td>$${f.fair_value_low} – $${f.fair_value_high}</td>
      <td>${f.probability_of_positive_return != null ? (f.probability_of_positive_return * 100).toFixed(0) + "%" : "—"}</td>
      <td>${f.confidence}</td></tr>`;
  }).join("");
  const note = fcs.method_note
    ? `<div class="note">${fcs.method_note}</div>` : "";
  return `<div class="table-wrap"><table class="scen-table">
    <thead><tr><th>Horizon</th><th>Expected price</th><th>E[return]</th>
    <th>Range (bear–bull)</th><th>P(positive)</th><th>Confidence</th></tr></thead>
    <tbody>${rows}</tbody></table></div>${note}`;
}

function scenTable(scen, fc) {
  const rows = scen.map((s) => `
    <tr><td>${s.name}</td><td>${(s.probability * 100).toFixed(0)}%</td>
    <td>${s.eps != null ? s.eps.toFixed(2) : "—"}</td>
    <td>${s.valuation_multiple != null ? s.valuation_multiple.toFixed(1) + "×" : "—"}</td>
    <td>$${s.fair_value.toFixed(2)}</td></tr>`).join("");
  const exp = fc ? `<div class="note">Probability-weighted expected return
    ${fmtSignedPct(fc.expected_return_pct)} over 12 months
    (P(positive) ${fc.probability_of_positive_return != null ? (fc.probability_of_positive_return * 100).toFixed(0) + "%" : "—"},
    confidence ${fc.confidence || "—"}). Range $${fc.fair_value_low}–$${fc.fair_value_high}.
    Computed by calculate_scenario_values / calculate_expected_return.</div>` : "";
  return `<div class="table-wrap"><table class="scen-table">
    <thead><tr><th>Scenario</th><th>Prob.</th><th>EPS</th><th>Multiple</th><th>Fair value</th></tr></thead>
    <tbody>${rows}</tbody></table></div>${exp}`;
}

/* ---------------- quarterly table (accessibility table view) ---------------- */
function renderQuarterlyTable(root, periods) {
  root.innerHTML = `<details class="qtable panel wide"><summary>Quarterly history — table view</summary>
    <div class="table-wrap"><table class="coverage"><thead><tr>
      <th>Period</th><th>End</th><th>Filed</th><th>Revenue</th><th>Gross profit</th>
      <th>Op income</th><th>Net income</th><th>Dil. EPS</th><th>OCF</th><th>FCF</th><th>Source</th>
    </tr></thead><tbody>
    ${[...periods].reverse().map((p) => `<tr>
      <td style="cursor:default"><span class="tk">${p.period_id}</span>${p.derived_q4 ? ' <span class="mini">(derived)</span>' : ""}</td>
      <td>${p.period_end}</td><td>${p.filed_at || "—"}</td>
      <td>${fmtMoney(p.income_statement.revenue)}</td>
      <td>${fmtMoney(p.income_statement.gross_profit)}</td>
      <td>${fmtMoney(p.income_statement.operating_income)}</td>
      <td>${fmtMoney(p.income_statement.net_income)}</td>
      <td>${p.income_statement.diluted_eps != null ? p.income_statement.diluted_eps.toFixed(2) : "—"}</td>
      <td>${fmtMoney(p.cash_flow.operating_cash_flow)}</td>
      <td>${fmtMoney(p.cash_flow.free_cash_flow)}</td>
      <td class="mini">${p.form || ""}</td>
    </tr>`).join("")}
    </tbody></table></div></details>`;
}

/* ---------------- boot ---------------- */
(async function boot() {
  try {
    state.companies = await fetchJSON("/api/companies");
  } catch (e) {
    $("#main").innerHTML = `<div class="loading">Failed to reach API: ${e.message}</div>`;
    return;
  }
  renderSidebar();
  renderHome();
})();
