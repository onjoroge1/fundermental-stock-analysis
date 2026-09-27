'use strict';
const $=id=>document.getElementById(id);
const make=(tag,content,cls)=>{const x=document.createElement(tag);if(content!==undefined)x.textContent=content;if(cls)x.className=cls;return x;};
const numeric=v=>v!==null&&v!==undefined&&v!==''&&typeof v!=='boolean'&&Number.isFinite(Number(v));
const money=v=>numeric(v)?'$'+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
let csrf='',settings=null,tradingMode={mode:'RESEARCH',version:0},running=false;
const errors={INVALID_LOGIN:'Username or password was not accepted.',ADMIN_PASSWORD_NOT_CONFIGURED:'Admin password is not configured on the server.',LOGIN_RATE_LIMITED:'Too many login attempts. Try again after the five-minute rate window.',PASSWORD_LENGTH_INVALID:'Use 12–128 characters.',NEW_PASSWORD_MUST_DIFFER:'Choose a different password.',CAPTURE_PAUSED:'Research is paused. Resume it before starting a pilot step.',SETTINGS_CHANGED_RELOAD:'Another request changed these controls. Refresh before trying again.',TRADING_SETTINGS_CHANGED_RELOAD:'Trading mode changed in another request. Refresh and retry.',TRADING_MODE_NOT_SUPPORTED:'Only RESEARCH and PAPER are available in Agent Trading v1.',DAILY_PILOT_LIMIT_REACHED:'The three-run UTC daily budget has been reached. Existing runs can still be resumed.',ADMIN_STORAGE_OR_OPERATION_UNAVAILABLE:'Admin storage or operation unavailable. Inspect server status.',LOGIN_REQUIRED:'Your session expired. Sign in again.'};
function message(value,error=false){$('message').textContent=value;$('message').className='message '+(error?'error':'success');$('message').hidden=false;}
function show(id){for(const x of ['loading','login','change','console'])$(x).hidden=x!==id;}
async function api(path,method='GET',data,extra={}){const headers={...extra};if(method!=='GET'){headers['Content-Type']='application/json';if(csrf)headers['X-CSRF-Token']=csrf;}const response=await fetch('/api/operator'+path,{method,headers,credentials:'same-origin',cache:'no-store',body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok){if(response.status===401&&result.error_code==='LOGIN_REQUIRED'){csrf='';show('login');}throw Error(errors[result.error_code]||result.error_code||'Request failed.');}return result;}
async function session(){try{const s=await api('/session');csrf=s.csrf_token||'';if(!s.authenticated)show('login');else{show('console');await refresh();}}catch(e){show('loading');$('loading').replaceChildren(make('h2','Admin is not ready'),make('p',e.message));}}
function bindForm(id,handler){$(id).addEventListener('submit',async e=>{e.preventDefault();const buttons=[...e.target.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await handler();}catch(error){message(error.message,true);}finally{buttons.forEach(b=>b.disabled=false);}});}
bindForm('login-form',async()=>{const pass=$('password').value;$('password').value='';const s=await api('/login','POST',{username:$('username').value,password:pass});csrf=s.csrf_token;show('console');await refresh();});
bindForm('change-form',async()=>{const current=$('current-password').value,next=$('new-password').value;$('current-password').value='';$('new-password').value='';const s=await api('/password','POST',{current_password:current,new_password:next});csrf=s.csrf_token;show('console');message('Password changed. Previous sessions were revoked.');await refresh();});
async function logout(){try{await api('/logout','POST',{});csrf='';show('login');}catch(e){message(e.message,true);}}
$('logout').addEventListener('click',logout);$('change-logout').addEventListener('click',logout);$('password-button').addEventListener('click',()=>show('change'));
function tradingText(v){const t=v.trading||{};if(!t.status)return 'Not processed';return (t.execution_mode||'RESEARCH')+' · '+t.status+(t.action?' · '+t.action:'');}
function renderRuns(runs){$('runs').replaceChildren();if(!runs.length){$('runs').append(make('div','No manual pilot runs yet. Starting a run creates a durable, resumable record.','empty'));return;}for(const r of runs){const card=make('article',undefined,'card run'),head=make('div',undefined,'run-head'),title=make('div');title.append(make('strong',r.created_at),make('div',r.run_id,'mono hint'));head.append(title);if(r.items.some(i=>['PENDING','RUNNING'].includes(i.status))){const resume=make('button','Resume','secondary');resume.disabled=running||!settings.capture_enabled;resume.addEventListener('click',()=>drive(r.run_id));head.append(resume);}card.append(head);const wrap=make('div',undefined,'table-wrap'),table=make('table'),thead=make('thead'),tr=make('tr');for(const label of ['Stock','Task','Research','Paper intent','Price date','Evidence / issue'])tr.append(make('th',label));thead.append(tr);table.append(thead);const tb=make('tbody');for(const item of r.items){const row=make('tr'),v=item.result||{},state=make('td');state.append(make('span',item.status,'status '+item.status));row.append(make('td',item.ticker),state,make('td',v.decision_status?v.decision_status+' / '+v.action:'Not yet captured'),make('td',tradingText(v)),make('td',v.price_date||'—'));const info=make('td');if(v.decision_id){const link=make('a','Open stock journal');link.href='/agents/'+encodeURIComponent(item.ticker);info.append(link,make('div',v.decision_id,'mono hint'));}if(v.error_code)info.append(make('p',v.error_code));const t=v.trading||{};if(t.blockers&&t.blockers.length)info.append(make('p','Paper: '+t.blockers.join(' · '),'hint'));if(v.provider_status)info.append(make('div','Massive: '+v.provider_status+' · News: '+v.news_status,'hint'));if(v.blockers&&v.blockers.length)info.append(make('p',v.blockers.join(' · '),'hint'));row.append(info);tb.append(row);}table.append(tb);wrap.append(table);card.append(wrap);$('runs').append(card);}}
function tradeTime(value){
  if(!value)return 'Not recorded';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?'Not recorded':date.toLocaleString('en-US',{timeZone:'America/New_York',year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
}
function savedSignal(risk){
  const selector=(risk||{}).selector||{};
  const parts=[];
  if(selector.selected_action)parts.push('Selected '+selector.selected_action);
  if(selector.desired_side)parts.push('Direction '+selector.desired_side);
  if(numeric(selector.score))parts.push('Score '+Number(selector.score).toFixed(1));
  const thresholds=selector.thresholds||{};
  if(numeric(thresholds.long_min))parts.push('Long at ≥ '+thresholds.long_min);
  if(numeric(thresholds.short_max))parts.push('Short at ≤ '+thresholds.short_max);
  if(risk&&risk.source_report_classification)parts.push('Report '+risk.source_report_classification);
  return parts.join(' · ')||'Signal details were not recorded.';
}
function tradeRationale(x,closed){
  const cell=make('td',undefined,'trade-rationale'),details=make('details');
  cell.append(make('p',savedSignal(x.entry_risk),'trade-signal'));
  details.append(make('summary','Reasons & evidence'));
  const add=(label,value)=>{const p=make('p');p.append(make('strong',label+': '),document.createTextNode(value||'Not recorded'));details.append(p);};
  add('Opening reason',x.entry_rationale);
  add('Source thesis',x.source_thesis);
  add('Thesis invalidation',x.source_invalidation);
  if(closed){add('Closing reason',x.exit_rationale||x.exit_reason);add('Closing signal',savedSignal(x.exit_risk));}
  add('Recorded research horizon',x.horizon_sessions?x.horizon_sessions+' sessions (not a scheduled close)':'Not recorded');
  add('Simulated costs','Entry '+money(x.entry_cost_usd)+(closed?' · Exit '+money(x.exit_cost_usd):' · Exit cost applies when closed'));
  add('Entry price date',x.entry_market_date);
  if(closed)add('Exit price date',x.exit_market_date);
  const risk=x.entry_risk||{};
  add('Strategy version',(risk.selector||{}).version);
  if(numeric(risk.max_position_pct))add('Position limit at entry',risk.max_position_pct+'% of equity');
  if(numeric(risk.max_gross_pct))add('Gross exposure limit at entry',risk.max_gross_pct+'% of equity');
  add('Source report',x.source_report_id);
  add('Opening decision',x.source_decision_id);
  if(closed)add('Closing decision',x.exit_decision_id);
  add('Position ID',x.position_id);
  const link=make('a','View '+x.ticker+' decision journal');link.href='/agents/'+encodeURIComponent(x.ticker);details.append(link);
  cell.append(details);return cell;
}
function renderPositionRows(id,positions,closed){
  const body=$(id);body.replaceChildren();
  for(const x of positions){
    const tr=make('tr'),stock=make('td'),dates=make('td'),prices=make('td'),pnl=make('td');
    stock.append(make('strong',x.ticker),make('div',x.side,'hint'),make('span',closed?'CLOSED':'OPEN','status '+(closed?'CLOSED':'OPEN')));
    dates.append(make('div','Opened '+tradeTime(x.opened_at)),make('div',closed?'Closed '+tradeTime(x.closed_at):'Still open','hint'));
    prices.append(make('div','Entry '+money(x.entry_price)),make('div',(closed?'Exit ':'Latest ')+money(closed?x.exit_price:x.price),'hint'));
    if(!closed)prices.append(make('div','Price date '+(x.market_date||'Unavailable'),'hint'));
    const value=closed?x.realized_pnl_usd:x.unrealized_pnl_usd;
    pnl.append(make('strong',money(value)),make('div',numeric(value)?'After '+(closed?'entry + exit costs':'entry cost'):'Current mark unavailable','hint'));
    tr.append(stock,dates,make('td',numeric(x.paper_units)?Number(x.paper_units).toLocaleString('en-US',{maximumFractionDigits:6}):'—'),prices,make('td',money(closed?x.entry_notional_usd:x.gross_notional_usd)),pnl,tradeRationale(x,closed));
    body.append(tr);
  }
}
function renderPaper(p){
  $('paper-equity').textContent=money(p.equity_usd);
  $('paper-pnl').textContent='Realized '+money(p.realized_pnl_usd)+' · Unrealized '+money(p.unrealized_pnl_usd);
  $('paper-exposure').textContent='Gross exposure '+money(p.gross_exposure_usd)+' · '+(p.open_count||0)+' open positions';
  const positions=p.positions||[],closed=p.closed_positions||[];
  $('paper-empty').hidden=positions.length>0;$('paper-closed-empty').hidden=closed.length>0;
  $('paper-history-summary').textContent=p.closed_history_truncated?'Showing the latest '+closed.length+' of '+p.closed_count+' closed positions.':closed.length+' closed position'+(closed.length===1?'':'s');
  renderPositionRows('paper-positions',positions,false);renderPositionRows('paper-closed',closed,true);
}
function renderTrading(t){tradingMode=t.mode||{mode:'RESEARCH',version:0};const paper=t.paper||{};const paperMode=tradingMode.mode==='PAPER';$('trading-mode-badge').textContent='MODE: '+tradingMode.mode+' · BROKER OFF';$('trading-state').textContent=paperMode?'Paper simulation active':'Research only';$('trading-detail').textContent=paperMode?'Recorded agent decisions can create simulated fills after deterministic risk checks.':'Research decisions are recorded; no simulated position is opened.';$('mode-research').disabled=!paperMode;$('mode-paper').disabled=paperMode;renderPaper(paper);}
function renderIntelligence(v){const body=$('intelligence-rows');body.replaceChildren();const rows=(v&&v.rows)||[];const usable=rows.filter(x=>x.status==='OK');$('intelligence-empty').hidden=usable.length>0;for(const x of usable){const tr=make('tr');const state=(x.direction||'—')+(x.bias_score!==null&&x.bias_score!==undefined?' · '+Number(x.bias_score).toFixed(2):'');const technical=(x.technical_trend||'—')+' · '+(x.volatility_regime||'—');const news=x.news_pressure===null||x.news_pressure===undefined?'—':Number(x.news_pressure).toFixed(2);const router=(x.router_instrument||'—')+(x.router_strategy?' · '+x.router_strategy:'')+(x.router_selected?' · '+x.router_selected:'');const bandit=(x.bandit_selected||'—')+(x.bandit_ucb!==null&&x.bandit_ucb!==undefined?' · UCB '+Number(x.bandit_ucb).toFixed(2):'')+(x.bandit_observations!==null&&x.bandit_observations!==undefined?' · n='+x.bandit_observations:'');const reward=x.latest_reward===null||x.latest_reward===undefined?'—':Number(x.latest_reward).toFixed(3);tr.append(make('td',x.ticker),make('td',x.mode||'—'),make('td',state),make('td',technical),make('td',news),make('td',router),make('td',bandit),make('td',reward));body.append(tr);}}
async function refresh(){try{const data=await api('/dashboard');settings=data.controls;$('capture-state').textContent=settings.capture_enabled?'Research running':'Research paused';$('capture-detail').textContent='Control version '+settings.version+' · No redeploy required.';$('toggle-capture').textContent=settings.capture_paused?'Resume research':'Pause research';$('run-pilot').disabled=running||!settings.capture_enabled;$('updated').textContent='Controls saved at '+settings.updated_at;renderTrading(data.trading||{});renderIntelligence(data.intelligence_v2||{});renderRuns(data.runs);const massive=data.connections.massive;$('massive-state').textContent=massive.configured?'Server-side key configured':'No server-side key configured';$('observations').replaceChildren();for(const o of massive.observations){const div=make('div',undefined,'observation');div.append(make('strong',o.ticker),make('span',o.status+' · '+(o.market_date||'No market date')));$('observations').append(div);}$('audit').replaceChildren();for(const e of data.audit){const d=make('div',undefined,'audit-item');d.append(make('strong',e.event),make('p',e.actor+' · '+e.occurred_at,'hint'),make('p',JSON.stringify(e.details),'mono'));$('audit').append(d);}if(!data.audit.length)$('audit').append(make('p','No administrative events.','hint'));}catch(e){message(e.message,true);}}
$('refresh').addEventListener('click',refresh);
$('toggle-capture').addEventListener('click',async()=>{if(!settings)return;const paused=!settings.capture_paused;const reason=window.prompt((paused?'Pause':'Resume')+' research: enter a short reason for the audit log.');if(reason===null)return;try{await api('/controls','POST',{capture_paused:paused,expected_version:settings.version,reason});message(paused?'New research captures are paused. In-flight work may finish.':'Research resumed.');await refresh();}catch(e){message(e.message,true);}});
async function setMode(mode){if(tradingMode.mode===mode)return;const reason=window.prompt('Switch agent trading mode to '+mode+'? Enter a short audit reason.');if(reason===null)return;try{await api('/trading-mode','POST',{mode,expected_version:tradingMode.version||0,reason});message(mode==='PAPER'?'Paper simulation enabled. Broker submission remains disabled.':'Research-only mode enabled. Existing paper positions remain recorded and are not silently closed.');await refresh();}catch(e){message(e.message,true);}}
$('mode-research').addEventListener('click',()=>setMode('RESEARCH'));$('mode-paper').addEventListener('click',()=>setMode('PAPER'));
async function drive(runId){if(running)return;running=true;try{for(let i=0;i<5;i++){await refresh();const result=await api('/runs/'+encodeURIComponent(runId)+'/step','POST',{});await refresh();if(result.status==='NO_RUNNABLE_ITEM'){message('No new step can be claimed. Refresh status; an existing step may still hold its lease.');break;}if(result.status==='FAILED'){message('A pilot step failed. The saved record preserves the error; no result was invented.',true);break;}}}catch(e){message(e.message,true);}finally{running=false;await refresh();}}
$('run-pilot').addEventListener('click',async()=>{if(running)return;const paper=tradingMode.mode==='PAPER';const prompt=paper?'Run all five agents and simulate deterministic paper trades where qualified? No broker orders will be sent.':'Run bounded research for all five pilot stocks? No simulated or broker trades will be created.';if(!window.confirm(prompt))return;const runId=crypto.randomUUID();try{await api('/runs','POST',{request_id:runId});await drive(runId);}catch(e){message(e.message,true);}});
session();
