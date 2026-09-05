# -*- coding: utf-8 -*-
"""
fetch.py — 抓取编排：领域归属 → 去重 → 事实核查机械层 → 落库。

流程（固定顺序，不可跳过）：
  1. sources.fetch_all()  并发抓取，失败降级
  2. assign_topics()      把每条新闻归属到领域（支持多归属）
  3. dedup()              按 URL 规范化 + 标题归一化去重
  4. credibility.assess() 信源分级 / 交叉验证 / 低质信号 / 置信度评分
  5. store.upsert_item()  落库存档（含完整溯源信息）
"""

import re
import time
import urllib.parse

from . import config, credibility, sources, store

_TRACKING_PARAMS = re.compile(
    r"^(utm_|spm|from|ref|share|fbclid|gclid|_ga|source$|share_token)", re.I)


def normalize_url(url):
    """URL 规范化：去 tracking 参数、fragment、协议与 www 差异。"""
    if not url:
        return ""
    try:
        p = urllib.parse.urlparse(url)
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        q = urllib.parse.parse_qsl(p.query, keep_blank_values=False)
        q = [(k, v) for k, v in q if not _TRACKING_PARAMS.match(k)]
        query = urllib.parse.urlencode(q)
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url


def normalize_title(title):
    if not title:
        return ""
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())
    return t


# ---------------------------------------------------------------- 领域归属


_ASCII_ONLY = re.compile(r"^[a-z0-9\s]+$")


def _kw_pattern(kw):
    """
    生成关键词匹配模式。
    纯 ASCII 的短词（AI、GDP、Fed、oil 等缩写）必须按「前后非字母数字」匹配，
    否则 "AI" 会命中 raise / against / maintain，"Fed" 会命中 federal。
    注意不能用 \\b：中英混排时 \\b 在中文边界上不生效，会漏判「人工智能AI芯片」。
    """
    if len(kw) <= 4 and _ASCII_ONLY.match(kw):
        return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
    return None


def _keyword_hits(text, keywords):
    """统计关键词命中数（长词优先，避免短词重复计数）。"""
    hits = 0
    seen_spans = []
    for kw in sorted(set(keywords), key=len, reverse=True):
        if not kw:
            continue
        k = kw.lower()
        pat = _kw_pattern(k)
        if pat is not None:
            spans = [(m.start(), m.end()) for m in pat.finditer(text)]
        else:
            spans = []
            pos = 0
            while True:
                idx = text.find(k, pos)
                if idx == -1:
                    break
                spans.append((idx, idx + len(k)))
                pos = idx + len(k)
        for s, e in spans:
            # 避免同一片段被长短词重复计数
            if not any(a <= s < b for a, b in seen_spans):
                hits += 1
                seen_spans.append((s, e))
    return hits


def assign_topics(items, topics, settings):
    """
    为每条新闻计算领域归属分数，写入 item["topics"] 与 item["topic"]（主领域）。

    分数规则：标题命中 x3，摘要命中 x1。
    加权区分来源类型（关键）：
      - query 型（关键词搜索）：条目天然对应发起查询的领域，记 +5
      - stream 型（固定栏目流）：**不加权**，必须靠关键词命中来判定归属，
        否则一条新闻会被同时塞进所有领域（曾导致各领域内容完全相同）
    """
    active = [t for t in topics if t["enabled"] and not t["archived"]]
    active_ids = {t["id"] for t in active}
    min_score = int(settings.get("fetch", {}).get("min_topic_score", 3))

    kept = []
    for it in items:
        title = (it.get("title") or "").lower()
        summary = (it.get("summary") or "").lower()
        scores = {}

        for t in active:
            kws = list(t.get("query_zh") or []) + list(t.get("query_en") or [])
            kws = [k.lower() for k in kws if k]
            if not kws:
                continue
            s = _keyword_hits(title, kws) * 3 + _keyword_hits(summary, kws)
            if s > 0:
                scores[t["id"]] = s

        is_query = it.get("fetch_kind") == "query"
        if is_query:
            for tid in (it.get("topics") or []):
                if tid in active_ids:
                    scores[tid] = scores.get(tid, 0) + 5

        if not scores:
            it["topics"] = []
            it["topic"] = ""
            it["_topic_score"] = 0
            continue

        # 流式来源要求达到最低命中分，避免无关内容混入
        best = max(scores.values())
        if not is_query and best < min_score:
            it["topics"] = []
            it["topic"] = ""
            it["_topic_score"] = best
            continue

        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        it["topics"] = [tid for tid, _ in ranked]
        it["topic"] = ranked[0][0]
        it["_topic_score"] = ranked[0][1]
        kept.append(it)
    return kept


# ---------------------------------------------------------------- 去重


def dedup(items):
    """按规范化 URL 与标题去重，保留置信度更高、信息更全的一条。"""
    by_url = {}
    by_title = {}
    out = []
    removed = 0
    for it in items:
        it["_norm_url"] = normalize_url(it.get("url"))
        it["_norm_title"] = normalize_title(it.get("title"))
        key_u = it["_norm_url"]
        key_t = it["_norm_title"]
        dup = False
        if key_u and key_u in by_url:
            dup = True
            prev = by_url[key_u]
            if (it.get("confidence") or 0) > (prev.get("confidence") or 0):
                by_url[key_u] = it
        if not dup and key_t and key_t in by_title:
            dup = True
            prev = by_title[key_t]
            if (it.get("confidence") or 0) > (prev.get("confidence") or 0):
                by_title[key_t] = it
        if dup:
            removed += 1
            continue
        if key_u:
            by_url[key_u] = it
        if key_t:
            by_title[key_t] = it
        out.append(it)
    return out, removed


# ---------------------------------------------------------------- 主流程


def run(date=None, only_topic=None, settings=None, home=None, verbose=True):
    """
    执行一次完整抓取。返回结构化结果 dict。
    """
    p = config.ensure_home(home)
    settings = settings or config.load_settings(home)
    topics = config.load_topics(home)
    health = config.load_health(home)
    date = date or time.strftime("%Y-%m-%d", time.localtime())
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    active = config.active_topics(topics)
    if only_topic:
        active = [t for t in active if t["id"] == only_topic]
    if not active:
        return {
            "ok": False,
            "error": "没有启用中的领域。可用 `topic list` 查看，"
                     "或用 `topic add` 新增、`topic restore` 恢复已归档领域。",
            "date": date,
        }

    # 1) 抓取
    raw_items, report = sources.fetch_all(
        active, settings, health, only_topic=only_topic, verbose=verbose)

    # 2) 时间窗过滤（无发布时间的保留，已由 nodate flag 标记）
    window_h = int(settings.get("fetch", {}).get("window_hours", 36))
    cutoff = time.time() - window_h * 3600
    before_window = len(raw_items)
    fresh = [it for it in raw_items
             if not it.get("published_ts") or it["published_ts"] >= cutoff]
    dropped_stale = before_window - len(fresh)

    # 3) 领域归属
    assigned = assign_topics(fresh, topics, settings)

    # 4) 事实核查机械层（含可信央媒放松策略）
    trusted_groups = set(settings.get("trusted_groups", []) or [])
    assessed, vstats = credibility.assess(assigned, trusted_groups=trusted_groups)

    # 5) 去重
    deduped, removed = dedup(assessed)

    # 6) 落库
    conn = store.init_db(p["db"])
    stored = 0
    new_ids = []
    for it in deduped:
        it["dedup_key"] = it.get("_norm_url") or it.get("url") or it.get("title")
        it["cluster_key"] = "%s-%s" % (date, it.get("cluster_id") or 0)
        it["published_at"] = it.get("published_raw") or ""
        is_new, row_id = store.upsert_item(conn, it, date)
        it["id"] = row_id
        if is_new:
            stored += 1
            new_ids.append(row_id)

    finished = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    store.record_run(conn, date, started, finished, len(raw_items), stored,
                     [t["id"] for t in active], len(report.get("ok", [])),
                     len(report.get("failed", [])),
                     "topic=%s" % (only_topic or "all"))
    conn.close()
    config.save_health(health, home)

    # 7) 按领域汇总（供 LLM 生成日报）
    by_topic = {}
    for t in active:
        cand = [it for it in deduped if t["id"] in (it.get("topics") or [])]
        cand.sort(key=lambda x: (-(x.get("confidence") or 0),
                                 -(x.get("published_ts") or 0)))
        over_min = [it for it in cand if (it.get("confidence") or 0) >= t["min_confidence"]]
        by_topic[t["id"]] = {
            "topic_id": t["id"],
            "topic_name": t["name"],
            "min_confidence": t["min_confidence"],
            "max_items": t["max_items"],
            "candidates": [_brief(it) for it in cand],
            "recommended": [_brief(it) for it in over_min[:t["max_items"]]],
        }

    return {
        "ok": True,
        "date": date,
        "topics": [t["id"] for t in active],
        "counts": {
            "fetched": len(raw_items),
            "dropped_stale": dropped_stale,
            "after_topic_assign": len(assigned),
            "duplicates_removed": removed,
            "stored_new": stored,
            "verdict": vstats,
        },
        "sources": report,
        "by_topic": by_topic,
    }


def _brief(it):
    """供 LLM 阅读的精简条目（含溯源与核查证据）。"""
    a = it.get("_assess") or {}
    return {
        "id": it.get("id"),
        "title": it.get("title"),
        "summary": (it.get("summary") or "")[:300],
        "url": it.get("url"),
        "publisher": it.get("publisher"),
        "domain": it.get("publisher_domain"),
        "tier": it.get("tier"),
        "tier_reason": it.get("tier_reason"),
        "published": it.get("published_raw") or "",
        "confidence": it.get("confidence"),
        "verdict": it.get("verdict"),
        "independent_sources": a.get("independent_sources", 0),
        "independent_groups": a.get("independent_groups", []),
        "flags": a.get("flags", []),
        "is_opinion": a.get("is_opinion", False),
        "topics": it.get("topics") or [],
        "reasons": a.get("reasons", []),
        "source_name": it.get("source_name"),
    }
