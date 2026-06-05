# 云端复盘页（GitHub Pages · 只读）

`docs/` 是一份**纯静态**的【复盘页】快照，可直接发布到 GitHub Pages，
无需后端 / SQLite 即可在云端查看全部已平仓数据、明细抽屉与各项统计。

## 组成

| 文件 | 说明 |
|---|---|
| `index.html` | 复用真实前端的复盘页（由 `frontend/index.html` 改造：相对引用 + 注入 shim） |
| `app.js` | `frontend/js/app.js` 的拷贝（渲染逻辑零改动） |
| `static-shim.js` | 静态垫片：拦截 `/api/review/*` GET → 读本地 JSON；屏蔽写操作与 SSE |
| `api/review/*.json` | 由导出脚本生成的接口快照（summary / suggestions / closed_positions / 各机位明细） |
| `.nojekyll` | 关闭 Jekyll，按原样发布静态文件 |

## 刷新数据（本地有数据库时执行）

```bash
# 仓库根目录
python3 scripts/export_review_static.py   # 重新导出全部复盘数据到 docs/api/
cp frontend/js/app.js docs/app.js          # 渲染逻辑有更新时同步
```

导出使用 Flask test client 调真实路由，统计/归因逻辑与本地一致，无漂移。

## 开启 GitHub Pages

仓库 **Settings → Pages → Build and deployment**：

- Source：`Deploy from a branch`
- Branch：`main` ，目录选 `/docs`

保存后访问 `https://<用户名>.github.io/FOSS/` 即默认进入只读复盘页。

> 写操作（重算 / 编辑 / 删除 / 应用建议）与筛选在云端已禁用；
> 如需可写或按条件筛选，请在本地运行 `python3 run.py` 使用完整版。
