---
description: 记忆库巩固管线（备份→补元数据→stale标记→重建索引，报告重复与悬空链接）
---

执行记忆库巩固。参数 `$ARGUMENTS` 可为 `run`（落盘）或空（默认 dry-run 只看报告）：

1. 先调 `memory_consolidate(dry_run=True)` 出报告
2. 向用户呈现：将执行的动作（backfill/mark_stale/promote_links）、重复嫌疑对、悬空链接
3. 若参数为 `run` 或用户确认：调 `memory_consolidate(dry_run=False)` 落盘（自动快照到 _backups/）
4. 对报告中的重复嫌疑对逐个判断：内容取并集 `memory_update` 到保留方，被并方 `memory_delete`
5. 对悬空链接：目标记忆值得建就建，不值得就从 links 移除
6. 最后调 `memory_stats` 展示巩固后的健康度
