# UI 实现矩阵 — 复盘页 Phase 5

> 版本：2026-05-17  
> 状态：✅ 已验收（实现与测试覆盖）

| # | 区域 | UI 元素 | 实现规格 | 状态覆盖 | 验收 |
|---|------|---------|---------|---------|------|
| 1 | 筛选 | 时间/标的/池/最少样本 | `#review-filters` 表单项 + `reviewFilterQueryString()` | default | ✅ |
| 2 | 指标卡 | ROE vs 真实年化 | `avg_realized_roe` / `avg_annualized_return` 分卡展示 | default | ✅ |
| 3 | 条件切片 | 九维表格 | `#review-condition-slices`，含少样本标记 | empty / low_sample | ✅ |
| 4 | 表现体检 | 四类卡片 | `#review-performance` | empty | ✅ |
| 5 | 评分关联 | Spearman + 分位 | `#review-correlation` | empty | ✅ |
| 6 | 设置建议 | 应用按钮 | `POST /api/review/suggestions/apply` + confirm | loading / error | ✅ |
| 7 | 文案 | 去「因子」 | 页面无「因子切片」字样 | — | ✅ |
