---
description: 跨项目检索长期记忆（BM25+重要度+新鲜度加权）
---

用 memory-nexus 的 `memory_search` 工具检索用户给的 `$ARGUMENTS`：

1. 调 `memory_search(query="$ARGUMENTS")`，若结果不足换同义词/英文关键词再搜一轮
2. 对最相关的 1-3 条结果调 `memory_read` 取全文
3. 需要相关背景时用 `memory_graph(name)` 扩展关联记忆
4. 用中文汇总发现，注明每条信息来自哪条记忆（name）
