---
description: 记忆库健康体检（类型分布/体积/陈旧/悬空链接/零使用）
---

调 `memory_stats`（可带 project 参数）输出体检报告，并给出处置建议：

- stale_candidates 多 → 建议 `/memory-consolidate`
- dangling_links 非空 → 逐条说明哪条链接指向不存在的记忆
- oversized / never_used 多 → 建议拆分或归档，列出具体记忆名
- 最后用一段话总结记忆库整体健康状态
