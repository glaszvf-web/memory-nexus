---
name: memory-nexus
description: ZCode 长期记忆库管理规范。何时需要：要保存/检索/更新长期记忆、memory_search/memory_write/memory_consolidate 等 MCP 工具的使用、判断某信息该不该记、记忆库体检与巩固、跨项目找记忆。涉及记忆读写一律先加载本技能。
---

# Memory Nexus — 长期记忆工程规范

记忆库由 MCP 工具 `memory-nexus` 管理（BM25 检索 + 元数据索引 + 巩固管线 + 回收站 + 快照）。
**铁律：一切记忆读写走工具，不裸写文件**——工具负责校验、入索引、刷新 MEMORY.md 路由表，绕过即产生索引腐化。

## 1. 分层检索（上下文管理）

- **L1 路由表**：MEMORY.md 每次会话自动注入，只用于定位，细节不要凭它展开。
- **L2 检索**：`memory_search(query)` 中英混合关键词，跨项目，返回打分排序+摘要。命中后需要细节 → `memory_read(name)`。
- **L3 图扩展**：一条记忆不够理解全局时 → `memory_graph(name, depth=2)` 沿关联链拉出相关记忆群。
- 检索两轮无结果 → 换同义词/英文关键词再试一次，仍无果视为"记忆不存在"，别硬编。

## 2. 写入规则（规则机制）

写入前**必须先 `memory_search` 查重**（mem0 冲突消解）：

- 已有相近记忆且信息是更新 → `memory_update`（保留 created，自动刷 updated）
- 已有记忆是过时错误 → `memory_update` 覆盖 body，不要新建平行记忆
- 确实是新事实 → `memory_write`

五类型判定（type）：

| type | 判定 | 默认 importance |
|------|------|-----|
| user | 用户是谁、偏好、人设设定 | 9 |
| feedback | 用户对我工作方式的纠正与确认（含 Why/How to apply） | 9 |
| method | 可复用方法论、踩坑经验、工具链套路 | 7 |
| project | 工程进行时状态、位置、下一步 | 5 |
| reference | 外部资源指针（路径/URL/账号位置） | 4 |

**禁记清单**：代码里可查的结构与实现细节；git 历史能回答的事；一次性对话内容；明文密码/密钥（密码走密码档案，记忆只记"档案在哪"）；未经用户确认的猜测。

**单事实原则**：一条记忆一个主题；body > 8KB 会被拒收——拆条。description 是检索钩子，必须写清关键词（中文+英文缩写都放）。tags 3-6 个，覆盖会用来搜的词。相关记忆用 links 双向串联。

## 3. 会话末捕获（claude-mem 式压缩）

长会话收尾时自问：本会话产生了什么**跨会话仍然成立**的事实？

- 新工程启动/完结、新踩坑、用户新偏好/纠正、新工具位置 → 立即 `memory_write`
- 纯执行过程、结果可从产物验证 → 不记
- 元记忆：对记忆系统自身的经验（如某类检索总失败、某记忆总被误命中）→ 写入 type=method 且 tags 含 `memory-nexus-meta` 的记忆——这是系统自我进化的原料

## 4. 巩固与自我迭代

- 每 ~10 次会话或每次 `memory_stats` 出现 stale_candidates/dangling_links 时跑 `memory_consolidate(dry_run=True)` 看报告，确认合理后 `dry_run=False` 落盘（自动快照到 _backups/）
- 报告里的重复嫌疑对（tag_overlap≥0.6）→ 人工判断合并：内容取并集 `memory_update` 到保留方，另一方 `memory_delete`
- 检索失败但事实重要 → 补写记忆并在 description 里加失败时的那个查询词
- 旧记忆被新会话证伪 → 当场 `memory_update`，不留双胞胎

## 5. 预防机制

- 删除一律 `memory_delete`（软删进回收站，可 `memory_restore`），禁止直接删文件
- 巩固/迁移前自动快照；重大手工整理前可 `memory_backup`
- memory_write 的凭据警告必须当回事：改记"位置"而非"内容"
- 索引可疑（stats 里 no_metadata 增多、MEMORY.md 与文件不符）→ `memory_consolidate(dry_run=False)` 重建

## 6. 默认项目

工具默认操作记忆最多的主项目；`memory_projects()` 可列出全部，跨项目检索自动进行，写入/更新/删除可传 project 参数切换。
