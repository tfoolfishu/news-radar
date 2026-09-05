# -*- coding: utf-8 -*-
"""
sources.py — 信源注册表 + 抓取 + 解析。

设计要点：
1. 零第三方依赖（仅标准库），Python 3.8+ 可运行。
2. 源分两类：
   - query 型：支持关键词查询的搜索型 RSS（聚合器）
   - stream 型：固定栏目的 RSS 直连（官方/媒体自有源）
3. 聚合器（Google News / Bing News）抓取到的条目，其真实发布者取自
   item 的 <source url="..."> 标签，tier 按 *原始发布者域名* 判定，
   而不是按聚合器判定。这是事实核查正确性的前提。
4. 抓取失败降级：单源失败不影响整体；连续失败进入冷却，冷却期内跳过。
"""

import gzip
import io
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

from . import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# ---------------------------------------------------------------- 源注册表
#
# tier 说明（详见 references/source-tiers.md）：
#   A = 一手权威（政府/央行/监管机构/国际组织/官方通讯社/交易所公告）
#   B = 主流权威媒体（有编辑部与纠错机制）
#   C = 一般门户/聚合/地方媒体
#   D = 自媒体/内容农场/无署名（默认低可信）
#
# 聚合器 kind=query 的 tier 字段写 "AGG"，仅表示"需按原始发布者重定向定级"。

SOURCES = [
    # ---------------- query 型（聚合器，覆盖长尾与自定义领域） ----------------
    {"id": "gnews_zh", "name": "Google News (中文)", "kind": "query", "tier": "AGG",
     "lang": "zh", "region": "cn", "url": "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
    {"id": "gnews_en", "name": "Google News (English)", "kind": "query", "tier": "AGG",
     "lang": "en", "region": "intl", "url": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"},

    # ---------------- stream 型：官方一手（Tier A） ----------------
    {"id": "fed_press", "name": "美联储 FRB 新闻稿", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"id": "ecb_press", "name": "欧洲央行 ECB", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://www.ecb.europa.eu/rss/press.html"},
    {"id": "imf_news", "name": "IMF 新闻", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://www.imf.org/en/News/RSS?language=eng&series=IMF%20News"},
    {"id": "un_news", "name": "联合国新闻", "kind": "stream", "tier": "A",
     "lang": "zh", "region": "intl", "url": "https://news.un.org/feed/subscribe/zh/news/all/rss.xml"},
    {"id": "sec_press", "name": "美国 SEC 公告", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://www.sec.gov/news/pressreleases.rss"},
    {"id": "un_news_en", "name": "UN News (English)", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml"},

    # 注：新华网、人民网、中国日报、新浪、财新的官方 RSS 经实测已停更或
    # 不含 pubDate（返回的是数月乃至数年前的存档内容），故不作为抓取源。
    # 但它们的域名仍保留在 credibility 的 A 级白名单中 ——
    # 通过聚合搜索渠道抓到这些媒体的文章时，依然会被正确定级为 A 级。

    # ---------------- stream 型：主流权威媒体（Tier B） ----------------
    # 中新网各频道：实测实时更新且带 pubDate
    {"id": "chinanews", "name": "中国新闻网 滚动", "kind": "stream", "tier": "B",
     "lang": "zh", "region": "cn", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml"},
    {"id": "chinanews_top", "name": "中国新闻网 要闻", "kind": "stream", "tier": "B",
     "lang": "zh", "region": "cn", "url": "https://www.chinanews.com.cn/rss/importnews.xml"},
    {"id": "chinanews_finance", "name": "中国新闻网 财经", "kind": "stream", "tier": "B",
     "lang": "zh", "region": "cn", "url": "https://www.chinanews.com.cn/rss/finance.xml"},
    {"id": "chinanews_world", "name": "中国新闻网 国际", "kind": "stream", "tier": "B",
     "lang": "zh", "region": "cn", "url": "https://www.chinanews.com.cn/rss/world.xml"},
    {"id": "chinanews_society", "name": "中国新闻网 社会", "kind": "stream", "tier": "B",
     "lang": "zh", "region": "cn", "url": "https://www.chinanews.com.cn/rss/society.xml"},
    {"id": "npr_politics", "name": "NPR Politics", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.npr.org/1014/rss.xml"},
    {"id": "npr_science", "name": "NPR Science", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.npr.org/1007/rss.xml"},
    {"id": "bbc_world", "name": "BBC World", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"id": "bbc_business", "name": "BBC Business", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"id": "bbc_tech", "name": "BBC Technology", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
    {"id": "npr_world", "name": "NPR World", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.npr.org/1004/rss.xml"},
    {"id": "npr_business", "name": "NPR Business", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.npr.org/1006/rss.xml"},
    {"id": "npr_tech", "name": "NPR Technology", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://feeds.npr.org/1019/rss.xml"},
    {"id": "guardian_world", "name": "The Guardian World", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://www.theguardian.com/world/rss"},
    {"id": "aljazeera", "name": "Al Jazeera", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"id": "ft_home", "name": "Financial Times", "kind": "stream", "tier": "B",
     "lang": "en", "region": "intl", "url": "https://www.ft.com/rss/home"},
    {"id": "ap_topnews", "name": "AP Top News", "kind": "stream", "tier": "A",
     "lang": "en", "region": "intl", "url": "https://rsshub.app/apnews/topics/apf-topnews"},

    # ---------------- stream 型：一般门户（Tier C） ----------------
    {"id": "tmtpost", "name": "钛媒体", "kind": "stream", "tier": "C",
     "lang": "zh", "region": "cn", "url": "https://www.tmtpost.com/rss.xml"},
    {"id": "solidot", "name": "Solidot 科技资讯", "kind": "stream", "tier": "C",
     "lang": "zh", "region": "cn", "url": "https://www.solidot.org/index.rss"},
    {"id": "ithome", "name": "IT之家", "kind": "stream", "tier": "C",
     "lang": "zh", "region": "cn", "url": "https://www.ithome.com/rss/"},
    {"id": "leiphone", "name": "雷峰网", "kind": "stream", "tier": "C",
     "lang": "zh", "region": "cn", "url": "https://www.leiphone.com/feed"},
    {"id": "infoq_cn", "name": "InfoQ 中国", "kind": "stream", "tier": "C",
     "lang": "zh", "region": "cn", "url": "https://www.infoq.cn/feed"},
]

SOURCES_BY_ID = {s["id"]: s for s in SOURCES}


# ---------------------------------------------------------------- HTTP


def _decode(raw_bytes, declared=None):
    """按 XML 声明 → utf-8 → gbk 顺序探测编码。"""
    for enc in [e for e in [declared, "utf-8", "gb18030", "latin-1"] if e]:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _decompress(resp, raw):
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            return raw
    if "deflate" in enc:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                return zlib.decompress(raw)
            except Exception:
                return raw
    return raw


def http_get(url, timeout=12, retries=1):
    """返回 (text, err)。err 为 None 表示成功。"""
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            raw = _decompress(resp, raw)
            declared = None
            head = raw[:200].decode("ascii", errors="ignore")
            m = re.search(r"encoding=[\"']([\w\-]+)[\"']", head)
            if m:
                declared = m.group(1)
            return _decode(raw, declared), None
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (400, 401, 403, 404, 410):
                return None, last  # 永久性失败，不重试
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:80])
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:80])
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))
    return None, last


# ---------------------------------------------------------------- 解析

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text):
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    # 数字/十六进制字符实体，如 &#8195;（全角空格）、&#x2014;
    text = re.sub(r"&#(\d+);", lambda m: _safe_chr(m.group(1), 10), text)
    text = re.sub(r"&#[xX]([0-9a-fA-F]+);", lambda m: _safe_chr(m.group(1), 16), text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'")
                .replace("&apos;", "'").replace("&mdash;", "—")
                .replace("&ldquo;", "“").replace("&rdquo;", "”")
                .replace("&hellip;", "…"))
    return _WS_RE.sub(" ", text).strip()


def _safe_chr(code, base):
    try:
        n = int(code, base)
        return chr(n) if 0 < n < 0x110000 else " "
    except (ValueError, OverflowError):
        return " "


def _localname(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_published(text):
    """解析 RSS RFC822 / Atom ISO8601 时间，返回 epoch 秒；失败返回 0。"""
    if not text:
        return 0
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt:
            return dt.timestamp()
    except Exception:
        pass
    iso = text.replace("Z", "+00:00")
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(iso).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            import datetime as _dt
            return _dt.datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    return 0


def _find_all(root, name):
    return [el for el in root.iter() if _localname(el.tag) == name]


def parse_feed(text, src, limit=40):
    """把 RSS/Atom/RDF 文本解析为条目列表。"""
    if not text:
        return []
    # 去 BOM 与前导空白（部分源如美联储 RSS 带 UTF-8 BOM，会导致解析失败）
    text = text.lstrip("\ufeff").strip()
    if not text.startswith("<"):
        return []

    def _try_parse(s):
        try:
            return ET.fromstring(s)
        except ET.ParseError:
            return None

    root = _try_parse(text)
    if root is None:
        # 容错：截断到最后一个完整 item/entry 后补全根标签重试
        tail = "</channel></rss>"
        cut = text.rfind("</item>")
        if cut == -1:
            cut = text.rfind("</entry>")
            tail = "</feed>"
        if cut > 0:
            end = cut + len("</item>") if "</item>" in text[cut:cut + 10] else cut + len("</entry>")
            root = _try_parse(text[:end] + tail)
        if root is None:
            return []

    items = _find_all(root, "item") or _find_all(root, "entry")
    out = []
    for el in items[:limit]:
        title = link = summary = pub = ""
        publisher = ""
        publisher_url = ""
        for child in el:
            ln = _localname(child.tag)
            val = (child.text or "").strip()
            if ln == "title" and not title:
                title = val
            elif ln == "link":
                href = (child.attrib.get("href") or "").strip()
                rel = child.attrib.get("rel", "alternate")
                if href and (rel == "alternate" or not link):
                    if not link:
                        link = href
                elif val and not link:
                    link = val
            elif ln in ("description", "summary", "content", "encoded") and not summary:
                summary = val
            elif ln in ("pubDate", "published", "updated", "date") and not pub:
                pub = val
            elif ln == "source":
                # Google News 在此给出真实发布者
                publisher = _clean(val) or publisher
                publisher_url = (child.attrib.get("url") or "").strip()

        title = _clean(title)
        if not title or not link:
            continue

        # 真实发布者域名：优先 item 的 <source url>，否则取链接域名
        domain = ""
        if publisher_url:
            domain = urllib.parse.urlparse(publisher_url).netloc.lower()
        if not domain:
            domain = urllib.parse.urlparse(link).netloc.lower()
        domain = domain[4:] if domain.startswith("www.") else domain

        out.append({
            "title": title,
            "url": link,
            "summary": _clean(summary)[:600],
            "published_ts": parse_published(pub),
            "published_raw": pub,
            "publisher": publisher or src["name"],
            "publisher_domain": domain,
            "source_id": src["id"],
            "source_name": src["name"],
            "source_tier_declared": src["tier"],
            "fetch_kind": src["kind"],
            "lang": src["lang"],
            "region": src["region"],
        })
    return out


# ---------------------------------------------------------------- 健康度


def is_cooled_down(src_id, health, settings):
    h = health.get(src_id) or {}
    until = h.get("disabled_until", 0)
    if until and time.time() < until:
        return True, h
    return False, h


def _record_ok(src_id, health):
    h = health.get(src_id) or {}
    h.update({
        "last_ok": int(time.time()),
        "fail_streak": 0,
        "last_error": "",
        "disabled_until": 0,
        "ok_count": h.get("ok_count", 0) + 1,
    })
    health[src_id] = h


def _record_fail(src_id, health, err, settings):
    h = health.get(src_id) or {}
    streak = h.get("fail_streak", 0) + 1
    h.update({
        "last_fail": int(time.time()),
        "fail_streak": streak,
        "last_error": str(err)[:200],
        "fail_count": h.get("fail_count", 0) + 1,
    })
    threshold = int(settings.get("fetch", {}).get("fail_streak_disable", 3))
    if streak >= threshold:
        cooldown = int(settings.get("fetch", {}).get("disable_cooldown_hours", 6))
        h["disabled_until"] = int(time.time()) + cooldown * 3600
    health[src_id] = h


# ---------------------------------------------------------------- 抓取编排


def build_tasks(topics, settings, only_topic=None):
    """
    根据领域配置生成抓取任务列表。
    每个任务 = (source, query, topic_id)
    """
    tasks = []
    seen = set()
    for t in topics:
        if not (t["enabled"] and not t["archived"]):
            continue
        if only_topic and t["id"] != only_topic:
            continue

        regions = t.get("regions") or ["cn", "intl"]
        want_zh = "cn" in regions and bool(t["query_zh"])
        want_en = "intl" in regions and bool(t["query_en"])

        # 1) query 型源：按领域关键词发起搜索
        for src in SOURCES:
            if src["kind"] != "query":
                continue
            queries = []
            if src["lang"] == "zh" and want_zh:
                queries = t["query_zh"]
            elif src["lang"] == "en" and want_en:
                queries = t["query_en"]
            if src["id"] == "bing_news":
                queries = (t["query_zh"] if want_zh else []) + (t["query_en"] if want_en else [])
            for q in queries:
                key = (src["id"], q, t["id"])
                if key in seen:
                    continue
                seen.add(key)
                tasks.append((src, q, t["id"]))

        # 2) stream 型源：全量拉取，后续按关键词归属领域
        for src in SOURCES:
            if src["kind"] != "stream":
                continue
            if src["region"] not in regions:
                continue
            key = (src["id"], "", t["id"])
            if key in seen:
                continue
            seen.add(key)
            tasks.append((src, "", t["id"]))

    # stream 型源对多个领域重复拉取无意义，去重为一次拉取 + 多领域归属
    dedup = {}
    for src, q, tid in tasks:
        k = (src["id"], q)
        dedup.setdefault(k, {"src": src, "q": q, "topics": set()})["topics"].add(tid)
    return [{"src": v["src"], "query": v["q"], "topics": sorted(v["topics"])}
            for v in dedup.values()]


def fetch_all(topics, settings, health, only_topic=None, verbose=True):
    """
    并发抓取所有任务。返回 (items, report)
    items: 解析后的条目（含 topics 候选）
    report: 各源成功/失败/冷却情况
    """
    tasks = build_tasks(topics, settings, only_topic)
    if not tasks:
        return [], {"tasks": 0, "ok": [], "failed": [], "cooled": [], "note": "无可用领域或未启用任何领域"}

    timeout = int(settings.get("fetch", {}).get("timeout", 12))
    workers = int(settings.get("fetch", {}).get("max_workers", 12))
    limit = int(settings.get("fetch", {}).get("per_query_limit", 25))

    report = {"tasks": len(tasks), "ok": [], "failed": [], "cooled": []}
    items = []

    def _absorb(text, src, task):
        parsed = parse_feed(text, src, limit=limit)
        for it in parsed:
            it["topics"] = list(task["topics"])
            it["matched_query"] = task["query"]
            items.append(it)
        return len(parsed)

    # 按源分组，先做冷却过滤
    groups = {}
    for task in tasks:
        groups.setdefault(task["src"]["id"], {"src": task["src"], "tasks": []})["tasks"].append(task)

    live = {}
    for sid, grp in groups.items():
        cooled, _ = is_cooled_down(sid, health, settings)
        if cooled:
            report["cooled"].append({"id": sid, "name": grp["src"]["name"]})
        else:
            live[sid] = grp

    # 生成待抓 URL；对 query 型源先探测一次，失败则跳过其全部任务，
    # 避免一个不可达的搜索源产生上百个必然失败的请求。
    planned = []
    for sid, grp in live.items():
        src = grp["src"]
        urls = []
        for t in grp["tasks"]:
            u = src["url"]
            if t["query"]:
                u = src["url"].format(q=urllib.parse.quote(t["query"]))
            urls.append((u, src, t))

        if src["kind"] == "query" and len(urls) > 1:
            probe_url, probe_src, probe_task = urls[0]
            text, err = http_get(probe_url, timeout=timeout, retries=0)
            if err:
                _record_fail(sid, health, err, settings)
                report["failed"].append({
                    "id": sid, "name": src["name"], "error": err,
                    "skipped": len(urls),
                })
                continue
            _record_ok(sid, health)
            n = _absorb(text, probe_src, probe_task)
            report["ok"].append({"id": sid, "name": src["name"], "count": n})
            planned.extend(urls[1:])
        else:
            planned.extend(urls)

    def worker(url, src, task):
        text, err = http_get(url, timeout=timeout, retries=0)
        return (src, task, text, err)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, u, s, t): (s, t) for u, s, t in planned}
        for fut in as_completed(futs):
            src, task, text, err = fut.result()
            if err:
                _record_fail(src["id"], health, err, settings)
                report["failed"].append({
                    "id": src["id"], "name": src["name"], "error": err,
                })
                continue
            _record_ok(src["id"], health)
            n = _absorb(text, src, task)
            report["ok"].append({"id": src["id"], "name": src["name"], "count": n})

    report["ok"].sort(key=lambda x: -x["count"])
    return items, report
