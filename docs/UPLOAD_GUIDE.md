# 上传指引（给执行上传的 AI）

## 1. 仓库信息（建议值）

| 项 | 值 |
|---|---|
| 仓库名 | `memory-nexus` |
| 描述（英文） | Long-term memory management plugin for AI coding agents: BM25 bilingual retrieval, metadata index, consolidation pipeline, link graph, trash & snapshots. 12 MCP tools. |
| 描述（中文） | AI 编码智能体的长期记忆库管理插件：BM25 中英混合检索、元数据索引、巩固管线、关联图、回收站与快照，12 个 MCP 工具。 |
| 可见性 | public |
| 标签 | mcp, memory, long-term-memory, ai-agent, knowledge-management, python |
| 默认分支 | main |

## 2. 脱敏复核（打包方已完成，上传前复核）

- [x] 全部文件无个人称谓与身份字符串（全库身份字符串 grep 应为 0 命中 为 0）
- [x] 迁移示例数据 slug 已中性化（user-working-style / user-workflow-rules）
- [x] 无真实记忆库数据（只有引擎代码与迁移脚本示例）
- [x] .ai-workspace / __pycache__ 已排除

## 3. 上传步骤

```bash
cd <本目录>
git init -b main
git add -A
git commit -m "Memory Nexus v0.1.1 — long-term memory plugin for AI agents"
gh repo create memory-nexus --public --source=. --push \
  --description "Long-term memory plugin for AI agents: BM25 retrieval, consolidation pipeline, link graph"
```

无 gh 时用方式 B（先在平台建空仓）：
```bash
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/memory-nexus.git
git push -u origin main
```

## 4. 上传后动作

- Releases → tag `v0.1.1`
- 仓库 README 已含架构说明与工具清单，无需额外补充
- 可选：把 `.zcode-plugin/plugin.json` 里的安装方式写进 README 顶部（当前 README 已有说明）

## 5. 禁止事项

- 禁止把任何真实记忆库目录（MEMORY.md / index.json / _backups / _trash）提交进来
- 本插件是通用 MCP 工具，README 中若提及特定宿主名称保持中性表述
