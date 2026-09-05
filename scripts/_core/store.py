# -*- coding: utf-8 -*-
"""
store.py — SQLite 存档层。

存档是「备查」的核心：所有抓取到的条目连同完整溯源信息（发布者、域名、
信源等级、原始链接、发布时间、抓取源、匹配关键词）一并落库，永不删除。
领域被归档后，其历史数据仍然保留且可被 query 检索，只是不再抓取与推送。
"""

import json
import os
import re
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,
    dedup_key           TEXT NOT NULL,
    cluster_key         TEXT,
    topic               TEXT,
    topics              TEXT,
    title               TEXT NOT NULL,
    summary             TEXT,
    url                 TEXT,
    publisher           TEXT,
    publisher_domain    TEXT,
    media_group         TEXT,
    tier                TEXT,
    tier_reason         TEXT,
    lang                TEXT,
    region              TEXT,
    published_ts        INTEGER,
    published_at        TEXT,
    fetch_source_id     TEXT,
    fetch_source_name   TEXT,
    matched_query       TEXT,
    confidence          INTEGER DEFAULT 0,
    verdict             TEXT,
    flags               TEXT,
    independent_sources INTEGER DEFAULT 0,
    independent_groups  TEXT,
    cluster_size        INTEGER DEFAULT 1,
    is_opinion          INTEGER DEFAULT 0,
    has_anon            INTEGER DEFAULT 0,
    llm_verdict         TEXT,
    llm_note            TEXT,
    llm_checked_at      TEXT,
    status              TEXT DEFAULT 'pending',
    in_digest           INTEGER DEFAULT 0,
    created_at          TEXT,
    UNIQUE(date, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_items_date      ON items(date);
CREATE INDEX IF NOT EXISTS idx_items_topic     ON items(topic);
CREATE INDEX IF NOT EXISTS idx_items_conf      ON items(confidence);
CREATE INDEX IF NOT EXISTS idx_items_status    ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_pubdate   ON items(published_ts);

CREATE TABLE IF NOT EXISTS digests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT UNIQUE,
    path        TEXT,
    item_count  INTEGER DEFAULT 0,
    topic_count INTEGER DEFAULT 0,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    fetched      INTEGER DEFAULT 0,
    stored       INTEGER DEFAULT 0,
    topics       TEXT,
    sources_ok   INTEGER DEFAULT 0,
    sources_fail INTEGER DEFAULT 0,
    note         TEXT
);
"""


def connect(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(db_path):
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("topics", "flags", "independent_groups"):
        if isinstance(d.get(key), str) and d[key]:
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    return d


# ---------------------------------------------------------------- 写入


def upsert_item(conn, item, date):
    """写入或更新一条新闻。返回 (is_new, row_id)。"""
    dedup = item.get("dedup_key") or item.get("url") or item.get("title")
    cur = conn.execute(
        "SELECT id, llm_verdict, llm_note, status FROM items WHERE date=? AND dedup_key=?",
        (date, dedup))
    existing = cur.fetchone()

    topics = item.get("topics") or []
    payload = {
        "date": date,
        "dedup_key": dedup,
        "cluster_key": item.get("cluster_key", ""),
        "topic": item.get("topic", ""),
        "topics": json.dumps(topics, ensure_ascii=False),
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "url": item.get("url", ""),
        "publisher": item.get("publisher", ""),
        "publisher_domain": item.get("publisher_domain", ""),
        "media_group": item.get("media_group", ""),
        "tier": item.get("tier", ""),
        "tier_reason": item.get("tier_reason", ""),
        "lang": item.get("lang", ""),
        "region": item.get("region", ""),
        "published_ts": int(item.get("published_ts") or 0),
        "published_at": item.get("published_at", ""),
        "fetch_source_id": item.get("source_id", ""),
        "fetch_source_name": item.get("source_name", ""),
        "matched_query": item.get("matched_query", ""),
        "confidence": int(item.get("confidence") or 0),
        "verdict": item.get("verdict", ""),
        "flags": json.dumps(item.get("flags") or [], ensure_ascii=False),
        "independent_sources": int(item.get("independent_sources") or 0),
        "independent_groups": json.dumps(
            sorted(item.get("independent_groups") or []), ensure_ascii=False),
        "cluster_size": int(item.get("cluster_size") or 1),
        "is_opinion": 1 if item.get("is_opinion") else 0,
        "has_anon": 1 if item.get("has_anon") else 0,
        "created_at": _now(),
    }

    if existing:
        # 已存在：刷新核查指标，但保留人工/LLM 复核结论
        sets = []
        vals = []
        for k, v in payload.items():
            if k in ("date", "dedup_key", "created_at"):
                continue
            sets.append("%s=?" % k)
            vals.append(v)
        vals.extend([existing["id"]])
        conn.execute("UPDATE items SET %s WHERE id=?" % ",".join(sets), vals)
        conn.commit()
        return False, existing["id"]

    cols = ",".join(payload.keys())
    marks = ",".join(["?"] * len(payload))
    cur = conn.execute("INSERT INTO items (%s) VALUES (%s)" % (cols, marks),
                       list(payload.values()))
    conn.commit()
    return True, cur.lastrowid


def set_llm_verdict(conn, item_id, verdict, note=""):
    status = {"accept": "approved", "reject": "rejected", "hold": "held"}.get(verdict, "pending")
    conn.execute(
        "UPDATE items SET llm_verdict=?, llm_note=?, llm_checked_at=?, status=? WHERE id=?",
        (verdict, note, _now(), status, item_id))
    conn.commit()


def mark_in_digest(conn, item_ids):
    if not item_ids:
        return
    marks = ",".join(["?"] * len(item_ids))
    conn.execute("UPDATE items SET in_digest=1 WHERE id IN (%s)" % marks, list(item_ids))
    conn.commit()


def record_digest(conn, date, path, item_count, topic_count):
    conn.execute(
        "INSERT OR REPLACE INTO digests (date, path, item_count, topic_count, created_at)"
        " VALUES (?,?,?,?,?)", (date, path, item_count, topic_count, _now()))
    conn.commit()


def record_run(conn, date, started, finished, fetched, stored, topics, ok, fail, note=""):
    conn.execute(
        "INSERT INTO runs (date, started_at, finished_at, fetched, stored, topics,"
        " sources_ok, sources_fail, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (date, started, finished, fetched, stored, json.dumps(topics, ensure_ascii=False),
         ok, fail, note))
    conn.commit()


# ---------------------------------------------------------------- 查询


def get_items(conn, date=None, date_from=None, date_to=None, topic=None,
              status=None, min_confidence=None, limit=200, only_digest=False):
    sql = "SELECT * FROM items WHERE 1=1"
    args = []
    if date:
        sql += " AND date=?"
        args.append(date)
    if date_from:
        sql += " AND date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND date<=?"
        args.append(date_to)
    if topic:
        sql += " AND (topic=? OR topics LIKE ?)"
        args.extend([topic, '%"{}"%'.format(topic)])
    if status:
        sql += " AND status=?"
        args.append(status)
    if min_confidence is not None:
        sql += " AND confidence>=?"
        args.append(int(min_confidence))
    if only_digest:
        sql += " AND in_digest=1"
    sql += " ORDER BY confidence DESC, published_ts DESC LIMIT ?"
    args.append(int(limit))
    cur = conn.execute(sql, args)
    return [row_to_dict(r) for r in cur.fetchall()]


def get_item(conn, item_id):
    cur = conn.execute("SELECT * FROM items WHERE id=?", (item_id,))
    return row_to_dict(cur.fetchone())


def search_items(conn, keywords, date=None, date_from=None, date_to=None,
                 topic=None, limit=30):
    """
    关键词检索。中英文混合：英文按词匹配，中文按 2-gram 片段匹配。
    按命中数 + 置信度排序。
    """
    rows = get_items(conn, date=date, date_from=date_from, date_to=date_to,
                     topic=topic, limit=1000)
    kws = [k.lower() for k in keywords if k and k.strip()]
    scored = []
    for r in rows:
        text = ((r.get("title") or "") + " " + (r.get("summary") or "")).lower()
        if not kws:
            scored.append((0, r))
            continue
        hits = 0
        for k in kws:
            # 短 ASCII 词（ai / gdp / fed）按词边界匹配，避免命中 raise、against
            if len(k) <= 4 and re.fullmatch(r"[a-z0-9\s]+", k):
                if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text):
                    hits += 1
            elif k in text:
                hits += 1
        if hits:
            scored.append((hits, r))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("confidence") or 0)))
    return [r for _, r in scored[:limit]]


def stats(conn):
    out = {}
    cur = conn.execute("SELECT COUNT(*) c, MIN(date) d1, MAX(date) d2 FROM items")
    row = cur.fetchone()
    out["total_items"] = row["c"] if row else 0
    out["date_min"] = row["d1"] if row else None
    out["date_max"] = row["d2"] if row else None

    cur = conn.execute("SELECT date, COUNT(*) c FROM items GROUP BY date ORDER BY date DESC LIMIT 14")
    out["by_date"] = [{"date": r["date"], "count": r["c"]} for r in cur.fetchall()]

    cur = conn.execute("SELECT topic, COUNT(*) c FROM items GROUP BY topic ORDER BY c DESC")
    out["by_topic"] = [{"topic": r["topic"], "count": r["c"]} for r in cur.fetchall()]

    cur = conn.execute(
        "SELECT status, COUNT(*) c FROM items GROUP BY status")
    out["by_status"] = [{"status": r["status"], "count": r["c"]} for r in cur.fetchall()]

    cur = conn.execute("SELECT COUNT(*) c FROM digests")
    out["digest_count"] = cur.fetchone()["c"]
    return out
