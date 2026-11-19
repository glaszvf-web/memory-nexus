# -*- coding: utf-8 -*-
"""Memory Nexus v1 迁移: 把现有平铺记忆无损升级到元数据化结构.

流程: 全量zip备份 -> 逐条补全 frontmatter (type/tags/links/importance/时间戳)
     -> 生成 index.json -> 重写 MEMORY.md 为分组路由表 (原版保留为 .v1.bak).

用法: python migrate_v1.py [--dry-run] [--project <name>]
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))
from memory_engine import LINK_RE, Memory, MemoryStore, now_iso  # noqa: E402

ROOT = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "memories", "projects")
DEFAULT_PROJECT = "z-da1ce96d678c7255"

# 人工映射表: 这些记忆是 ZCode 亲手写的, 覆盖表比自动推断准
OVERRIDES: dict[str, dict] = {
    "user-working-style": {"type": "user", "tags": ["user-profile", "用户", "working-style"], "importance": 10},
    "user-workflow-rules": {"type": "feedback", "tags": ["workflow", "用户", "rules"], "importance": 10},
    "tech-viz-reply-style": {"type": "feedback", "tags": ["viz", "reply-style", "回复风格"], "importance": 9},
    "pic-stitch-rule": {"type": "feedback", "tags": ["stitch", "拼接", "rules"], "importance": 7},
    "workflow-delivery-gate": {"type": "feedback", "tags": ["workflow", "delivery", "铁律"], "importance": 8},
    "file-hook-patterns": {"type": "method", "tags": ["file-hook", "hook", "安全扫描"], "importance": 7},
    "hanhua-binary-method": {"type": "method", "tags": ["hanhua", "binary-mod", "方法论"], "importance": 8,
                             "links": ["rng-configurator-hanhua", "seishoujo-hanhua"]},
    "tools-pitfalls-2026-10": {"type": "method", "tags": ["pitfalls", "工具", "经验"], "importance": 8,
                               "links": ["four-games-install"]},
    "remote-eyes-vision-loop": {"type": "method", "tags": ["vision", "analyze-image", "远程眼睛"], "importance": 6},
    "proxy-github-download": {"type": "reference", "tags": ["proxy", "github", "下载"], "importance": 5},
    "dlss5-swapper": {"type": "reference", "tags": ["dlss", "swapper", "工具"], "importance": 4},
    "lenovo-av-hips": {"type": "reference", "tags": ["杀软", "lenovo", "EPERM"], "importance": 5},
    "pptx-build-env": {"type": "reference", "tags": ["pptx", "libreoffice", "构建环境"], "importance": 5},
    "qwen-image-comfyui": {"type": "project", "tags": ["comfyui", "生图", "qwen"], "importance": 6},
    "upscale-pro": {"type": "project", "tags": ["upscale", "超分", "拖放"], "importance": 6},
    "ai-video-pipeline": {"type": "project", "tags": ["video", "ai-video", "管线"], "importance": 5},
    "lightyear-website": {"type": "project", "tags": ["website", "摄影", "光影流年"], "importance": 6},
    "shorekeeper-agent": {"type": "project", "tags": ["守岸人", "voice", "agent"], "importance": 5},
    "deskpet-project": {"type": "project", "tags": ["deskpet", "桌宠", "归档"], "importance": 4},
    "four-games-install": {"type": "project", "tags": ["games", "安装", "E盘"], "importance": 6,
                           "links": ["seishoujo-hanhua"]},
    "seishoujo-hanhua": {"type": "project", "tags": ["hanhua", "聖娼女", "g00"], "importance": 6,
                         "links": ["hanhua-binary-method", "four-games-install"]},
    "rng-configurator-hanhua": {"type": "project", "tags": ["hanhua", "metro", "rng"], "importance": 5,
                                "links": ["hanhua-binary-method"]},
    "dlb-fury-mod-project": {"type": "project", "tags": ["dying-light", "mod", "fury"], "importance": 5},
    "witcher3-modpack-backup": {"type": "project", "tags": ["witcher3", "backup", "整合包"], "importance": 4},
    "gta5-enhanced-opt": {"type": "project", "tags": ["gta5", "优化", "steam"], "importance": 4},
    "cp2077-25-mod-migration": {"type": "project", "tags": ["cyberpunk", "mod", "迁移"], "importance": 4},
    "beyond-heavyrain-opt": {"type": "project", "tags": ["quantic", "优化", "hdr"], "importance": 4},
    "buwenfanchen-modding": {"type": "project", "tags": ["a1", "mod", "修仙"], "importance": 5},
}

# 二轮: overrides 里声明的 links 自动补反向
for _src, _ov in list(OVERRIDES.items()):
    for _tgt in _ov.get("links", []):
        if _tgt in OVERRIDES and _src not in OVERRIDES[_tgt].get("links", []):
            OVERRIDES.setdefault(_tgt, {}).setdefault("links", []).append(_src)

METHOD_HINTS = ("方法论", "方法", "坑", "pitfall", "流程", "原则", "规则", "howto")


def guess_type(slug: str, mem: Memory) -> str:
    if mem.mtype in ("user", "feedback", "project", "reference", "method"):
        return mem.mtype
    text = (slug + " " + mem.description + " " + mem.body[:500]).lower()
    if any(h in text for h in METHOD_HINTS):
        return "method"
    if slug.startswith("user-") or "用户" in text:
        return "user"
    return "project"


def migrate(project: str, dry_run: bool) -> None:
    proj_root = os.path.join(ROOT, project)
    # 与 Nexus._resolve_memory_dir 一致: 优先 <project>/memory/ 布局
    pdir = os.path.join(proj_root, "memory")
    if not os.path.isdir(pdir):
        pdir = proj_root
    assert os.path.isdir(pdir), f"项目目录不存在: {pdir}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # 1. 备份 (dry_run 也做备份, 只读保险)
    bdir = os.path.join(pdir, "_backups")
    os.makedirs(bdir, exist_ok=True)
    zpath = os.path.join(bdir, f"migrate-v1-{stamp}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in os.listdir(pdir):
            p = os.path.join(pdir, fn)
            if os.path.isfile(p):
                z.write(p, fn)
    print(f"[backup] {zpath}")

    # 2. 保留 MEMORY.md 原版
    md_path = os.path.join(pdir, "MEMORY.md")
    if os.path.isfile(md_path):
        shutil.copy2(md_path, os.path.join(bdir, f"MEMORY.md.v1-{stamp}.bak"))

    store = MemoryStore(pdir)
    actions = []
    for slug, m in store.memories.items():
        ov = OVERRIDES.get(slug, {})
        changed = []
        new_type = ov.get("type") or guess_type(slug, m)
        if m.mtype != new_type:
            m.mtype = new_type
            changed.append("type")
        if not m.created:
            m.created = datetime.fromtimestamp(os.path.getmtime(m.source_path)).strftime("%Y-%m-%d")
            changed.append("created")
        if not m.updated:
            m.updated = m.created
            changed.append("updated")
        if ov.get("tags"):
            merged = sorted(set(m.tags) | set(ov["tags"]))
            if merged != m.tags:
                m.tags = merged
                changed.append("tags")
        if ov.get("links"):
            merged = sorted(set(m.links) | set(ov["links"]))
            if merged != m.links:
                m.links = merged
                changed.append("links")
        if ov.get("importance") and m.importance != ov["importance"]:
            m.importance = ov["importance"]
            changed.append("importance")
        implicit = [l for l in LINK_RE.findall(m.body) if l not in m.links]
        if implicit:
            m.links.extend(implicit)
            changed.append("links+body")
        if changed:
            actions.append({"memory": slug, "fields": changed})
            if not dry_run:
                with io.open(m.source_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(m.to_markdown())

    if dry_run:
        print(f"[dry-run] 将更新 {len(actions)} 条:")
        for a in actions:
            print("  ", a)
    else:
        store.scan()
        store.refresh_md_and_index()
        print(f"[done] 更新 {len(actions)} 条 frontmatter, index.json 与 MEMORY.md 路由表已重建")
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    args = sys.argv[1:]
    is_dry = "--dry-run" in args
    proj = DEFAULT_PROJECT
    if "--project" in args:
        proj = args[args.index("--project") + 1]
    migrate(proj, is_dry)
