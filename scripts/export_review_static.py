#!/usr/bin/env python3
"""把【复盘页】所需的只读接口导出为静态 JSON，供 GitHub Pages 离线浏览。

用法（仓库根目录执行）：

    python3 scripts/export_review_static.py

它会复用真实的 Flask 路由（同一套统计/归因逻辑，零漂移），抓取「无筛选 =
全部数据」的复盘响应，并按接口路径镜像写入 ``docs/api/...``：

    docs/api/review/summary.json
    docs/api/review/suggestions.json
    docs/api/review/closed_positions.json
    docs/api/review/positions/<id>/attribution.json
    docs/api/review/positions/<id>/snapshot.json
    docs/api/review/positions/<id>/diagnosis.json
    docs/api/review/_manifest.json   # 导出时间与机位清单

前端的 static-shim.js 会把页面里的 ``/api/review/*`` GET 请求改读这些文件。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT_ROOT = REPO_ROOT / "docs" / "api"


def _write_json(rel_api_path: str, payload) -> Path:
    """把 payload 写到 docs/api/<rel_api_path>.json（rel_api_path 不含前缀斜杠）。"""
    target = OUT_ROOT / (rel_api_path + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return target


def _get_json(client, url: str):
    resp = client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"GET {url} -> HTTP {resp.status_code}: {resp.data[:200]!r}")
    return resp.get_json()


def main() -> int:
    from server import create_app

    app = create_app()
    client = app.test_client()

    print("· 导出 /api/review/summary（全部数据，无筛选）")
    summary = _get_json(client, "/api/review/summary")
    _write_json("review/summary", summary)

    print("· 导出 /api/review/suggestions")
    try:
        suggestions = _get_json(client, "/api/review/suggestions")
    except RuntimeError as exc:
        print(f"  ! suggestions 失败，写入空集: {exc}")
        suggestions = {"suggestions": []}
    _write_json("review/suggestions", suggestions)

    print("· 导出 /api/review/closed_positions")
    closed = _get_json(client, "/api/review/closed_positions")
    _write_json("review/closed_positions", closed)

    # 机位明细（抽屉用）：归因 / 快照 / 诊断
    positions = summary.get("closed_positions") or closed.get("positions") or []
    ids = [p["id"] for p in positions if p.get("id") is not None]
    print(f"· 导出 {len(ids)} 个机位明细（attribution/snapshot/diagnosis）")
    for pid in ids:
        for kind in ("attribution", "snapshot", "diagnosis"):
            url = f"/api/review/positions/{pid}/{kind}"
            try:
                _write_json(f"review/positions/{pid}/{kind}", _get_json(client, url))
            except RuntimeError as exc:
                print(f"  ! {url} 失败: {exc}")
                _write_json(
                    f"review/positions/{pid}/{kind}",
                    {"error": "export_failed", "detail": str(exc)},
                )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_count": summary.get("trade_count"),
        "position_ids": ids,
    }
    _write_json("review/_manifest", manifest)

    print(f"✓ 导出完成 -> {OUT_ROOT}")
    print(f"  机位数: {len(ids)}  交易笔数: {summary.get('trade_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
