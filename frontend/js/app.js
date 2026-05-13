/* ============================================================
   期权卖方助手 — SPA Core
   ============================================================ */

const API = '';  // same origin

/** Market/display timezone (matches scheduler settlement times). */
const APP_TIME_ZONE = 'America/New_York';

// ---- State ----
let _currentPage = 'screener';
let _pendingEntry = null;  // candidate row for entry modal
let _settings = {};

// ================================================================
// Router
// ================================================================
function getPage() {
  const hash = location.hash.replace('#', '') || 'screener';
  return ['screener','positions','review','settings'].includes(hash) ? hash : 'screener';
}

function setSectionLoading(page, on) {
  const sec = document.getElementById(`page-${page}`);
  if (!sec) return;
  const el = sec.querySelector('.js-page-loading');
  if (!el) return;
  el.classList.toggle('hidden', !on);
}

function setScreenerScanLoading(on) {
  const panel = document.getElementById('screener-candidates-panel');
  if (!panel) return;
  const el = panel.querySelector('.js-screener-scan-loading');
  if (!el) return;
  el.classList.toggle('hidden', !on);
}

function showPage(page) {
  _currentPage = page;
  document.querySelectorAll('section[id^="page-"]').forEach(s => s.classList.add('hidden'));
  document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
  const section = document.getElementById(`page-${page}`);
  if (section) section.classList.remove('hidden');
  const link = document.querySelector(`.nav-link[data-page="${page}"]`);
  if (link) link.classList.add('active');
  loadPage(page);
}

async function loadPage(page) {
  setSectionLoading(page, true);
  try {
    if (page === 'screener') await loadScreener();
    else if (page === 'positions') await loadPositions();
    else if (page === 'review') await loadReview();
    else if (page === 'settings') await loadSettings();
  } finally {
    setSectionLoading(page, false);
  }
}

window.addEventListener('hashchange', () => showPage(getPage()));

// ================================================================
// Toast
// ================================================================
const _toasts = [];
function toast(message, level = 'info') {
  if (_toasts.length >= 5 && level !== 'danger') return;
  const container = document.getElementById('toast-container');
  const div = document.createElement('div');
  div.className = `toast-item toast-${level}`;
  div.innerHTML = `<span class="flex-1">${message}</span>`;
  if (level !== 'danger') {
    setTimeout(() => div.remove(), 5000);
  } else {
    const btn = document.createElement('button');
    btn.className = 'text-white/70 hover:text-white text-xs ml-2';
    btn.textContent = '✕';
    btn.onclick = () => div.remove();
    div.appendChild(btn);
  }
  container.appendChild(div);
  _toasts.push(div);
}

// ================================================================
// SSE
// ================================================================
function connectSSE() {
  const dot = document.getElementById('sse-dot');
  const label = document.getElementById('sse-label');

  const es = new EventSource(`${API}/api/events/stream`);
  es.addEventListener('event', e => {
    try {
      const ev = JSON.parse(e.data);
      const lvl = ev.level === 'danger' ? 'danger' : ev.level === 'warn' ? 'warn' : 'info';
      toast(ev.title, lvl);
      refreshBell();
      if (_currentPage === 'positions') loadPositions();
    } catch {}
  });
  es.onopen = () => {
    dot.className = 'w-2 h-2 rounded-full bg-green-400';
    label.textContent = '已连接';
  };
  es.onerror = () => {
    dot.className = 'w-2 h-2 rounded-full bg-red-500';
    label.textContent = '断开';
    setTimeout(connectSSE, 5000);
    es.close();
  };
}

// ================================================================
// Bell / Events
// ================================================================
async function refreshBell() {
  const data = await apiFetch('/api/events?unread=true&limit=20');
  const count = data.length;
  const badge = document.getElementById('bell-count');
  if (count > 0) {
    badge.textContent = count > 9 ? '9+' : count;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
  const list = document.getElementById('bell-list');
  list.innerHTML = data.slice(0, 20).map(ev => {
    const lvlClass = ev.level === 'danger' ? 'text-red-400' : ev.level === 'warn' ? 'text-yellow-400' : 'text-blue-400';
    return `<li class="px-3 py-2 hover:bg-gray-700 cursor-pointer text-xs" onclick="ackEvent(${ev.id})">
      <span class="${lvlClass} font-medium">[${ev.level.toUpperCase()}]</span>
      <span class="ml-1 text-gray-200">${ev.title}</span>
      <div class="text-gray-500 mt-0.5">${ev.created_at ? formatEtDatetime(ev.created_at) : ''}</div>
    </li>`;
  }).join('') || '<li class="px-3 py-4 text-center text-gray-500 text-xs">无未读事件</li>';
}

async function ackEvent(id) {
  await apiFetch(`/api/events/${id}/ack`, {method: 'PUT'});
  refreshBell();
}

document.getElementById('bell-btn').addEventListener('click', () => {
  const dd = document.getElementById('bell-dropdown');
  dd.classList.toggle('hidden');
});

document.getElementById('mark-all-read').addEventListener('click', async () => {
  await apiFetch('/api/events/all-read', {method: 'POST'});
  refreshBell();
});

document.addEventListener('click', e => {
  const bc = document.getElementById('bell-container');
  if (!bc.contains(e.target)) {
    document.getElementById('bell-dropdown').classList.add('hidden');
  }
});

// ================================================================
// #screener
// ================================================================

/** Mirrors app/core/strategy.py score_csp_candidates weights & normalization ranges. */
const _SCORE_WEIGHT_DEFAULTS = {
  annualized_roi: 0.35,
  iv_rank: 0.25,
  spread_pct: 0.15,
  margin_buffer: 0.15,
  open_interest: 0.10,
};

function _scoreWeight(weights, key) {
  const v = weights && typeof weights === 'object' ? weights[key] : undefined;
  return typeof v === 'number' && !Number.isNaN(v) ? v : _SCORE_WEIGHT_DEFAULTS[key];
}

function buildScoreFormulaTipHtml(weights) {
  if (!weights || typeof weights !== 'object') {
    return '<span class="text-gray-400">无法读取当前评分权重。</span>';
  }
  const wRoi = _scoreWeight(weights, 'annualized_roi');
  const wIv = _scoreWeight(weights, 'iv_rank');
  const wSp = _scoreWeight(weights, 'spread_pct');
  const wMb = _scoreWeight(weights, 'margin_buffer');
  const wOi = _scoreWeight(weights, 'open_interest');
  const f = (x) => x.toFixed(2);
  const terms = [
    `${f(wRoi)}×norm(年化ROI,0.15,0.50)`,
    `${f(wIv)}×norm(IV排名,30,90)，缺数据按50`,
    `${f(wSp)}×(1−norm(价差,0.02,0.15))`,
    `${f(wMb)}×norm(安全垫,0.05,0.30)`,
    `${f(wOi)}×norm(未平仓量,50,5000)`,
  ];
  return `
    <span class="block text-gray-300 mb-1">当前配置：先对每项归一化到 0～1，再按下式加权求和（与后台筛选打分一致）。</span>
    <span class="block font-mono text-[10px] text-indigo-200/95 mb-1.5 break-all leading-relaxed">Score = ${terms.join(' + ')}</span>
    <span class="block text-gray-400 mb-1">
      价差=(卖−买)/Mid；安全垫=(现价−行权)/现价；年化ROI=(Mid/行权)×(365/DTE)；IV排名缺省时按 50 代入 norm。
    </span>
    <span class="block text-gray-500 border-t border-gray-700 pt-1.5 leading-snug">
      norm(x,a,b)=min(1,max(0,(x−a)/(b−a)))。权重可在「设置 → 评分权重」修改。
    </span>
  `;
}

function renderScoreFormulaTip(weights) {
  const el = document.getElementById('score-formula-tip');
  if (!el) return;
  el.innerHTML = buildScoreFormulaTipHtml(weights);
}

async function syncScoreFormulaTip() {
  try {
    const s = await apiFetch('/api/settings');
    renderScoreFormulaTip(s.scoring_weights);
  } catch {
    renderScoreFormulaTip(null);
  }
}

/** Current API returns { schema, candidates, run }; legacy returned a bare candidate array []. */
function normalizeScanLatestResponse(raw) {
  if (Array.isArray(raw)) {
    return { candidates: raw, run: null };
  }
  if (raw && typeof raw === 'object') {
    return {
      candidates: Array.isArray(raw.candidates) ? raw.candidates : [],
      run: raw.run != null ? raw.run : null,
      schema: raw.schema,
    };
  }
  return { candidates: [], run: null };
}

async function fetchScanLatest() {
  const raw = await apiFetch('/api/scan/latest');
  return normalizeScanLatestResponse(raw);
}

/** Poll until the given scan run row has finished_at (manual scan); avoids stale /latest data. */
async function pollScanRunUntilDone(runId, maxWaitMs = 120000, intervalMs = 2000) {
  const t0 = Date.now();
  let last = { candidates: [], run: null };
  while (Date.now() - t0 < maxWaitMs) {
    const raw = await apiFetch(`/api/scan/run/${runId}`);
    last = normalizeScanLatestResponse(raw);
    const run = last.run;
    if (run && run.finished_at) {
      return last;
    }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  toast('扫描等待超时（可能仍需更久），请稍后刷新本页查看', 'warn');
  return last;
}

/** Same as pre–run_id API: latest stays on previous finished row until the new run completes. */
async function pollScanLatestUntilNewerFinished(baselineRunId, maxWaitMs = 120000, intervalMs = 2000) {
  const t0 = Date.now();
  let last = { candidates: [], run: null };
  while (Date.now() - t0 < maxWaitMs) {
    last = await fetchScanLatest();
    const run = last.run;
    if (run && run.finished_at && run.id > baselineRunId) {
      return last;
    }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  toast('扫描等待超时（可能仍需更久），请稍后刷新本页查看', 'warn');
  return last;
}

/** Match repo `_watchlist_entry_enabled`: NULL/missing counts as enabled. */
function isWatchlistEntryEnabled(w) {
  const v = w.enabled;
  if (v === null || v === undefined) return true;
  const n = Number(v);
  return !Number.isNaN(n) ? n !== 0 : Boolean(v);
}

async function loadScreener() {
  const [wl, scanData] = await Promise.all([
    apiFetch('/api/watchlist'),
    fetchScanLatest(),
  ]);
  await syncScoreFormulaTip();
  const symbols = wl.filter(isWatchlistEntryEnabled).map(w => w.symbol).join(', ');
  document.getElementById('watchlist-input').value = symbols;

  const { candidates, run } = scanData;
  renderCandidates(candidates, run);
}

function renderCandidates(rows, scanRun) {
  const tbody = document.getElementById('candidates-tbody');
  const empty = document.getElementById('candidates-empty');
  if (!rows || rows.length === 0) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    if (!scanRun) {
      empty.textContent = '暂无扫描记录，点击「立即扫描」开始筛选';
    } else if (!scanRun.finished_at) {
      empty.textContent = '扫描进行中，请稍候（拉取期权链可能需要数十秒）…';
    } else {
      empty.textContent = '本轮扫描已完成：暂无符合当前「入场过滤」条件的合约，可在「设置」放宽条件或更换标的。';
    }
    return;
  }
  empty.classList.add('hidden');
  tbody.innerHTML = rows.map(r => `
    <tr class="hover:bg-gray-700/50 cursor-pointer text-center text-xs">
      <td class="px-3 py-2 text-left font-medium text-white">
        ${r.symbol}
        ${r.iv_rank !== null && r.iv_rank !== undefined ? `<span class="ml-1 badge-info text-xs">IV${Math.round(r.iv_rank)}</span>` : ''}
      </td>
      <td>${r.expiration || '-'}</td>
      <td>${fmt(r.strike, 2)}</td>
      <td>${fmt(r.mid, 2)}</td>
      <td>${fmt(r.delta, 3)}</td>
      <td>${r.dte ?? '-'}</td>
      <td>${r.iv ? (r.iv * 100).toFixed(1) + '%' : '-'}</td>
      <td>${r.iv_rank != null ? r.iv_rank.toFixed(0) : '-'}</td>
      <td class="font-medium ${(r.annualized_roi ?? 0) >= 0.25 ? 'text-green-400' : ''}">${r.annualized_roi ? (r.annualized_roi * 100).toFixed(1) + '%' : '-'}</td>
      <td>${r.pop ? (r.pop * 100).toFixed(0) + '%' : '-'}</td>
      <td>${r.spread_pct ? (r.spread_pct * 100).toFixed(1) + '%' : '-'}</td>
      <td>${r.margin_buffer ? (r.margin_buffer * 100).toFixed(1) + '%' : '-'}</td>
      <td class="font-bold text-indigo-300">${fmt(r.score, 3)}</td>
      <td>
        <button onclick='openEntryModal(${JSON.stringify(r)})'
          class="bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded text-xs">入场</button>
      </td>
    </tr>
  `).join('');
}

document.getElementById('btn-scan').addEventListener('click', async () => {
  const btn = document.getElementById('btn-scan');
  btn.textContent = '扫描中...';
  btn.disabled = true;
  setScreenerScanLoading(true);
  toast('正在请求扫描…', 'info');
  try {
    const baseline = await fetchScanLatest();
    const baselineRunId = baseline.run && baseline.run.id != null ? baseline.run.id : 0;
    const started = await apiFetch('/api/scan/run', {method: 'POST'});
    const runId = started && typeof started === 'object' && started.run_id != null
      ? started.run_id
      : null;
    toast('后台扫描已启动，等待行情与期权链…', 'info');
    const { candidates, run } = runId != null
      ? await pollScanRunUntilDone(runId)
      : await pollScanLatestUntilNewerFinished(baselineRunId);
    renderCandidates(candidates, run);
    const n = candidates && candidates.length;
    const done = run && run.finished_at;
    if (done && (!n || n === 0)) {
      toast('本轮扫描已完成：当前过滤条件下无候选。可在「设置」放宽入场过滤。', 'warn');
    } else if (n > 0) {
      toast(`已加载 ${n} 条候选`, 'info');
    }
  } catch(e) {
    toast('扫描启动失败: ' + e.message, 'danger');
  } finally {
    setScreenerScanLoading(false);
    btn.textContent = '立即扫描';
    btn.disabled = false;
  }
});

document.getElementById('btn-save-watchlist').addEventListener('click', async () => {
  const raw = document.getElementById('watchlist-input').value;
  await apiFetch('/api/watchlist', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({symbols: raw}),
  });
  toast('观察名单已保存', 'info');
  if (_currentPage === 'screener') setSectionLoading('screener', true);
  try {
    await loadScreener();
  } finally {
    if (_currentPage === 'screener') setSectionLoading('screener', false);
  }
});

// ================================================================
// Entry Modal
// ================================================================
function openEntryModal(row) {
  _pendingEntry = row;
  const info = document.getElementById('modal-info');
  info.innerHTML = `
    <div><span class="text-gray-400">标的：</span>${row.symbol}</div>
    <div><span class="text-gray-400">到期：</span>${row.expiration}（DTE ${row.dte}）</div>
    <div><span class="text-gray-400">行权价：</span>${row.strike}</div>
    <div><span class="text-gray-400">当前 Mid：</span>${fmt(row.mid, 2)}</div>
    <div><span class="text-gray-400">Delta：</span>${fmt(row.delta, 3)}</div>
    <div><span class="text-gray-400">年化ROI：</span>${row.annualized_roi ? (row.annualized_roi * 100).toFixed(1) + '%' : '-'}</div>
  `;
  document.getElementById('modal-premium').value = row.mid ? row.mid.toFixed(2) : '';
  document.getElementById('modal-contracts').value = 1;
  document.getElementById('modal-notes').value = '';
  document.getElementById('entry-modal').classList.remove('hidden');
}

document.getElementById('modal-cancel').addEventListener('click', () => {
  document.getElementById('entry-modal').classList.add('hidden');
  _pendingEntry = null;
});

document.getElementById('modal-confirm').addEventListener('click', async () => {
  if (!_pendingEntry) return;
  const premium = parseFloat(document.getElementById('modal-premium').value);
  const contracts = parseInt(document.getElementById('modal-contracts').value, 10);
  const notes = document.getElementById('modal-notes').value;
  if (isNaN(premium) || premium <= 0) { toast('请输入有效的成交 Mid', 'danger'); return; }
  try {
    await apiFetch('/api/positions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol: _pendingEntry.symbol,
        expiration: _pendingEntry.expiration,
        strike: _pendingEntry.strike,
        contracts,
        open_premium: premium,
        open_candidate_id: _pendingEntry.id,
        notes,
      }),
    });
    toast(`${_pendingEntry.symbol} 入场成功`, 'info');
    document.getElementById('entry-modal').classList.add('hidden');
    _pendingEntry = null;
  } catch(e) {
    toast('入场失败: ' + e.message, 'danger');
  }
});

// ================================================================
// #positions
// ================================================================
const POSITIONS_EMPTY_DEFAULT =
  '暂无持仓。可在筛选页确认入场，或使用本页「手动添加」录入卖 Put 持仓。';

/** Format an ISO-8601 instant for display in US Eastern (DST-aware). */
function formatEtDatetime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  let s = new Intl.DateTimeFormat('en-CA', {
    timeZone: APP_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d);
  s = s.replace(', ', ' ');
  return `${s} 美东`;
}

function formatQuotedAt(iso) {
  return formatEtDatetime(iso);
}

function updateGlobalQuoteLabel(iso) {
  const el = document.getElementById('global-quote-as-of');
  if (!el) return;
  if (!iso) {
    el.classList.add('hidden');
    el.textContent = '';
    el.removeAttribute('title');
    return;
  }
  el.textContent = '行情时间：' + formatQuotedAt(iso);
  el.title = iso;
  el.classList.remove('hidden');
}

function openManualPositionModal() {
  document.getElementById('manual-symbol').value = '';
  document.getElementById('manual-expiration').value = '';
  document.getElementById('manual-strike').value = '';
  document.getElementById('manual-contracts').value = '1';
  document.getElementById('manual-premium').value = '';
  document.getElementById('manual-notes').value = '';
  document.getElementById('manual-position-modal').classList.remove('hidden');
}

function closeManualPositionModal() {
  document.getElementById('manual-position-modal').classList.add('hidden');
}

document.getElementById('btn-manual-position').addEventListener('click', () => openManualPositionModal());
document.getElementById('manual-position-cancel').addEventListener('click', () => closeManualPositionModal());
document.getElementById('manual-position-save').addEventListener('click', async () => {
  const symbol = (document.getElementById('manual-symbol').value || '').trim().toUpperCase().replace(/\s+/g, '');
  const expiration = document.getElementById('manual-expiration').value;
  const strike = parseFloat(document.getElementById('manual-strike').value);
  const contracts = parseInt(document.getElementById('manual-contracts').value, 10);
  const open_premium = parseFloat(document.getElementById('manual-premium').value);
  const notesRaw = (document.getElementById('manual-notes').value || '').trim();

  if (!symbol) {
    toast('请输入标的代码', 'danger');
    return;
  }
  if (!expiration) {
    toast('请选择到期日', 'danger');
    return;
  }
  if (isNaN(strike) || strike <= 0) {
    toast('请输入有效行权价', 'danger');
    return;
  }
  if (isNaN(contracts) || contracts < 1) {
    toast('张数至少为 1', 'danger');
    return;
  }
  if (isNaN(open_premium) || open_premium <= 0) {
    toast('请输入有效的开仓权利金（每股）', 'danger');
    return;
  }

  const body = {
    symbol,
    expiration,
    strike,
    contracts,
    open_premium,
  };
  if (notesRaw) body.notes = notesRaw;

  try {
    await apiFetch('/api/positions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    toast(`${symbol} 已添加`, 'info');
    closeManualPositionModal();
    await loadPositions();
  } catch (e) {
    toast('添加失败：' + e.message, 'danger');
  }
});

function renderPositionCard(p) {
  const m = p.mark || {};
  const spotTxt = m.quote_error ? '—' : fmt(m.spot, 2);
  const midTxt = m.quote_error ? '—' : fmt(m.option_mid, 2);
  const pnlPctTxt = (m.quote_error || m.pnl_pct == null) ? '—' : (m.pnl_pct * 100).toFixed(1) + '%';
  const pnlUsdTxt = (m.quote_error || m.unrealized_pnl_usd == null) ? '—' : ('$' + fmt(m.unrealized_pnl_usd, 2));
  let errHtml = '';
  if (m.quote_error) {
    errHtml = `<div class="text-[10px] text-red-400 break-words">标的：${String(m.quote_error).slice(0, 200)}</div>`;
  } else if (m.chain_error) {
    errHtml = `<div class="text-[10px] text-yellow-500 break-words">期权：${String(m.chain_error).slice(0, 200)}（Mid 暂用开仓价）</div>`;
  }
  return `
      <div class="bg-gray-800 rounded-lg p-4 space-y-2">
        <div class="flex items-center justify-between gap-2">
          <span class="font-bold text-white shrink-0">${p.symbol}</span>
          <span class="text-xs text-gray-400 border border-gray-600 rounded px-2 py-0.5 shrink-0">OPEN</span>
        </div>
        <div class="text-xs text-gray-400 space-y-0.5">
          <div>股票现价 <span class="text-white">${spotTxt}</span></div>
          <div>期权理论价（BS）<span class="text-white">${midTxt}</span></div>
          <div>浮盈比例 <span class="text-white">${pnlPctTxt}</span> · 未实现盈亏 <span class="text-emerald-300">${pnlUsdTxt}</span></div>
          <div>行权价 <span class="text-white">${p.strike}</span> | 到期 <span class="text-white">${p.expiration}</span></div>
          <div>开仓价 <span class="text-white">${fmt(p.open_premium, 2)}</span> × ${p.contracts} 张</div>
          <div>开仓时间 ${p.open_at ? formatEtDatetime(p.open_at) : '-'}</div>
        </div>
        ${errHtml}
        ${p.notes ? `<div class="text-xs text-gray-500 italic break-words">${p.notes}</div>` : ''}
        <div class="flex gap-2 mt-2">
          <button onclick="closePosition(${p.id}, '${p.symbol}')"
            class="flex-1 bg-red-700 hover:bg-red-600 text-white text-xs py-1 rounded">平仓</button>
        </div>
      </div>`;
}

async function loadPositions() {
  const grid = document.getElementById('positions-grid');
  const empty = document.getElementById('positions-empty');
  try {
    const data = await apiFetch('/api/positions/marks');
    const positions = data.positions || [];
    updateGlobalQuoteLabel(data.quoted_at);
    if (!positions.length) {
      grid.innerHTML = '';
      empty.textContent = POSITIONS_EMPTY_DEFAULT;
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');
    grid.innerHTML = positions.map(renderPositionCard).join('');
  } catch (e) {
    toast('加载持仓失败：' + e.message, 'danger');
    grid.innerHTML = '';
    empty.textContent = '加载失败，请稍后重试或检查服务：' + e.message;
    empty.classList.remove('hidden');
  }
}

window.closePosition = async function(pid, symbol) {
  const premium = parseFloat(prompt(`平仓价格（buy back mid），${symbol}：`));
  if (isNaN(premium)) return;
  const reasons = ['take_profit_50','take_profit_75','time_14d','time_7d','danger_3pct','delta_breach','manual'];
  const reason = prompt(`出场原因（${reasons.join('/')}）：`, 'manual');
  if (!reason) return;
  try {
    await apiFetch(`/api/positions/${pid}/close`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({close_premium: premium, close_reason: reason}),
    });
    toast(`${symbol} 已平仓`, 'info');
    loadPositions();
  } catch(e) {
    toast('平仓失败: ' + e.message, 'danger');
  }
};

document.getElementById('btn-refresh-quotes').addEventListener('click', async () => {
  const btn = document.getElementById('btn-refresh-quotes');
  btn.disabled = true;
  try {
    if (_currentPage === 'positions') {
      await loadPositions();
      toast('行情已更新', 'info');
    } else {
      const data = await apiFetch('/api/positions/marks');
      updateGlobalQuoteLabel(data.quoted_at);
      toast(`已更新行情时间 ${formatQuotedAt(data.quoted_at)}`, 'info');
    }
  } catch (e) {
    toast('刷新失败：' + e.message, 'danger');
  } finally {
    btn.disabled = false;
  }
});

// ================================================================
// #review
// ================================================================
const REVIEW_CLOSE_REASON_LABELS = {
  expired_otm: '到期 OTM',
  take_profit_50: '止盈（50%）',
  take_profit_strong: '强止盈',
  assigned: '指派',
  manual: '手动',
  delta_breach: 'Delta 突破',
  time_danger: '时间危险',
  time_warning: '时间警告',
};

function reviewCloseReasonLabel(code) {
  if (code == null || code === '') return '-';
  return REVIEW_CLOSE_REASON_LABELS[code] ?? code;
}

async function loadReview() {
  let summary = null;
  try {
    summary = await apiFetch('/api/review/summary');
  } catch {
    document.getElementById('review-summary').innerHTML =
      '<div class="col-span-full text-sm text-gray-500">复盘数据不可用（尚无已结束持仓）</div>';
    document.getElementById('breakdown-tbody').innerHTML =
      '<tr><td colspan="4" class="text-center text-gray-500 py-3 text-xs">—</td></tr>';
    document.getElementById('review-orders-tbody').innerHTML =
      '<tr><td colspan="11" class="text-center text-gray-500 py-3 text-xs">—</td></tr>';
    return;
  }
  const realizedNum = summary.total_realized_pnl != null ? Number(summary.total_realized_pnl) : null;
  const realizedCls = realizedNum == null ? 'text-indigo-300' : realizedNum >= 0 ? 'text-emerald-400' : 'text-rose-400';
  const maeeNum = summary.avg_maee != null ? Number(summary.avg_maee) : null;
  const mfeNum = summary.avg_mfe != null ? Number(summary.avg_mfe) : null;
  const cards = [
    {
      label: '结转盈亏',
      value: realizedNum == null ? '-' : '$' + realizedNum.toFixed(0),
      valueClass: realizedCls,
      hint: '所有已结束持仓的已实现盈亏合计',
    },
    {label: '总胜率', value: summary.win_rate != null ? (summary.win_rate * 100).toFixed(1) + '%' : '-'},
    {
      label: '资本回报率（均值）',
      value: summary.avg_roe != null ? (summary.avg_roe * 100).toFixed(1) + '%' : '-',
      hint: '已实现盈亏 / 入场冻结保证金（行权价×100×张数）',
    },
    {
      label: '开仓权利金合计',
      value: summary.total_premium != null ? '$' + summary.total_premium.toFixed(0) : '-',
      hint: '各笔开仓应收权利金相加',
    },
    {label: '交易笔数', value: summary.trade_count ?? '-'},
    {
      label: '夏普比率',
      value: summary.sharpe_ratio != null ? summary.sharpe_ratio.toFixed(2) : '-',
      hint: '逐笔 ROE 均值 / 标准差，衡量风险调整后收益',
    },
    {
      label: '索提诺比率',
      value: summary.sortino_ratio != null ? summary.sortino_ratio.toFixed(2) : '-',
      hint: '逐笔 ROE 均值 / 下行标准差，排除上行波动',
    },
    {
      label: 'MAEE / MFE 均值',
      value: (maeeNum != null && mfeNum != null)
        ? (maeeNum * 100).toFixed(1) + '% / +' + (mfeNum * 100).toFixed(1) + '%'
        : '-',
      hint: '最大浮亏比例均值 / 最大浮盈比例均值（从雷达快照）',
    },
  ];
  document.getElementById('review-summary').innerHTML = cards.map(c => `
    <div class="bg-gray-800 rounded-lg p-4 text-center">
      <div class="text-xl font-bold ${c.valueClass || 'text-indigo-300'} leading-tight">${c.value}</div>
      <div class="text-xs text-gray-400 mt-1 leading-snug">${c.label}</div>
      ${c.hint ? `<div class="text-[10px] text-gray-500 mt-0.5 leading-tight">${c.hint}</div>` : ''}
    </div>
  `).join('');

  const breakdown = summary.by_close_reason || [];
  document.getElementById('breakdown-tbody').innerHTML = breakdown.map(row => `
    <tr class="text-center text-xs">
      <td class="text-left py-1">${row.close_reason ?? '-'}</td>
      <td>${row.count ?? 0}</td>
      <td>${row.win_rate != null ? (row.win_rate * 100).toFixed(0) + '%' : '-'}</td>
      <td>${row.avg_roi != null ? (row.avg_roi * 100).toFixed(1) + '%' : '-'}</td>
    </tr>
  `).join('') || '<tr><td colspan="4" class="text-center text-gray-500 py-3 text-xs">暂无数据</td></tr>';

  let orders = [];
  let ordersLoadFailed = false;
  if (Array.isArray(summary.closed_positions)) {
    orders = summary.closed_positions;
  } else {
    try {
      const closedPayload = await apiFetch('/api/review/closed_positions');
      orders = closedPayload.positions || [];
    } catch {
      ordersLoadFailed = true;
    }
  }

  if (ordersLoadFailed) {
    document.getElementById('review-orders-tbody').innerHTML =
      '<tr><td colspan="11" class="text-center text-red-400 py-3 text-xs">历史订单加载失败</td></tr>';
  } else if (!orders.length) {
    document.getElementById('review-orders-tbody').innerHTML =
      '<tr><td colspan="11" class="text-center text-gray-500 py-3 text-xs">尚无已结束成交</td></tr>';
  } else {
    document.getElementById('review-orders-tbody').innerHTML = orders.map(p => {
      const pnl = p.realized_pnl;
      const pnlNum = pnl != null ? Number(pnl) : null;
      const pnlCls = pnlNum == null ? 'text-gray-300' : pnlNum >= 0 ? 'text-emerald-400' : 'text-rose-400';
      const pnlTxt = pnlNum == null ? '-' : '$' + pnlNum.toFixed(0);
      const note = (p.notes != null && String(p.notes).trim() !== '') ? String(p.notes) : '—';
      return `
    <tr class="text-xs cursor-pointer hover:bg-gray-700/50" onclick="openAttrDrawer(${p.id}, '${(p.symbol||'').replace(/'/g,"\\'")} ${p.expiration || ''} $${fmt(p.strike,0)} Put')">
      <td class="text-left py-1.5 font-medium text-gray-200">${p.symbol ?? '-'}</td>
      <td class="text-left py-1.5 text-gray-300">${p.expiration ?? '-'}</td>
      <td class="text-right py-1.5 text-gray-300">${fmt(p.strike, 2)}</td>
      <td class="text-right py-1.5 text-gray-300">${p.contracts ?? '-'}</td>
      <td class="text-left py-1.5 text-gray-400 whitespace-nowrap">${formatEtDatetime(p.open_at)}</td>
      <td class="text-right py-1.5 text-gray-300">${fmt(p.open_premium, 2)}</td>
      <td class="text-left py-1.5 text-gray-400 whitespace-nowrap">${formatEtDatetime(p.close_at)}</td>
      <td class="text-right py-1.5 text-gray-300">${p.close_premium != null ? fmt(p.close_premium, 2) : '—'}</td>
      <td class="text-right py-1.5 font-medium ${pnlCls}">${pnlTxt}</td>
      <td class="text-left py-1.5 text-gray-300">${reviewCloseReasonLabel(p.close_reason)}</td>
      <td class="text-left py-1.5 text-gray-400 max-w-[8rem] truncate" title="${note.replace(/"/g, '&quot;')}">${note}</td>
    </tr>`;
    }).join('');
  }
}

// ================================================================
// Attribution Drawer
// ================================================================

function openAttrDrawer(positionId, title) {
  document.getElementById('attr-drawer-title').textContent = title || '持仓详情';
  document.getElementById('attr-drawer-body').innerHTML =
    '<div class="flex items-center justify-center py-8 text-gray-500 text-sm">加载中…</div>';
  document.getElementById('attr-drawer-overlay').classList.remove('hidden');
  const drawer = document.getElementById('attr-drawer');
  drawer.classList.remove('translate-x-full');
  drawer.classList.add('translate-x-0');
  loadAttrDrawerData(positionId);
}

function closeAttrDrawer() {
  document.getElementById('attr-drawer-overlay').classList.add('hidden');
  const drawer = document.getElementById('attr-drawer');
  drawer.classList.remove('translate-x-0');
  drawer.classList.add('translate-x-full');
}

async function loadAttrDrawerData(positionId) {
  try {
    const [attr, snap] = await Promise.all([
      apiFetch(`/api/review/positions/${positionId}/attribution`),
      apiFetch(`/api/review/positions/${positionId}/snapshot`),
    ]);
    document.getElementById('attr-drawer-body').innerHTML = renderAttrDrawer(attr, snap);
  } catch (e) {
    document.getElementById('attr-drawer-body').innerHTML =
      `<div class="text-rose-400 text-sm py-4 text-center">加载失败：${e.message}</div>`;
  }
}

function renderAttrDrawer(attr, snap) {
  const sections = [];

  // ---- PnL Attribution ----
  if (attr.data_available && attr.delta_contribution != null) {
    const totalPnl = attr.total_pnl || 0;
    const absTotal = Math.abs(totalPnl) || 1;
    const items = [
      { label: 'Delta 贡献', value: attr.delta_contribution, hint: '标的价格变动' },
      { label: 'Theta 贡献', value: attr.theta_contribution, hint: '时间流逝收益' },
      { label: '残差（含Vega）', value: attr.residual, hint: '隐含波动率变动等' },
    ];
    const bars = items.map(item => {
      const v = item.value ?? 0;
      const pct = Math.min(Math.abs(v) / absTotal * 100, 100).toFixed(0);
      const barCls = v >= 0 ? 'bg-emerald-500' : 'bg-rose-500';
      const txtCls = v >= 0 ? 'text-emerald-400' : 'text-rose-400';
      return `
        <div class="space-y-0.5">
          <div class="flex justify-between text-xs">
            <span class="text-gray-300">${item.label}</span>
            <span class="${txtCls} font-medium">${v >= 0 ? '+' : ''}$${v.toFixed(1)}</span>
          </div>
          <div class="text-[10px] text-gray-500">${item.hint}</div>
          <div class="h-1.5 rounded-full bg-gray-700">
            <div class="h-1.5 rounded-full ${barCls}" style="width:${pct}%"></div>
          </div>
        </div>`;
    }).join('');
    const totalCls = totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
    sections.push(`
      <div class="bg-gray-800 rounded-lg p-3 space-y-3">
        <div class="flex justify-between text-xs font-semibold text-gray-200 border-b border-gray-700 pb-2">
          <span>PnL 归因（首阶 BS）</span>
          <span class="${totalCls}">${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(1)} 总计</span>
        </div>
        ${bars}
        <div class="text-[10px] text-gray-500 pt-1">持仓 ${attr.days_held ?? '?'} 天 · 雷达快照 ${attr.radar_points ?? 0} 条</div>
      </div>`);
  } else if (attr.data_available === false) {
    sections.push(`
      <div class="bg-gray-800 rounded-lg p-3 text-center text-xs text-gray-500">
        PnL 归因不可用（无入场希腊值数据）
      </div>`);
  }

  // ---- MAE / MFE ----
  if (attr.mae != null || attr.mfe != null) {
    const maePct = attr.mae != null ? (attr.mae * 100).toFixed(1) : '-';
    const mfePct = attr.mfe != null ? (attr.mfe * 100).toFixed(1) : '-';
    const maeCls = attr.mae != null && attr.mae < 0 ? 'text-rose-400' : 'text-gray-300';
    sections.push(`
      <div class="bg-gray-800 rounded-lg p-3">
        <div class="text-xs font-semibold text-gray-200 mb-2">浮动极值（入场时机评估）</div>
        <div class="grid grid-cols-2 gap-3 text-center">
          <div>
            <div class="text-lg font-bold ${maeCls}">${maePct}%</div>
            <div class="text-[10px] text-gray-400">MAEE（最大浮亏）</div>
            <div class="text-[10px] text-gray-500">越负说明接飞刀风险越高</div>
          </div>
          <div>
            <div class="text-lg font-bold text-emerald-400">${mfePct}%</div>
            <div class="text-[10px] text-gray-400">MFE（最大浮盈）</div>
            <div class="text-[10px] text-gray-500">止盈机会最大化参考</div>
          </div>
        </div>
      </div>`);
  }

  // ---- Entry Snapshot ----
  const snapshotData = (snap && snap.open_snapshot) || (snap && snap.candidate_data);
  if (snapshotData) {
    const rows = [];
    const fmtPct = v => v != null ? (v * 100).toFixed(1) + '%' : '-';
    const fmtNum = (v, d=2) => v != null ? Number(v).toFixed(d) : '-';
    if (snapshotData.iv_rank != null) rows.push(['IV Rank', fmtNum(snapshotData.iv_rank, 1) + '%']);
    if (snapshotData.iv != null) rows.push(['隐含波动率（IV）', fmtPct(snapshotData.iv)]);
    if (snapshotData.delta != null) rows.push(['Delta（入场）', fmtNum(snapshotData.delta, 3)]);
    if (snapshotData.theta != null) rows.push(['Theta（日衰）', fmtNum(snapshotData.theta, 4)]);
    if (snapshotData.spot != null) rows.push(['入场标的价', '$' + fmtNum(snapshotData.spot, 2)]);
    if (snapshotData.dte != null) rows.push(['入场 DTE', snapshotData.dte + ' 天']);
    if (snapshotData.rsi_6 != null) rows.push(['RSI(6)', fmtNum(snapshotData.rsi_6, 1)]);
    if (snapshotData.rsi_12 != null) rows.push(['RSI(12)', fmtNum(snapshotData.rsi_12, 1)]);
    if (snapshotData.rsi_24 != null) rows.push(['RSI(24)', fmtNum(snapshotData.rsi_24, 1)]);
    if (snapshotData.bb_distance_pct != null) {
      const bbv = Number(snapshotData.bb_distance_pct);
      const bbCls = bbv < 0 ? 'text-rose-400' : 'text-gray-200';
      rows.push(['距布林带下轨', `<span class="${bbCls}">${bbv.toFixed(1)}%</span>`]);
    }
    if (snapshotData.annualized_roi != null) rows.push(['入场年化收益', fmtPct(snapshotData.annualized_roi)]);
    if (snapshotData.score != null) rows.push(['候选评分', fmtNum(snapshotData.score, 1)]);

    if (rows.length > 0) {
      const tableRows = rows.map(([k, v]) => `
        <tr class="border-b border-gray-700 last:border-0">
          <td class="py-1 text-gray-400 text-xs">${k}</td>
          <td class="py-1 text-right text-xs text-gray-100">${v}</td>
        </tr>`).join('');
      sections.push(`
        <div class="bg-gray-800 rounded-lg p-3">
          <div class="text-xs font-semibold text-gray-200 mb-2">入场环境快照</div>
          <table class="w-full">${tableRows}</table>
          <div class="text-[10px] text-gray-500 mt-1">${snap.open_snapshot ? '✓ 实时捕获' : '来源：候选记录'}</div>
        </div>`);
    }
  } else {
    sections.push(`
      <div class="bg-gray-800 rounded-lg p-3 text-center text-xs text-gray-500">
        入场快照不可用（历史持仓未捕获技术指标）
      </div>`);
  }

  return sections.join('') || '<div class="text-gray-500 text-sm text-center py-4">无可用数据</div>';
}

// ================================================================
// #settings（展示用中文标签；data-key 仍为后端 JSON 键名）
// ================================================================
const SETTINGS_FIELD_LABELS = {
  filters: {
    delta_min: 'Delta 下限',
    delta_max: 'Delta 上限',
    dte_min: '最短到期（天）',
    dte_max: '最长到期（天）',
    annualized_roi_min: '最低年化收益',
    spread_pct_max: '买卖价差上限',
    iv_rank_min: '最低 IV 排名',
    margin_buffer_min: '最低保证金冗余',
    min_open_interest: '最小未平仓量',
    exclude_earnings_within_days: '财报前几日不入场',
  },
  exits: {
    take_profit_pct: '止盈阈值',
    take_profit_strong_pct: '强止盈阈值',
    time_warning_dte: '时间警告（剩余天）',
    time_danger_dte: '时间危险（剩余天）',
    danger_distance_pct: '危险价差距离',
    delta_breach_abs: 'Delta 突破（绝对值）',
  },
  scoring_weights: {
    annualized_roi: '年化收益',
    iv_rank: 'IV 排名',
    spread_pct: '价差',
    margin_buffer: '保证金冗余',
    open_interest: '未平仓量',
  },
  schedule: {
    screener_minutes: '筛选间隔（分钟）',
    radar_minutes: '雷达间隔（分钟）',
    settlement_time_et: '结算时刻（美东）',
    iv_refresh_time_et: 'IV 刷新时刻（美东）',
  },
  fees: {
    usd_per_contract: '每张合约单边手续费（美元）；平仓与到期指派按开仓+平仓双边合计',
  },
};

function settingsFieldLabel(groupKey, fieldKey) {
  return SETTINGS_FIELD_LABELS[groupKey]?.[fieldKey] ?? fieldKey;
}

async function loadSettings() {
  _settings = await apiFetch('/api/settings');
  renderSettingsForm(_settings);
}

function renderSettingsForm(s) {
  const container = document.getElementById('settings-form');
  const groups = [
    {key: 'filters', label: '入场过滤'},
    {key: 'exits', label: '出场信号'},
    {key: 'scoring_weights', label: '评分权重'},
    {key: 'schedule', label: '调度计划'},
    {key: 'fees', label: '费用'},
  ];
  container.innerHTML = groups.map(g => {
    const data = s[g.key] ?? {};
    const fields = Object.entries(data).map(([k, v]) => `
      <div class="flex items-center gap-3">
        <label class="min-w-[10rem] max-w-[12rem] shrink-0 text-xs text-gray-400 leading-snug">${settingsFieldLabel(g.key, k)}</label>
        <input data-group="${g.key}" data-key="${k}" type="${typeof v === 'number' ? 'number' : 'text'}"
          step="any" value="${v}"
          class="flex-1 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-indigo-500" />
      </div>
    `).join('');
    return `<div class="bg-gray-800 rounded-lg p-4 space-y-2">
      <h3 class="text-sm font-semibold text-gray-300 mb-2">${g.label}</h3>
      ${fields}
    </div>`;
  }).join('');
}

document.getElementById('btn-save-settings').addEventListener('click', async () => {
  const inputs = document.querySelectorAll('#settings-form input[data-group]');
  const partial = {};
  inputs.forEach(inp => {
    const g = inp.dataset.group, k = inp.dataset.key;
    if (!partial[g]) partial[g] = {};
    const v = inp.value;
    partial[g][k] = isNaN(parseFloat(v)) ? v : parseFloat(v);
  });
  try {
    _settings = await apiFetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(partial),
    });
    renderScoreFormulaTip(_settings.scoring_weights);
    toast('设置已保存，Worker 调度已更新', 'info');
  } catch(e) {
    toast('保存失败: ' + e.message, 'danger');
  }
});

// ================================================================
// Utilities
// ================================================================
async function apiFetch(url, options = {}) {
  const resp = await fetch(API + url, options);
  const text = await resp.text();
  if (!resp.ok) {
    let msg = text || resp.statusText;
    try {
      const j = JSON.parse(text);
      if (j && typeof j.error === 'string' && j.error) msg = j.error;
    } catch (_) { /* plain text body */ }
    throw new Error(msg || resp.statusText);
  }
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json') || ct.includes('+json')) {
    try {
      return JSON.parse(text);
    } catch (_) {
      throw new Error('响应不是合法 JSON');
    }
  }
  const t = text.trim();
  if (
    (t.startsWith('{') && t.endsWith('}')) ||
    (t.startsWith('[') && t.endsWith(']'))
  ) {
    try {
      return JSON.parse(text);
    } catch (_) { /* return raw below */ }
  }
  return text;
}

function fmt(v, decimals = 2) {
  if (v == null || v === undefined || v === '') return '-';
  const n = parseFloat(v);
  return isNaN(n) ? '-' : n.toFixed(decimals);
}

// ================================================================
// Boot
// ================================================================
(async () => {
  showPage(getPage());
  connectSSE();
  refreshBell();
  // Refresh bell every 60s
  setInterval(refreshBell, 60000);
})();
