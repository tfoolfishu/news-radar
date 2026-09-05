# -*- coding: utf-8 -*-
"""
query.py — 存档检索，供「看过日报后追问」场景使用。

问询时系统调取当日（或指定范围）的存档原文作答，而不是凭记忆回答。
检索结果始终携带完整溯源信息（发布者、信源等级、发布时间、原始链接、
置信度），因此回答可以逐条标注出处。
"""

import os
import re
import time

from . import config, store

_CJK = r"\u4e00-\u9fff"


def tokenize_query(q):
    """把用户问句切成检索词：英文按词，中文按 2~4 字滑窗。"""
    q = (q or "").lower()
    en = re.findall(r"[a-z0-9]{2,}", q)
    zh_runs = re.findall(r"[" + _CJK + r"]+", q)
    zh = []
    for run in zh_runs:
        if len(run) <= 4:
            zh.append(run)
            continue
        # 长句切成 2-gram，保证短标题也能命中
        for i in range(len(run) - 1):
            zh.append(run[i:i + 2])
    # 去重保序
    seen = set()
    out = []
    for t in en + zh:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def resolve_date(date_expr, today=None):
    """解析日期表达式：today / yesterday / YYYY-MM-DD / lastNd"""
    today = today or time.strftime("%Y-%m-%d", time.localtime())
    expr = (date_expr or "today").strip().lower()
    if expr in ("today", "今日", "今天"):
        return today, today
    if expr in ("yesterday", "昨日", "昨天"):
        from datetime import date as _d, timedelta
        y = (_d.fromisoformat(today) - timedelta(days=1)).isoformat()
        return y, y
    m = re.match(r"^last(\d+)d?$", expr)
    if m:
        from datetime import date as _d, timedelta
        n = int(m.group(1))
        start = (_d.fromisoformat(today) - timedelta(days=n - 1)).isoformat()
        return start, today
    if expr in ("all", "*", "全部"):
        return None, None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", expr):
        return expr, expr
    return today, today


def search(keywords_or_sentence, date_expr="today", topic=None, limit=15,
           min_confidence=None, include_held=False, home=None):
    """检索存档，返回条目列表与查询元信息。"""
    p = config.ensure_home(home)
    conn = store.init_db(p["db"])

    d_from, d_to = resolve_date(date_expr)
    kws = tokenize_query(keywords_or_sentence)

    rows = store.search_items(
        conn, kws,
        date=(d_from if d_from == d_to else None),
        date_from=(d_from if d_from != d_to else None),
        date_to=(d_to if d_from != d_to else None),
        topic=topic,
        limit=limit * 4,
    )
    if not include_held:
        # 存疑条目不参与直接作答，除非显式要求
        rows = [r for r in rows if r.get("verdict") != "hold"]
    if min_confidence is not None:
        rows = [r for r in rows if (r.get("confidence") or 0) >= min_confidence]

    rows = rows[:limit]
    conn.close()
    return {
        "keywords": kws,
        "date_from": d_from,
        "date_to": d_to,
        "topic": topic,
        "count": len(rows),
        "items": rows,
    }


def render(items, with_evidence=True):
    """把检索结果渲染为可引用的 Markdown。"""
    if not items:
        return "_存档中未找到相关内容。可尝试换用更短的关键词，或放宽日期范围（如 `--date last7d`）。_"
    out = []
    for i, it in enumerate(items, 1):
        tier = it.get("tier") or "C"
        pub = it.get("publisher") or it.get("publisher_domain") or "未知来源"
        out.append("**%d. %s**" % (i, it.get("title") or ""))
        summary = (it.get("summary") or "").strip()
        if summary:
            out.append("")
            out.append(summary[:400])
        out.append("")
        out.append("- 来源：%s（%s 级）　|　发布：%s　|　置信度：%d/100"
                   % (pub, tier, _ts(it.get("published_ts")) or it.get("published_at") or "时间未知",
                      it.get("confidence") or 0))
        groups = it.get("independent_groups") or []
        if len(groups) >= 2:
            out.append("- 交叉验证：%d 家独立来源互证（%s）" % (len(groups), "、".join(groups[:6])))
        elif len(groups) == 1:
            out.append("- 交叉验证：单一来源（%s），未经独立核实" % groups[0])
        else:
            out.append("- 交叉验证：无")
        out.append("- 原文：<%s>" % (it.get("url") or ""))
        if it.get("llm_note"):
            out.append("- 复核备注：%s" % it["llm_note"])
        flags = it.get("flags") or []
        named = [f for f in flags if f and not f.startswith(("sensational", "thin"))]
        if named:
            out.append("- 提示：%s" % "; ".join(named))
        out.append("")
    return "\n".join(out)


def _ts(ts):
    if not ts:
        return "时间未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def read_digest(date, home=None):
    """读取指定日期的日报文本。"""
    p = config.ensure_home(home)
    d, _ = resolve_date(date)
    path = os.path.join(p["archive"], "%s.md" % (d or ""))
    if not os.path.exists(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), path


def overview(home=None):
    """存档概览：有哪些日期的日报、覆盖哪些领域。"""
    p = config.ensure_home(home)
    conn = store.init_db(p["db"])
    s = store.stats(conn)
    conn.close()
    files = []
    if os.path.isdir(p["archive"]):
        files = sorted([f[:-3] for f in os.listdir(p["archive"]) if f.endswith(".md")],
                       reverse=True)
    s["archive_dates"] = files[:30]
    s["home"] = p["home"]
    return s
