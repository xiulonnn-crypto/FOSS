/* ============================================================
 * static-shim.js — GitHub Pages 只读复盘垫片
 *
 * 复用真实前端 app.js 的全部渲染逻辑，但在没有后端的纯静态环境下：
 *   1) 默认进入【复盘】页；
 *   2) 拦截 window.fetch，把 /api/review/* 的 GET 请求改读同目录的静态 JSON
 *      （由 scripts/export_review_static.py 导出）；
 *   3) 屏蔽所有写操作（POST/PUT/...）与 SSE，避免云端报错；
 *   4) 隐藏筛选 / CSV 导出 / 更新快照等无法静态执行的入口。
 *
 * 必须在 app.js 之前加载（本文件不依赖 app.js 的任何符号）。
 * ============================================================ */
(function () {
  'use strict';

  // 默认落在复盘页（须在底部内联引导脚本读取 hash 之前执行）。
  if (!location.hash) {
    try { history.replaceState(null, '', location.pathname + location.search + '#review'); }
    catch (_) { location.hash = '#review'; }
  }

  function jsonResponse(obj, status) {
    return new Response(JSON.stringify(obj), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /** 把 /api/... 请求路径映射到同目录下的静态 JSON 相对路径；无映射返回 null。 */
  function mapApiToStatic(pathname) {
    var p = pathname.replace(/\/+$/, '');
    if (p === '/api/review/summary') return 'api/review/summary.json';
    if (p === '/api/review/suggestions') return 'api/review/suggestions.json';
    if (p === '/api/review/closed_positions') return 'api/review/closed_positions.json';
    var m = p.match(/^\/api\/review\/positions\/(\d+)\/(attribution|snapshot|diagnosis)$/);
    if (m) return 'api/review/positions/' + m[1] + '/' + m[2] + '.json';
    return null;
  }

  /** 其它未导出的 GET 接口给出无害默认值，避免页面报错。 */
  function benignDefault(pathname) {
    if (pathname.indexOf('/api/events') === 0) return [];
    return {};
  }

  var origFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var method = ((init && init.method) ||
      (typeof input === 'object' && input && input.method) || 'GET').toUpperCase();

    var pathname = url;
    try { pathname = new URL(url, document.baseURI).pathname; } catch (_) {}

    // 非 /api 请求（理论上没有）走原生 fetch。
    if (pathname.indexOf('/api/') !== 0) {
      return origFetch ? origFetch(input, init) : Promise.reject(new Error('fetch unavailable'));
    }

    // 静态只读：拦截所有写操作。
    if (method !== 'GET') {
      return Promise.resolve(jsonResponse(
        { ok: false, error: '静态只读模式：云端复盘页不支持写操作（重算 / 编辑 / 删除 / 应用建议）。' },
        200,
      ));
    }

    var rel = mapApiToStatic(pathname);
    if (rel === null) {
      return Promise.resolve(jsonResponse(benignDefault(pathname), 200));
    }
    if (!origFetch) return Promise.resolve(jsonResponse(benignDefault(pathname), 200));
    return origFetch(rel, { method: 'GET' }).then(function (r) {
      return r && r.ok ? r : jsonResponse(benignDefault(pathname), 200);
    }).catch(function () {
      return jsonResponse(benignDefault(pathname), 200);
    });
  };

  // 关闭 SSE：纯静态没有事件流。
  window.EventSource = function () {
    return {
      addEventListener: function () {},
      removeEventListener: function () {},
      close: function () {},
      onopen: null, onmessage: null, onerror: null,
      readyState: 1,
    };
  };

  // 隐藏无法静态执行的入口，并挂只读横幅。
  function decorateReadonly() {
    var hideIds = [
      'review-filters',
      'btn-export-csv',
      'btn-refresh-entry-snapshots',
      'btn-review-apply-filters',
    ];
    hideIds.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });

    // 仅保留【复盘】导航，其余页面在静态环境下无数据。
    document.querySelectorAll('.nav-link[data-page]').forEach(function (a) {
      if (a.getAttribute('data-page') !== 'review') a.style.display = 'none';
    });

    // 顶部只读提示条。
    var review = document.getElementById('page-review');
    if (review && !document.getElementById('static-readonly-banner')) {
      var banner = document.createElement('div');
      banner.id = 'static-readonly-banner';
      banner.className =
        'rounded-lg border border-indigo-700 bg-indigo-950/40 px-4 py-2 text-xs text-indigo-200';
      banner.textContent = '只读云端复盘（GitHub Pages）：展示导出时的全部已平仓数据，写操作与筛选已禁用。';
      review.insertBefore(banner, review.firstChild);
      // 用原生 fetch 读导出清单，补充数据快照时间（相对路径，避开 /api 拦截）。
      if (origFetch) {
        origFetch('api/review/_manifest.json').then(function (r) {
          return r && r.ok ? r.json() : null;
        }).then(function (m) {
          if (m && m.generated_at) {
            banner.textContent += '  数据快照（UTC）：' + m.generated_at +
              '，交易笔数：' + (m.trade_count != null ? m.trade_count : '—');
          }
        }).catch(function () {});
      }
    }
  }

  // 把行内的「重算 / 编辑 / 删除」改为只读提示（按钮由 app.js 动态生成）。
  function stubMutations() {
    var msg = '静态只读模式：该操作需要本地后端，云端不可用。';
    ['recalcClosedEntryInsights', 'openClosedPositionEditor',
     'confirmDeleteClosedReviewPosition'].forEach(function (fn) {
      window[fn] = function () {
        try { if (typeof toast === 'function') return toast(msg, 'warn'); } catch (_) {}
        alert(msg);
      };
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      decorateReadonly();
      stubMutations();
    });
  } else {
    decorateReadonly();
    stubMutations();
  }
  // app.js 用 defer 在 DOMContentLoaded 后执行，这里再兜底覆盖一次写操作函数。
  window.addEventListener('load', stubMutations);
})();
