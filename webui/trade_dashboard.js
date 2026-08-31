const $ = (id) => document.getElementById(id);
const fmtPct = (v, digits=1) => v == null ? '—' : `${(Number(v)*100).toFixed(digits)}%`;
const fmtNumPct = (v, digits=1) => v == null ? '—' : `${Number(v).toFixed(digits)}%`;
const fmtNum = (v, digits=2) => v == null ? '—' : Number(v).toFixed(digits);
const cls = (v) => Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : '';
const esc = (s) => String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

let state = null;

function card(label, value, sub='') {
  return `<div class="card"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="sub">${esc(sub)}</div></div>`;
}

function expressionName(row) {
  const exp = row?.trade_expression;
  if (!exp) return '—';
  if (exp.expression === 'option') return exp.selected?.strategy_type || 'option';
  return exp.expression || exp.status || '—';
}

function renderSummary(data) {
  const p = data.portfolio || {};
  const e = p.exposures || {};
  const indexed = data.research_index?.indexed_count ?? 0;
  const cohorts = data.forward_paper?.cohorts?.length ?? 0;
  $('summaryCards').innerHTML = [
    card('Gross exposure', fmtPct(e.gross), `${p.position_count || 0} proposed positions`),
    card('Net exposure', `<span class="${cls(e.net)}">${fmtPct(e.net)}</span>`, `Beta ${fmtNum(e.beta,2)}`),
    card('Research indexed', indexed, 'Opportunity ranking coverage'),
    card('Forward cohorts', cohorts, data.forward_paper?.status || 'PENDING'),
  ].join('');
  $('portfolioMeta').textContent = p.proposal_id ? `proposal ${p.proposal_id.slice(0,8)} · ${p.created_at || ''}` : 'No persisted P2 proposal';
}

function renderPositions(data) {
  const rows = data.portfolio?.positions || [];
  if (!rows.length) {
    $('positionsBody').innerHTML = `<tr><td colspan="9"><div class="empty">No persisted P2 portfolio proposal yet.</div></td></tr>`;
    return;
  }
  $('positionsBody').innerHTML = rows.map(row => {
    const weight = Number(row.weight || 0);
    const side = weight > 0 ? 'long' : 'short';
    const quality = row.data_quality_status || 'UNKNOWN';
    return `<tr>
      <td class="ticker">${esc(row.ticker)}</td>
      <td><span class="pill ${side}">${esc(row.direction)}</span></td>
      <td>${fmtPct(row.weight)}</td>
      <td class="${cls(row.expected_excess_return_pct)}">${fmtNumPct(row.expected_excess_return_pct)}</td>
      <td>${row.prob_outperform == null ? '—' : `${(Number(row.prob_outperform)*100).toFixed(1)}%`}</td>
      <td class="${cls(row.stock_expected_return_12m_pct)}">${fmtNumPct(row.stock_expected_return_12m_pct)}</td>
      <td><span class="pill ${quality === 'PASS' ? 'long' : quality === 'WARN' ? 'warn' : ''}">${esc(quality)}</span></td>
      <td>${esc(expressionName(row))}</td>
      <td><button class="mini-button" data-detail="${esc(row.ticker)}">Inspect</button></td>
    </tr>`;
  }).join('');
  document.querySelectorAll('[data-detail]').forEach(btn => btn.addEventListener('click', () => showDetail(btn.dataset.detail)));
}

function renderRankList(id, rows) {
  const el = $(id);
  if (!rows?.length) {
    el.innerHTML = `<div class="empty">Waiting for compact research-index coverage.</div>`;
    return;
  }
  el.innerHTML = rows.map((row, i) => `<div class="rank-row">
    <div class="rank-num">#${i+1}</div>
    <div class="ticker">${esc(row.ticker)}</div>
    <div><div class="rank-meta">${esc(row.sector || '—')} · ${esc(row.classification || '—')}</div><div class="rank-meta">Quality ${fmtNum(row.quality_score,0)} · data ${esc(row.data_quality_status || '—')}</div></div>
    <div class="rank-value ${cls(row.expected_return_12m_pct)}">${fmtNumPct(row.expected_return_12m_pct)}</div>
  </div>`).join('');
}

function renderValidation(data) {
  const lab = data.strategy_lab || {};
  if (lab.status !== 'OK') {
    $('strategyLab').innerHTML = `<div class="empty">${esc(lab.reason || 'Strategy Lab has not run yet.')}</div>`;
  } else {
    const eligible = lab.eligible || {};
    $('strategyLab').innerHTML = Object.entries(eligible).map(([mode, names]) => `<div class="validation-block"><strong>${esc(mode)}</strong><div class="tag-list">${names.length ? names.map(n => `<span class="pill long">${esc(n)}</span>`).join('') : '<span class="muted">No policies eligible</span>'}</div></div>`).join('') + `<div class="muted">Run ${esc(lab.run_id || '')} · ${esc(lab.as_of || '')}</div>`;
  }

  const forward = data.forward_paper || {};
  const cohorts = forward.cohorts || [];
  if (!cohorts.length) {
    $('forwardPaper').innerHTML = `<div class="empty">No frozen Forward Paper v2 cohorts yet.</div>`;
  } else {
    $('forwardPaper').innerHTML = cohorts.map(row => `<div class="validation-block"><strong>${esc(row.policy_name)} · ${esc(row.mode)}</strong><div class="tag-list"><span class="pill ${row.incubation?.status === 'REVIEW_ELIGIBLE' ? 'long' : row.incubation?.status === 'FAILED' ? 'short' : 'warn'}">${esc(row.incubation?.status || 'COLLECTING')}</span><span class="muted">${row.incubation?.complete_marks || 0} marks · ${row.incubation?.age_calendar_days || 0} days</span></div></div>`).join('');
  }
}

function renderInvalidations(data) {
  const rows = data.portfolio?.positions || [];
  $('invalidations').innerHTML = rows.length ? rows.map(row => `<div class="invalidation-card"><h3>${esc(row.ticker)} <span class="pill ${row.direction === 'LONG' ? 'long' : 'short'}">${esc(row.direction)}</span></h3>${row.thesis_summary ? `<p class="muted">${esc(row.thesis_summary)}</p>` : ''}<ul>${(row.invalidation_conditions || []).slice(0,5).map(x => `<li>${esc(x)}</li>`).join('') || '<li>No explicit invalidation conditions persisted.</li>'}</ul></div>`).join('') : `<div class="empty">No portfolio positions to monitor.</div>`;
}

function renderAutomation(data) {
  $('automation').textContent = JSON.stringify(data.automation || {}, null, 2);
}

function showDetail(ticker) {
  const row = state?.portfolio?.positions?.find(x => x.ticker === ticker);
  if (!row) return;
  $('detailTitle').textContent = `${ticker} decision evidence`;
  $('detailBody').innerHTML = `
    <div class="detail-section"><h4>Thesis</h4><p>${esc(row.thesis_summary || 'No thesis summary persisted.')}</p></div>
    <div class="detail-section"><h4>Invalidation</h4><ul>${(row.invalidation_conditions || []).map(x => `<li>${esc(x)}</li>`).join('') || '<li>None persisted.</li>'}</ul></div>
    <div class="detail-section"><h4>Persisted trade expression</h4><pre class="json">${esc(JSON.stringify(row.trade_expression || {}, null, 2))}</pre></div>
    <div class="detail-section"><h4>On-demand analysis</h4><p><a class="button" target="_blank" href="${esc(row.option_recommendation_url)}">Run PR35 option recommendation</a> <a class="button secondary" target="_blank" href="${esc(row.research_url)}">Open research packet</a></p></div>`;
  $('detailDialog').showModal();
}

function render(data) {
  state = data;
  $('statusStrip').className = 'status-strip';
  $('statusStrip').textContent = '';
  renderSummary(data);
  renderPositions(data);
  renderRankList('bullishList', data.opportunities?.bullish);
  renderRankList('bearishList', data.opportunities?.bearish);
  renderValidation(data);
  renderInvalidations(data);
  renderAutomation(data);
}

async function load() {
  $('app').classList.add('loading');
  try {
    const response = await fetch('/api/v1/trade-dashboard', {cache:'no-store'});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    render(await response.json());
  } catch (err) {
    $('statusStrip').className = 'status-strip error';
    $('statusStrip').textContent = `Dashboard unavailable: ${err.message}`;
  } finally {
    $('app').classList.remove('loading');
  }
}

$('refreshButton').addEventListener('click', load);
$('closeDialog').addEventListener('click', () => $('detailDialog').close());
load();
