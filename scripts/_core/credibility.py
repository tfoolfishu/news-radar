# -*- coding: utf-8 -*-
"""
credibility.py — 事实核查的「机械层」。

这一层做的是 *确定性、可重复* 的工作，必须由脚本完成，不交给 LLM 自由发挥：
  1. 信源权威性分级（A/B/C/D）
  2. 媒体集团归一化（同集团不同域名只算一个独立来源）
  3. 事件聚类与交叉验证计数（多少家独立媒体报了同一件事）
  4. 新鲜度 / 一手性 / 低质信号检测
  5. 综合置信度评分与建议处置

LLM 只负责语义层（是否存在实质矛盾、是否属于观点而非事实、数字是否可追溯）。
评分模型与阈值见 references/factcheck-rules.md。
"""

import math
import re
import time
import urllib.parse

# ---------------------------------------------------------------- 信源分级

# Tier A — 一手权威：政府、央行、监管机构、国际组织、官方通讯社、交易所
TIER_A_DOMAINS = {
    # 国际组织
    "un.org", "news.un.org", "imf.org", "worldbank.org", "who.int", "wto.org",
    "oecd.org", "ecb.europa.eu", "europa.eu", "eurostat.ec.europa.eu",
    "bis.org", "iea.org", "opec.org", "fatf-gafi.org",
    # 美国官方
    "federalreserve.gov", "sec.gov", "treasury.gov", "bls.gov", "bea.gov",
    "census.gov", "whitehouse.gov", "state.gov", "ustreas.gov", "ftc.gov",
    "commerce.gov", "energy.gov", "cdc.gov", "nih.gov", "nasa.gov",
    # 英国/欧盟/其他官方
    "gov.uk", "bankofengland.gov.uk", "ons.gov.uk", "bundesbank.de",
    "banque-france.fr", "esm.europa.eu",
    # 中国官方
    "gov.cn", "stats.gov.cn", "pbc.gov.cn", "mof.gov.cn", "ndrc.gov.cn",
    "mofcom.gov.cn", "miit.gov.cn", "moe.gov.cn", "nhc.gov.cn",
    "mohrss.gov.cn", "samr.gov.cn", "csrc.gov.cn", "safe.gov.cn",
    "customs.gov.cn", "sasac.gov.cn", "most.gov.cn",
    # 官方通讯社与党媒主渠道
    "xinhuanet.com", "news.cn", "xinhua.news.cn", "people.com.cn",
    "cctv.com", "chinadaily.com.cn", "govt.chinadaily.com.cn",
    # 国际通讯社
    "apnews.com", "reuters.com", "afp.com", "dpa.com", "kyodonews.net",
    "jiji.com", "tass.com", "bloomberg.com",
}

# Tier B — 主流权威媒体（有编辑部、署名、纠错机制）
TIER_B_DOMAINS = {
    "bbc.co.uk", "bbc.com", "ft.com", "wsj.com", "nytimes.com", "economist.com",
    "theguardian.com", "washingtonpost.com", "npr.org", "aljazeera.net",
    "aljazeera.com", "cnbc.com", "scmp.com", "nikkei.com", "asia.nikkei.com",
    "handelsblatt.com", "lemonde.fr", "faz.net", "spiegel.de", "zeit.de",
    "elpais.com", "corriere.it", "forbes.com", "axios.com", "politico.com",
    "thehill.com", "nature.com", "science.org", "thelancet.com", "nejm.org",
    # 中文主流财经/时政
    "caixin.com", "yicai.com", "jiemian.com", "cbnweek.com", "chinanews.com.cn",
    "bjnews.com.cn", "thepaper.cn", "chinatimes.com", "zaobao.com.sg",
    "stcn.com", "cnstock.com", "cs.com.cn", "xinhua08.com", "eeo.com.cn",
}

# 补充可信集团的域名映射（MEDIA_GROUPS 未单独列出的，这里补齐以便 trusted_groups 命中）
MEDIA_GROUPS_EXTRA = {
    "央视": ["cctv.com", "cctv.cn"],
}

# Tier C — 一般门户、聚合、垂直媒体
TIER_C_DOMAINS = {
    "sina.com.cn", "sina.com", "sohu.com", "163.com", "qq.com", "ifeng.com",
    "people.com", "huanqiu.com", "cet.com.cn", "ce.cn", "economicdaily.com.cn",
    "gmw.cn", "cnr.cn", "workercn.cn", "youth.cn", "rednet.cn",
    "36kr.com", "huxiu.com", "tmtpost.com", "iyiou.com", "leiphone.com",
    "infoq.cn", "csdn.net", "zhihu.com", "zhihuicloud.com",
    "cnbayarea.org.cn", "stdaily.com", "cnet.com", "techcrunch.com",
    "theverge.com", "wired.com", "arstechnica.com", "engadget.com",
    "zdnet.com", "cbsnews.com", "abcnews.go.com", "usatoday.com", "time.com",
    "newsweek.com", "independent.co.uk", "telegraph.co.uk", "dailymail.co.uk",
}

# Tier D — 自媒体 / 内容农场 / 无署名聚合（默认低可信）
TIER_D_PATTERNS = [
    "baijiahao.baidu.com", "toutiao.com", "toutiaostatic.com", "sohu.com/a/",
    "mp.weixin.qq.com", "weixin.qq.com", "weibo.com", "xiaohongshu.com",
    "douyin.com", "kuaishou.com", "medium.com", "substack.com", "blogspot.",
    "wordpress.com", "jianshu.com", "tianya.cn", "tieba.baidu.com",
]

# 媒体集团归一化：同一集团的不同域名/子域只计为一个独立来源
MEDIA_GROUPS = {
    "BBC": ["bbc.co.uk", "bbc.com"],
    "新华社": ["xinhuanet.com", "news.cn", "xinhua.news.cn"],
    "人民日报社": ["people.com.cn", "people.com"],
    "财新": ["caixin.com"],
    "第一财经": ["yicai.com"],
    "中国新闻网": ["chinanews.com.cn"],
    "新浪": ["sina.com.cn", "sina.com"],
    "搜狐": ["sohu.com"],
    "网易": ["163.com"],
    "腾讯": ["qq.com"],
    "凤凰": ["ifeng.com"],
    "澎湃": ["thepaper.cn"],
    "界面": ["jiemian.com"],
    "路透社": ["reuters.com"],
    "美联社": ["apnews.com"],
    "法新社": ["afp.com"],
    "彭博": ["bloomberg.com"],
    "金融时报": ["ft.com"],
    "华尔街日报": ["wsj.com"],
    "纽约时报": ["nytimes.com"],
    "华盛顿邮报": ["washingtonpost.com"],
    "经济学人": ["economist.com"],
    "卫报": ["theguardian.com"],
    "NPR": ["npr.org"],
    "半岛电视台": ["aljazeera.net", "aljazeera.com"],
    "CNBC": ["cnbc.com"],
    "南华早报": ["scmp.com"],
    "日经": ["nikkei.com", "asia.nikkei.com"],
    "证券时报": ["stcn.com"],
    "中国证券报": ["cs.com.cn"],
    "上证报": ["cnstock.com"],
}

# 官方机构关键词（用于识别一手来源）
OFFICIAL_URL_HINTS = [
    "/press/", "/pressrelease", "/newsroom", "/statement", "/notice",
    "/announcement", "/gonggao", "/zhengce", "/yaowen",
]

TIER_SCORE = {"A": 55, "B": 45, "C": 30, "D": 8}
TIER_LABEL = {
    "A": "一手权威（官方/通讯社/国际组织）",
    "B": "主流权威媒体",
    "C": "一般门户或垂直媒体",
    "D": "自媒体或聚合内容（低可信）",
}

# 严重信号：即便对可信央媒也不放宽，必须完整核查
_SERIOUS_FLAGS = {
    "anonymous:依赖匿名或未经具名的消息源",
    "opinion:观点或评论类内容，非事实陈述",
    "sensational:标题含情绪化表述",
    "column:疑似栏目聚合页，非单条新闻",
    "nopublisher:无法识别发布者",
}
# 央媒单条报道的置信度下限（放松但不免检：仍走交叉验证/时效/严重信号判定）
TRUSTED_FLOOR = 66


# ---------------------------------------------------------------- 工具


def normalize_domain(url_or_domain):
    if not url_or_domain:
        return ""
    d = url_or_domain.strip().lower()
    if "://" in d:
        d = urllib.parse.urlparse(d).netloc.lower()
    d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def domain_suffix_match(domain, candidates):
    """判断 domain 是否等于或属于候选域名的子域。"""
    for c in candidates:
        if domain == c or domain.endswith("." + c):
            return True
    return False


def classify_tier(domain):
    """返回 (tier, 理由)。"""
    d = normalize_domain(domain)
    if not d:
        return "D", "无法识别发布者域名"
    if domain_suffix_match(d, TIER_A_DOMAINS):
        return "A", "官方或权威一手来源"
    for pat in TIER_D_PATTERNS:
        if pat in d:
            return "D", "自媒体或聚合平台内容"
    if domain_suffix_match(d, TIER_B_DOMAINS):
        return "B", "主流权威媒体"
    if domain_suffix_match(d, TIER_C_DOMAINS):
        return "C", "一般门户或垂直媒体"
    # 未登记域名：按通用规则兜底
    if d.endswith(".gov") or d.endswith(".gov.cn") or d.endswith(".gov.uk"):
        return "A", "政府域名"
    if d.endswith(".edu") or d.endswith(".edu.cn") or d.endswith(".ac.uk"):
        return "B", "学术机构"
    if d.endswith(".org"):
        return "C", "机构站点（未登记）"
    return "C", "未登记来源，按一般媒体处理"


def media_group(domain):
    d = normalize_domain(domain)
    for group, doms in MEDIA_GROUPS.items():
        if domain_suffix_match(d, doms):
            return group
    for group, doms in MEDIA_GROUPS_EXTRA.items():
        if domain_suffix_match(d, doms):
            return group
    # 未登记：用注册主域作为集团标识
    parts = d.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return d or "unknown"


# ---------------------------------------------------------------- 低质信号

_SENSATIONAL_WORDS = [
    "震惊", "惊呆", "炸了", "突发！", "罕见", "独家！", "疯了", "彻底",
    "万万没想到", "速看", "刚刚！", "重磅！", "爆", "内幕",
    "shocking", "breaking!!!", "you won't believe", "insane", "unbelievable",
]
_OPINION_WORDS = [
    "评论", "观点", "社评", "述评", "分析", "解读", "展望", "预测", "猜想",
    "opinion", "editorial", "commentary", "analysis", "opinion:", "view",
    "perspective", "forecast", "predicts",
]
_ANONYMOUS_WORDS = [
    "知情人士", "消息人士", "匿名", "不愿具名", "据传", "网传", "疑似",
    "sources said", "people familiar", "anonymous", "reportedly", "allegedly",
    " rumor", "rumour",
]
_REPRINT_WORDS = ["转载", "原文来源", "本文来源", "编辑:", "责编:", "综合报道"]


# flag 前缀 -> 在 detect_flags 中的扣分（与 detect_flags 保持同步）
_FLAG_PENALTY = {
    "sensational": 10,
    "opinion": 6,
    "anonymous": 12,
    "reprint": 3,
    "thin": 4,
    "column": 15,
    "short": 10,
    "nodate": 8,
    "nopublisher": 10,
}


def _flag_penalty(flag):
    """根据 flag 文本反查其扣分（供可信央媒放松时使用）。"""
    if flag in _SERIOUS_FLAGS:
        # 严重信号的扣分取实际值在 detect_flags 中定义；这里直接返回其基础值
        return _FLAG_PENALTY.get(flag.split(":")[0], 0)
    return _FLAG_PENALTY.get(flag.split(":")[0], 0)


def detect_flags(item):
    """检测低质/风险信号。返回 (flags 列表, 扣分, 加分项, {"is_opinion","has_anon"})。"""
    flags = []
    penalty = 0
    bonus = 0
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    text = (title + " " + summary).lower()

    # 标题党
    for w in _SENSATIONAL_WORDS:
        if w.lower() in text:
            flags.append("sensational:标题含情绪化表述")
            penalty += 10
            break

    # 观点/评论（不是事实，需明确标注）
    is_opinion = False
    for w in _OPINION_WORDS:
        if w.lower() in title.lower():
            is_opinion = True
            break
    if is_opinion:
        flags.append("opinion:观点或评论类内容，非事实陈述")
        penalty += 6

    # 匿名/单一来源
    has_anon = False
    for w in _ANONYMOUS_WORDS:
        if w.lower() in text:
            has_anon = True
            break
    if has_anon:
        flags.append("anonymous:依赖匿名或未经具名的消息源")
        penalty += 12

    # 转载痕迹
    for w in _REPRINT_WORDS:
        if w in title or w in summary:
            flags.append("reprint:疑似转载内容")
            penalty += 3
            break

    # 无摘要 / 摘要过短
    if len(summary) < 30:
        flags.append("thin:缺少有效摘要")
        penalty += 4

    # 栏目聚合页 / 播报汇总（不是单条新闻，不应占据日报正文）
    if re.match(r"^\s*[【\[]", title) or any(
            w in title for w in ("播报", "快讯汇总", "日报】", "周报", "月报",
                                 "盘点", "一周", "早报", "晚报", "午报")):
        flags.append("column:疑似栏目聚合页，非单条新闻")
        penalty += 15

    # 标题过短，信息量不足
    if len(title) < 10:
        flags.append("short:标题过短，信息量不足")
        penalty += 10

    # 无发布时间
    if not item.get("published_ts"):
        flags.append("nodate:缺少发布时间")
        penalty += 8
    else:
        bonus += 3

    # 数字类断言（需可追溯原始出处）
    if re.search(r"\d+(\.\d+)?\s*(%|％|亿美元|万亿元|亿元|亿美元|billion|trillion|million)", title + " " + summary):
        flags.append("numeric:含具体数值，需核对原始出处")

    # 缺失发布者
    if not item.get("publisher_domain"):
        flags.append("nopublisher:无法识别发布者")
        penalty += 10

    return flags, penalty, bonus, {"is_opinion": is_opinion, "has_anon": has_anon}


def freshness_adjust(published_ts, now=None):
    """新鲜度加分/扣分。"""
    now = now or time.time()
    if not published_ts:
        return 0, "unknown"
    age_h = (now - published_ts) / 3600.0
    if age_h < 0:
        return 0, "future"
    if age_h <= 24:
        return 8, "24h内"
    if age_h <= 48:
        return 4, "48h内"
    if age_h <= 72:
        return 0, "72h内"
    return -8, "超过72小时"


def is_primary_source(url, domain):
    """是否为一手来源（官方原始发布）。"""
    d = normalize_domain(domain)
    if domain_suffix_match(d, TIER_A_DOMAINS):
        return True
    low = (url or "").lower()
    for hint in OFFICIAL_URL_HINTS:
        if hint in low:
            return True
    return False


# ---------------------------------------------------------------- 聚类

_CJK = r"\u4e00-\u9fff"
_PUNCT = re.compile(r"[^\w" + _CJK + r"]+")


def tokenize(text):
    """中英文混合分词：英文按词，中文按 bigram。"""
    if not text:
        return set()
    t = text.lower()
    t = _PUNCT.sub(" ", t)
    tokens = set()
    en = re.findall(r"[a-z0-9]{2,}", t)
    tokens.update(en)
    cjk_runs = re.findall(r"[" + _CJK + r"]{2,}", t)
    for run in cjk_runs:
        if len(run) < 2:
            continue
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def cluster_items(items, threshold=0.5):
    """
    把描述同一事件的条目聚为一类（并查集 + 倒排索引加速）。
    返回 cluster_id 列表（与 items 等长），以及 clusters 字典。
    """
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    token_sets = [tokenize((it.get("title") or "")) for i, it in enumerate(items)]

    # 倒排索引：token -> item 下标
    inv = {}
    for i, toks in enumerate(token_sets):
        for tok in toks:
            inv.setdefault(tok, []).append(i)

    # 只在共享 token 的候选对之间比较，避免 O(n^2)
    checked = set()
    for tok, idxs in inv.items():
        if len(idxs) > 60:  # 超高频 token 无区分度，跳过
            continue
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                i, j = idxs[a_pos], idxs[b_pos]
                if i > j:
                    i, j = j, i
                key = (i, j)
                if key in checked:
                    continue
                checked.add(key)
                ti, tj = token_sets[i], token_sets[j]
                if not ti or not tj:
                    continue
                inter = len(ti & tj)
                if inter < 3:
                    continue
                union_size = len(ti | tj)
                jac = inter / float(union_size)
                if jac >= threshold:
                    union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    cluster_ids = [0] * n
    clusters = {}
    for cid, (root, members) in enumerate(sorted(groups.items()), start=1):
        for i in members:
            cluster_ids[i] = cid
        clusters[cid] = members
    return cluster_ids, clusters


# ---------------------------------------------------------------- 评分


def corroboration_bonus(independent_groups):
    """交叉验证加分：独立媒体集团越多，可信度越高。"""
    n = len(independent_groups)
    if n <= 1:
        return 0
    if n == 2:
        return 18
    if n == 3:
        return 26
    return 32


def score_item(item, tier, independent_groups, flags_ctx, now=None, trusted=False):
    """
    计算置信度（0-100）与建议处置。
    trusted=True 时对权威央媒放松：降低非严重信号扣分，并对无严重信号的
    单条报道设置下限（TRUSTED_FLOOR），但仍完整保留交叉验证/时效性/严重信号判定。
    """
    base = TIER_SCORE.get(tier, 30)
    penalty, bonus = 0, 0
    reasons = []

    cb = corroboration_bonus(independent_groups)
    bonus += cb
    if cb:
        reasons.append("获 %d 家独立来源报道 (+%d)" % (len(independent_groups), cb))
    else:
        reasons.append("单一来源，未经交叉验证 (+0)")

    reasons.append("信源等级 %s (基准 %d)" % (tier, base))

    if is_primary_source(item.get("url"), item.get("publisher_domain")):
        bonus += 8
        reasons.append("指向一手原始出处 (+8)")

    fa, flabel = freshness_adjust(item.get("published_ts"), now)
    if fa:
        if fa > 0:
            bonus += fa
        else:
            penalty += abs(fa)
        reasons.append("时效性：%s (%+d)" % (flabel, fa))

    f_penalty, f_bonus = flags_ctx["penalty"], flags_ctx["bonus"]
    flags = flags_ctx["flags"]
    serious = [f for f in flags if f in _SERIOUS_FLAGS]
    if trusted:
        # 放松：非严重信号扣分大幅降低；严重信号仍保留较强扣分
        relaxed = 0
        for f in flags:
            if f in _SERIOUS_FLAGS:
                continue
            # 估算该 flag 在 detect_flags 中的扣分（与那里保持一致）
            relaxed += _flag_penalty(f)
        f_penalty = sum(_flag_penalty(f) for f in serious) + int(relaxed * 0.25)
        reasons.append("权威央媒：非严重信号扣分已放松")
    penalty += f_penalty
    bonus += f_bonus
    for f in flags:
        reasons.append("信号：%s" % f)

    score = int(max(0, min(100, base + bonus - penalty)))

    if trusted and not serious:
        if score < TRUSTED_FLOOR:
            reasons.append("央媒下限保护：%d → %d" % (score, TRUSTED_FLOOR))
            score = TRUSTED_FLOOR

    if score >= 72:
        verdict = "publish"
    elif score >= 45:
        verdict = "review"
    else:
        verdict = "hold"

    # 单源封顶：独立来源不足 2 家时，无论分数多高，verdict 最高只到 review，
    # 必须经 LLM 语义复核后才可能进入正文（落实 factcheck-rules.md §一.2/§三）
    if verdict == "publish" and len(independent_groups) < 2:
        verdict = "review"
        reasons.append("单源封顶：独立来源不足 2 家，publish 降为 review")

    return {
        "confidence": score,
        "verdict": verdict,
        "tier": tier,
        "independent_sources": len(independent_groups),
        "independent_groups": sorted(independent_groups),
        "reasons": reasons,
        "flags": flags_ctx["flags"],
        "is_opinion": flags_ctx["is_opinion"],
        "has_anon": flags_ctx["has_anon"],
    }


def assess(items, now=None, trusted_groups=None):
    """
    对一批条目做完整的事实核查机械层评估。
    就地写入 item["_assess"]，并返回统计。
    trusted_groups：可信信源集团集合（如 {"新华社","人民日报社"}），
                    命中则对该条放松惩罚并设下限，但仍保留核查。
    """
    now = now or time.time()
    trusted_groups = set(trusted_groups or set())
    cluster_ids, clusters = cluster_items(items)

    # 先给每条定 tier / 集团 / flags
    for idx, it in enumerate(items):
        domain = it.get("publisher_domain") or normalize_domain(it.get("url"))
        it["publisher_domain"] = domain
        tier, tier_reason = classify_tier(domain)
        it["tier"] = tier
        it["tier_reason"] = tier_reason
        it["media_group"] = media_group(domain)
        flags, penalty, bonus, ctx = detect_flags(it)
        it["_flags_ctx"] = {
            "flags": flags, "penalty": penalty, "bonus": bonus,
            "is_opinion": ctx["is_opinion"], "has_anon": ctx["has_anon"],
        }

    # 每个簇统计独立媒体集团
    for cid, members in clusters.items():
        groups = set()
        for i in members:
            g = items[i].get("media_group")
            if g:
                groups.add(g)
        for i in members:
            items[i]["cluster_id"] = cid
            items[i]["cluster_size"] = len(members)
            items[i]["independent_groups"] = groups
            trusted = items[i].get("media_group") in trusted_groups
            a = score_item(items[i], items[i]["tier"], groups,
                           items[i]["_flags_ctx"], now=now, trusted=trusted)
            items[i]["_assess"] = a
            items[i]["confidence"] = a["confidence"]
            items[i]["verdict"] = a["verdict"]

    stats = {
        "total": len(items),
        "clusters": len(clusters),
        "publish": sum(1 for i in items if i.get("verdict") == "publish"),
        "review": sum(1 for i in items if i.get("verdict") == "review"),
        "hold": sum(1 for i in items if i.get("verdict") == "hold"),
    }
    return items, stats
