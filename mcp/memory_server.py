# -*- coding: utf-8 -*-
"""Memory Nexus MCP server — ZCode 长期记忆库管理的工具面.

启动: 由插件清单 mcpServers 以 stdio 拉起, 无需手动运行.
环境变量 MEMORY_NEXUS_ROOT 可覆盖 projects 根 (默认 ~/.zcode/cli/memories/projects).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from memory_engine import MEMORY_TYPES, Nexus, default_root

mcp = FastMCP(
    "memory-nexus",
    instructions=(
        "长期记忆库管理工具。写入前先 memory_search 查重；新记忆用 memory_write "
        "(自动校验+入索引+刷新MEMORY.md)，修改用 memory_update，禁止绕过工具裸写记忆文件。"
    ),
)

_nexus = Nexus(default_root())


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


@mcp.tool()
def memory_search(query: str, type: str = "", tags: str = "", limit: int = 8) -> str:
    """跨项目检索长期记忆 (BM25+中文bigram, 重要度/新鲜度/使用度加权)。
    query 支持中英混合关键词; type 可选 user/feedback/project/reference/method;
    tags 逗号分隔。返回排序结果与摘要片段, 自动累计命中计数。"""
    store = _nexus.main
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    if type and type not in MEMORY_TYPES:
        return _j({"ok": False, "errors": [f"type 须为 {MEMORY_TYPES} 之一"]})
    results = _nexus.search_all(query, mtype=type, tags=tag_list, limit=max(1, min(limit, 30)))
    return _j({
        "ok": True, "query": query, "count": len(results),
        "results": [{k: r[k] for k in ("name", "project", "type", "score", "description", "tags", "updated", "snippet")} for r in results],
        "next": "细节用 memory_read(name, project); 相关记忆用 memory_graph(name)",
    })


@mcp.tool()
def memory_read(name: str, project: str = "") -> str:
    """读取单条记忆全文+完整元数据 (会累计 use_count)。"""
    store, note = _nexus.pick(project)
    m = store.get(name)
    if not m:
        errs = [f"记忆 '{name}' 不存在"]
        if note:
            errs.append(note)
        return _j({"ok": False, "errors": errs})
    store.touch(name)
    return _j({
        "ok": True, "name": m.slug, "type": m.mtype, "description": m.description,
        "tags": m.tags, "links": m.links, "importance": m.importance, "status": m.status,
        "created": m.created, "updated": m.updated, "use_count": m.use_count,
        "body": m.body, "path": m.source_path, **({"note": note} if note else {}),
    })


@mcp.tool()
def memory_write(name: str, description: str, type: str, body: str,
                 tags: str = "", links: str = "", importance: int = 0, project: str = "") -> str:
    """写入新记忆 (校验唯一性/frontmatter/大小/敏感凭据, 原子落盘, 自动入索引+刷新 MEMORY.md)。
    type: user=用户画像 feedback=行为纠正 project=工程状态 reference=外部资源 method=方法论;
    importance 1-10 (默认按类型); tags/links 逗号分隔; links 指向其他记忆 slug。"""
    store, note = _nexus.pick(project)
    from memory_engine import Memory
    mem = Memory(
        slug=name, description=description, mtype=type, body=body,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        links=[l.strip() for l in links.split(",") if l.strip()],
        importance=importance or 0,
    )
    if not mem.importance:
        from memory_engine import TYPE_DEFAULT_IMPORTANCE
        mem.importance = TYPE_DEFAULT_IMPORTANCE.get(type, 5)
    res = store.write(mem)
    if note:
        res["note"] = note
    return _j(res)


@mcp.tool()
def memory_update(name: str, description: str = "", body: str = "", type: str = "",
                  tags: str = "", links: str = "", importance: int = 0,
                  status: str = "", project: str = "") -> str:
    """部分更新已有记忆 (保留 created, 刷新 updated; 空=不改)。状态可置 active/stale/archived。"""
    store, note = _nexus.pick(project)
    fields: dict = {}
    for k, v in (("description", description), ("body", body), ("type", type),
                 ("status", status), ("importance", importance)):
        if v:
            fields[k] = v
    if tags:
        fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if links:
        fields["links"] = [l.strip() for l in links.split(",") if l.strip()]
    if not fields:
        return _j({"ok": False, "errors": ["未提供任何更新字段"]})
    res = store.update(name, **fields)
    if note:
        res.setdefault("note", note)
    return _j(res)


@mcp.tool()
def memory_delete(name: str, project: str = "") -> str:
    """软删除记忆 (移入 _trash/ 回收站, 可用 memory_restore 恢复; 同时清出索引与 MEMORY.md)。"""
    store, note = _nexus.pick(project)
    res = store.delete(name)
    if note:
        res.setdefault("note", note)
    return _j(res)


@mcp.tool()
def memory_restore(name: str, project: str = "") -> str:
    """从回收站恢复最近一次被软删除的记忆。"""
    store, note = _nexus.pick(project)
    res = store.restore(name)
    if note:
        res.setdefault("note", note)
    return _j(res)


@mcp.tool()
def memory_link(name: str, targets: str, project: str = "") -> str:
    """为记忆建立关联 (targets 逗号分隔的 slug, 默认双向链接), 供 memory_graph 图检索扩展。"""
    store, note = _nexus.pick(project)
    res = store.link(name, [t.strip() for t in targets.split(",") if t.strip()])
    if note:
        res.setdefault("note", note)
    return _j(res)


@mcp.tool()
def memory_graph(name: str, depth: int = 2, project: str = "") -> str:
    """沿 links 与 [[双链]] 展开记忆关联图 (BFS), 检索命中一条后用它扩展相关上下文。"""
    store, note = _nexus.pick(project)
    res = store.graph(name, depth)
    if note:
        res.setdefault("note", note)
    return _j(res)


@mcp.tool()
def memory_stats(project: str = "") -> str:
    """记忆库健康体检: 类型分布/体积/超限/陈旧候选/悬空链接/零使用记忆。"""
    store, note = _nexus.pick(project)
    res = store.stats()
    if note:
        res["note"] = note
    return _j(res)


@mcp.tool()
def memory_consolidate(dry_run: bool = True, project: str = "") -> str:
    """巩固管线: 自动备份→补全元数据→提升裸[[链接]]→标记stale→重建索引与MEMORY.md;
    另报告重复嫌疑与悬空链接 (语义合并决策留给模型)。默认 dry_run=True 只出报告。"""
    store, note = _nexus.pick(project)
    res = store.consolidate(dry_run=dry_run)
    if note:
        res["note"] = note
    return _j(res)


@mcp.tool()
def memory_backup(tag: str = "manual", project: str = "") -> str:
    """手动快照整个记忆目录到 _backups/ (zip, 保留最近10份)。"""
    store, note = _nexus.pick(project)
    res = store.backup(tag)
    if note:
        res["note"] = note
    return _j(res)


@mcp.tool()
def memory_projects() -> str:
    """列出所有记忆项目及各自记忆数, 指明当前默认主项目。"""
    return _j({
        "ok": True,
        "default_project": _nexus._default,
        "projects": {n: {"count": len(s.memories)} for n, s in _nexus.stores.items()},
    })


if __name__ == "__main__":
    mcp.run()
