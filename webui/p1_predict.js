/* P1 Prediction Lab overlay. Loaded after app.js and intentionally replaces
   only renderPredict; the rest of the dashboard remains unchanged. */

renderPredict = async function (ticker) {
  const m = $("#main");
  const sel = ticker || state.predictTicker || "AAPL";
  state.predictTicker = sel;
  const options = state.companies.map((c) =>
    `<option value="${c.ticker}" ${c.ticker === sel ? "selected" : ""}>${c.ticker}</option>`).join("");

  m.innerHTML = `
    <div class="page-head">
      <div>
        <div class="eyebrow">P1 DECISION INTELLIGENCE</div>
        <div class="page-title">Prediction Lab <select id="pred-ticker" class="ticker-select">${options}</select></div>
      </div>
    </div>
    <div class="page-sub">Benchmark-relative forecasts, current market regime, macro stress and observed option-implied information. Research models remain challengers until their point-in-time out-of-sample gates pass.</div>
    <div id="pred-body"><div class="loading">Loading ${sel} decision intelligence…</div></div>`;
  $("#pred-ticker").addEventListener("change", (e) => renderPredict(e.target.value));

  let r;
  try { r = await fetchJSON(`/api/p1/${sel}`); }
  catch (e) { $("#pred-body").innerHTML = `<div class="banner">P1 load failed: ${e.message}</div>`; return; }
  if (r.status !== "OK") {
    $("#pred-body").innerHTML = `<div class="banner">No P1 view: ${r.reason || r.status}</div>`;
    return;
  }

  const h20 = (r.alpha.horizons || []).find((x) => x.days === 20) || {};
  const h63 = (r.alpha.horizons || []).find((x) => x.days === 63) || {};
  const regime = r.regime || {};
  const rf = regime.features || {};
  const macro = (r.macro || {}).features || {};
  const opt = (r.options_implied || {}).features || {};
  const research = r.p1_research || {};
  const promotion = research.promotion || {};
  const alphaPromotion = r.alpha.promotion || {};
  const expectations = r.alpha.current_expectation_features || {};

  const pct100 = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
  const num = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);
  const signed = (v, d = 1) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const pill = (v) => `<span class="p1-pill">${v || "PENDING"}</span>`;

  const modelRows = Object.entries(research.models || {}).map(([name, model]) => {
    const verdict = model.verdict || {};
    const metric = model.ensemble_mean_ic ?? model.lightgbm_mean_ic ?? model.options_mean_ic ?? model.macro_mean_ic ?? null;
    const passed = verdict.ensemble_beats_best_single_model ?? verdict.lightgbm_beats_all_controls ?? verdict.options_model_beats_all_controls ?? false;
    return `<tr><td>${name}</td><td>${model.status || "—"}</td><td>${metric == null ? "—" : Number(metric).toFixed(3)}</td><td>${passed ? "PASS" : "NO"}</td></tr>`;
  }).join("") || `<tr><td colspan="4">No completed P1 research run yet.</td></tr>`;

  $("#pred-body").innerHTML = `
    <div class="p1-status-row">
      ${pill(`Confidence ${r.confidence}`)}
      ${pill(`Regime ${regime.classification || "UNKNOWN"}`)}
      ${pill(`P1 ${promotion.decision || "PENDING"}`)}
      ${pill(`Alpha ${alphaPromotion.deployed_as_primary ? "PRIMARY" : "CHALLENGER"}`)}
    </div>

    <div class="p1-grid">
      <div class="p1-card"><div class="p1-k">20d expected excess</div><div class="p1-v">${signed(h20.expected_excess_return_pct)}</div><div class="p1-s">vs ${r.alpha.benchmark || "benchmark"}</div></div>
      <div class="p1-card"><div class="p1-k">20d P(outperform)</div><div class="p1-v">${pct100(h20.prob_outperform)}</div><div class="p1-s">OOS gate: ${(h20.validation || {}).passes ? "pass" : "not passed"}</div></div>
      <div class="p1-card"><div class="p1-k">63d expected excess</div><div class="p1-v">${signed(h63.expected_excess_return_pct)}</div><div class="p1-s">residual σ ${h63.residual_sigma_pct == null ? "—" : num(h63.residual_sigma_pct, 1) + "%"}</div></div>
      <div class="p1-card"><div class="p1-k">63d P(outperform)</div><div class="p1-v">${pct100(h63.prob_outperform)}</div><div class="p1-s">P(underperform by 10pp): ${pct100(h63.prob_underperform_benchmark_by_10pct)}</div></div>
    </div>

    <div class="p1-two">
      <section class="p1-panel">
        <h3>Market + macro regime</h3>
        <div class="p1-facts">
          <span>SPY 63d momentum <b>${rf.market_mom_63 == null ? "—" : signed((Math.exp(rf.market_mom_63) - 1) * 100)}</b></span>
          <span>SPY vol 21d <b>${rf.market_vol_21 == null ? "—" : pct100(rf.market_vol_21)}</b></span>
          <span>Sector vs SPY 63d <b>${rf.sector_vs_spy_63 == null ? "—" : signed(rf.sector_vs_spy_63 * 100)}</b></span>
          <span>VIX <b>${num(macro.vix_level, 1)}</b></span>
          <span>10y–2y <b>${num(macro.curve_10y2y, 2)}</b></span>
          <span>HY OAS <b>${num(macro.hy_oas, 2)}</b></span>
        </div>
      </section>
      <section class="p1-panel">
        <h3>Options-implied state</h3>
        ${r.options_implied.available ? `<div class="p1-facts">
          <span>ATM IV <b>${pct100(opt.atm_iv)}</b></span>
          <span>25Δ put-call skew <b>${pct100(opt.iv_skew_25d)}</b></span>
          <span>Term slope <b>${pct100(opt.term_slope)}</b></span>
          <span>Expected move <b>${opt.expected_move_pct == null ? "—" : num(opt.expected_move_pct, 1) + "%"}</b></span>
          <span>Put/call OI <b>${num(opt.put_call_oi_ratio, 2)}</b></span>
          <span>IV percentile <b>${pct100(opt.iv_percentile)}</b></span>
        </div>` : `<div class="banner">No persisted option surface yet. Run capture_option_surfaces.py where TWS/IB Gateway is reachable.</div>`}
      </section>
    </div>

    <div class="p1-two">
      <section class="p1-panel">
        <h3>Expectations evidence</h3>
        <div class="p1-facts">
          <span>EPS revision <b>${expectations.eps_revision == null ? "—" : signed(expectations.eps_revision * 100)}</b></span>
          <span>Revenue revision <b>${expectations.revenue_revision == null ? "—" : signed(expectations.revenue_revision * 100)}</b></span>
          <span>Latest EPS surprise <b>${expectations.latest_eps_surprise == null ? "—" : signed(expectations.latest_eps_surprise * 100)}</b></span>
          <span>4Q EPS surprise <b>${expectations.trailing_4q_eps_surprise == null ? "—" : signed(expectations.trailing_4q_eps_surprise * 100)}</b></span>
        </div>
      </section>
      <section class="p1-panel">
        <h3>Promotion state</h3>
        <div class="p1-facts">
          <span>P1 decision <b>${promotion.decision || "PENDING"}</b></span>
          <span>Selected challenger <b>${promotion.selected_candidate || "none"}</b></span>
          <span>Option coverage gate <b>${promotion.data_gate ? "PASS" : "NOT PASSED"}</b></span>
          <span>Production primary <b>${promotion.deployed_as_primary ? "YES" : "NO"}</b></span>
        </div>
      </section>
    </div>

    <section class="p1-panel">
      <h3>Out-of-sample model scoreboard</h3>
      <div class="table-wrap"><table><thead><tr><th>Model</th><th>Status</th><th>Mean IC</th><th>Incremental gate</th></tr></thead><tbody>${modelRows}</tbody></table></div>
    </section>

    <div class="page-sub p1-method">${(r.methodology || []).map((x) => `• ${x}`).join("<br>")}</div>`;
};
