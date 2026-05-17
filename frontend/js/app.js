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
let _lastScanRun = null;
let _lastPoolRows = [];
let _lastWatchRows = [];

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
// Toast（顶部居中浮层，默认约 3s 自动消失）
// ================================================================
const _toasts = [];
const TOAST_AUTO_HIDE_MS = 3000;

/** Remove oldest info/warn toasts so new feedback is never silently dropped at the cap. */
function _makeToastSlotPreferNonDanger(maxKeep = 4) {
  _pruneStaleToasts();
  while (_toasts.length > maxKeep) {
    const idx = _toasts.findIndex(el => el.isConnected && !el.classList.contains('toast-danger'));
    if (idx < 0) break;
    _toasts.splice(idx, 1)[0]?.remove();
  }
}

function toast(message, level = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) {
    console.warn('toast-container missing', message);
    return;
  }
  if (level !== 'danger') _makeToastSlotPreferNonDanger(4);
  const div = document.createElement('div');
  div.className = `toast-item toast-${level}`;
  div.setAttribute('role', 'status');

  let hideTimer = null;
  const armAutoHide = () => {
    hideTimer = setTimeout(() => {
      hideTimer = null;
      div.remove();
    }, TOAST_AUTO_HIDE_MS);
  };
  const dismiss = () => {
    if (hideTimer !== null) clearTimeout(hideTimer);
    hideTimer = null;
    div.remove();
  };

  if (level === 'danger') {
    div.innerHTML = `
      <span class="toast-danger-icon shrink-0 mt-0.5 inline-flex" aria-hidden="true">
        <svg class="h-5 w-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      </span>`;
    const textSpan = document.createElement('span');
    textSpan.className = 'toast-text flex-1 min-w-0';
    textSpan.textContent = message;
    div.appendChild(textSpan);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
      'shrink-0 rounded px-1.5 py-0.5 text-red-200/90 hover:bg-red-500/20 hover:text-white text-xs';
    btn.setAttribute('aria-label', '关闭');
    btn.textContent = '✕';
    btn.onclick = dismiss;
    div.appendChild(btn);
    _pruneStaleToasts();
    container.appendChild(div);
    _toasts.push(div);
    armAutoHide();
    return;
  }

  const textSpan = document.createElement('span');
  textSpan.className = 'flex-1 min-w-0';
  textSpan.textContent = message;
  div.appendChild(textSpan);
  armAutoHide();
  _pruneStaleToasts();
  container.appendChild(div);
  _toasts.push(div);
}

/** Drop references to detached toast nodes */
function _pruneStaleToasts() {
  for (let i = _toasts.length - 1; i >= 0; i--) {
    if (!_toasts[i].isConnected) _toasts.splice(i, 1);
  }
}

function normalizeHttpErrorMessage(bodyText, statusTextFallback) {
  const raw = String(bodyText ?? '').trim();
  if (!raw) return statusTextFallback || '请求失败';
  let obj = null;
  if (/^[{[]/.test(raw)) {
    try {
      obj = JSON.parse(raw);
    } catch (_) {
      obj = null;
    }
    if (obj && typeof obj === 'object' && typeof obj.error === 'string' && obj.error.trim()) {
      let msg = obj.error.trim();
      if (typeof obj.hint === 'string' && obj.hint.trim())
        msg = `${msg} — ${obj.hint.trim()}`;
      return msg;
    }
  }
  if (/<!doctype|<html[\s>]/i.test(raw)) {
    const t = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(raw);
    if (t && t[1]) return t[1].replace(/\s+/g, ' ').trim();
    const h = /<h1[^>]*>([\s\S]*?)<\/h1>/i.exec(raw);
    if (h && h[1])
      return h[1].replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
    return statusTextFallback || '请求失败（非常规响应）';
  }
  const one = raw.replace(/\s+/g, ' ');
  return one.length <= 420 ? one : one.slice(0, 417) + '…';
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
      scheduleRefreshBell();
      if (_currentPage === 'positions') scheduleLoadPositions();
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

function normalizePoolOptionsResponse(raw) {
  const rows = raw && Array.isArray(raw.options) ? raw.options : Array.isArray(raw) ? raw : [];
  return rows.map(row => ({
    ...row,
    option_pool_id: row.option_pool_id ?? row.id,
    source: 'pool',
  }));
}

function normalizeUnderlyingsResponse(raw) {
  return raw && Array.isArray(raw.underlyings) ? raw.underlyings : Array.isArray(raw) ? raw : [];
}

function normalizeOptionWatchesResponse(raw) {
  return raw && Array.isArray(raw.watches) ? raw.watches : Array.isArray(raw) ? raw : [];
}

function optionPoolFilterQuery() {
  const params = new URLSearchParams();
  const status = document.getElementById('option-pool-status-filter')?.value || 'NEW,ACTIVE';
  const quality = document.getElementById('option-pool-quality-filter')?.value || '';
  const entrySignalStatus = document.getElementById('option-pool-entry-signal-filter')?.value || '';
  const minScore = document.getElementById('option-pool-min-score')?.value || '';
  const minDte = document.getElementById('option-pool-min-dte')?.value || '';
  const maxDte = document.getElementById('option-pool-max-dte')?.value || '';
  if (status) params.set('status', status);
  if (quality) params.set('quality_grade', quality);
  if (entrySignalStatus) params.set('entry_signal_status', entrySignalStatus);
  if (minScore) params.set('min_score', minScore);
  if (minDte) params.set('min_dte', minDte);
  if (maxDte) params.set('max_dte', maxDte);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function fetchOptionPoolRows() {
  return normalizePoolOptionsResponse(await apiFetch(`/api/pool/options${optionPoolFilterQuery()}`));
}

async function refreshPoolSections(scanRun = _lastScanRun) {
  const [underlyingsRaw, poolRows, watchesRaw] = await Promise.all([
    apiFetch('/api/pool/underlyings'),
    fetchOptionPoolRows(),
    apiFetch('/api/watch/options'),
  ]);
  const underlyings = normalizeUnderlyingsResponse(underlyingsRaw);
  const watches = normalizeOptionWatchesResponse(watchesRaw);
  _lastPoolRows = poolRows;
  renderUnderlyings(underlyings);
  renderCandidates(poolRows, scanRun, { source: 'pool' });
  renderOptionWatches(watches);
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

/** Only GET polling — never POST /api/scan/run (manual scan is exclusively btn-scan). */
let _resumeScanPollRunId = null;

async function resumeUnfinishedScanPolling(runId) {
  if (runId == null || _resumeScanPollRunId === runId) return;
  _resumeScanPollRunId = runId;
  setScreenerScanLoading(true);
  try {
    const { candidates, run } = await pollScanRunUntilDone(runId);
    _lastScanRun = run;
    await refreshPoolSections(run);
    const n = candidates && candidates.length;
    const done = run && run.finished_at;
    if (done && (!n || n === 0)) {
      toast('本轮扫描已完成：当前过滤条件下无候选。可在「设置」放宽入场过滤。', 'warn');
    } else if (n > 0) {
      toast(`已加载 ${n} 条候选`, 'info');
    }
  } catch (e) {
    toast('拉取扫描结果失败：' + e.message, 'danger');
  } finally {
    setScreenerScanLoading(false);
    if (_resumeScanPollRunId === runId) _resumeScanPollRunId = null;
  }
}

async function loadScreener() {
  const [underlyingsRaw, poolRows, watchesRaw, scanData] = await Promise.all([
    apiFetch('/api/pool/underlyings'),
    fetchOptionPoolRows(),
    apiFetch('/api/watch/options'),
    fetchScanLatest(),
  ]);
  await syncScoreFormulaTip();
  const underlyings = normalizeUnderlyingsResponse(underlyingsRaw);
  const watches = normalizeOptionWatchesResponse(watchesRaw);
  const symbols = underlyings.filter(isWatchlistEntryEnabled).map(w => w.symbol).join(', ');
  document.getElementById('watchlist-input').value = symbols;

  const { run } = scanData;
  _lastScanRun = run;
  _lastPoolRows = poolRows;
  renderUnderlyings(underlyings);
  renderCandidates(poolRows, run, { source: 'pool' });
  renderOptionWatches(watches);
  // Page refresh / reopen must not POST a new scan; only resume watching an already-started run.
  if (run && !run.finished_at && run.id != null) {
    void resumeUnfinishedScanPolling(run.id);
  }
}

function poolStatusBadgeHtml(status) {
  const s = String(status || 'unknown').toUpperCase();
  const map = {
    ACTIVE: ['启用', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'],
    PAUSED: ['暂停', 'bg-amber-500/15 text-amber-300 border-amber-500/30'],
    ARCHIVED: ['归档', 'bg-gray-600/30 text-gray-300 border-gray-500/40'],
    NEW: ['NEW', 'bg-blue-500/15 text-blue-300 border-blue-500/30'],
    STALE: ['STALE', 'bg-amber-500/15 text-amber-300 border-amber-500/30'],
    EXPIRED: ['EXPIRED', 'bg-gray-600/30 text-gray-300 border-gray-500/40'],
    BLOCKED: ['BLOCKED', 'bg-red-500/15 text-red-300 border-red-500/30'],
    WATCHING: ['观察中', 'bg-blue-500/15 text-blue-300 border-blue-500/30'],
    READY: ['已达标', 'bg-amber-500/15 text-amber-300 border-amber-500/30'],
    IGNORED: ['已忽略', 'bg-gray-600/30 text-gray-300 border-gray-500/40'],
    OPENED: ['已入场', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'],
  };
  const [label, cls] = map[s] || [s, 'bg-gray-600/30 text-gray-300 border-gray-500/40'];
  return `<span class="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}">${label}</span>`;
}

function getEntrySignal(row) {
  const signal = row && typeof row.entry_signal === 'object' && row.entry_signal !== null
    ? row.entry_signal
    : {};
  const status = signal.status || row?.entry_signal_status || 'UNKNOWN';
  return {
    ...signal,
    status,
    decision_score: signal.decision_score ?? row?.entry_signal_score ?? null,
    summary: signal.summary || row?.entry_signal_summary || '',
    reasons: Array.isArray(signal.reasons) ? signal.reasons : [],
    blockers: Array.isArray(signal.blockers) ? signal.blockers : [],
  };
}

function entrySignalBadgeHtml(row) {
  const signal = getEntrySignal(row);
  const status = String(signal.status || 'UNKNOWN').toUpperCase();
  const map = {
    OPENABLE: ['可开仓', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'],
    WAIT: ['等待', 'bg-amber-500/15 text-amber-300 border-amber-500/30'],
    REJECT: ['拒绝', 'bg-red-500/15 text-red-300 border-red-500/30'],
    EXPIRED: ['过期', 'bg-gray-600/30 text-gray-300 border-gray-500/40'],
    UNKNOWN: ['未知', 'bg-gray-600/30 text-gray-300 border-gray-500/40'],
  };
  const [label, cls] = map[status] || map.UNKNOWN;
  const score = signal.decision_score != null ? ` · ${signal.decision_score}` : '';
  const title = [label + score, signal.summary].filter(Boolean).join('\n');
  return `<span title="${escapeAttr(title)}" class="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}">${label}${score}</span>`;
}

function entrySignalWarningHtml(row) {
  const signal = getEntrySignal(row);
  const status = String(signal.status || 'UNKNOWN').toUpperCase();
  if (status === 'OPENABLE') return '';
  const cls = status === 'REJECT'
    ? 'border-red-500/30 bg-red-500/10 text-red-200'
    : 'border-amber-500/30 bg-amber-500/10 text-amber-200';
  const label = status === 'WAIT' ? '当前为等待信号' : status === 'REJECT' ? '当前为拒绝信号' : '开仓信号未知';
  return `<div class="md:col-span-2 rounded border ${cls} px-2 py-1">${label}：${escapeHtml(signal.summary || '请先核对报价、风险和流动性。')}</div>`;
}

function renderUnderlyings(rows) {
  const el = document.getElementById('underlying-pool-list');
  if (!el) return;
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div class="rounded border border-gray-700 bg-gray-900/50 px-3 py-3 text-sm text-gray-400 sm:col-span-2 lg:col-span-3">标的池为空，请先添加观察标的。</div>';
    return;
  }
  el.innerHTML = rows.map(row => {
    const summary = row.last_pool_summary || {};
    const tags = Array.isArray(row.tags) && row.tags.length
      ? row.tags.map(tag => `<span class="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300">${escapeHtml(tag)}</span>`).join('')
      : '';
    return `
      <div class="rounded border border-gray-700 bg-gray-900/40 px-3 py-2 text-xs">
        <div class="flex items-center justify-between gap-2">
          <span class="font-semibold text-gray-100">${escapeHtml(row.symbol || '-')}</span>
          ${poolStatusBadgeHtml(row.pool_status)}
        </div>
        <div class="mt-1 flex flex-wrap gap-1">${tags}</div>
        <div class="mt-1 text-gray-400">最近候选 ${row.last_candidate_count ?? 0} · 合约 ${summary.contracts_seen ?? '-'}</div>
        ${row.notes ? `<div class="mt-1 break-words text-gray-500">${escapeHtml(row.notes)}</div>` : ''}
      </div>`;
  }).join('');
}

function getCandidateQuality(row) {
  const dq = row && typeof row.data_quality === 'object' && row.data_quality !== null
    ? row.data_quality
    : {};
  const flags = Array.isArray(dq.flags)
    ? dq.flags
    : Array.isArray(row?.quality_flags) ? row.quality_flags : [];
  return {
    grade: dq.grade || row?.quality_grade || 'unknown',
    score: dq.score ?? row?.quality_score ?? null,
    flags,
    quote_age_seconds: dq.quote_age_seconds ?? row?.quote_age_seconds ?? null,
    greeks_source: dq.greeks_source ?? row?.greeks_source ?? null,
    iv_rank_source: dq.iv_rank_source ?? row?.iv_rank_source ?? null,
  };
}

function qualityBadgeHtml(row) {
  const q = getCandidateQuality(row);
  const grade = String(q.grade || 'unknown').toUpperCase();
  const map = {
    A: ['可决策', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', null],
    B: ['可观察', 'bg-amber-500/15 text-amber-300 border-amber-500/30', null],
    C: ['数据不足', 'bg-red-500/15 text-red-300 border-red-500/30', null],
    UNKNOWN: [
      '未评级',
      'bg-gray-600/30 text-gray-300 border-gray-500/40',
      '该候选没有质量字段，通常来自旧扫描结果或旧接口响应；重新扫描后会生成完整评级。',
    ],
  };
  const [label, cls, help] = map[grade] || map.UNKNOWN;
  const score = q.score != null ? ` · ${q.score}` : '';
  const title = [
    `质量：${label}${score}`,
    help ? `说明：${help}` : null,
    q.greeks_source ? `Greeks：${q.greeks_source}` : null,
    q.iv_rank_source ? `IV Rank：${q.iv_rank_source}` : null,
    q.quote_age_seconds != null ? `行情年龄：约 ${q.quote_age_seconds}s` : null,
    q.flags && q.flags.length ? `标记：${q.flags.join(', ')}` : null,
  ].filter(Boolean).join('\n');
  return `<span title="${escapeAttr(title)}" aria-label="${escapeAttr(title)}" class="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}">${label}${score}</span>`;
}

function topRejectionReasons(diagnostics, limit = 3) {
  const counts = diagnostics?.totals?.rejection_counts || {};
  return Object.entries(counts)
    .filter(([, v]) => Number(v) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, limit)
    .map(([k, v]) => `${formatRejectionReason(k)} ${v} 条`);
}

function formatRejectionReason(key) {
  const labels = {
    invalid_bid_ask: '双边报价无效',
    wide_spread: '价差过宽',
    delta_missing: 'Delta 不符合或缺失',
    oi_below_min: '未平仓量过低',
    dte_out_of_range: 'DTE 不在范围',
    roi_below_min: '收益率不足',
    margin_buffer_low: '安全垫不足',
    earnings_within_window: '财报窗口内',
    iv_rank_below_min: 'IV 排名不足',
    provider_error: '行情源错误',
    quality_grade_c: '数据质量不足',
  };
  return labels[key] || key.replace(/_/g, ' ');
}

function renderScanDiagnostics(scanRun) {
  const el = document.getElementById('scan-diagnostics-summary');
  if (!el) return;
  const d = scanRun && scanRun.diagnostics;
  if (!d || !d.totals) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  const totals = d.totals || {};
  const qc = totals.quality_counts || {};
  const reasons = topRejectionReasons(d);
  const parts = [
    `本轮扫描：标的 ${totals.symbols ?? '-'}，失败 ${totals.failed_symbols ?? 0}，合约 ${totals.contracts_seen ?? 0}，候选 ${totals.candidates ?? scanRun?.candidate_count ?? 0}`,
    `质量：A ${qc.A ?? 0} / B ${qc.B ?? 0} / C ${qc.C ?? 0}`,
  ];
  if (reasons.length) parts.push(`主要过滤：${reasons.join('，')}`);
  el.textContent = parts.join('。');
  el.classList.remove('hidden');
}

function candidateActionButtonsHtml(row, source) {
  const encoded = JSON.stringify(row);
  const buttons = [
    `<button onclick='openEntrySignalModal(${encoded})'
      class="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-1 rounded text-xs">决策卡</button>`,
    `<button onclick='openEntryModal(${encoded})'
      class="bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded text-xs">入场</button>`,
  ];
  if (source === 'pool' && row.option_pool_id != null) {
    if (row.is_watched && row.watch_id) {
      buttons.push(`<button onclick='ignoreOptionWatch(${Number(row.watch_id)})'
        class="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-1 rounded text-xs">忽略</button>`);
    } else {
      buttons.push(`<button onclick='watchOptionPool(${Number(row.option_pool_id)})'
        class="bg-emerald-600 hover:bg-emerald-500 text-white px-2 py-1 rounded text-xs">观察</button>`);
      buttons.push(`<button onclick='ignoreOptionPool(${Number(row.option_pool_id)})'
        class="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-1 rounded text-xs">忽略</button>`);
    }
  }
  return `<div class="flex flex-wrap justify-center gap-1">${buttons.join('')}</div>`;
}

function renderCandidates(rows, scanRun, options = {}) {
  const tbody = document.getElementById('candidates-tbody');
  const empty = document.getElementById('candidates-empty');
  const source = options.source || 'scan';
  renderScanDiagnostics(scanRun);
  if (!rows || rows.length === 0) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    if (source === 'pool') {
      empty.textContent = '合约池为空，可运行扫描或放宽过滤。';
    } else if (!scanRun) {
      empty.textContent = '暂无扫描记录，可使用「特定搜索」或点击「立即扫描」';
    } else if (!scanRun.finished_at) {
      empty.textContent = '扫描进行中，请稍候（拉取期权链可能需要数十秒）…';
    } else {
      const reasons = topRejectionReasons(scanRun.diagnostics);
      empty.textContent = reasons.length
        ? `本轮扫描已完成：暂无候选。主要原因：${reasons.join('，')}。`
        : '本轮扫描已完成：暂无符合当前「入场过滤」条件的合约，可在「设置」放宽条件或更换标的。';
    }
    return;
  }
  empty.classList.add('hidden');
  tbody.innerHTML = rows.map(r => `
    <tr class="hover:bg-gray-700/50 cursor-pointer text-center text-xs">
      <td class="px-3 py-2 text-left font-medium text-white">
        ${r.symbol}
        ${r.iv_rank !== null && r.iv_rank !== undefined ? `<span class="ml-1 badge-info text-xs">IV${Math.round(r.iv_rank)}</span>` : ''}
        ${r.status ? `<div class="mt-1">${poolStatusBadgeHtml(r.status)}</div>` : ''}
        ${r.is_watched ? `<div class="mt-1 text-[11px] text-emerald-300">已加入观察池</div>` : ''}
      </td>
      <td>${qualityBadgeHtml(r)}</td>
      <td>
        <div class="flex flex-col items-center gap-1">
          ${entrySignalBadgeHtml(r)}
          ${getEntrySignal(r).summary ? `<span class="max-w-[10rem] break-words text-[11px] normal-case text-gray-400">${escapeHtml(getEntrySignal(r).summary)}</span>` : ''}
        </div>
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
      <td>${candidateActionButtonsHtml(r, source)}</td>
    </tr>
  `).join('');
}

function watchDistanceText(option, watch) {
  const parts = [];
  if (watch.target_premium != null) {
    const diff = Number(watch.target_premium) - Number(option.mid ?? 0);
    parts.push(`Premium ${diff <= 0 ? '达标' : '差 ' + fmt(diff, 2)}`);
  }
  if (watch.target_score != null) {
    const diff = Number(watch.target_score) - Number(option.score ?? 0);
    parts.push(`Score ${diff <= 0 ? '达标' : '差 ' + fmt(diff, 3)}`);
  }
  if (watch.target_margin_buffer != null) {
    const diff = Number(watch.target_margin_buffer) - Number(option.margin_buffer ?? 0);
    parts.push(`安全垫 ${diff <= 0 ? '达标' : '差 ' + (diff * 100).toFixed(1) + '%'}`);
  }
  return parts.length ? parts.join(' · ') : '无显式目标，合约可行动时即达标';
}

function renderOptionWatches(rows) {
  _lastWatchRows = rows || [];
  const grid = document.getElementById('option-watch-grid');
  if (!grid) return;
  if (!_lastWatchRows.length) {
    grid.innerHTML = '<div class="rounded border border-gray-700 bg-gray-900/50 px-3 py-4 text-sm text-gray-400 md:col-span-2 xl:col-span-3">观察池为空，可从合约池点击「观察」。</div>';
    return;
  }
  grid.innerHTML = _lastWatchRows.map(watch => {
    const option = watch.option || {};
    const id = Number(watch.id);
    return `
      <div class="rounded border border-gray-700 bg-gray-900/40 p-3 text-xs">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="font-semibold text-gray-100">${escapeHtml(option.symbol || '-')} ${escapeHtml(option.expiration || '-')} ${fmt(option.strike, 2)}P</div>
            <div class="mt-1 flex flex-wrap gap-1">${poolStatusBadgeHtml(watch.status)} ${poolStatusBadgeHtml(option.status)}</div>
          </div>
          <div>${qualityBadgeHtml(option)}</div>
        </div>
        <div class="mt-2 grid grid-cols-2 gap-2 text-gray-300">
          <div>Mid <span class="text-white">${fmt(option.mid, 2)}</span></div>
          <div>DTE <span class="text-white">${option.dte ?? '-'}</span></div>
          <div>Score <span class="text-white">${fmt(option.score, 3)}</span></div>
          <div>安全垫 <span class="text-white">${option.margin_buffer != null ? (option.margin_buffer * 100).toFixed(1) + '%' : '-'}</span></div>
        </div>
        <div class="mt-2 rounded border border-gray-700 bg-gray-950/30 px-2 py-1.5">
          <div class="flex flex-wrap items-center gap-2">${entrySignalBadgeHtml(option)}
            <button onclick='openEntrySignalModal(${JSON.stringify(option)})' class="text-[11px] text-indigo-300 hover:text-indigo-200">查看决策卡</button>
          </div>
          ${getEntrySignal(option).summary ? `<div class="mt-1 break-words text-gray-400">${escapeHtml(getEntrySignal(option).summary)}</div>` : ''}
        </div>
        <div class="mt-2 break-words rounded bg-gray-950/40 px-2 py-1 text-gray-400">${escapeHtml(watchDistanceText(option, watch))}</div>
        <div class="mt-2 grid grid-cols-3 gap-1">
          <input id="watch-target-premium-${id}" type="number" step="0.01" value="${watch.target_premium ?? ''}" placeholder="Premium"
            class="min-w-0 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-100" />
          <input id="watch-target-score-${id}" type="number" step="0.01" value="${watch.target_score ?? ''}" placeholder="Score"
            class="min-w-0 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-100" />
          <input id="watch-target-margin-${id}" type="number" step="0.01" value="${watch.target_margin_buffer ?? ''}" placeholder="Margin"
            class="min-w-0 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-100" />
        </div>
        <div class="mt-2 flex flex-wrap gap-1">
          <button onclick="saveWatchTargets(${id})" class="rounded bg-gray-700 px-2 py-1 text-gray-100 hover:bg-gray-600">保存目标</button>
          <button onclick="openWatchEntryModal(${id})" class="rounded bg-indigo-600 px-2 py-1 text-white hover:bg-indigo-500">确认入场</button>
          <button onclick="ignoreOptionWatch(${id})" class="rounded bg-gray-700 px-2 py-1 text-gray-100 hover:bg-gray-600">忽略</button>
        </div>
      </div>`;
  }).join('');
}

function entrySignalMetricCardHtml(title, items) {
  const rows = items
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([label, value]) => `<div class="flex justify-between gap-3"><span class="text-gray-500">${escapeHtml(label)}</span><span class="text-gray-200 text-right">${escapeHtml(value)}</span></div>`)
    .join('');
  return `<div class="rounded border border-gray-700 bg-gray-900/40 p-3">
    <div class="mb-2 text-xs font-semibold text-indigo-200">${escapeHtml(title)}</div>
    <div class="space-y-1 text-xs">${rows || '<div class="text-gray-500">暂无数据</div>'}</div>
  </div>`;
}

function entrySignalReasonsHtml(signal) {
  const reasons = Array.isArray(signal.reasons) ? signal.reasons : [];
  if (!reasons.length) return '<div class="text-xs text-gray-500">暂无原因明细</div>';
  const cls = {
    positive: 'text-emerald-300',
    warn: 'text-amber-300',
    blocker: 'text-red-300',
    info: 'text-gray-300',
  };
  return reasons.slice(0, 10).map(reason => `
    <div class="rounded border border-gray-700 bg-gray-950/30 px-2 py-1.5 text-xs">
      <div class="${cls[reason.severity] || cls.info}">${escapeHtml(reason.message || reason.code || '-')}</div>
      <div class="mt-0.5 break-words text-gray-500">${escapeHtml(reason.dimension || '-')}${reason.threshold != null ? ` · 阈值 ${escapeHtml(JSON.stringify(reason.threshold))}` : ''}${reason.current != null ? ` · 当前 ${escapeHtml(JSON.stringify(reason.current))}` : ''}</div>
    </div>
  `).join('');
}

function openEntrySignalModal(row) {
  const signal = getEntrySignal(row);
  const modal = document.getElementById('entry-signal-modal');
  const title = document.getElementById('entry-signal-title');
  const body = document.getElementById('entry-signal-body');
  if (!modal || !body) return;
  const metrics = signal.metrics || {};
  const ret = metrics.return || {};
  const risk = metrics.risk || {};
  const liq = metrics.liquidity || {};
  const vol = metrics.volatility || {};
  const timing = metrics.timing || {};
  const dq = metrics.data_quality || {};
  if (title) title.textContent = `${row.symbol || '-'} ${row.expiration || '-'} ${fmt(row.strike, 2)}P 开仓决策卡`;
  body.innerHTML = `
    <div class="flex flex-wrap items-center gap-2">${entrySignalBadgeHtml(row)}<span class="break-words text-gray-300">${escapeHtml(signal.summary || '暂无信号摘要')}</span></div>
    <div class="grid gap-3 sm:grid-cols-2">
      ${entrySignalMetricCardHtml('收益', [
        ['Premium', fmt(ret.premium ?? row.mid, 2)],
        ['年化 ROI', ret.annualized_roi != null ? (ret.annualized_roi * 100).toFixed(1) + '%' : '-'],
        ['最大收益/张', ret.max_profit != null ? '$' + fmt(ret.max_profit, 0) : '-'],
        ['占用资金/张', ret.capital_usage != null ? '$' + fmt(ret.capital_usage, 0) : '-'],
      ])}
      ${entrySignalMetricCardHtml('风险', [
        ['现价 / 行权价', `${fmt(risk.spot ?? row.spot, 2)} / ${fmt(risk.strike ?? row.strike, 2)}`],
        ['安全垫', risk.margin_buffer != null ? (risk.margin_buffer * 100).toFixed(1) + '%' : '-'],
        ['Delta', fmt(risk.delta ?? row.delta, 3)],
        ['DTE', String(risk.dte ?? row.dte ?? '-')],
      ])}
      ${entrySignalMetricCardHtml('流动性', [
        ['Bid / Ask', `${fmt(liq.bid ?? row.bid, 2)} / ${fmt(liq.ask ?? row.ask, 2)}`],
        ['价差', liq.spread_pct != null ? (liq.spread_pct * 100).toFixed(1) + '%' : '-'],
        ['未平仓量', String(liq.open_interest ?? row.open_interest ?? '-')],
      ])}
      ${entrySignalMetricCardHtml('波动与时机', [
        ['IV', vol.iv != null ? (vol.iv * 100).toFixed(1) + '%' : '-'],
        ['IV Rank', vol.iv_rank != null ? fmt(vol.iv_rank, 0) : '-'],
        ['RSI 6', timing.rsi_6 != null ? fmt(timing.rsi_6, 1) : '-'],
        ['布林距离', timing.bb_distance_pct != null ? fmt(timing.bb_distance_pct, 1) + '%' : '-'],
      ])}
      ${entrySignalMetricCardHtml('数据质量', [
        ['评级', dq.quality_grade || row.quality_grade || '-'],
        ['质量分', String(dq.quality_score ?? row.quality_score ?? '-')],
        ['Greeks', dq.greeks_source || row.greeks_source || '-'],
        ['IV Rank 来源', dq.iv_rank_source || row.iv_rank_source || '-'],
      ])}
    </div>
    <div>
      <div class="mb-2 text-xs font-semibold text-indigo-200">原因</div>
      <div class="space-y-2">${entrySignalReasonsHtml(signal)}</div>
    </div>
  `;
  modal.classList.remove('hidden');
}

function closeEntrySignalModal() {
  document.getElementById('entry-signal-modal')?.classList.add('hidden');
}

document.getElementById('entry-signal-close')?.addEventListener('click', closeEntrySignalModal);

async function watchOptionPool(optionPoolId) {
  await apiFetch('/api/watch/options', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ option_pool_id: optionPoolId }),
  });
  toast('已加入观察池', 'info');
  await refreshPoolSections();
}

async function ignoreOptionPool(optionPoolId) {
  const watch = await apiFetch('/api/watch/options', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ option_pool_id: optionPoolId }),
  });
  await apiFetch(`/api/watch/options/${watch.id}/ignore`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ignore_reason: 'user_ignored_from_pool' }),
  });
  toast('已忽略该合约', 'info');
  await refreshPoolSections();
}

async function ignoreOptionWatch(watchId) {
  await apiFetch(`/api/watch/options/${watchId}/ignore`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ignore_reason: 'user_ignored' }),
  });
  toast('观察项已忽略', 'info');
  await refreshPoolSections();
}

async function saveWatchTargets(watchId) {
  const premium = document.getElementById(`watch-target-premium-${watchId}`)?.value;
  const score = document.getElementById(`watch-target-score-${watchId}`)?.value;
  const margin = document.getElementById(`watch-target-margin-${watchId}`)?.value;
  const body = {
    target_premium: premium === '' ? null : Number(premium),
    target_score: score === '' ? null : Number(score),
    target_margin_buffer: margin === '' ? null : Number(margin),
  };
  await apiFetch(`/api/watch/options/${watchId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  toast('观察目标已保存', 'info');
  await refreshPoolSections();
}

function openWatchEntryModal(watchId) {
  const watch = _lastWatchRows.find(row => Number(row.id) === Number(watchId));
  if (!watch || !watch.option) {
    toast('未找到观察项', 'danger');
    return;
  }
  const option = watch.option;
  openEntryModal({
    ...option,
    id: option.latest_candidate_id,
    option_pool_id: watch.option_pool_id,
    option_watchlist_id: watch.id,
    from_option_watch: true,
    watch_status: watch.status,
  });
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
    _lastScanRun = run;
    await refreshPoolSections(run);
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

function openSpecificSearchModal() {
  const modal = document.getElementById('specific-search-modal');
  const wl = document.getElementById('watchlist-input').value.trim();
  const first = wl.split(/[\s,，]+/).map(s => s.trim()).filter(Boolean)[0];
  const symEl = document.getElementById('specific-symbol');
  if (first && !symEl.value.trim()) {
    symEl.value = String(first).toUpperCase();
  }
  modal.classList.remove('hidden');
}

function closeSpecificSearchModal() {
  document.getElementById('specific-search-modal').classList.add('hidden');
}

document.getElementById('btn-specific-search').addEventListener('click', () => {
  openSpecificSearchModal();
});

document.getElementById('specific-search-cancel').addEventListener('click', () => {
  closeSpecificSearchModal();
});

document.getElementById('specific-search-submit').addEventListener('click', async () => {
  const btn = document.getElementById('specific-search-submit');
  const symbol = document.getElementById('specific-symbol').value.trim();
  const expiration = document.getElementById('specific-expiration').value;
  const strikeNum = parseFloat(document.getElementById('specific-strike').value);
  if (!symbol) {
    toast('请填写标的', 'danger');
    return;
  }
  if (!expiration) {
    toast('请选择到期日', 'danger');
    return;
  }
  if (!(strikeNum > 0)) {
    toast('请输入大于 0 的行权价', 'danger');
    return;
  }
  const prevLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = '搜索中…';
  setScreenerScanLoading(true);
  toast('正在拉取该合约…', 'info');
  try {
    const data = await apiFetch('/api/scan/specific', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol,
        expiration,
        strike: strikeNum,
      }),
    });
    const normalized = normalizeScanLatestResponse(data);
    _lastScanRun = normalized.run;
    renderCandidates(normalized.candidates, normalized.run, { source: 'specific' });
    closeSpecificSearchModal();
    toast('已展示该合约（与扫描表格列一致）', 'info');
  } catch (e) {
    toast('特定搜索失败：' + e.message, 'danger');
  } finally {
    setScreenerScanLoading(false);
    btn.disabled = false;
    btn.textContent = prevLabel;
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

document.getElementById('btn-refresh-option-pool')?.addEventListener('click', async () => {
  setScreenerScanLoading(true);
  try {
    await refreshPoolSections();
  } finally {
    setScreenerScanLoading(false);
  }
});

document.getElementById('btn-refresh-option-watch')?.addEventListener('click', async () => {
  await refreshPoolSections();
});

[
  'option-pool-status-filter',
  'option-pool-quality-filter',
  'option-pool-entry-signal-filter',
  'option-pool-min-score',
  'option-pool-min-dte',
  'option-pool-max-dte',
].forEach(id => {
  document.getElementById(id)?.addEventListener('change', async () => {
    setScreenerScanLoading(true);
    try {
      await refreshPoolSections();
    } finally {
      setScreenerScanLoading(false);
    }
  });
});

// ================================================================
// Entry Modal
// ================================================================
function openEntryModal(row) {
  _pendingEntry = row;
  const info = document.getElementById('modal-info');
  const q = getCandidateQuality(row);
  const qualityWarn = ['B', 'UNKNOWN'].includes(String(q.grade || 'unknown').toUpperCase())
    ? `<div class="md:col-span-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">数据质量为 ${qualityBadgeHtml(row)}，请核对行情源和报价后再确认。</div>`
    : '';
  const signalWarn = entrySignalWarningHtml(row);
  info.innerHTML = `
    <div><span class="text-gray-400">标的：</span>${row.symbol}</div>
    <div><span class="text-gray-400">质量：</span>${qualityBadgeHtml(row)}</div>
    <div><span class="text-gray-400">开仓信号：</span>${entrySignalBadgeHtml(row)}</div>
    <div><span class="text-gray-400">到期：</span>${row.expiration}（DTE ${row.dte}）</div>
    <div><span class="text-gray-400">行权价：</span>${row.strike}</div>
    <div><span class="text-gray-400">当前 Mid：</span>${fmt(row.mid, 2)}</div>
    <div><span class="text-gray-400">Delta：</span>${fmt(row.delta, 3)}</div>
    <div><span class="text-gray-400">年化ROI：</span>${row.annualized_roi ? (row.annualized_roi * 100).toFixed(1) + '%' : '-'}</div>
    ${qualityWarn}
    ${signalWarn}
  `;
  document.getElementById('modal-premium').value = row.mid ? row.mid.toFixed(2) : '';
  document.getElementById('modal-contracts').value = 1;
  document.getElementById('modal-notes').value = '';
  const eoa = document.getElementById('entry-open-at');
  if (eoa) eoa.value = defaultDatetimeLocalNow();
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
    const pe = _pendingEntry;
    const body = {
      symbol: pe.symbol,
      expiration: pe.expiration,
      strike: pe.strike,
      contracts,
      open_premium: premium,
      notes,
    };
    if (pe.option_pool_id != null && pe.option_pool_id !== '') body.option_pool_id = pe.option_pool_id;
    if (pe.option_watchlist_id != null && pe.option_watchlist_id !== '') body.option_watchlist_id = pe.option_watchlist_id;
    if (pe.option_pool_id != null && pe.latest_candidate_id != null && pe.latest_candidate_id !== '') {
      body.open_candidate_id = pe.latest_candidate_id;
    } else if (pe.id != null && pe.id !== '') {
      body.open_candidate_id = pe.id;
    }
    const metricKeys = [
      'iv_rank', 'iv', 'delta', 'theta', 'vega', 'spot', 'dte',
      'annualized_roi', 'score',
    ];
    for (const k of metricKeys) {
      const v = pe[k];
      if (v != null && v !== '') body[k] = v;
    }
    const q = getCandidateQuality(pe);
    body.quality_grade = q.grade;
    if (q.score != null) body.quality_score = q.score;
    if (Array.isArray(q.flags)) body.quality_flags = q.flags;
    if (q.quote_age_seconds != null) body.quote_age_seconds = q.quote_age_seconds;
    if (q.greeks_source) body.greeks_source = q.greeks_source;
    if (q.iv_rank_source) body.iv_rank_source = q.iv_rank_source;
    const signal = getEntrySignal(pe);
    if (pe.latest_entry_signal_id != null) body.entry_signal_id = pe.latest_entry_signal_id;
    if (signal.status) body.entry_signal_status = signal.status;
    if (signal.decision_score != null) body.entry_signal_score = signal.decision_score;
    if (signal.summary) body.entry_signal_summary = signal.summary;
    if (signal.schema === 'entry_signal_v1') body.entry_signal = signal;
    const oa = document.getElementById('entry-open-at');
    if (oa && oa.value) {
      const iso = fromDatetimeLocalToIso(oa.value);
      if (!iso) { toast('开仓时间无效', 'danger'); return; }
      body.open_at = iso;
    }
    const url = pe.from_option_watch && pe.option_watchlist_id != null
      ? `/api/watch/options/${pe.option_watchlist_id}/open`
      : '/api/positions';
    await apiFetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    toast(`${_pendingEntry.symbol} 入场成功`, 'info');
    document.getElementById('entry-modal').classList.add('hidden');
    _pendingEntry = null;
    if (_currentPage === 'screener') await refreshPoolSections();
  } catch(e) {
    toast('入场失败: ' + e.message, 'danger');
  }
});

// ================================================================
// #positions — marks fetch dedupe / SSE-triggered refresh
// ================================================================
let _marksFetchPromise = null;
let _marksFastFetchPromise = null;

function fetchPositionsMarksPayload(options = {}) {
  const fast = !!options.fast;
  const force = !!options.force;
  if (fast) {
    if (!_marksFastFetchPromise || force) {
      _marksFastFetchPromise = apiFetch('/api/positions/marks?fast=1').finally(() => {
        _marksFastFetchPromise = null;
      });
    }
    return _marksFastFetchPromise;
  }
  if (!_marksFetchPromise || force) {
    _marksFetchPromise = apiFetch('/api/positions/marks').finally(() => {
      _marksFetchPromise = null;
    });
  }
  return _marksFetchPromise;
}

let _bellRefreshTimer = null;
/** Coalesce bell API calls (SSE can emit many events in a burst). */
function scheduleRefreshBell() {
  if (_bellRefreshTimer !== null) clearTimeout(_bellRefreshTimer);
  _bellRefreshTimer = setTimeout(() => {
    _bellRefreshTimer = null;
    void refreshBell();
  }, 350);
}

let _positionsRefreshTimer = null;
/** Debounce background reload while remaining on positions (SSE). */
function scheduleLoadPositions() {
  if (_currentPage !== 'positions') return;
  if (_positionsRefreshTimer !== null) clearTimeout(_positionsRefreshTimer);
  _positionsRefreshTimer = setTimeout(() => {
    _positionsRefreshTimer = null;
    void loadPositions();
  }, 400);
}

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

/** local datetime-local value from UTC ISO (browser local). */
function toDatetimeLocalValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromDatetimeLocalToIso(localVal) {
  if (!localVal) return null;
  const d = new Date(localVal);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function defaultDatetimeLocalNow() {
  return toDatetimeLocalValue(new Date().toISOString());
}

function closeReasonRowsToHtml(rows) {
  return rows.map(([v, lab]) => `<option value="${v}">${lab}</option>`).join('');
}

/** 手动平仓弹层：含「到期自动平仓」（仅请求体传 expiry_auto，不入库）。 */
function manualClosePositionReasonOptionsHtml() {
  const rows = [
    ['manual', '手动'],
    ['expiry_auto', '到期自动平仓'],
    ['roll_extend', '展期'],
    ['take_profit_50', '止盈（50%）'],
    ['take_profit_75', '止盈（75%）'],
    ['take_profit_fast', '快速止盈'],
    ['take_profit_strong', '强止盈'],
    ['time_14d', '时间 14 天'],
    ['time_7d', '时间 7 天'],
    ['time_warning', '时间警告'],
    ['time_danger', '时间危险'],
    ['danger_3pct', '危险 3%'],
    ['delta_breach', 'Delta 突破'],
    ['loss_breach', '浮亏防守'],
    ['expired_otm', '到期 OTM'],
    ['assigned', '指派'],
  ];
  return closeReasonRowsToHtml(rows);
}

function closeReasonSelectOptionsHtml() {
  const rows = [
    ['manual', '手动'],
    ['roll_extend', '展期'],
    ['take_profit_50', '止盈（50%）'],
    ['take_profit_75', '止盈（75%）'],
    ['take_profit_fast', '快速止盈'],
    ['take_profit_strong', '强止盈'],
    ['time_14d', '时间 14 天'],
    ['time_7d', '时间 7 天'],
    ['time_warning', '时间警告'],
    ['time_danger', '时间危险'],
    ['danger_3pct', '危险 3%'],
    ['delta_breach', 'Delta 突破'],
    ['loss_breach', '浮亏防守'],
    ['expired_otm', '到期 OTM'],
    ['assigned', '指派'],
  ];
  return closeReasonRowsToHtml(rows);
}

function syncClosePositionModalFields() {
  const sel = document.getElementById('close-position-reason');
  const premiumBlock = document.getElementById('close-position-premium-block');
  const atBlock = document.getElementById('close-position-at-block');
  const label = document.getElementById('close-position-label');
  if (!sel || !premiumBlock || !atBlock || !label) return;
  const isExpiryAuto = sel.value === 'expiry_auto';
  premiumBlock.classList.toggle('hidden', isExpiryAuto);
  atBlock.classList.toggle('hidden', isExpiryAuto);
  if (isExpiryAuto) {
    label.textContent =
      '标的 ' + (label.dataset.symbol || '') + '：到期自动平仓（价外），无需买回价与时间；平仓时间为期权到期日美东 16:00。';
  } else {
    label.textContent =
      '标的 ' + (label.dataset.symbol || '') + '：填写买回 Mid、出场原因；平仓时间默认此刻。';
  }
  label.title = label.textContent;
}

function initCloseReasonSelects() {
  const a = document.getElementById('close-position-reason');
  const b = document.getElementById('position-edit-close-reason');
  if (a) a.innerHTML = manualClosePositionReasonOptionsHtml();
  if (b) b.innerHTML = closeReasonSelectOptionsHtml();
}
initCloseReasonSelects();

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
  const moa = document.getElementById('manual-open-at');
  if (moa) moa.value = defaultDatetimeLocalNow();
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
  const moa = document.getElementById('manual-open-at');
  if (moa && moa.value) {
    const iso = fromDatetimeLocalToIso(moa.value);
    if (!iso) { toast('开仓时间无效', 'danger'); return; }
    body.open_at = iso;
  }

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

let _lastPositionsRows = [];

function findLoadedPosition(positionId) {
  return _lastPositionsRows.find(p => String(p.id) === String(positionId)) || null;
}

function getExitSignal(row) {
  if (!row) return null;
  const signal = row.exit_signal || row.exit_signal_payload || null;
  if (signal && !signal.id && !signal.exit_signal_id && row.latest_exit_signal_id) {
    return {...signal, id: row.latest_exit_signal_id, exit_signal_id: row.latest_exit_signal_id};
  }
  return signal;
}

function exitActionLabel(action) {
  return {
    HOLD: '继续持有',
    HOLD_TO_EXPIRY: '等待到期',
    TAKE_PROFIT: '止盈',
    ACCELERATE_TAKE_PROFIT: '加速止盈',
    TIME_EXIT: '时间退出',
    DEFEND: '防守',
    EXPIRED: '已过期',
    UNKNOWN: '未知',
  }[action || 'UNKNOWN'] || '未知';
}

function exitBadgeClass(severity) {
  if (severity === 'danger') return 'border-red-500/60 bg-red-950/70 text-red-200';
  if (severity === 'warn') return 'border-yellow-500/60 bg-yellow-950/70 text-yellow-100';
  return 'border-blue-500/60 bg-blue-950/70 text-blue-100';
}

function exitReasonLabel(reason) {
  const labels = {
    take_profit_50: '止盈 50%',
    take_profit_75: '止盈 75%',
    take_profit_fast: '快速止盈',
    time_14d: '时间 14 天',
    time_7d: '时间 7 天',
    danger_3pct: '危险 3%',
    delta_breach: 'Delta 突破',
    loss_breach: '浮亏防守',
    expired_otm: '到期 OTM',
    spot_below_strike: '跌破行权价',
    margin_buffer_negative: '安全垫为负',
    expiry_hold_candidate: '可等待到期',
    hold_conditions: '继续观察条件',
    position_expired: '合约已过期',
    position_missing_fields: '持仓字段缺失',
    mark_unavailable: '行情不可用',
    mark_missing_fields: '行情字段缺失',
    mark_complete: '行情字段完整',
  };
  return labels[reason] || reason || '无';
}

function exitSeverityLabel(severity) {
  return {
    danger: '高风险',
    warn: '需处理',
    info: '提示',
  }[severity || 'info'] || '提示';
}

function exitDimensionLabel(dimension) {
  return {
    return: '收益',
    profit: '收益',
    risk: '风险',
    time: '时间',
    data_quality: '数据质量',
    other: '其他',
  }[dimension || 'other'] || '其他';
}

function exitFieldLabel(key) {
  return {
    dte: '剩余天数',
    dte_max: '最多剩余天数',
    current_mid: '当前买回中价',
    max_mid: '最高剩余价值',
    min_margin_buffer: '最低安全垫',
    margin_buffer: '安全垫',
    pnl_pct: '浮盈比例',
    max_holding_days: '最多持有天数',
    time_warning_dte: '时间提醒天数',
    time_danger_dte: '时间危险天数',
    take_profit_pct: '普通止盈比例',
    take_profit_strong_pct: '强止盈比例',
    delta_breach_abs: 'Delta 绝对值',
    loss_pnl_pct_danger: '浮亏防守比例',
    spot: '标的价格',
    strike: '行权价',
    open_premium: '开仓权利金',
    valid_mark: '有效行情标记',
    symbol: '标的',
    expiration: '到期日',
  }[key] || key;
}

function fmtMaybePct(value) {
  if (value == null || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return escapeHtml(String(value));
  return (n * 100).toFixed(1) + '%';
}

function toFiniteNumber(value) {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fmtPremium(value) {
  const n = toFiniteNumber(value);
  if (n == null) return '—';
  return n.toFixed(2);
}

function fmtUsd(value) {
  const n = toFiniteNumber(value);
  if (n == null) return '—';
  const sign = n < 0 ? '-' : '';
  return `${sign}$${Math.abs(n).toFixed(0)}`;
}

function formatExitValue(value, key = '') {
  if (value == null || value === '') return '—';
  if (Array.isArray(value)) {
    return value.map(item => exitFieldLabel(String(item))).join('、') || '—';
  }
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([k, v]) => `${exitFieldLabel(k)} ${formatExitValue(v, k)}`)
      .join('；') || '—';
  }
  const n = Number(value);
  if (Number.isFinite(n)) {
    if (
      key.includes('pct') ||
      key.includes('buffer') ||
      key.includes('margin') ||
      key === 'delta_breach_abs' ||
      key === 'delta'
    ) {
      return fmtMaybePct(n);
    }
    if (key.includes('mid') || key.includes('premium') || key === 'spot' || key === 'strike') {
      return fmtPremium(n);
    }
    return Number.isInteger(n) ? String(n) : n.toFixed(2);
  }
  return String(value) === 'valid mark' ? '有效行情标记' : String(value);
}

function estimatePnlUsd(openPremium, closeMid, contracts) {
  if (openPremium == null || closeMid == null || contracts == null) return null;
  return (openPremium - closeMid) * 100 * contracts;
}

function estimatePnlUsdAtPct(openPremium, pnlPct, contracts) {
  if (openPremium == null || pnlPct == null || contracts == null) return null;
  return openPremium * pnlPct * 100 * contracts;
}

function findExitReason(signal, code) {
  const reasons = Array.isArray(signal?.reasons) ? signal.reasons : [];
  return reasons.find(reason => reason.code === code) || null;
}

function exitThresholdFromReason(signal, code, key, fallback) {
  const threshold = findExitReason(signal, code)?.threshold;
  if (threshold && typeof threshold === 'object' && !Array.isArray(threshold)) {
    const n = toFiniteNumber(threshold[key]);
    return n == null ? fallback : n;
  }
  const n = toFiniteNumber(threshold);
  return n == null ? fallback : n;
}

function renderExitScenarioTable(pos, signal) {
  const metrics = signal?.metrics || {};
  const openPremium = toFiniteNumber(metrics.open_premium ?? pos?.open_premium);
  const currentMid = toFiniteNumber(metrics.current_mid);
  const contracts = Math.max(1, Math.round(toFiniteNumber(pos?.contracts) || 1));
  const takeProfit = exitThresholdFromReason(signal, 'take_profit_50', '', 0.50);
  const strongProfit = exitThresholdFromReason(signal, 'take_profit_75', '', 0.75);
  const fastProfit = exitThresholdFromReason(signal, 'take_profit_fast', 'pnl_pct', takeProfit);
  const fastDays = exitThresholdFromReason(signal, 'take_profit_fast', 'max_holding_days', 5);
  const timeWarning = exitThresholdFromReason(signal, 'time_14d', '', 14);
  const timeDanger = exitThresholdFromReason(signal, 'time_7d', '', 7);
  const deltaBreach = exitThresholdFromReason(signal, 'delta_breach', '', 0.40);
  const lossBreach = exitThresholdFromReason(signal, 'loss_breach', '', -0.50);
  const expiryMaxMid = exitThresholdFromReason(signal, 'expiry_hold_candidate', 'max_mid', 0.05);
  const expiryMinBuffer = exitThresholdFromReason(signal, 'expiry_hold_candidate', 'min_margin_buffer', 0.05);

  const closeAtPct = pct => openPremium == null ? null : openPremium * (1 - pct);
  const rows = [
    {
      name: '快速止盈',
      trigger: `持有不超过 ${fastDays} 天且浮盈达到 ${fmtMaybePct(fastProfit)}`,
      target: `买回价约 ${fmtPremium(closeAtPct(fastProfit))}`,
      pnl: fmtUsd(estimatePnlUsdAtPct(openPremium, fastProfit, contracts)),
    },
    {
      name: '普通止盈',
      trigger: `浮盈达到 ${fmtMaybePct(takeProfit)}`,
      target: `买回价约 ${fmtPremium(closeAtPct(takeProfit))}`,
      pnl: fmtUsd(estimatePnlUsdAtPct(openPremium, takeProfit, contracts)),
    },
    {
      name: '强止盈',
      trigger: `浮盈达到 ${fmtMaybePct(strongProfit)}`,
      target: `买回价约 ${fmtPremium(closeAtPct(strongProfit))}`,
      pnl: fmtUsd(estimatePnlUsdAtPct(openPremium, strongProfit, contracts)),
    },
    {
      name: '时间退出',
      trigger: `DTE ≤ ${timeWarning} 天提醒，DTE ≤ ${timeDanger} 天危险`,
      target: currentMid == null ? '按届时买回价评估' : `当前买回价 ${fmtPremium(currentMid)}`,
      pnl: fmtUsd(estimatePnlUsd(openPremium, currentMid, contracts)),
    },
    {
      name: '等待到期',
      trigger: `DTE ≤ ${timeDanger} 天、剩余价值 ≤ ${fmtPremium(expiryMaxMid)}、安全垫 ≥ ${fmtMaybePct(expiryMinBuffer)}`,
      target: `最多再释放约 ${fmtUsd((currentMid ?? expiryMaxMid) * 100 * contracts)}`,
      pnl: '保留剩余权利金，风险仍需人工确认',
    },
    {
      name: '防守/止损',
      trigger: `现价 ≤ 行权价、|Delta| ≥ ${fmtMaybePct(deltaBreach)} 或浮亏 ≤ ${fmtMaybePct(lossBreach)}`,
      target: `亏损阈值买回价约 ${fmtPremium(closeAtPct(lossBreach))}`,
      pnl: fmtUsd(estimatePnlUsdAtPct(openPremium, lossBreach, contracts)),
    },
  ];

  return `
    <div class="rounded border border-gray-700 bg-gray-900/50 p-3">
      <div class="mb-1 text-xs font-semibold text-indigo-200">退出阈值场景</div>
      <div class="mb-2 text-[11px] leading-snug text-gray-500 break-words">
        按 ${contracts} 张合约和开仓权利金 ${fmtPremium(openPremium)} 估算收益；实际成交会受买卖价差和免费行情延迟影响。
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full text-left text-[11px]">
          <thead class="text-gray-500">
            <tr>
              <th class="py-1 pr-3 font-medium">场景</th>
              <th class="py-1 pr-3 font-medium">触发阈值</th>
              <th class="py-1 pr-3 font-medium">参考买回价</th>
              <th class="py-1 font-medium">估算收益</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-800 text-gray-300">
            ${rows.map(row => `
              <tr>
                <td class="py-1.5 pr-3 whitespace-nowrap text-gray-100">${escapeHtml(row.name)}</td>
                <td class="py-1.5 pr-3 min-w-[10rem] break-words">${escapeHtml(row.trigger)}</td>
                <td class="py-1.5 pr-3 min-w-[7rem] break-words">${escapeHtml(row.target)}</td>
                <td class="py-1.5 min-w-[7rem] break-words">${escapeHtml(row.pnl)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

function urgencyTooltipHtml() {
  const text = '紧迫度是 0-100 的动作处理优先级，由动作类型、严重度、盈利/时间/防守条件综合计算；它不是收益预测，也不会自动交易。';
  return `
    <span class="relative inline-flex group/urgency-help align-middle">
      <button type="button" tabindex="0"
        class="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-gray-600 text-[10px] font-bold leading-none text-gray-400 hover:border-indigo-400 hover:text-indigo-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        aria-label="${escapeAttr(text)}">?</button>
      <span role="tooltip"
        class="pointer-events-none absolute left-1/2 top-full z-30 mt-2 hidden w-64 -translate-x-1/2 rounded border border-gray-600 bg-gray-950 px-3 py-2 text-left text-[11px] font-normal leading-snug text-gray-200 shadow-xl group-hover/urgency-help:block group-focus-within/urgency-help:block">
        ${escapeHtml(text)}
      </span>
    </span>`;
}

function renderExitSignalStrip(p) {
  const signal = getExitSignal(p);
  if (!signal) {
    return `<div class="rounded border border-gray-700 bg-gray-900/60 p-2 text-xs text-gray-500">暂无动作建议</div>`;
  }
  const metrics = signal.metrics || {};
  const cls = exitBadgeClass(signal.severity);
  const score = signal.urgency_score != null ? Math.round(Number(signal.urgency_score)) : null;
  return `
    <div class="rounded border border-gray-700 bg-gray-900/60 p-2 space-y-2">
      <div class="flex flex-wrap items-center gap-2">
        <span class="inline-flex items-center rounded border px-2 py-0.5 text-[11px] ${cls}">
          ${escapeHtml(exitActionLabel(signal.action))}
        </span>
        ${score != null ? `<span class="inline-flex items-center gap-1 text-[11px] text-gray-400">紧迫度 ${score}${urgencyTooltipHtml()}</span>` : ''}
        ${signal.suggested_close_reason ? `<span class="text-[11px] text-gray-500">${escapeHtml(exitReasonLabel(signal.suggested_close_reason))}</span>` : ''}
      </div>
      <div class="text-xs text-gray-300 break-words">${escapeHtml(signal.summary || '未形成建议说明')}</div>
      <div class="grid grid-cols-2 gap-1 text-[11px] text-gray-400">
        <div>浮盈 <span class="text-gray-200">${fmtMaybePct(metrics.pnl_pct)}</span></div>
        <div>DTE <span class="text-gray-200">${metrics.dte ?? '—'}</span></div>
        <div>Delta <span class="text-gray-200">${metrics.delta != null ? fmt(metrics.delta, 2) : '—'}</span></div>
        <div>安全垫 <span class="text-gray-200">${fmtMaybePct(metrics.margin_buffer)}</span></div>
      </div>
    </div>`;
}

function renderExitReasons(signal) {
  const reasons = Array.isArray(signal?.reasons) ? signal.reasons : [];
  if (!reasons.length) return '<div class="text-sm text-gray-500">暂无明细。</div>';
  const groups = {};
  for (const reason of reasons) {
    const key = reason.dimension || 'other';
    if (!groups[key]) groups[key] = [];
    groups[key].push(reason);
  }
  return Object.entries(groups).map(([dim, rows]) => `
    <div class="rounded border border-gray-700 bg-gray-900/50 p-3">
      <div class="mb-2 text-xs font-semibold text-indigo-200">${escapeHtml(exitDimensionLabel(dim))}</div>
      <div class="space-y-2">
        ${rows.map(reason => `
          <div class="rounded bg-gray-800/70 p-2">
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <span class="font-medium text-gray-200">${escapeHtml(exitReasonLabel(reason.code))}</span>
              <span class="text-gray-500">${escapeHtml(exitSeverityLabel(reason.severity))}</span>
              <span class="${reason.passed ? 'text-emerald-300' : 'text-yellow-300'}">${reason.passed ? '规则已满足' : '未满足/需关注'}</span>
            </div>
            <div class="mt-1 text-xs text-gray-300 break-words">${escapeHtml(reason.message || '')}</div>
            <div class="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-1 text-[11px] text-gray-500">
              <div>当前：${escapeHtml(formatExitValue(reason.current, reason.code))}</div>
              <div>阈值：${escapeHtml(formatExitValue(reason.threshold, reason.code))}</div>
            </div>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

window.openExitSignalModal = function(positionId) {
  const pos = findLoadedPosition(positionId);
  const signal = getExitSignal(pos);
  const title = document.getElementById('exit-signal-title');
  const body = document.getElementById('exit-signal-body');
  if (!signal) {
    title.textContent = '持仓动作建议';
    body.innerHTML = '<div class="text-sm text-gray-500">暂无动作建议。</div>';
  } else {
    const metrics = signal.metrics || {};
    title.textContent = `${pos?.symbol || ''} · ${exitActionLabel(signal.action)}`;
    body.innerHTML = `
      <div class="rounded border border-gray-700 bg-gray-900/60 p-3 space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <span class="inline-flex items-center rounded border px-2 py-0.5 text-xs ${exitBadgeClass(signal.severity)}">${escapeHtml(exitActionLabel(signal.action))}</span>
          <span class="text-xs text-gray-400">级别 ${escapeHtml(exitSeverityLabel(signal.severity))}</span>
          <span class="inline-flex items-center gap-1 text-xs text-gray-400">紧迫度 ${signal.urgency_score ?? '—'}${urgencyTooltipHtml()}</span>
        </div>
        <div class="text-sm text-gray-200 break-words">${escapeHtml(signal.summary || '')}</div>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs text-gray-400">
          <div>浮盈 <span class="text-gray-200">${fmtMaybePct(metrics.pnl_pct)}</span></div>
          <div>DTE <span class="text-gray-200">${metrics.dte ?? '—'}</span></div>
          <div>Delta <span class="text-gray-200">${metrics.delta != null ? fmt(metrics.delta, 2) : '—'}</span></div>
          <div>安全垫 <span class="text-gray-200">${fmtMaybePct(metrics.margin_buffer)}</span></div>
        </div>
      </div>
      ${renderExitScenarioTable(pos, signal)}
      ${renderExitReasons(signal)}
    `;
  }
  document.getElementById('exit-signal-modal')?.classList.remove('hidden');
};

function closeExitSignalModal() {
  document.getElementById('exit-signal-modal')?.classList.add('hidden');
}

document.getElementById('exit-signal-close')?.addEventListener('click', closeExitSignalModal);

window.openContinueHoldModal = function(positionId) {
  const pos = findLoadedPosition(positionId);
  const signal = getExitSignal(pos);
  document.getElementById('continue-hold-position-id').value = String(positionId);
  document.getElementById('continue-hold-exit-signal-id').value =
    signal?.id || signal?.exit_signal_id || pos?.latest_exit_signal_id || '';
  document.getElementById('continue-hold-reason').value = '';
  document.getElementById('continue-hold-notes').value = '';
  document.getElementById('continue-hold-label').textContent =
    `${pos?.symbol || ''} · 当前建议：${exitActionLabel(signal?.action)}。`;
  document.getElementById('continue-hold-modal')?.classList.remove('hidden');
};

document.getElementById('continue-hold-cancel')?.addEventListener('click', () => {
  document.getElementById('continue-hold-modal')?.classList.add('hidden');
});

document.getElementById('continue-hold-confirm')?.addEventListener('click', async () => {
  const pid = document.getElementById('continue-hold-position-id').value;
  const reason = (document.getElementById('continue-hold-reason').value || '').trim();
  const notes = (document.getElementById('continue-hold-notes').value || '').trim();
  const exitSignalId = (document.getElementById('continue-hold-exit-signal-id').value || '').trim();
  if (!reason) {
    toast('请输入继续持有原因', 'danger');
    return;
  }
  const body = { action_type: 'CONTINUE', reason };
  if (notes) body.notes = notes;
  if (exitSignalId) body.exit_signal_id = parseInt(exitSignalId, 10);
  try {
    await apiFetch(`/api/positions/${pid}/action-log`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    document.getElementById('continue-hold-modal')?.classList.add('hidden');
    toast('已记录继续持有原因', 'info');
    await loadPositions();
  } catch (e) {
    toast('保存失败：' + e.message, 'danger');
  }
});

function renderPositionCard(p) {
  const m = p.mark || {};
  const spotTxt = m.quote_error ? '—' : fmt(m.spot, 2);
  const midTxt = m.quote_error ? '—' : fmt(m.option_mid, 2);
  let basisTxt = '';
  if (!m.quote_error) {
    if (m.mark_basis === 'mid') basisTxt = '（市价Mid）';
    else if (m.mark_basis === 'bs') basisTxt = '（BS）';
    else if (m.mark_basis === 'mid_fallback') basisTxt = '（Mid·宽价差）';
    else if (m.cached) basisTxt = '（缓存）';
    else if (m.mark_pending) basisTxt = '（待刷新）';
  }
  const pnlPctTxt = (m.quote_error || m.pnl_pct == null) ? '—' : (m.pnl_pct * 100).toFixed(1) + '%';
  const pnlUsdTxt = (m.quote_error || m.unrealized_pnl_usd == null) ? '—' : ('$' + fmt(m.unrealized_pnl_usd, 2));
  let errHtml = '';
  if (m.quote_error) {
    errHtml =
      `<div class="text-[10px] text-red-400 break-words">${escapeHtml(String(m.quote_error).slice(0, 200))}</div>`;
  } else if (m.chain_error) {
    errHtml =
      `<div class="text-[10px] text-yellow-500 break-words">${escapeHtml(String(m.chain_error).slice(0, 200))}（Mid 暂用开仓价）</div>`;
  }
  return `
      <div class="bg-gray-800 rounded-lg p-4 space-y-2">
        <div class="flex items-center justify-between gap-2">
          <span class="font-bold text-white shrink-0">${escapeHtml(p.symbol ?? '')}</span>
          <span class="text-xs text-gray-400 border border-gray-600 rounded px-2 py-0.5 shrink-0">OPEN</span>
        </div>
        <div class="text-xs text-gray-400 space-y-0.5">
          <div>股票现价 <span class="text-white">${spotTxt}</span></div>
          <div>期权估价<span class="text-gray-500">${basisTxt}</span> <span class="text-white">${midTxt}</span></div>
          <div>浮盈比例 <span class="text-white">${pnlPctTxt}</span> · 未实现盈亏 <span class="text-emerald-300">${pnlUsdTxt}</span></div>
          <div>行权价 <span class="text-white">${p.strike}</span> | 到期 <span class="text-white">${escapeHtml(String(p.expiration ?? ''))}</span></div>
          <div>开仓价 <span class="text-white">${fmt(p.open_premium, 2)}</span> × ${p.contracts} 张</div>
          <div>开仓时间 ${p.open_at ? formatEtDatetime(p.open_at) : '-'}</div>
        </div>
        ${errHtml}
        ${renderExitSignalStrip(p)}
        ${p.notes ? `<div class="text-xs text-gray-500 italic break-words">${escapeHtml(p.notes)}</div>` : ''}
        <div class="flex flex-wrap gap-2 mt-2">
          <button type="button" data-pos-act="signal" data-pos-id="${p.id}"
            class="flex-1 min-w-[5.5rem] bg-indigo-700 hover:bg-indigo-600 text-white text-xs py-1 rounded">查看建议</button>
          <button type="button" data-pos-act="continue" data-pos-id="${p.id}"
            class="flex-1 min-w-[5.5rem] bg-gray-700 hover:bg-gray-600 text-white text-xs py-1 rounded">继续持有</button>
          <button type="button" data-pos-act="edit" data-pos-id="${p.id}"
            class="flex-1 min-w-[4.5rem] bg-gray-700 hover:bg-gray-600 text-white text-xs py-1 rounded">编辑</button>
          <button type="button" data-pos-act="close" data-pos-id="${p.id}" data-pos-symbol="${escapeAttr(p.symbol || '')}"
            class="flex-1 min-w-[4.5rem] bg-red-700 hover:bg-red-600 text-white text-xs py-1 rounded">平仓</button>
        </div>
      </div>`;
}

async function loadPositions() {
  const grid = document.getElementById('positions-grid');
  const empty = document.getElementById('positions-empty');
  try {
    const data = await fetchPositionsMarksPayload({fast: true, force: true});
    const positions = data.positions || [];
    _lastPositionsRows = positions;
    updateGlobalQuoteLabel(null);
    if (!positions.length) {
      grid.innerHTML = '';
      _lastPositionsRows = [];
      empty.textContent = POSITIONS_EMPTY_DEFAULT;
      empty.classList.remove('hidden');
      return true;
    }
    empty.classList.add('hidden');
    grid.innerHTML = positions.map(renderPositionCard).join('');
    fetchPositionsMarksPayload({force: true})
      .then(liveData => {
        if (_currentPage !== 'positions') return;
        const livePositions = liveData.positions || [];
        _lastPositionsRows = livePositions;
        updateGlobalQuoteLabel(liveData.quoted_at);
        if (!livePositions.length) return;
        grid.innerHTML = livePositions.map(renderPositionCard).join('');
      })
      .catch(e => {
        if (_currentPage === 'positions') {
          toast('实时行情刷新失败，已显示缓存持仓：' + e.message, 'warn');
        }
      });
    return true;
  } catch (e) {
    toast('加载持仓失败：' + e.message, 'danger');
    _lastPositionsRows = [];
    grid.innerHTML = '';
    empty.textContent = '加载失败，请稍后重试或检查服务：' + e.message;
    empty.classList.remove('hidden');
    return false;
  }
}

window.openClosePositionModal = function (pid, symbol, exitSignal) {
  document.getElementById('close-position-id').value = String(pid);
  document.getElementById('close-position-exit-signal-id').value =
    exitSignal?.id || exitSignal?.exit_signal_id || '';
  const label = document.getElementById('close-position-label');
  label.dataset.symbol = symbol;
  const metrics = exitSignal?.metrics || {};
  document.getElementById('close-position-premium').value =
    metrics.current_mid != null ? String(metrics.current_mid) : '';
  const ca = document.getElementById('close-position-at');
  if (ca) ca.value = defaultDatetimeLocalNow();
  const sel = document.getElementById('close-position-reason');
  if (sel) {
    const suggested = exitSignal?.suggested_close_reason;
    const hasSuggested = suggested && Array.from(sel.options).some(opt => opt.value === suggested);
    sel.value = hasSuggested ? suggested : 'manual';
  }
  syncClosePositionModalFields();
  if (exitSignal?.summary) {
    label.textContent += ` 建议：${exitSignal.summary}`;
    label.title = label.textContent;
  }
  document.getElementById('close-position-modal').classList.remove('hidden');
};

const closePosReasonEl = document.getElementById('close-position-reason');
if (closePosReasonEl) {
  closePosReasonEl.addEventListener('change', syncClosePositionModalFields);
}

document.getElementById('close-position-cancel').addEventListener('click', () => {
  document.getElementById('close-position-modal').classList.add('hidden');
});

document.getElementById('close-position-confirm').addEventListener('click', async () => {
  const pid = document.getElementById('close-position-id').value;
  const reason = document.getElementById('close-position-reason').value;
  const atEl = document.getElementById('close-position-at');
  const exitSignalId = (document.getElementById('close-position-exit-signal-id').value || '').trim();

  let payload;
  if (reason === 'expiry_auto') {
    payload = { expiry_auto: true };
  } else {
    const premium = parseFloat(document.getElementById('close-position-premium').value);
    if (isNaN(premium)) {
      toast('请输入有效的买回价格', 'danger');
      return;
    }
    payload = { close_premium: premium, close_reason: reason };
    if (atEl && atEl.value) {
      const iso = fromDatetimeLocalToIso(atEl.value);
      if (!iso) {
        toast('平仓时间无效', 'danger');
        return;
      }
      payload.close_at = iso;
    }
  }
  if (exitSignalId) payload.exit_signal_id = parseInt(exitSignalId, 10);

  try {
    await apiFetch(`/api/positions/${pid}/close`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    document.getElementById('close-position-modal').classList.add('hidden');
    toast('平仓成功', 'info');
    await loadPage(_currentPage);
  } catch (e) {
    toast('平仓失败: ' + e.message, 'danger');
  }
});

window.openPositionEditor = async function (positionId) {
  try {
    const pos = await apiFetch(`/api/positions/${positionId}`);
    openPositionEditModalFromRow(pos);
  } catch (e) {
    toast('加载持仓失败：' + e.message, 'danger');
  }
};

window.openClosedPositionEditor = function (positionId) {
  window.openPositionEditor(positionId);
};

window.confirmDeleteClosedReviewPosition = async function (positionId) {
  if (!confirm(
    '确定删除该笔历史订单？确认后将标记为已删除，复盘列表与汇总统计将不再包含该笔。',
  )) return;
  try {
    await apiFetch(`/api/review/positions/${positionId}/delete`, { method: 'POST' });
    toast('已标记删除', 'info');
    document.getElementById('position-edit-modal')?.classList.add('hidden');
    closeAttrDrawer();
    await loadReview();
  } catch (e) {
    toast('删除失败：' + e.message, 'danger');
  }
};

function openPositionEditModalFromRow(pos) {
  const isOpen = pos.state === 'OPEN';
  document.getElementById('position-edit-id').value = String(pos.id);
  document.getElementById('position-edit-title').textContent = isOpen ? '编辑持仓' : '编辑历史订单';
  document.getElementById('position-edit-hint').textContent = isOpen
    ? '可修改标的、到期、行权价、张数、开仓权利金与时间等。'
    : '可修改开/平仓时间与价格等；修改开仓价、平仓价或张数后会按规则重算已实现盈亏。';
  document.getElementById('position-edit-symbol').value = pos.symbol || '';
  document.getElementById('position-edit-expiration').value = pos.expiration || '';
  document.getElementById('position-edit-strike').value = pos.strike != null ? String(pos.strike) : '';
  document.getElementById('position-edit-contracts').value =
    pos.contracts != null ? String(pos.contracts) : '1';
  document.getElementById('position-edit-open-premium').value =
    pos.open_premium != null ? String(pos.open_premium) : '';
  document.getElementById('position-edit-open-at').value = toDatetimeLocalValue(pos.open_at);
  document.getElementById('position-edit-notes').value = pos.notes != null ? String(pos.notes) : '';
  const closedBlock = document.getElementById('position-edit-closed-block');
  if (!isOpen) {
    closedBlock.classList.remove('hidden');
    document.getElementById('position-edit-close-at').value = toDatetimeLocalValue(pos.close_at);
    document.getElementById('position-edit-close-premium').value =
      pos.close_premium != null ? String(pos.close_premium) : '';
    const cr = document.getElementById('position-edit-close-reason');
    if (cr && pos.close_reason) cr.value = String(pos.close_reason);
  } else {
    closedBlock.classList.add('hidden');
  }
  document.getElementById('position-edit-modal').classList.remove('hidden');
}

document.getElementById('position-edit-cancel').addEventListener('click', () => {
  document.getElementById('position-edit-modal').classList.add('hidden');
});

document.getElementById('position-edit-save').addEventListener('click', async () => {
  const id = (document.getElementById('position-edit-id').value || '').trim();
  if (!/^\d+$/.test(id)) {
    toast('无效的持仓编号，请关闭后重新打开编辑', 'danger');
    return;
  }
  const closedBlock = document.getElementById('position-edit-closed-block');
  const isClosedVisible = !closedBlock.classList.contains('hidden');
  const oIso = fromDatetimeLocalToIso(document.getElementById('position-edit-open-at').value);
  if (!oIso) {
    toast('开仓时间无效', 'danger');
    return;
  }
  const body = {
    symbol: (document.getElementById('position-edit-symbol').value || '').trim(),
    expiration: document.getElementById('position-edit-expiration').value,
    strike: parseFloat(document.getElementById('position-edit-strike').value),
    contracts: parseInt(document.getElementById('position-edit-contracts').value, 10),
    open_premium: parseFloat(document.getElementById('position-edit-open-premium').value),
    open_at: oIso,
    notes: document.getElementById('position-edit-notes').value,
  };
  if (!body.symbol) {
    toast('请输入标的', 'danger');
    return;
  }
  if (!body.expiration) {
    toast('请选择到期日', 'danger');
    return;
  }
  if (isNaN(body.strike) || body.strike <= 0) {
    toast('行权价无效', 'danger');
    return;
  }
  if (isNaN(body.contracts) || body.contracts < 1) {
    toast('张数无效', 'danger');
    return;
  }
  if (isNaN(body.open_premium) || body.open_premium <= 0) {
    toast('开仓价无效', 'danger');
    return;
  }
  if (isClosedVisible) {
    const cIso = fromDatetimeLocalToIso(document.getElementById('position-edit-close-at').value);
    if (!cIso) {
      toast('平仓时间无效', 'danger');
      return;
    }
    body.close_at = cIso;
    body.close_premium = parseFloat(document.getElementById('position-edit-close-premium').value);
    body.close_reason = document.getElementById('position-edit-close-reason').value;
    if (isNaN(body.close_premium) || body.close_premium < 0) {
      toast('平仓价无效', 'danger');
      return;
    }
  }
  try {
    await apiFetch(`/api/positions/${id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    toast('已保存', 'info');
    document.getElementById('position-edit-modal').classList.add('hidden');
    await loadPage(_currentPage);
  } catch (e) {
    toast('保存失败：' + e.message, 'danger');
  }
});

document.getElementById('btn-refresh-quotes').addEventListener('click', async () => {
  const btn = document.getElementById('btn-refresh-quotes');
  const label = btn.textContent;
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  btn.textContent = '刷新中…';
  try {
    if (_currentPage === 'positions') {
      const ok = await loadPositions();
      if (ok) toast('行情刷新成功', 'info');
    } else {
      const data = await fetchPositionsMarksPayload();
      updateGlobalQuoteLabel(data.quoted_at);
      const t = data.quoted_at ? formatQuotedAt(data.quoted_at) : '';
      toast(t ? `行情刷新成功（${t}）` : '行情刷新成功', 'info');
    }
  } catch (e) {
    toast('刷新失败：' + e.message, 'danger');
  } finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    btn.textContent = label;
  }
});

// ================================================================
// #review
// ================================================================
const REVIEW_CLOSE_REASON_LABELS = {
  expired_otm: '到期 OTM',
  take_profit_50: '止盈（50%）',
  take_profit_75: '止盈（75%）',
  take_profit_strong: '强止盈',
  roll_extend: '展期',
  assigned: '指派',
  manual: '手动',
  delta_breach: 'Delta 突破',
  time_danger: '时间危险',
  time_warning: '时间警告',
  time_14d: '时间 14 天',
  time_7d: '时间 7 天',
  danger_3pct: '危险 3%',
};

function reviewCloseReasonLabel(code) {
  if (code == null || code === '') return '-';
  return REVIEW_CLOSE_REASON_LABELS[code] ?? code;
}

function reviewPct(value, decimals = 1, signed = false) {
  if (value == null || value === '') return '-';
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  const prefix = signed && n > 0 ? '+' : '';
  return prefix + (n * 100).toFixed(decimals) + '%';
}

function renderReviewSuggestions(items) {
  const el = document.getElementById('review-suggestions');
  if (!el) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    el.innerHTML = '<div class="text-xs text-gray-500">暂无可操作建议；继续积累已平仓交易后会自动生成。</div>';
    return;
  }
  const cls = {
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
    info: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-100',
  };
  el.innerHTML = rows.map(row => `
    <div class="rounded border ${cls[row.severity] || cls.info} px-3 py-2">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="text-sm font-semibold leading-snug">${escapeHtml(row.title || '复盘建议')}</div>
        ${row.setting_key ? `<span class="rounded bg-gray-950/40 px-1.5 py-0.5 text-[10px] text-gray-300">${escapeHtml(row.setting_key)}</span>` : ''}
      </div>
      <div class="mt-1 break-words text-xs leading-relaxed text-gray-300">${escapeHtml(row.detail || '')}</div>
    </div>
  `).join('');
}

function renderReviewFactorSlices(slices) {
  const el = document.getElementById('review-factor-slices');
  if (!el) return;
  const rows = Array.isArray(slices) ? slices : [];
  if (!rows.length) {
    el.innerHTML = '<div class="text-xs text-gray-500">暂无因子切片；已结束持仓生成入场快照后会显示。</div>';
    return;
  }
  el.innerHTML = rows.map(slice => {
    const buckets = Array.isArray(slice.buckets) ? slice.buckets : [];
    const body = buckets.map(bucket => `
      <tr class="border-t border-gray-700/70 text-xs">
        <td class="py-1.5 pr-2 text-left text-gray-200">${escapeHtml(bucket.label || bucket.bucket || '-')}</td>
        <td class="py-1.5 text-right text-gray-300">${bucket.count ?? 0}</td>
        <td class="py-1.5 text-right text-gray-300">${reviewPct(bucket.win_rate, 0)}</td>
        <td class="py-1.5 text-right text-gray-300">${reviewPct(bucket.avg_roe, 1, true)}</td>
        <td class="py-1.5 text-right text-gray-300">${bucket.avg_holding_days != null ? Number(bucket.avg_holding_days).toFixed(1) + 'd' : '-'}</td>
        <td class="py-1.5 text-right text-gray-300">${reviewPct(bucket.avg_maee, 1, true)}</td>
        <td class="py-1.5 text-right text-gray-300">${reviewPct(bucket.avg_mfe, 1, true)}</td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="py-3 text-center text-xs text-gray-500">暂无数据</td></tr>';
    return `
      <div class="rounded border border-gray-700 bg-gray-900/40 p-3">
        <div class="mb-2 text-sm font-medium text-indigo-200">${escapeHtml(slice.label || slice.factor || '-')}</div>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[34rem]">
            <thead class="text-[10px] uppercase text-gray-500">
              <tr>
                <th class="pb-1 text-left">桶</th>
                <th class="pb-1 text-right">笔数</th>
                <th class="pb-1 text-right">胜率</th>
                <th class="pb-1 text-right">ROE</th>
                <th class="pb-1 text-right">持仓</th>
                <th class="pb-1 text-right">MAEE</th>
                <th class="pb-1 text-right">MFE</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </div>
    `;
  }).join('');
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
      '<tr><td colspan="12" class="text-center text-gray-500 py-3 text-xs">—</td></tr>';
    renderReviewSuggestions([]);
    renderReviewFactorSlices([]);
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
      hint: '最大浮亏比例均值 / 最大浮盈比例均值（优先 intraday_bs，无则雷达快照）',
    },
  ];
  document.getElementById('review-summary').innerHTML = cards.map(c => `
    <div class="bg-gray-800 rounded-lg p-4 text-center">
      <div class="text-xl font-bold ${c.valueClass || 'text-indigo-300'} leading-tight">${c.value}</div>
      <div class="text-xs text-gray-400 mt-1 leading-snug">${c.label}</div>
      ${c.hint ? `<div class="text-[10px] text-gray-500 mt-0.5 leading-tight">${c.hint}</div>` : ''}
    </div>
  `).join('');

  renderReviewSuggestions(summary.setting_suggestions || []);
  renderReviewFactorSlices(summary.factor_slices || []);

  const breakdown = summary.by_close_reason || [];
  document.getElementById('breakdown-tbody').innerHTML = breakdown.map(row => `
    <tr class="text-center text-xs">
      <td class="text-left py-1">${reviewCloseReasonLabel(row.close_reason)}</td>
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
      '<tr><td colspan="12" class="text-center text-red-400 py-3 text-xs">历史订单加载失败</td></tr>';
  } else if (!orders.length) {
    document.getElementById('review-orders-tbody').innerHTML =
      '<tr><td colspan="12" class="text-center text-gray-500 py-3 text-xs">尚无已结束成交</td></tr>';
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
      <td class="py-1.5 text-left" onclick="event.stopPropagation();">
        <button type="button" class="text-amber-400 hover:text-amber-300 text-xs font-medium"
          onclick="window.recalcClosedEntryInsights(${p.id})">重算</button>
        <button type="button" class="text-indigo-400 hover:text-indigo-300 text-xs font-medium ml-2"
          onclick="window.openClosedPositionEditor(${p.id})">编辑</button>
        <button type="button" class="ml-2 text-rose-400 hover:text-rose-300 text-xs font-medium"
          onclick="window.confirmDeleteClosedReviewPosition(${p.id})">删除</button>
      </td>
      <td class="text-left py-1.5 text-gray-400 max-w-[8rem] truncate" title="${note.replace(/"/g, '&quot;')}">${note}</td>
    </tr>`;
    }).join('');
  }
}

window.recalcClosedEntryInsights = async function (positionId) {
  if (!confirm(
    '将删除该机位的全部雷达快照，并按开仓日历日 BS 反算入场 IV/Greeks，再用日线收盘生成回放曲线（持仓期内常数 IV，近似实盘）。仅限已平仓。继续？',
  )) {
    toast('已取消入场重算', 'info');
    return;
  }
  const ctrl = new AbortController();
  /** yfinance / 合并快照可能较慢，超时给明确反馈以免误以为无响应 */
  const tid = setTimeout(() => ctrl.abort(), 180000);
  try {
    toast('入场重算进行中…（请勿重复点击）', 'warn');
    const r = await apiFetch(`/api/review/positions/${positionId}/entry_recalc`, {
      method: 'POST',
      signal: ctrl.signal,
    });
    const n = r.radar_rows_inserted != null ? r.radar_rows_inserted : 0;
    toast(`入场重算完成：回放雷达 ${n} 条`, 'info');
    try {
      if (_currentPage === 'review') await loadReview();
      const overlay = document.getElementById('attr-drawer-overlay');
      if (overlay && !overlay.classList.contains('hidden')) await loadAttrDrawerData(positionId);
    } catch (reloadErr) {
      toast(`重算已成功，刷新列表/抽屉失败：${reloadErr.message}`, 'warn');
    }
  } catch (e) {
    const msg =
      e && e.name === 'AbortError'
        ? '入场重算超时（行情请求过慢或无响应），请稍后重试'
        : e.message || String(e);
    toast('入场重算失败：' + msg, 'danger');
  } finally {
    clearTimeout(tid);
  }
};

document.getElementById('btn-refresh-entry-snapshots')?.addEventListener('click', async () => {
  if (!confirm(
    '将按每笔开仓日期，把入场环境快照写入数据库（与侧栏「入场环境快照」同源：候选希腊值 + 入场日 RSI/布林带等）。'
      + '包含未平仓与已平仓持仓，需请求行情接口，笔数多时可能耗时数分钟。继续？',
  )) return;
  const btn = document.getElementById('btn-refresh-entry-snapshots');
  const label = btn?.textContent;
  try {
    if (btn) {
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      btn.textContent = '更新中…';
    }
    const r = await apiFetch('/api/review/snapshots/refresh_entry', { method: 'POST' });
    const nErr = Array.isArray(r.errors) ? r.errors.length : 0;
    const skip = r.skipped_empty != null ? Number(r.skipped_empty) : 0;
    const msg =
      `持仓共 ${r.total ?? 0} 笔（含未平仓）：已写入 ${r.saved ?? 0} 笔` +
      (skip ? `，${skip} 笔无可用数据` : '') +
      (nErr ? `，${nErr} 笔出错` : '');
    toast(msg, nErr ? 'warn' : 'info');
    if (_currentPage === 'review') await loadReview();
  } catch (e) {
    toast('批量更新入场快照失败：' + e.message, 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      btn.textContent = label || '更新入场快照';
    }
  }
});

// ================================================================
// Attribution Drawer
// ================================================================

function openAttrDrawer(positionId, title) {
  document.getElementById('attr-drawer-title').textContent = title || '持仓详情';
  document.getElementById('attr-drawer-body').innerHTML =
    '<div class="flex items-center justify-center py-12 text-gray-500 text-sm">加载中…</div>';
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
  attr = attr || {};
  const snapCardShell =
    'rounded-xl border border-gray-700/80 bg-gray-900/90 shadow-sm';

  const dash = '<span class="text-gray-600 tabular-nums">—</span>';

  const snapshotData = (snap && snap.open_snapshot) || (snap && snap.candidate_data);

  /** 检查某个 excursion 块（massive / intraday_bs）的 MAE 或 MFE 任一可展示 */
  function excursionRenderable(m) {
    if (!m || !m.option_ticker) return false;
    const ok = v => v != null && v !== '' && Number.isFinite(Number(v));
    return ok(m.mae_pnl_pct) || ok(m.mfe_pnl_pct);
  }

  const massive = snap && snap.open_snapshot && snap.open_snapshot.massive;
  const intradayBs = snap && snap.open_snapshot && snap.open_snapshot.intraday_bs;

  /**
   * 浮动极值卡片来源优先级：
   *   1. intraday_bs（5m/1h × BS）  →  始终显示，用精细数据
   *   2. 雷达/回放（attr.mae/mfe）  →  显示，当 intraday_bs 无数据时
   *   3. 若无 intraday_bs 且 Massive 有 EOD 数据 →  隐藏（避免与 Massive 卡片重复 EOD 粒度）
   */
  const intradayHasData = excursionRenderable(intradayBs);
  const massiveHasData = excursionRenderable(massive);
  const hideRadarFloatingMaeMfe = !intradayHasData && massiveHasData;

  // ---- Card 1: PnL Attribution ----
  let cardPnL;
  if (attr.data_available && attr.delta_contribution != null) {
    const totalPnl = Number(attr.total_pnl) || 0;
    const absTotal = Math.abs(totalPnl) || 1;
    const items = [
      { label: 'Delta 贡献', value: attr.delta_contribution, hint: '标的价格变动' },
      { label: 'Theta 贡献', value: attr.theta_contribution, hint: '时间流逝收益' },
      { label: '残差（含 Vega）', value: attr.residual, hint: '隐含波动率变动等' },
    ];
    const bars = items.map(item => {
      const v = Number(item.value);
      const safeV = Number.isFinite(v) ? v : 0;
      const pct = Math.min(Math.abs(safeV) / absTotal * 100, 100).toFixed(0);
      const barCls = safeV >= 0 ? 'bg-emerald-500' : 'bg-rose-500';
      const txtCls = safeV >= 0 ? 'text-emerald-400' : 'text-rose-400';
      const sign = safeV >= 0 ? '+' : '';
      return `
        <div class="space-y-1.5">
          <div class="flex justify-between items-baseline gap-2 text-sm">
            <span class="text-gray-100">${item.label}</span>
            <span class="${txtCls} font-semibold tabular-nums">${sign}$${safeV.toFixed(1)}</span>
          </div>
          <p class="text-xs text-gray-500">${item.hint}</p>
          <div class="h-2 rounded-full bg-gray-800 overflow-hidden">
            <div class="h-full rounded-full ${barCls}" style="width:${pct}%"></div>
          </div>
        </div>`;
    }).join('');
    const totalCls = totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
    const totalSign = totalPnl >= 0 ? '+' : '';
    cardPnL = `
      <section class="${snapCardShell} p-4 space-y-4">
        <div class="flex justify-between items-baseline gap-2 border-b border-gray-700/90 pb-3">
          <span class="text-sm font-semibold text-gray-100">PnL 归因（首阶 BS）</span>
          <span class="text-sm font-semibold tabular-nums ${totalCls}">${totalSign}$${totalPnl.toFixed(1)} 总计</span>
        </div>
        ${bars}
        <p class="text-xs text-gray-500 pt-1">持仓 ${attr.days_held ?? '?'} 天 · 雷达快照 ${attr.radar_points ?? 0} 条</p>
      </section>`;
  } else {
    let msg =
      attr.data_available === false
        ? '无入场希腊值，无法计算归因'
        : '缺少平仓雷达或价差数据，暂无法分解归因';
    if (attr.data_available !== false) {
      const incompleteEntry =
        attr.entry_delta == null || attr.spot_open == null || attr.entry_theta == null;
      const noSpotAtClose = attr.spot_close == null;
      if (incompleteEntry) {
        msg =
          '无入场 Greek/标的价可用（常为「特定搜索」旧流程未落库）。可走「更新入场快照」或为该笔持仓关联候选后重刷。';
      } else if (noSpotAtClose) {
        msg =
          '缺少平仓时点附近标的价（雷达快照）；持仓极短或未跑 worker 雷达时易发。平仓接口会尽力写入一条收盘雷达——若仍为空白可检查期权行情抓取是否报错。';
      }
    }
    cardPnL = `
      <section class="${snapCardShell} p-4">
        <div class="flex justify-between items-baseline gap-2 border-b border-gray-700/90 pb-3 mb-4">
          <span class="text-sm font-semibold text-gray-100">PnL 归因（首阶 BS）</span>
          <span class="text-xs font-medium text-gray-600">—</span>
        </div>
        <p class="text-center text-xs text-gray-500 py-6">${msg}</p>
      </section>`;
  }

  // ---- Card 2: 浮动极值（入场时机评估）----
  // 来源优先级：intraday_bs（5m/1h × BS）> 雷达/回放（attr.mae/mfe）
  let excMae, excMfe, excSourceNote;
  if (intradayHasData) {
    excMae = intradayBs.mae_pnl_pct;
    excMfe = intradayBs.mfe_pnl_pct;
    let ivSrcLabel;
    if (intradayBs.iv_source === 'massive_eod_backfit') {
      ivSrcLabel = 'IV 来自 Massive EOD 反推';
    } else if (intradayBs.iv_source === 'implied_iv_open_fill_hold_window') {
      ivSrcLabel = '持仓窗 BS 按成交价反推隐含波动率（与开仓权利金一致；锚点更接近 0%）';
    } else if (intradayBs.iv_source === 'entry_snapshot_iv_hold_window') {
      ivSrcLabel = `持仓窗 BS 用入场快照 IV（${snapshotData && snapshotData.iv != null ? (Number(snapshotData.iv) * 100).toFixed(0) + '%' : '—'}）；反推失败时回退`;
    } else {
      ivSrcLabel = `IV 来自入场快照（常数 ${snapshotData && snapshotData.iv != null ? (Number(snapshotData.iv) * 100).toFixed(0) + '%' : '—'}）`;
    }
    const hlDesc = intradayBs.interval === 'hold_window_hl'
      ? '持仓时段内股价的 <strong>High / Low</strong>'
      : '每日股价 <strong>High / Low</strong>';
    const hw = intradayBs.hold_window || {};
    const rangePart = hw.open_date_et && hw.close_date_et
      ? `持仓 ET ${hw.open_date_et}〜${hw.close_date_et}（含首尾）。` : '';
    const barNote = intradayBs.interval === 'hold_window_hl'
      ? `本段共 ${intradayBs.bar_count ?? '—'} 条（窗内 Low + High）`
      : `每日 2 条极值，共 ${intradayBs.bar_count ?? '—'} 条`;
    excSourceNote = `<p class="text-xs text-indigo-400/80 mb-3 leading-snug">`
      + `${rangePart}按${hlDesc} 各算一次 BS 期权价（${ivSrcLabel}）。`
      + `${barNote}，非期权盘中成交价。</p>`;
  } else {
    excMae = attr.mae;
    excMfe = attr.mfe;
    excSourceNote = (snapshotData && snapshotData.replay_model)
      ? '<p class="text-xs text-amber-600/85 mb-3 leading-snug">当前 MAE/MFE 来自「入场重算」生成的日线 BS 回放（常数 IV），与真实盘中路径有偏差。</p>'
      : '';
    if (!excSourceNote && attr.mae_mfe_flat_replay) {
      excSourceNote = '<p class="text-xs text-gray-500 mb-3 leading-snug">雷达快照 PnL% 全程相同（标的不动或常数 IV），MAE/MFE 无法从日线柱估计。</p>';
    }
  }

  const maeTxt = excMae != null ? `${(Number(excMae) * 100).toFixed(1)}%` : null;
  const mfeTxt = excMfe != null ? `${(Number(excMfe) * 100).toFixed(1)}%` : null;
  let maeCls = 'text-gray-500';
  if (excMae != null) maeCls = Number(excMae) < 0 ? 'text-rose-400' : 'text-emerald-400';
  let mfeCls = 'text-gray-500';
  if (excMfe != null) mfeCls = Number(excMfe) >= 0 ? 'text-emerald-400' : 'text-rose-400';

  const cardMaeMfe = hideRadarFloatingMaeMfe
    ? ''
    : `
    <section class="${snapCardShell} p-4">
      <h4 class="text-sm font-semibold text-gray-100 mb-3">浮动极值（入场时机评估）</h4>
      ${excSourceNote}
      <div class="grid grid-cols-2 gap-6">
        <div class="text-center space-y-2">
          <div class="text-2xl font-bold tabular-nums ${maeCls}">${maeTxt ?? dash}</div>
          <div class="text-xs text-gray-400">MAEE（最大浮亏）</div>
          <div class="text-xs text-gray-500 leading-snug">越负说明接飞刀风险越高</div>
        </div>
        <div class="text-center space-y-2">
          <div class="text-2xl font-bold tabular-nums ${mfeCls}">${mfeTxt ?? dash}</div>
          <div class="text-xs text-gray-400">MFE（最大浮盈）</div>
          <div class="text-xs text-gray-500 leading-snug">止盈机会最大化参考</div>
        </div>
      </div>
    </section>`;

  let cardMassive = '';
  // 当 intraday_bs（日内 H/L × BS）有展示数值时，Massive EOD 数据已被取代，隐藏该卡片。
  if (massive && massive.option_ticker && !intradayHasData) {
    const maeM = massive.mae_pnl_pct;
    const mfeM = massive.mfe_pnl_pct;
    const maeMTxt = maeM != null ? `${(Number(maeM) * 100).toFixed(1)}%` : dash;
    const mfeMTxt = mfeM != null ? `${(Number(mfeM) * 100).toFixed(1)}%` : dash;
    const maeMCls = maeM != null && Number(maeM) < 0 ? 'text-rose-400' : 'text-gray-500';
    const mfeMCls = mfeM != null && Number(mfeM) >= 0 ? 'text-emerald-400' : 'text-gray-500';
    const hw = massive.hold_window || {};
    const clip = massive.fetch_clip || {};
    const rangeBits = [];
    if (hw.open_date_et && hw.close_date_et) {
      rangeBits.push(`持仓日历（ET）${hw.open_date_et}〜${hw.close_date_et}（含首尾）`);
    }
    if (clip.start_date_et && clip.end_date_et) {
      rangeBits.push(`请求区间（已截 2 年）${clip.start_date_et}〜${clip.end_date_et}`);
    }
    const rangeLine = rangeBits.length ? `${rangeBits.join(' · ')}。` : '';
    // Massive 卡片只在 intraday_bs 无数据时展示（见上方 !intradayHasData 条件）
    const massiveIntro = `${rangeLine}按 <span class="text-gray-400">${massive.option_ticker}</span> 在持仓日历内的<strong>日终收盘</strong>序列估算 MAE/MFE（相对首根柱 PnL%；EOD，≤2 年历史；非盘中路径）。平仓日若为盘中，最后一根 EOD 可能<strong>晚于</strong>实际平仓时刻。`;
    cardMassive = `
    <section class="${snapCardShell} p-4">
      <h4 class="text-sm font-semibold text-gray-100 mb-1">Massive 日终期权 K 线极值</h4>
      <p class="text-xs text-gray-500 mb-4 leading-snug">
        ${massiveIntro}
        条数 ${massive.bar_count ?? '—'} · 拉取 ${massive.fetched_at ? String(massive.fetched_at).replace('T', ' ').slice(0, 19) : '—'}
      </p>
      <div class="grid grid-cols-2 gap-6">
        <div class="text-center space-y-2">
          <div class="text-xl font-bold tabular-nums ${maeMCls}">${maeMTxt}</div>
          <div class="text-xs text-gray-400">MAEE（Massive）</div>
        </div>
        <div class="text-center space-y-2">
          <div class="text-xl font-bold tabular-nums ${mfeMCls}">${mfeMTxt}</div>
          <div class="text-xs text-gray-400">MFE（Massive）</div>
        </div>
      </div>
    </section>`;
  }

  // ---- Card 3: Entry snapshot（固定行顺序，对齐设计稿）----
  const fmtFracPct = v =>
    v != null && v !== '' ? `${(Number(v) * 100).toFixed(1)}%` : null;
  const fmtNumStr = (v, d) =>
    v != null && v !== '' && Number.isFinite(Number(v)) ? Number(v).toFixed(d) : null;

  let bbHtml = null;
  if (snapshotData && snapshotData.bb_distance_pct != null && snapshotData.bb_distance_pct !== '') {
    const bbv = Number(snapshotData.bb_distance_pct);
    const bbCls = bbv < 0 ? 'text-rose-400' : 'text-gray-50';
    bbHtml = `<span class="${bbCls}">${bbv.toFixed(1)}%</span>`;
  }

  const ivRankStr = snapshotData ? fmtNumStr(snapshotData.iv_rank, 1) : null;
  const rowSpecs = snapshotData
    ? [
        ['IV Rank', ivRankStr != null ? `${ivRankStr}%` : null],
        ['隐含波动率（IV）', fmtFracPct(snapshotData.iv)],
        ['Delta（入场）', fmtNumStr(snapshotData.delta, 3)],
        ['Theta（日衰）', fmtNumStr(snapshotData.theta, 4)],
        ['入场标的价', snapshotData.spot != null ? `$${fmtNumStr(snapshotData.spot, 2)}` : null],
        ['入场 DTE', snapshotData.dte != null ? `${snapshotData.dte} 天` : null],
        ['RSI(6)', fmtNumStr(snapshotData.rsi_6, 1)],
        ['RSI(12)', fmtNumStr(snapshotData.rsi_12, 1)],
        ['RSI(24)', fmtNumStr(snapshotData.rsi_24, 1)],
        ['距布林带下轨', bbHtml, true],
        ['入场年化收益', fmtFracPct(snapshotData.annualized_roi)],
        ['候选评分', fmtNumStr(snapshotData.score, 1)],
      ]
    : [];

  let cardSnap;
  if (rowSpecs.length) {
    const tableRows = rowSpecs
      .map(parts => {
        const label = parts[0];
        const cell = parts[1];
        const rawHtml = parts[2];
        let inner;
        if (cell != null && cell !== '') {
          inner = rawHtml ? cell : `<span class="text-gray-50 tabular-nums">${cell}</span>`;
        } else {
          inner = dash;
        }
        return `
        <tr class="border-b border-gray-800 last:border-b-0">
          <td class="py-2.5 text-xs text-gray-400 align-top">${label}</td>
          <td class="py-2.5 text-right text-xs font-medium align-top">${inner}</td>
        </tr>`;
      })
      .join('');
    const fromRealtime = !!(snap && snap.open_snapshot);
    const realtimeSpan =
      snapshotData && snapshotData.replay_model ? '回放补算快照' : '实时捕获';
    const replayFoot = fromRealtime && snapshotData.replay_model
      ? '<p class="mt-2 text-xs text-amber-600/85 leading-snug">入场希腊：按开仓日历日与已记录开仓价反推 IV（BS）；MAE/MFE 若基于此回放，为日线收盘 + 持仓期常数 IV 的近似路径。</p>'
      : '';
    const footer = fromRealtime
      ? `<div class="mt-3 pt-3 border-t border-gray-800 flex items-center gap-1.5 text-xs text-gray-500">
           <span class="text-emerald-500 font-semibold" aria-hidden="true">✓</span>
           <span>${realtimeSpan}</span>
         </div>${replayFoot}`
      : `<div class="mt-3 pt-3 border-t border-gray-800 text-xs text-gray-500">来源：候选记录</div>${replayFoot}`;
    cardSnap = `
      <section class="${snapCardShell} p-4">
        <h4 class="text-sm font-semibold text-gray-100 mb-1">入场环境快照</h4>
        <table class="w-full border-collapse">${tableRows}</table>
        ${footer}
      </section>`;
  } else {
    cardSnap = `
      <section class="${snapCardShell} p-4">
        <div class="flex justify-between items-baseline gap-2 border-b border-gray-700/90 pb-3 mb-4">
          <span class="text-sm font-semibold text-gray-100">入场环境快照</span>
          <span class="text-xs font-medium text-gray-600">—</span>
        </div>
        <p class="text-center text-xs text-gray-500 py-6">暂无入场环境快照（可先执行「更新入场快照」）</p>
      </section>`;
  }

  return `${cardPnL}${cardMaeMfe}${cardMassive}${cardSnap}`;
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
  integrations: {
    massive_enrich_closed: '已平仓 Massive 增补（0 关 / 1 开；需 .env 中 MASSIVE_API_KEY）',
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
    {key: 'integrations', label: '外部数据'},
  ];
  container.innerHTML = groups.map(g => {
    let raw = s[g.key] ?? {};
    if (g.key === 'integrations' && (!raw || Object.keys(raw).length === 0)) {
      raw = { massive_enrich_closed: 0 };
    }
    const data = raw;
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
    const msg = normalizeHttpErrorMessage(text, resp.statusText);
    throw new Error(msg || resp.statusText || '请求失败');
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

/** Safe text for interpolating into HTML body text nodes. */
function escapeHtml(raw) {
  return String(raw)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Safe escaping for values in double-quoted data-* attributes. */
function escapeAttr(raw) {
  return String(raw)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;');
}

document.getElementById('positions-grid').addEventListener('click', ev => {
  const btn = ev.target.closest('[data-pos-act]');
  if (!btn) return;
  const idStr = btn.getAttribute('data-pos-id');
  const id = parseInt(idStr ?? '', 10);
  if (!Number.isFinite(id)) return;
  const act = btn.getAttribute('data-pos-act');
  const pos = findLoadedPosition(id);
  if (act === 'signal') window.openExitSignalModal(id);
  else if (act === 'continue') window.openContinueHoldModal(id);
  else if (act === 'edit') void window.openPositionEditor(id);
  else if (act === 'close')
    window.openClosePositionModal(
      id,
      btn.getAttribute('data-pos-symbol') || '',
      getExitSignal(pos),
    );
});

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
