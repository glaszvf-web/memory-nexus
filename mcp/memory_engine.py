# -*- coding: utf-8 -*-
"""Memory Nexus engine — ZCode 长期记忆库管理核心逻辑.

分层设计（借鉴 Letta/MemGPT 的 OS 式记忆分层）:
  L1  MEMORY.md     路由表, 每次会话由宿主注入 (常驻"核心记忆")
  L2  index.json    全库元数据索引 (每条记忆的 type/tags/links/importance/时间戳)
  L3  *.md 正文     单事实记忆文件, 按需加载 (外置"归档记忆")

时间模型 (借鉴 Zep/Graphiti 双时间轴):
  created   知识诞生时间
  updated   最后一次内容修改时间
  last_used 最后一次被检索命中时间
  status    active -> stale -> archived 生命周期

巩固模型 (借鉴 mem0 两阶段管线):
  写入即校验 (ADD/UPDATE 冲突消解), 巩固管线定期合并/标记/重建索引.

本模块零第三方依赖除 PyYAML; 所有磁盘写入均原子 (tmp + os.replace).
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import yaml

# ---------------------------------------------------------------- 常量

MEMORY_TYPES = ("user", "feedback", "project", "reference", "method")
STATUSES = ("active", "stale", "archived")
TYPE_DEFAULT_IMPORTANCE = {"user": 9, "feedback": 9, "method": 7, "project": 5, "reference": 4}
BODY_SOFT_LIMIT = 8 * 1024          # 单条记忆正文软上限 8KB
STALE_DAYS = 120                    # 超过且低使用 -> 标记 stale
KEEP_BACKUPS = 10                   # 快照保留份数
MEMORY_MD_HEADER_LINES = 9          # MEMORY.md 头部路由规则行数预算

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LINK_RE = re.compile(r"\[\[([a-z0-9-]+)\]\]")

# 疑似凭据模式: 写入时警告 (不阻止, 密码档案类记忆合法存在)
SECRET_PATTERNS = (
    re.compile(r"(?i)password\s*[=:：]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[=:：]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[0-9a-f]{32,}\b"),
)

INDEX_NAME = "index.json"
MEMORY_MD = "MEMORY.md"
DIR_BACKUPS = "_backups"
DIR_TRASH = "_trash"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".nx-tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 分词 / BM25

def tokenize(text: str) -> list[str]:
    """英文按词, 中文按单字+二元组 (bigram), 全部小写."""
    tokens = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text)]
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        chars = list(seg)
        tokens.extend(chars)
        tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return tokens


@dataclass
class BM25:
    k1: float = 1.5
    b: float = 0.75

    docs: dict[str, list[str]] = field(default_factory=dict)
    df: dict[str, int] = field(default_factory=dict)
    avgdl: float = 0.0
    n: int = 0

    def add(self, doc_id: str, tokens: list[str]) -> None:
        self.docs[doc_id] = tokens
        self.n += 1
        for t in set(tokens):
            self.df[t] = self.df.get(t, 0) + 1

    def finalize(self) -> None:
        self.avgdl = (sum(len(d) for d in self.docs.values()) / self.n) if self.n else 0.0

    def score(self, doc_id: str, query_tokens: list[str]) -> float:
        doc = self.docs.get(doc_id)
        if not doc or not query_tokens:
            return 0.0
        dl = len(doc)
        tf = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            # 中文单字信息量低 (几乎必然命中), 降权避免纯噪声结果
            w = 0.25 if len(q) == 1 and ord(q[0]) > 0x2E7F else 1.0
            idf = (self.n - self.df.get(q, 0) + 0.5) / (self.df.get(q, 0) + 0.5)
            f = tf[q]
            s += w * idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1)))
        return s


# ---------------------------------------------------------------- 记忆条目

@dataclass
class Memory:
    slug: str
    body: str = ""
    description: str = ""
    mtype: str = "project"
    created: str = ""
    updated: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    importance: int = 5
    status: str = "active"
    use_count: int = 0
    last_used: str = ""
    source_path: str = ""

    @property
    def exists(self) -> bool:
        return bool(self.source_path)

    # ---- frontmatter 序列化 (与 ZCode 内置自动记忆格式双向兼容)
    def to_markdown(self) -> str:
        meta = {
            "type": self.mtype,
            "created": self.created or now_iso(),
            "updated": self.updated or now_iso(),
            "tags": self.tags,
            "links": self.links,
            "importance": self.importance,
            "status": self.status,
            "use_count": self.use_count,
            "last_used": self.last_used or None,
        }
        fm = yaml.safe_dump(
            {"name": self.slug, "description": self.description, "metadata": meta},
            allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
        return f"---\n{fm}---\n\n{self.body.strip()}\n"

    @staticmethod
    def parse(path: str) -> "Memory":
        with io.open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
        data = yaml.safe_load(m.group(1)) if m else {}
        body = m.group(2).strip() if m else raw.strip()
        meta = (data or {}).get("metadata") or {}
        name = (data or {}).get("name") or os.path.splitext(os.path.basename(path))[0]
        return Memory(
            slug=str(name),
            body=body,
            description=str((data or {}).get("description") or ""),
            mtype=str(meta.get("type") or "project"),
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            tags=[str(t) for t in (meta.get("tags") or [])],
            links=[str(l) for l in (meta.get("links") or [])],
            importance=int(meta.get("importance") or TYPE_DEFAULT_IMPORTANCE.get(str(meta.get("type")), 5)),
            status=str(meta.get("status") or "active"),
            use_count=int(meta.get("use_count") or 0),
            last_used=str(meta.get("last_used") or ""),
            source_path=path,
        )


# ---------------------------------------------------------------- 存储层

class MemoryStore:
    """单个项目记忆目录的存储 + 检索引擎."""

    def __init__(self, project_dir: str, project_name: str = ""):
        self.dir = project_dir
        self.memories: dict[str, Memory] = {}
        self._project_name = project_name
        self.scan()

    # ---- 基础
    @property
    def index_path(self) -> str:
        return os.path.join(self.dir, INDEX_NAME)

    @property
    def project_name(self) -> str:
        if self._project_name:
            return self._project_name
        base = os.path.basename(self.dir.rstrip("/\\"))
        # <project>/memory/ 布局下, 目录名恒为 memory, 项目名取上一层
        if base == "memory":
            return os.path.basename(os.path.dirname(self.dir.rstrip("/\\")))
        return base

    def scan(self) -> int:
        self.memories = {}
        if not os.path.isdir(self.dir):
            return 0
        for fn in os.listdir(self.dir):
            if not fn.endswith(".md") or fn == MEMORY_MD or fn.startswith("_"):
                continue
            path = os.path.join(self.dir, fn)
            try:
                mem = Memory.parse(path)
                self.memories[mem.slug] = mem
            except Exception as e:  # 坏文件不拖垮整库
                self.memories[os.path.splitext(fn)[0]] = Memory(
                    slug=os.path.splitext(fn)[0], source_path=path, status="active",
                    description=f"[parse-error: {e}]")
        return len(self.memories)

    def get(self, slug: str) -> Memory | None:
        return self.memories.get(slug)

    def rebuild_index(self) -> dict:
        idx = {
            "version": 1,
            "generated_at": now_iso(),
            "project": self.project_name,
            "memories": {
                s: {
                    "type": m.mtype, "description": m.description, "tags": m.tags,
                    "links": m.links, "importance": m.importance, "status": m.status,
                    "created": m.created, "updated": m.updated,
                    "use_count": m.use_count, "last_used": m.last_used,
                    "size": len(m.body.encode("utf-8")),
                }
                for s, m in sorted(self.memories.items())
            },
        }
        _atomic_write(self.index_path, json.dumps(idx, ensure_ascii=False, indent=2))
        return idx

    # ---- 校验
    def validate(self, mem: Memory, is_new: bool) -> list[str]:
        errs = []
        if not SLUG_RE.match(mem.slug):
            errs.append(f"name '{mem.slug}' 不合法: 小写字母/数字/连字符, ≤64字符")
        if mem.mtype not in MEMORY_TYPES:
            errs.append(f"type '{mem.mtype}' 不在 {MEMORY_TYPES}")
        if not (1 <= mem.importance <= 10):
            errs.append("importance 必须在 1-10")
        if not mem.description or len(mem.description) > 200:
            errs.append("description 必填且 ≤200 字符 (它是检索命中与索引展示的钩子)")
        if is_new and mem.slug in self.memories:
            errs.append(f"已存在同名记忆, 请改用 memory_update (mem0 冲突消解: 新事实→更新旧记忆, 而非新建重复)")
        if len(mem.body.encode("utf-8")) > BODY_SOFT_LIMIT:
            errs.append(f"正文 {len(mem.body.encode('utf-8'))}B 超过 {BODY_SOFT_LIMIT}B 软上限, 应拆分为多条单事实记忆")
        return errs

    @staticmethod
    def secret_scan(body: str) -> list[str]:
        hits = []
        for pat in SECRET_PATTERNS:
            m = pat.search(body)
            if m:
                hits.append(m.group(0)[:40])
        return hits

    def dangling_links(self) -> list[tuple[str, str]]:
        out = []
        for s, m in self.memories.items():
            for l in m.links:
                if l not in self.memories:
                    out.append((s, l))
            for l in LINK_RE.findall(m.body):
                if l not in self.memories and l not in m.links:
                    out.append((s, l))
        return out

    # ---- 检索 (BM25 + 重要度 + 新鲜度 + 使用度)
    def build_bm25(self) -> BM25:
        bm = BM25()
        for s, m in self.memories.items():
            toks = (
                tokenize(s) * 3
                + tokenize(" ".join(m.tags)) * 3
                + tokenize(m.description) * 2
                + tokenize(m.body)
            )
            bm.add(s, toks)
        bm.finalize()
        return bm

    @staticmethod
    def _days(datestr: str) -> float:
        try:
            return (datetime.now() - datetime.strptime(datestr, "%Y-%m-%d")).days
        except Exception:
            return 9999

    def rank(self, query: str, bm: BM25 | None = None,
             mtype: str = "", tags: list[str] | None = None, limit: int = 8) -> list[dict]:
        bm = bm or self.build_bm25()
        q = tokenize(query) if query else []
        out = []
        for s, m in self.memories.items():
            if mtype and m.mtype != mtype:
                continue
            if tags and not set(tags) & set(m.tags):
                continue
            rel = bm.score(s, q) if q else 0.0
            imp = (m.importance - 1) / 9
            fresh = max(0.0, 1 - self._days(m.updated or m.created) / 365)
            usage = min(1.0, math.log10(1 + m.use_count) / 2)
            # 有查询: BM25 主导; 无查询(浏览模式): 重要度主导
            score = (rel if q else 2.2 * imp) + 0.35 * fresh + 0.25 * usage
            if q and rel <= 0:
                continue
            out.append({
                "name": s, "type": m.mtype, "description": m.description,
                "importance": m.importance, "status": m.status,
                "tags": m.tags, "updated": m.updated or m.created,
                "score": round(score, 3),
                "snippet": m.body[:160].replace("\n", " "),
                "path": m.source_path,
            })
        out.sort(key=lambda r: -r["score"])
        # 噪声截断: 显著低于榜首的结果丢弃
        if out and q:
            top = out[0]["score"]
            out = [r for r in out if r["score"] >= 0.2 * top]
        return out[:limit]

    # ---- 写操作
    def write(self, mem: Memory) -> dict:
        errs = self.validate(mem, is_new=True)
        if errs:
            return {"ok": False, "errors": errs}
        warn = self.secret_scan(mem.body)
        mem.created = mem.created or now_iso()
        mem.updated = now_iso()
        path = os.path.join(self.dir, f"{mem.slug}.md")
        _atomic_write(path, mem.to_markdown())
        mem.source_path = path  # 回填路径, 否则后续 update/delete 拿到空路径
        self.memories[mem.slug] = mem
        self.refresh_md_and_index()
        r = {"ok": True, "written": path, "warnings": warn and [f"疑似凭据: {w} — 明文密码应存密码档案, 记忆里只记位置" for w in warn] or []}
        return r

    def update(self, slug: str, **fields) -> dict:
        m = self.memories.get(slug)
        if not m:
            return {"ok": False, "errors": [f"记忆 '{slug}' 不存在"]}
        if "description" in fields and fields["description"]:
            m.description = fields["description"]
        if "body" in fields and fields["body"]:
            m.body = fields["body"]
        if "type" in fields and fields["type"]:
            m.mtype = fields["type"]
        if "tags" in fields and fields["tags"] is not None:
            m.tags = sorted(set(str(t) for t in fields["tags"]))
        if "links" in fields and fields["links"] is not None:
            m.links = sorted(set(str(l) for l in fields["links"]))
        if "importance" in fields and fields["importance"]:
            m.importance = int(fields["importance"])
        if "status" in fields and fields["status"]:
            m.status = fields["status"]
        errs = self.validate(m, is_new=False)
        if errs:
            return {"ok": False, "errors": errs}
        warn = self.secret_scan(m.body)
        m.updated = now_iso()
        _atomic_write(m.source_path, m.to_markdown())
        self.refresh_md_and_index()
        return {"ok": True, "updated": slug, "warnings": warn and [f"疑似凭据: {w}" for w in warn] or []}

    def delete(self, slug: str) -> dict:
        m = self.memories.get(slug)
        if not m:
            return {"ok": False, "errors": [f"记忆 '{slug}' 不存在"]}
        trash_dir = os.path.join(self.dir, DIR_TRASH)
        os.makedirs(trash_dir, exist_ok=True)
        dst = os.path.join(trash_dir, f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
        os.replace(m.source_path, dst)
        del self.memories[slug]
        self.refresh_md_and_index()
        return {"ok": True, "soft_deleted": dst, "note": "已入回收站, 可 memory_restore 恢复"}

    def restore(self, slug: str) -> dict:
        trash_dir = os.path.join(self.dir, DIR_TRASH)
        if not os.path.isdir(trash_dir):
            return {"ok": False, "errors": ["回收站为空"]}
        cands = sorted(f for f in os.listdir(trash_dir) if f.startswith(slug + "-") and f.endswith(".md"))
        if not cands:
            return {"ok": False, "errors": [f"回收站中无 '{slug}'"]}
        if slug in self.memories:
            return {"ok": False, "errors": [f"同名记忆已存在, 先处理冲突再恢复"]}
        src = os.path.join(trash_dir, cands[-1])
        os.replace(src, os.path.join(self.dir, f"{slug}.md"))
        self.scan()
        self.refresh_md_and_index()
        return {"ok": True, "restored": slug, "from": cands[-1]}

    def link(self, slug: str, targets: list[str], bidirectional: bool = True) -> dict:
        m = self.memories.get(slug)
        if not m:
            return {"ok": False, "errors": [f"记忆 '{slug}' 不存在"]}
        missing = [t for t in targets if t not in self.memories]
        added = []
        for t in targets:
            if t not in m.links and t != slug:
                m.links.append(t)
                added.append(t)
        if bidirectional:
            for t in targets:
                tm = self.memories.get(t)
                if tm and slug not in tm.links:
                    tm.links.append(slug)
                    tm.updated = now_iso()
                    _atomic_write(tm.source_path, tm.to_markdown())
        m.updated = now_iso()
        _atomic_write(m.source_path, m.to_markdown())
        self.refresh_md_and_index()
        return {"ok": True, "linked": added, "missing_targets": missing}

    def touch(self, slug: str) -> None:
        """检索命中计数 (不刷 updated, 只刷 last_used)."""
        m = self.memories.get(slug)
        if not m:
            return
        m.use_count += 1
        m.last_used = now_iso()
        try:
            _atomic_write(m.source_path, m.to_markdown())
        except OSError:
            pass  # 计数失败不影响检索结果

    def graph(self, slug: str, depth: int = 2) -> dict:
        if slug not in self.memories:
            return {"ok": False, "errors": [f"记忆 '{slug}' 不存在"]}
        # 邻接双向化: frontmatter links 视为双向, body 内联 [[x]] 同样计入
        adj: dict[str, set[str]] = {}
        for s, m in self.memories.items():
            adj.setdefault(s, set())
            for l in m.links:
                if l in self.memories:
                    adj[s].add(l)
                    adj.setdefault(l, set()).add(s)
            for l in LINK_RE.findall(m.body):
                if l in self.memories:
                    adj[s].add(l)
                    adj.setdefault(l, set()).add(s)
        seen, frontier, edges = {slug}, [slug], []
        for _ in range(max(1, depth)):
            nxt = []
            for s in frontier:
                for l in sorted(adj.get(s, ())):
                    edges.append([s, l])
                    if l not in seen:
                        seen.add(l)
                        nxt.append(l)
            frontier = nxt
        return {
            "ok": True, "root": slug, "nodes": sorted(seen),
            "edges": sorted({tuple(e) for e in edges}),
            "node_info": {s: {"type": m.mtype, "description": m.description}
                          for s, m in self.memories.items() if s in seen},
        }

    # ---- 备份
    def backup(self, tag: str = "manual") -> dict:
        bdir = os.path.join(self.dir, DIR_BACKUPS)
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        zpath = os.path.join(bdir, f"{tag}-{stamp}.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in os.listdir(self.dir):
                if fn.startswith("_") or fn == INDEX_NAME:
                    continue
                p = os.path.join(self.dir, fn)
                if os.path.isfile(p):
                    z.write(p, fn)
        olds = sorted(f for f in os.listdir(bdir) if f.endswith(".zip"))
        for f in olds[:-KEEP_BACKUPS]:
            os.remove(os.path.join(bdir, f))
        return {"ok": True, "backup": zpath, "kept": min(len(olds), KEEP_BACKUPS)}

    # ---- MEMORY.md 路由表 (L1 常驻层, 由本引擎重写)
    def render_memory_md(self) -> str:
        lines = [
            "# Memory Index",
            "",
            "> Memory Nexus 管理: 检索用 mcp 工具 memory_search (BM25+重要度+新鲜度),",
            "> 写新记忆用 memory_write (自动校验+入索引+更新本表), 改用 memory_update。",
            "> 新会话先读本表定位, 需要细节再 memory_read。每 ~10 次会话跑 memory_consolidate。",
            "",
        ]
        by_type: dict[str, list[Memory]] = {}
        for m in self.memories.values():
            by_type.setdefault(m.mtype, []).append(m)
        order = ["user", "feedback", "method", "project", "reference"]
        for t in order:
            group = sorted(by_type.get(t, []), key=lambda m: -m.importance)
            if not group:
                continue
            label = {"user": "user 用户画像", "feedback": "feedback 行为纠正", "method": "method 方法论",
                     "project": "project 工程状态", "reference": "reference 外部资源"}[t]
            lines.append(f"## {label}")
            for m in group:
                mark = " ⚠️stale" if m.status == "stale" else ""
                lines.append(f"- [{m.slug}]({m.slug}.md) — {m.description}{mark}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def refresh_md_and_index(self) -> None:
        _atomic_write(os.path.join(self.dir, MEMORY_MD), self.render_memory_md())
        self.rebuild_index()

    # ---- 体检
    def stats(self) -> dict:
        mems = list(self.memories.values())
        by_type = {t: sum(1 for m in mems if m.mtype == t) for t in MEMORY_TYPES}
        oversized = [m.slug for m in mems if len(m.body.encode("utf-8")) > BODY_SOFT_LIMIT]
        stale = [m.slug for m in mems if m.status == "stale"]
        probably_stale = [
            m.slug for m in mems
            if m.status == "active" and self._days(m.updated or m.created) > STALE_DAYS
            and m.importance <= 6 and m.use_count == 0
        ]
        return {
            "project": self.project_name,
            "total": len(mems),
            "by_type": by_type,
            "total_kb": round(sum(len(m.body.encode('utf-8')) for m in mems) / 1024, 1),
            "memory_md_bytes": len(self.render_memory_md().encode("utf-8")),
            "oversized": oversized,
            "stale_marked": stale,
            "stale_candidates": probably_stale,
            "dangling_links": [[a, b] for a, b in self.dangling_links()],
            "no_metadata": [m.slug for m in mems if not m.created],
            "never_used": [m.slug for m in mems if m.use_count == 0],
        }

    # ---- 巩固管线 (mem0 式: 只做确定性变换, 语义合并决策留给模型)
    def consolidate(self, dry_run: bool = False) -> dict:
        report: dict = {"project": self.project_name, "dry_run": dry_run, "actions": [], "candidates": []}
        if not dry_run:
            report["backup"] = self.backup("consolidate")["backup"]
        # 1. 补全缺失元数据 (dry_run 只报告, 不改内存对象 — 避免污染后续操作)
        for m in list(self.memories.values()):
            changed = []
            if not m.created:
                changed.append("created")
            if not m.updated:
                changed.append("updated")
            if changed:
                report["actions"].append({"memory": m.slug, "action": "backfill", "fields": changed})
                if not dry_run:
                    m.created = m.created or datetime.fromtimestamp(
                        os.path.getmtime(m.source_path)).strftime("%Y-%m-%d")
                    m.updated = m.updated or m.created
                    _atomic_write(m.source_path, m.to_markdown())
        # 2. body 中的裸 [[link]] 提升到 frontmatter links
        for m in list(self.memories.values()):
            implicit = [l for l in LINK_RE.findall(m.body) if l not in m.links]
            if implicit:
                report["actions"].append({"memory": m.slug, "action": "promote_links", "links": implicit})
                if not dry_run:
                    m.links.extend(implicit)
                    _atomic_write(m.source_path, m.to_markdown())
        # 3. stale 标记
        for m in list(self.memories.values()):
            if (m.status == "active" and self._days(m.updated or m.created) > STALE_DAYS
                    and m.importance <= 6 and m.use_count == 0):
                report["actions"].append({"memory": m.slug, "action": "mark_stale"})
                if not dry_run:
                    m.status = "stale"
                    _atomic_write(m.source_path, m.to_markdown())
        # 4. 重复嫌疑 (tag 重叠 + 描述相似), 仅报告不动手
        slugs = sorted(self.memories)
        for i, a in enumerate(slugs):
            for b in slugs[i + 1:]:
                ma, mb = self.memories[a], self.memories[b]
                if not ma.tags or not mb.tags:
                    continue
                overlap = len(set(ma.tags) & set(mb.tags)) / max(1, len(set(ma.tags) | set(mb.tags)))
                if overlap >= 0.6 and ma.mtype == mb.mtype:
                    report["candidates"].append({
                        "pair": [a, b], "tag_overlap": round(overlap, 2),
                        "hint": "语义合并需模型判断: 内容取并集, 保留一个 slug, 另一个 memory_delete",
                    })
        # 5. 悬空链接
        dl = self.dangling_links()
        if dl:
            report["candidates"].append({
                "dangling_links": [[a, b] for a, b in dl],
                "hint": "对方记忆不存在: 要么创建, 要么从 links 里移除",
            })
        if not dry_run:
            self.refresh_md_and_index()
        report["stats"] = self.stats()
        return report


# ---------------------------------------------------------------- 多项目路由

class Nexus:
    """projects/ 根路由: 自动发现项目 (兼容 <project>/memory/ 与 <project>/ 直存两种布局),
    写操作默认主项目 (记忆最多者)."""

    @staticmethod
    def _resolve_memory_dir(project_root: str) -> str | None:
        """项目根 -> 记忆目录: 优先 <root>/memory/, 其次 <root>/ 本身."""
        sub = os.path.join(project_root, "memory")
        for cand in (sub, project_root):
            if os.path.isdir(cand) and any(
                    f.endswith(".md") for f in os.listdir(cand)):
                return cand
        return None

    def __init__(self, root: str, default_project: str = ""):
        self.root = root
        self.stores: dict[str, MemoryStore] = {}
        self.default_project = default_project
        if os.path.isdir(root):
            for name in os.listdir(root):
                pdir = Nexus._resolve_memory_dir(os.path.join(root, name))
                if pdir:
                    self.stores[name] = MemoryStore(pdir, project_name=name)
        if default_project and default_project in self.stores:
            self._default = default_project
        else:
            self._default = max(self.stores, key=lambda n: len(self.stores[n].memories)) if self.stores else ""

    @property
    def main(self) -> MemoryStore:
        return self.stores[self._default]

    def pick(self, project: str = "") -> tuple[MemoryStore, str | None]:
        if not project or project == self._default:
            return self.main, None
        if project not in self.stores:
            return self.main, f"项目 '{project}' 未发现, 已回退主项目 '{self._default}'"
        return self.stores[project], None

    def search_all(self, query: str, mtype: str = "", tags: list[str] | None = None, limit: int = 8) -> list[dict]:
        merged = []
        for name, st in self.stores.items():
            for r in st.rank(query, mtype=mtype, tags=tags, limit=limit):
                r["project"] = name
                merged.append(r)
        merged.sort(key=lambda r: -r["score"])
        results = merged[:limit]
        for r in results:  # 命中计数记到所属项目
            self.stores[r["project"]].touch(r["name"])
        return results


def default_root() -> str:
    env = os.environ.get("MEMORY_NEXUS_ROOT")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".zcode", "cli", "memories", "projects")
