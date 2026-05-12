/* ============================================================
   期权卖方助手 — SPA Core
   ============================================================ */

const API = '';  // same origin

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
  if (page === 'screener') await loadScreener();
  else if (page === 'positions') await loadPositions();
  else if (page === 'review') await loadReview();
  else if (page === 'settings') await loadSettings();
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
      <div class="text-gray-500 mt-0.5">${ev.created_at ? ev.created_at.slice(0,19) : ''}</div>
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
async function loadScreener() {
  // Load watchlist
  const wl = await apiFetch('/api/watchlist');
  const symbols = wl.map(w => w.symbol).join(', ');
  document.getElementById('watchlist-input').value = symbols;

  // Load latest candidates
  const candidates = await apiFetch('/api/scan/latest');
  renderCandidates(candidates);
}

function renderCandidates(rows) {
  const tbody = document.getElementById('candidates-tbody');
  const empty = document.getElementById('candidates-empty');
  if (!rows || rows.length === 0) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
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
  try {
    await apiFetch('/api/scan/run', {method: 'POST'});
    toast('扫描已在后台启动，请稍候刷新', 'info');
    setTimeout(() => { loadScreener(); btn.textContent = '立即扫描'; btn.disabled = false; }, 8000);
  } catch(e) {
    toast('扫描启动失败: ' + e.message, 'danger');
    btn.textContent = '立即扫描'; btn.disabled = false;
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
async function loadPositions() {
  const positions = await apiFetch('/api/positions?state=OPEN');
  const grid = document.getElementById('positions-grid');
  const empty = document.getElementById('positions-empty');
  if (!positions.length) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  grid.innerHTML = positions.map(p => {
    return `
      <div class="bg-gray-800 rounded-lg p-4 space-y-2">
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">${p.symbol}</span>
          <span class="text-xs text-gray-400 border border-gray-600 rounded px-2 py-0.5">OPEN</span>
        </div>
        <div class="text-xs text-gray-400 space-y-0.5">
          <div>行权价 <span class="text-white">${p.strike}</span> | 到期 <span class="text-white">${p.expiration}</span></div>
          <div>开仓价 <span class="text-white">${fmt(p.open_premium, 2)}</span> × ${p.contracts} 张</div>
          <div>开仓时间 ${p.open_at ? p.open_at.slice(0,10) : '-'}</div>
        </div>
        ${p.notes ? `<div class="text-xs text-gray-500 italic">${p.notes}</div>` : ''}
        <div class="flex gap-2 mt-2">
          <button onclick="closePosition(${p.id}, '${p.symbol}')"
            class="flex-1 bg-red-700 hover:bg-red-600 text-white text-xs py-1 rounded">平仓</button>
          <button onclick="viewRadar(${p.id}, '${p.symbol}')"
            class="flex-1 bg-gray-700 hover:bg-gray-600 text-xs py-1 rounded">雷达</button>
        </div>
      </div>`;
  }).join('');
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

window.viewRadar = async function(pid, symbol) {
  const snaps = await apiFetch(`/api/positions/${pid}/radar?limit=20`);
  if (!snaps.length) { toast(`${symbol} 暂无雷达快照`, 'info'); return; }
  const latest = snaps[0];
  alert(`${symbol} 最新雷达（${latest.taken_at?.slice(0,16) ?? ''}）\n` +
    `Spot: ${latest.spot ?? '-'}\nMid: ${latest.current_mid ?? '-'}\n` +
    `P&L%: ${latest.pnl_pct != null ? (latest.pnl_pct * 100).toFixed(1) + '%' : '-'}\n` +
    `信号: ${latest.signals ?? '[]'}`);
};

// ================================================================
// #review
// ================================================================
async function loadReview() {
  let summary = null;
  try {
    summary = await apiFetch('/api/review/summary');
  } catch {
    document.getElementById('review-summary').innerHTML =
      '<div class="col-span-4 text-sm text-gray-500">复盘数据不可用（尚无已结束持仓）</div>';
    return;
  }
  const cards = [
    {label: '总胜率', value: summary.win_rate != null ? (summary.win_rate * 100).toFixed(1) + '%' : '-'},
    {label: '平均年化', value: summary.avg_annualized_roi != null ? (summary.avg_annualized_roi * 100).toFixed(1) + '%' : '-'},
    {label: '累计权利金', value: summary.total_premium != null ? '$' + summary.total_premium.toFixed(0) : '-'},
    {label: '交易笔数', value: summary.trade_count ?? '-'},
  ];
  document.getElementById('review-summary').innerHTML = cards.map(c => `
    <div class="bg-gray-800 rounded-lg p-4 text-center">
      <div class="text-2xl font-bold text-indigo-300">${c.value}</div>
      <div class="text-xs text-gray-400 mt-1">${c.label}</div>
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
}

// ================================================================
// #settings
// ================================================================
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
        <label class="w-52 text-xs text-gray-400">${k}</label>
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
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(err || resp.statusText);
  }
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return resp.json();
  return resp.text();
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
