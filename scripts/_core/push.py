# -*- coding: utf-8 -*-
"""
push.py — 推送阈值与排序控制中枢。

把所有「推什么、推几条、按什么顺序」的决策收敛到本模块，便于统一测试：
  1. 每模块条数上限：topic.max_items，全局默认见 settings.push.default_max_items。
  2. 模块内排序：order_by ∈ {confidence, freshness, tier}。
  3. 质量分区排序：命中 priority_keywords 的置顶，命中 suppress_keywords 的置底，
     中性条目居中；每个分区内部再按 order_by 排序。
  4. 纯净推送文本：仅含「日期 / 模块编号 / 新闻内容」，无链接、无事实核查过程。

注意：抓取、核查、存档仍在 fetch/credibility/store 中完成，本模块只负责
「已通过核查的条目如何被筛选、排序、呈现给最终接收者」。
"""

import re
import time

from . import config, digest, store

ORDER_OPTIONS = ("confidence", "freshness", "tier")

_TIER_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
_ASCII_ONLY = re.compile(r"^[a-z0-9\s]+$")


def _kw_pattern(kw):
    """与 fetch._kw_pattern 一致的边界匹配：纯 ASCII 短词按前后非字母数字匹配。"""
    k = kw.lower()
    if len(k) <= 4 and _ASCII_ONLY.match(k):
        return re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
    return None


def _match_any(text_lower, keywords):
    """命中任一关键词即返回 True。中文按子串，英文短词按词边界。"""
    if not keywords:
        return False
    for kw in keywords:
        if not kw:
            continue
        k = kw.lower()
        pat = _kw_pattern(k)
        if pat is not None:
            if pat.search(text_lower):
                return True
        elif k in text_lower:
            return True
    return False


def classify_bucket(item, priority, suppress):
    """返回 priority / neutral / suppress 三档之一。"""
    text = ((item.get("title") or "") + " " + (item.get("summary") or "")).lower()
    if _match_any(text, priority):
        return "priority"
    if _match_any(text, suppress):
        return "suppress"
    return "neutral"


def order_value(item, order_by):
    """模块内次级排序的取值（越大越靠前）。"""
    if order_by == "freshness":
        return item.get("published_ts") or 0
    if order_by == "tier":
        return _TIER_RANK.get(item.get("tier") or "C", 2)
    return item.get("confidence") or 0


def rank(items, topic, settings):
    """
    对单个领域内、已通过 should_push 的条目做质量分区 + 排序。
    返回有序列表。稳定、不丢条目、不产生重复。
    """
    order_by = topic.get("order_by") or settings.get("push", {}).get("default_order_by", "confidence")
    priority = topic.get("priority_keywords") or []
    suppress = topic.get("suppress_keywords") or []

    buckets = {"priority": [], "neutral": [], "suppress": []}
    for it in items:
        buckets[classify_bucket(it, priority, suppress)].append(it)

    out = []
    for b in ("priority", "neutral", "suppress"):
        lst = sorted(buckets[b], key=lambda x: -order_value(x, order_by))
        out.extend(lst)
    return out


def gather(date=None, home=None, only_topic=None, conn=None):
    """
    聚合当日条目并按领域分组、排序。供 digest（存档版）与 push（纯净版）共用，
    确保两条链路对同一批数据的筛选/排序完全一致，避免体感不一致或丢条。
    """
    p = config.ensure_home(home)
    settings = config.load_settings(home)
    topics = config.load_topics(home)
    date = date or time.strftime("%Y-%m-%d", time.localtime())

    own_conn = conn is None
    if own_conn:
        conn = store.init_db(p["db"])

    active = config.active_topics(topics)
    if only_topic:
        active = [t for t in active if t["id"] == only_topic]

    rows = store.get_items(conn, date=date, limit=2000)
    by_tid = {t["id"]: t for t in active}

    grouped_all = {}   # tid -> 该领域全部条目（已排序，含存疑）
    held_all = []      # 所有未通过 should_push 的条目
    for r in rows:
        rtopics = r.get("topics") or ([r["topic"]] if r.get("topic") else [])
        placed = False
        for tid in rtopics:
            if tid in by_tid:
                grouped_all.setdefault(tid, []).append(r)
                placed = True
                break
        if not placed:
            continue
        if not digest.should_push(r):
            held_all.append(r)

    # 每个领域分别做质量分区排序
    grouped_ranked = {}
    for tid, items in grouped_all.items():
        grouped_ranked[tid] = rank(items, by_tid[tid], settings)

    if own_conn:
        conn.close()
    return {
        "grouped_ranked": grouped_ranked,
        "held_all": held_all,
        "topics": active,
        "settings": settings,
        "date": date,
        "rows": rows,
    }


def render_push(g, only_topic=None):
    """
    生成纯净推送文本：日期 + 模块编号 + 新闻内容，无链接、无核查过程。
    形如：
        【2026-09-05】每日资讯推送

        【模块1 · AI】
        1. 标题 —— 摘要（截断）
        2. ...
    """
    settings = g["settings"]
    date = g["date"]
    topics = g["topics"]
    if only_topic:
        topics = [t for t in topics if t["id"] == only_topic]
    grouped = g["grouped_ranked"]
    use_module_no = settings.get("push", {}).get("module_number", True)
    summary_len = int(settings.get("push", {}).get("summary_len", 200))

    lines = ["【%s】每日资讯推送" % date, ""]
    mod_no = 0
    for t in topics:
        items = grouped.get(t["id"], [])
        pushable = [i for i in items if digest.should_push(i)]
        main = pushable[:t["max_items"]]
        if not main:
            continue
        mod_no += 1
        header = ("【模块%d · %s】" % (mod_no, t["name"])) if use_module_no else ("【%s】" % t["name"])
        lines.append(header)
        for idx, it in enumerate(main, 1):
            title = (it.get("title") or "(无标题)").strip()
            summary = (it.get("summary") or "").strip().replace("\n", " ")
            line = "%d. %s" % (idx, title)
            if summary:
                line += " —— " + summary[:summary_len]
            lines.append(line)
        lines.append("")

    if mod_no == 0:
        lines.append("（当日无达到发布线的内容）")
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    return text
