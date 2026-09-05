# -*- coding: utf-8 -*-
"""
digest.py — 生成当日资讯日报（Markdown 存档）。

日报是「备查 + 推送」双用途产物：
  - 写入 <home>/archive/YYYY-MM-DD.md 永久存档
  - 同时作为 cronjob 推送给用户的正文

每条新闻都带完整溯源信息（发布者、信源等级、发布时间、原始链接、
交叉验证情况），以便日后问询时可直接引用作答。
"""

import os
import time

from . import config, credibility, store, push

TIER_MARK = {
    "A": "A 一手权威",
    "B": "B 主流媒体",
    "C": "C 一般媒体",
    "D": "D 低可信",
}

VERDICT_MARK = {
    "publish": "已通过自动核查",
    "review": "待复核",
    "hold": "存疑（未达发布线）",
}


def _fmt_time(ts, raw=""):
    if ts:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    return raw or "时间未知"


def _flag_list(it):
    flags = it.get("flags") or []
    if isinstance(flags, str):
        try:
            import json
            flags = json.loads(flags)
        except Exception:
            flags = []
    return flags or []


def should_push(it):
    """
    是否进入日报正文推送。以下情况一律排除，只进存疑区：
      - LLM 复核判为 reject
      - LLM 复核判为 hold（存疑不得与正文混同，对齐 factcheck-rules.md §七）
      - 机械层判为 hold（置信度未达发布线）
      - 栏目聚合页（除非 LLM 显式 accept）
    """
    if it.get("llm_verdict") == "reject":
        return False
    if it.get("llm_verdict") == "hold":
        return False
    if it.get("verdict") == "hold":
        return False
    flags = _flag_list(it)
    if any(str(f).startswith("column:") for f in flags) \
            and it.get("llm_verdict") != "accept":
        return False
    return True


def render_item(idx, it, show_evidence=True):
    lines = []
    title = it.get("title") or "(无标题)"
    lines.append("### %d. %s" % (idx, title))

    summary = (it.get("summary") or "").strip()
    if summary:
        lines.append("")
        lines.append(summary[:400])

    tier = it.get("tier") or "C"
    pub = it.get("publisher") or it.get("publisher_domain") or "未知来源"
    lines.append("")
    meta = "- **来源**：%s（%s）" % (pub, TIER_MARK.get(tier, tier))
    lines.append(meta)
    lines.append("- **发布**：%s" % _fmt_time(it.get("published_ts"), it.get("published_at")))
    lines.append("- **链接**：<%s>" % (it.get("url") or ""))

    groups = it.get("independent_groups") or []
    if isinstance(groups, str):
        try:
            import json
            groups = json.loads(groups)
        except Exception:
            groups = []
    if len(groups) >= 2:
        lines.append("- **交叉验证**：%d 家独立来源互证（%s）"
                     % (len(groups), "、".join(groups[:8])))
    elif len(groups) == 1:
        lines.append("- **交叉验证**：单一来源（%s），未经独立核实" % groups[0])
    else:
        lines.append("- **交叉验证**：无（无法识别独立来源）")

    lines.append("- **置信度**：%d/100（%s）"
                 % (it.get("confidence") or 0,
                    VERDICT_MARK.get(it.get("verdict"), "未评级")))

    flags = it.get("flags") or []
    if isinstance(flags, str):
        try:
            import json
            flags = json.loads(flags)
        except Exception:
            flags = []
    named = [f for f in flags if f and not f.startswith(("sensational", "thin"))]
    if named:
        lines.append("- **提示**：%s" % "; ".join(named))
    if it.get("llm_verdict"):
        lines.append("- **人工复核**：%s %s" % (
            {"accept": "采信", "reject": "否决", "hold": "暂缓"}.get(it["llm_verdict"], it["llm_verdict"]),
            ("— " + it["llm_note"]) if it.get("llm_note") else ""))
    lines.append("")
    return "\n".join(lines)


def build(date=None, home=None, only_topic=None, include_held=None, conn=None, mode="archive"):
    """生成日报文本与路径。

    mode="archive"：存档版，每条带完整溯源（来源/等级/链接/交叉验证），备查与问询用。
    mode="push"：纯净推送版，仅日期/模块编号/新闻内容，无链接无核查过程（实际推送用）。
    """
    p = config.ensure_home(home)
    settings = config.load_settings(home)
    date = date or time.strftime("%Y-%m-%d", time.localtime())

    include_held = settings.get("digest", {}).get(
        "include_unverified_section", True) if include_held is None else include_held

    g = push.gather(date=date, home=home, only_topic=only_topic, conn=conn)
    grouped_ranked = g["grouped_ranked"]
    held_all = g["held_all"]
    active = g["topics"]
    rows = g["rows"]

    if mode == "push":
        text = push.render_push(g, only_topic=only_topic)
        out_path = os.path.join(p["archive"], "%s.push.txt" % date)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        # 纯净版是存档版的派生视图，不重复写 digest 记录（由 archive 模式负责）
        count = sum(1 for line in text.splitlines() if line and line[0].isdigit() and ". " in line)
        return {"path": out_path, "text": text, "item_count": count}

    topic_names = [t["name"] for t in active]
    total_in = sum(len(v) for v in grouped_ranked.values())
    published_ids = []

    out = []
    out.append("# 每日资讯 · %s" % date)
    out.append("")
    out.append("> 生成时间：%s　|　覆盖领域：%s"
               % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                  " / ".join(topic_names) or "无"))
    out.append(">")
    out.append("> 本日报由 news-radar 自动生成。每条均附来源、信源等级与交叉验证情况，")
    out.append("> 可就其中任何一条追问，系统会回溯当日存档原文作答。")
    out.append("")

    if total_in == 0:
        out.append("## 无内容")
        out.append("")
        out.append("当日未抓取到符合发布线的新闻。可能原因：网络不通、信源全部失败、")
        out.append("或所有条目均未通过置信度门槛。建议执行 `newsctl.py sources test` 排查。")
        text = "\n".join(out)
        path = os.path.join(p["archive"], "%s.md" % date)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        store.record_digest(conn if conn else store.init_db(p["db"]), date, path, 0, 0)
        return {"path": path, "text": text, "item_count": 0}

    # 正文：按领域分段（已按 push 排序/分区，这里只筛选与截断）
    for t in active:
        items = grouped_ranked.get(t["id"], [])
        if not items:
            continue
        pushable = [i for i in items if should_push(i)]
        main = pushable[:t["max_items"]]
        out.append("---")
        out.append("")
        out.append("## %s" % t["name"])
        out.append("")
        if not main:
            out.append("_当日该领域无达到发布线的内容。_")
            out.append("")
            continue
        for idx, it in enumerate(main, 1):
            published_ids.append(it["id"])
            out.append(render_item(idx, it))
        rest = len(pushable) - len(main)
        if rest > 0:
            out.append("_（另有 %d 条因篇幅未列入，可就关键词追问查询）_" % rest)
            out.append("")

    # 存疑区
    if include_held and held_all:
        out.append("---")
        out.append("")
        out.append("## 存疑 / 未达发布线（不推送，仅存档备查）")
        out.append("")
        out.append("以下条目未进入正文：原因可能是单一来源未获交叉验证、依赖匿名信源、")
        out.append("来自低可信渠道、属于栏目聚合页，或复核时被明确否决。")
        out.append("**这些内容不作为已证实事实呈现**，仅存档备查、可供后续追问追溯。")
        out.append("")
        for idx, it in enumerate(held_all[:20], 1):
            out.append(render_item(idx, it))

    # 溯源附录
    out.append("---")
    out.append("")
    out.append("## 附录：本次抓取的信源情况")
    out.append("")
    out.append("| 指标 | 数值 |")
    out.append("| --- | --- |")
    out.append("| 当日入库条目 | %d |" % len(rows))
    out.append("| 进入日报 | %d |" % len(published_ids))
    out.append("| 存疑未推送 | %d |" % len(held_all))
    out.append("")
    out.append("_信源分级：A=一手权威（官方/通讯社/国际组织）；B=主流权威媒体；"
               "C=一般门户或垂直媒体；D=自媒体或聚合内容。_")
    out.append("")
    out.append("_置信度 = 信源基准分 + 交叉验证加分 + 一手性与时效性加分 − 低质信号扣分。_")
    out.append("")

    text = "\n".join(out)
    path = os.path.join(p["archive"], "%s.md" % date)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    store.mark_in_digest(conn if conn else store.init_db(p["db"]), published_ids)
    store.record_digest(conn if conn else store.init_db(p["db"]), date, path,
                        len(published_ids), len(grouped_ranked))
    return {"path": path, "text": text, "item_count": len(published_ids)}
