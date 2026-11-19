# -*- coding: utf-8 -*-
"""Memory Nexus 引擎沙箱测试: 临时目录全链路验证, 不碰真实记忆库.

临时目录由系统 TEMP 轮换清理, 测试产物不回滚删除 (避免递归删除类高危操作).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "mcp"))
from memory_engine import Memory, MemoryStore, Nexus, tokenize  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok " if cond else "  FAIL ") + name + (f" | {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nx-test-"))
    proj = tmp / "test-proj"
    proj.mkdir()
    st = MemoryStore(str(proj))

    # --- 分词
    toks = tokenize("聖娼女汉化 g00 管线 pipeline")
    check("tokenize: 中文bigram+英文词", "聖娼" in toks and "娼女" in toks and "pipeline" in toks, str(toks[:8]))

    # --- 写入
    r = st.write(Memory(slug="test-hanhua", description="汉化工程管线与g00图片层", mtype="project",
                        body="剧本层+Gameexe层+g00图片层三层汉化，g00编解码管线在 tmp_pwcrack/g00work",
                        tags=["hanhua", "汉化", "g00"], links=[], importance=6))
    check("write: 新记忆成功", r["ok"], json.dumps(r, ensure_ascii=False))
    r2 = st.write(Memory(slug="test-hanhua", description="重复", mtype="project", body="dup"))
    check("write: 重名被拒并提示 update", not r2["ok"] and "memory_update" in str(r2), str(r2))
    r3 = st.write(Memory(slug="Bad_Name", description="x", mtype="project", body="y"))
    check("write: 非法slug被拒", not r3["ok"], str(r3))
    # 下面这串是刻意编的假凭据：用来验证 write() 的凭据扫描警告会不会触发。
    # 它不是真 key，末尾 0000 是故意的。审计工具会把它标成 critical，那是误报。
    r4 = st.write(Memory(slug="test-secret", description="含凭据的记忆", mtype="reference",
                         body="api_key=sk-abcdef0123456789abcdef0000 位置在云上"))
    check("write: 凭据扫描警告", r4["ok"] and bool(r4["warnings"]), str(r4))
    st.write(Memory(slug="test-method", description="二进制mod汉化方法论", mtype="method",
                    body="改串先查引用；指针偏移陷阱；零洞≠死区。相关 [[test-hanhua]]", tags=["method"], importance=8))

    # --- 解析回读 (往返一致)
    st2 = MemoryStore(str(proj))
    m = st2.get("test-method")
    check("parse: frontmatter 往返", m is not None and m.importance == 8 and m.mtype == "method",
          f"imp={getattr(m, 'importance', None)}")

    # --- 检索
    hits = st2.rank("汉化")
    check("search: 中文关键词命中", bool(hits) and hits[0]["name"] in ("test-hanhua", "test-method"), str(hits[:2]))
    hits2 = st2.rank("g00 图片层")
    check("search: 混合词命中目标", bool(hits2) and hits2[0]["name"] == "test-hanhua", str(hits2[:2]))
    hits3 = st2.rank("", mtype="method")
    check("search: 无查询浏览模式按重要度", bool(hits3) and hits3[0]["name"] == "test-method", str(hits3[:2]))
    check("search: 无结果返回空", st2.rank("qqzzxxv") == [])
    noisy = st2.rank("不存在的词")
    check("search: 中文单字噪声抑制", len(noisy) <= 2, str(noisy))

    # --- 命中计数
    st2.touch("test-hanhua")
    st2.touch("test-hanhua")
    st3 = MemoryStore(str(proj))
    check("touch: use_count 持久化", st3.get("test-hanhua").use_count == 2, str(st3.get("test-hanhua").use_count))

    # --- 图
    g = st3.graph("test-hanhua")
    check("graph: 沿链接展开", g["ok"] and "test-method" in g["nodes"], str(g.get("nodes")))

    # --- 链接
    st3.link("test-hanhua", ["test-secret"])
    st4 = MemoryStore(str(proj))
    check("link: 双向链接", "test-secret" in st4.get("test-hanhua").links and "test-hanhua" in st4.get("test-secret").links)

    # --- 软删除/恢复
    d = st4.delete("test-secret")
    check("delete: 软删进回收站", d["ok"] and "_trash" in d["soft_deleted"], str(d))
    st5 = MemoryStore(str(proj))
    check("delete: 库中已移除", st5.get("test-secret") is None)
    rr = st5.restore("test-secret")
    check("restore: 回收站恢复", rr["ok"], str(rr))

    # --- 巩固 (dry_run vs 落盘)
    rep = st5.consolidate(dry_run=True)
    no_backup = "backup" not in rep
    st5b = MemoryStore(str(proj))
    links_unchanged = "test-hanhua" not in st5b.get("test-method").links
    check("consolidate: dry_run 零副作用", rep["dry_run"] and no_backup and links_unchanged,
          f"backup={not no_backup} links_promoted={not links_unchanged}")
    rep2 = st5.consolidate(dry_run=False)
    check("consolidate: 落盘含备份+索引", (proj / "index.json").exists() and "backups" in str(rep2.get("backup")), str(rep2.get("backup")))
    promoted = any(a["action"] == "promote_links" for a in rep2["actions"])
    st5c = MemoryStore(str(proj))
    check("consolidate: 裸[[链接]]提升到frontmatter", promoted and "test-hanhua" in st5c.get("test-method").links,
          str(rep2["actions"]))

    # --- 索引/路由表
    idx = json.loads((proj / "index.json").read_text(encoding="utf-8"))
    check("index: 生成且含全部记忆", set(idx["memories"]) == {"test-hanhua", "test-method", "test-secret"}, str(set(idx["memories"])))
    md = (proj / "MEMORY.md").read_text(encoding="utf-8")
    check("MEMORY.md: 分组路由表", "## project 工程状态" in md and "## method 方法论" in md and "test-hanhua" in md, md[:200])

    # --- stats
    s = st5.stats()
    check("stats: 汇总正确", s["total"] == 3 and s["by_type"]["project"] == 1, json.dumps(s, ensure_ascii=False)[:200])

    # --- Nexus 多项目
    proj_b = tmp / "proj-b"
    proj_b.mkdir()
    (proj_b / "b-mem.md").write_text(
        "---\nname: b-mem\ndescription: B项目记忆\nmetadata:\n  type: project\n---\n\n正文B\n",
        encoding="utf-8")
    nx = Nexus(str(tmp))
    check("nexus: 主项目=记忆最多者", nx._default == "test-proj", nx._default)
    cross = nx.search_all("汉化")
    check("nexus: 跨项目检索", any(r["name"] == "test-hanhua" for r in cross), str(cross[:1]))

    print(f"\n== {len(PASS)} passed, {len(FAIL)} failed ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
