# Memory Nexus — ZCode 长期记忆库管理插件

融合开源社区四大记忆方案的自编辑记忆系统：

| 来源 | 借鉴机制 |
|------|---------|
| [mem0](https://github.com/mem0ai/mem0) | 抽取→巩固两阶段管线，ADD/UPDATE 冲突消解 |
| [Letta/MemGPT](https://www.letta.com/blog/agent-memory) | OS 式分层记忆（常驻核心记忆 / 外置归档记忆）+ 自编辑工具 |
| [Zep/Graphiti](https://github.com/getzep/graphiti) | 双时间轴（created/updated/last_used）+ 生命周期状态机 |
| [claude-mem](https://docs.claude-mem.ai/introduction) | 会话末语义压缩捕获 → 可检索持久上下文 |

## 架构

```
L1  MEMORY.md      分组路由表, 每次会话由宿主注入 (核心记忆, 由引擎重写保持精简)
L2  index.json     全库元数据索引 (type/tags/links/importance/时间戳/使用计数)
L3  *.md           单事实记忆文件, BM25 中英混合检索按需加载 (归档记忆)

_backups/  巩固与迁移前自动 zip 快照, 保留 10 份
_trash/    软删除回收站, memory_restore 可恢复
```

## MCP 工具（11 个）

`memory_search`（BM25+重要度+新鲜度+使用度，跨项目，命中自动计数）·
`memory_read` · `memory_write`（校验/原子写/入索引/刷路由表/凭据警告）·
`memory_update` · `memory_delete`（软删）· `memory_restore` ·
`memory_link`（双向关联）· `memory_graph`（BFS 关联图展开）·
`memory_stats`（健康体检）· `memory_consolidate`（巩固管线）·
`memory_backup` · `memory_projects`

## 命令

`/memory-search` 检索 · `/memory-stats` 体检 · `/memory-consolidate` 巩固

## 技能

`skills/memory-nexus/SKILL.md` — 记忆工程规范：五类型判定、禁记清单、查重写入流程、
会话末捕获、巩固节奏、检索失败处置（自我进化回路）。

## 首次迁移

```bash
python assets/migrate_v1.py --dry-run   # 预览（自动做只读备份）
python assets/migrate_v1.py             # 落盘: 补全 28 条记忆元数据, 重建索引与路由表
```

## 依赖

Python 3.10+、PyYAML（无其他第三方依赖）。`MEMORY_NEXUS_ROOT` 环境变量可覆盖记忆根目录。
