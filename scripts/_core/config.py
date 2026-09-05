# -*- coding: utf-8 -*-
"""
config.py — 配置、路径解析、领域（topic）管理。

设计要点：
1. 所有可变状态放在 HOME 目录（默认 <skill>/data），skill 代码本身保持只读。
2. 内置默认领域与用户新增领域使用 *完全相同* 的数据结构与处理管道。
   DEFAULT_TOPICS 与 TOPIC_TEMPLATE 由同一个 normalize_topic() 归一化，
   因此新增领域在抓取、核查、归档、检索上的行为与默认领域逐字段一致。
3. normalize_topic() 是唯一的事实来源：缺失字段一律按模板补全，
   多余字段一律保留（不丢用户数据），从而保证体感一致且向后兼容。
"""

import json
import os

import sys
import time
from copy import deepcopy

SCHEMA_VERSION = 1

# ---------------------------------------------------------------- 路径解析


def skill_dir():
    """返回 skill 根目录（scripts/_core/ 的上两级）。"""
    # __file__ = <skill>/scripts/_core/config.py → 上溯三级
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_home():
    """
    数据目录优先级：
      1. 环境变量 NEWS_RADAR_HOME
      2. <skill_dir>/data
    """
    env = os.environ.get("NEWS_RADAR_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(skill_dir(), "data")


def paths(home=None):
    home = home or get_home()
    return {
        "home": home,
        "db": os.path.join(home, "news.db"),
        "topics": os.path.join(home, "topics.json"),
        "settings": os.path.join(home, "settings.json"),
        "archive": os.path.join(home, "archive"),
        "logs": os.path.join(home, "logs"),
        "health": os.path.join(home, "source_health.json"),
    }


def ensure_home(home=None):
    p = paths(home)
    for key in ("home", "archive", "logs"):
        os.makedirs(p[key], exist_ok=True)
    return p


# ---------------------------------------------------------------- 领域模板

# 领域模板：新增领域时以此为唯一蓝本。
# 任何新增字段必须同时加到模板和 normalize_topic() 的默认值里。
TOPIC_TEMPLATE = {
    "id": "",
    "name": "",
    "enabled": True,
    "archived": False,
    "builtin": False,
    "created_at": "",
    "updated_at": "",
    "archived_at": "",
    "query_zh": [],
    "query_en": [],
    "regions": ["cn", "intl"],
    "min_confidence": 50,
    # 每模块推送条数上限；默认与全局 push.default_max_items 一致（5 条）
    "max_items": 5,
    # 模块内排序方式：confidence（按置信度）| freshness（按时间）| tier（按信源等级）
    "order_by": "confidence",
    # 质量自检/调优：命中 priority_keywords 的条目置顶，命中 suppress_keywords 的置底
    "priority_keywords": [],
    "suppress_keywords": [],
    # 该模块关注重点的纯文本描述（供 selfcheck 与 LLM 判断质量时使用）
    "focus": "",
    "note": "",
}

# 内置默认领域。结构与 TOPIC_TEMPLATE 完全一致，
# 唯一区别是 builtin=True（仅用于 UI 展示，不参与任何流程分支）。
DEFAULT_TOPICS = [
    {
        "id": "politics",
        "name": "政治",
        "enabled": True,
        "archived": False,
        "builtin": True,
        "query_zh": [
            "政治", "外交", "国务院", "人大", "政协", "外交部", "国防部",
            "会谈", "访问", "峰会", "选举", "议会", "内阁", "总统", "首相",
            "联合国", "安理会", "制裁", "停火", "冲突", "战争", "双边",
            "多边", "领导人", "国事访问", "政策", "法规", "条例", "部署",
            "主席", "总理", "部长", "声明", "公报", "建交", "撤侨",
        ],
        "query_en": [
            "politics", "geopolitics", "diplomacy", "election", "sanctions",
            "summit", "government policy", "foreign policy", "parliament",
            "president", "prime minister", "ceasefire", "treaty", "nato",
            "security council", "state visit", "cabinet", "legislation",
        ],
        "regions": ["cn", "intl"],
        "min_confidence": 55,
        "max_items": 5,
        "note": "国内外政治、外交、政策与地缘动态",
    },
    {
        "id": "economy",
        "name": "经济",
        "enabled": True,
        "archived": False,
        "builtin": True,
        "query_zh": [
            "经济", "央行", "利率", "通胀", "通缩", "财政", "税收", "贸易",
            "GDP", "CPI", "PPI", "PMI", "股市", "A股", "港股", "美股",
            "汇率", "人民币", "美元", "降准", "降息", "货币", "关税",
            "出口", "进口", "投资", "消费", "房地产", "楼市", "期货",
            "黄金", "原油", "大宗商品", "营收", "财报", "IPO", "融资",
            "破产", "并购", "产能", "供应链", "就业", "失业",
        ],
        "query_en": [
            "economy", "central bank", "interest rate", "inflation",
            "fiscal policy", "trade", "GDP", "stock market", "currency",
            "recession", "tariff", "export", "import", "fed", "yields",
            "earnings", "merger", "bankruptcy", "oil price", "gold",
            "supply chain", "unemployment", "monetary policy",
        ],
        "regions": ["cn", "intl"],
        "min_confidence": 55,
        "max_items": 5,
        "note": "宏观经济、货币政策、贸易与市场关键数据",
    },
    {
        "id": "ai",
        "name": "AI",
        "enabled": True,
        "archived": False,
        "builtin": True,
        "query_zh": [
            "人工智能", "大模型", "AI", "算力", "芯片", "半导体", "GPU",
            "英伟达", "OpenAI", "GPT", "机器学习", "深度学习", "神经网络",
            "智能体", "Agent", "AIGC", "生成式", "数据中心", "算法",
            "训练", "推理", "自动驾驶", "机器人", "科技", "数字化",
            "开源模型", "参数", "多模态", "具身智能",
        ],
        "query_en": [
            "artificial intelligence", "AI model", "LLM", "machine learning",
            "semiconductor", "GPU", "data center", "generative AI", "nvidia",
            "openai", "deep learning", "neural network", "chatbot",
            "autonomous driving", "robotics", "chip", "foundation model",
        ],
        "regions": ["cn", "intl"],
        "min_confidence": 50,
        "max_items": 5,
        "order_by": "confidence",
        # 质量焦点：以「哪家公司/机构又发布了新模型、新技术、新开源、新融资」
        # 为第一优先级；「AI 在某行业的落地应用/赋能」排在此类公司新闻之后。
        "priority_keywords": [
            "发布", "推出", "推出新", "发布新", "上线", "开源", "新模型", "大模型发布",
            "GPT", "Claude", "Gemini", "Llama", "DeepSeek", "Qwen", "文心", "通义",
            "豆包", "混元", "Kimi", "智谱", "o1", "o3", "o4", "新版本", "新架构",
            "新芯片", "新算力", "融资", "估值", "发布模型", "官宣",
        ],
        "suppress_keywords": [
            "落地应用", "赋能", "数字化转型", "智能体应用", "应用案例", "场景落地",
            "助力", "改造", "覆盖行业", "协助", "提升效率", "降本",
        ],
        "focus": "关注头部 AI 公司与研究机构的新模型/新技术/开源/融资动态；"
                 "AI 落地应用类内容排在模型发布类之后。",
        "note": "人工智能技术、产业、算力与监管进展",
    },
    {
        "id": "livelihood",
        "name": "民生大事",
        "enabled": True,
        "archived": False,
        "builtin": True,
        "query_zh": [
            "民生", "社保", "医保", "养老", "住房", "房价", "楼市",
            "教育", "高考", "学校", "就业", "工资", "物价", "食品",
            "药品", "医疗", "医院", "公共卫生", "交通", "生育", "补贴",
            "低保", "乡村", "供水", "供暖", "燃气", "灾害", "应急",
            "安全生产", "消费维权", "退休", "户籍",
        ],
        "query_en": [
            "public welfare", "healthcare policy", "housing policy",
            "education policy", "employment", "cost of living",
            "food safety", "pension", "social security", "medicaid",
            "medicare", "minimum wage", "public health", "disaster",
        ],
        "regions": ["cn"],
        "min_confidence": 50,
        "max_items": 5,
        "note": "与公众生活直接相关的重大政策与事件",
    },
]

DEFAULT_SETTINGS = {
    "schema_version": SCHEMA_VERSION,
    "fetch": {
        "timeout": 12,
        "max_workers": 12,
        "per_query_limit": 25,
        "window_hours": 36,
        "fail_streak_disable": 3,
        # 连续失败达阈值后的基础冷却时长（小时）。之后若冷却结束仍失败，
        # 冷却时长按 cooldown_max_hours 封顶指数翻倍（见 sources._record_fail）
        "disable_cooldown_hours": 24,
        # 冷却时长封顶（小时，默认 7 天）：防止持续不可达源无限拉长冷却
        "cooldown_max_hours": 168,
        # 固定栏目源（stream）的条目需达到此关键词命中分才归入该领域，
        # 用于过滤无关内容；搜索型源（query）不受此限制
        "min_topic_score": 3,
    },
    "verify": {
        "auto_accept_score": 72,
        "review_score": 45,
        # 政治/经济等敏感领域的双源要求已由 credibility.score_item 的
        # 「单源封顶」规则统一强制（独立来源<2 → 最高 review），此处不再单独配置
    },
    "digest": {
        "include_unverified_section": True,
        "language": "zh",
    },
    # 推送阈值与排序中枢（push.py 读取此段）
    "push": {
        # 每模块默认推送条数；新增领域若不指定 max_items 即采用此值（=5）
        "default_max_items": 5,
        # 默认模块内排序方式：confidence | freshness | tier
        "default_order_by": "confidence",
        # 推送正文是否带「模块编号」
        "module_number": True,
        # 每条新闻摘要截断长度（字符）
        "summary_len": 200,
    },
    # 权威央媒：事实核查放松但仍保留（见 credibility.py）
    "trusted_groups": ["新华社", "人民日报社", "中国新闻网", "央视"],
    "retention_days": 0,  # 0 = 永不删除
}


# ---------------------------------------------------------------- 归一化


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def normalize_topic(raw, defaults=None):
    """
    把任意领域定义归一化为完整 schema。
    - 缺失字段用模板/默认值补全
    - 多余字段保留（不丢数据）
    - 保证内置与新增领域字段集合完全一致
    """
    out = {}
    for key, val in TOPIC_TEMPLATE.items():
        out[key] = deepcopy(val)
    src = dict(defaults or {})
    src.update(raw or {})
    for key, val in src.items():
        out[key] = val

    out["id"] = str(out.get("id") or "").strip().lower()
    out["name"] = str(out.get("name") or out["id"]).strip()
    out["enabled"] = bool(out.get("enabled", True))
    out["archived"] = bool(out.get("archived", False))
    out["builtin"] = bool(out.get("builtin", False))
    out["query_zh"] = [str(x).strip() for x in (out.get("query_zh") or []) if str(x).strip()]
    out["query_en"] = [str(x).strip() for x in (out.get("query_en") or []) if str(x).strip()]
    out["regions"] = [str(x).strip().lower() for x in (out.get("regions") or ["cn", "intl"])]
    try:
        out["min_confidence"] = int(out.get("min_confidence", 50))
    except (TypeError, ValueError):
        out["min_confidence"] = 50
    try:
        out["max_items"] = int(out.get("max_items", 5))
    except (TypeError, ValueError):
        out["max_items"] = 5
    out["min_confidence"] = max(0, min(100, out["min_confidence"]))
    out["max_items"] = max(1, min(100, out["max_items"]))
    allowed_order = ("confidence", "freshness", "tier")
    if out.get("order_by") not in allowed_order:
        out["order_by"] = "confidence"
    out["priority_keywords"] = [
        str(x).strip() for x in (out.get("priority_keywords") or []) if str(x).strip()]
    out["suppress_keywords"] = [
        str(x).strip() for x in (out.get("suppress_keywords") or []) if str(x).strip()]
    out["focus"] = str(out.get("focus") or "")
    out["note"] = str(out.get("note") or "")
    if not out.get("created_at"):
        out["created_at"] = _now_iso()
    out["updated_at"] = _now_iso()
    return out


def new_topic(topic_id, name=None, query_zh=None, query_en=None,
              regions=None, min_confidence=None, max_items=None, note=None,
              priority_keywords=None, suppress_keywords=None, focus=None):
    """基于模板创建一个全新领域（用于 topic add）。
    默认每模块 5 条，与内置领域体感完全一致（包括推送条数限制）。"""
    t = normalize_topic({
        "id": topic_id,
        "name": name or topic_id,
        "builtin": False,
        "query_zh": query_zh or [topic_id],
        "query_en": query_en or [],
        "regions": regions or ["cn", "intl"],
        "max_items": max_items if max_items is not None else DEFAULT_SETTINGS["push"]["default_max_items"],
        "priority_keywords": priority_keywords or [],
        "suppress_keywords": suppress_keywords or [],
        "focus": focus or "",
        "note": note or "",
    })
    if min_confidence is not None:
        t["min_confidence"] = min_confidence
    return t


# ---------------------------------------------------------------- 读写


def _read_json(path, fallback):
    if not os.path.exists(path):
        return deepcopy(fallback)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return deepcopy(fallback)


def _write_json(path, data):
    """原子写入：先写临时文件再 os.replace 覆盖。

    用 os.replace 而非 shutil.move —— 后者在某些环境下会触发删除动作
    （被安全策略拦截或产生竞态），os.replace 是纯粹的原子重命名。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)
    return path


def load_topics(home=None):
    p = paths(home)
    raw = _read_json(p["topics"], None)
    if raw is None:
        return [normalize_topic(t) for t in deepcopy(DEFAULT_TOPICS)]
    if isinstance(raw, dict):
        raw = raw.get("topics", [])
    return [normalize_topic(t) for t in raw]


def save_topics(topics, home=None):
    p = paths(home)
    return _write_json(p["topics"], {"schema_version": SCHEMA_VERSION, "topics": topics})


def get_topic(topics, topic_id):
    for t in topics:
        if t["id"] == str(topic_id).strip().lower():
            return t
    return None


def load_settings(home=None):
    p = paths(home)
    raw = _read_json(p["settings"], DEFAULT_SETTINGS)
    merged = deepcopy(DEFAULT_SETTINGS)
    for key, val in (raw or {}).items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def save_settings(settings, home=None):
    p = paths(home)
    return _write_json(p["settings"], settings)


def active_topics(topics):
    """参与抓取与日报的领域：启用 且 未归档。"""
    return [t for t in topics if t["enabled"] and not t["archived"]]


def load_health(home=None):
    p = paths(home)
    return _read_json(p["health"], {})


def save_health(health, home=None):
    p = paths(home)
    return _write_json(p["health"], health)
