#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
newsctl.py — news-radar 的统一命令行入口。

所有核心逻辑（抓取、分级、核查、归档、检索、日报）都实现在此脚本及
_core/ 模块中，调用方只负责传参，不自由发挥。

命令总览：
  init                     初始化数据目录
  topic  list|add|show|update|archive|restore|enable|disable
  fetch                    抓取当日资讯 → 事实核查机械层 → 存档
  verify list|set|batch    事实核查复核（LLM 语义层回写）
  digest                   生成当日日报（Markdown 存档，含完整溯源）
  push                     生成纯净推送文本（仅日期/模块编号/新闻内容，无链接无核查过程）
  topic  list|add|show|update|archive|restore|enable|disable|selfcheck
  query                    检索存档（问询场景）
  status                   存档与领域概览
  sources test|health      信源连通性测试与健康度

常用：
  python3 newsctl.py init
  python3 newsctl.py fetch --json
  python3 newsctl.py verify list
  python3 newsctl.py verify set --id 12 --verdict accept --note "两家通讯社互证"
  python3 newsctl.py digest
  python3 newsctl.py query --q "央行降准" --date today
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _core import config, digest, fetch as fetch_mod, query as query_mod, sources, store  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- helpers


def _out(text=""):
    print(text)


def _json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _die(msg, code=1):
    sys.stderr.write("错误：%s\n" % msg)
    sys.exit(code)


def _topics_or_die(home):
    topics = config.load_topics(home)
    if not topics:
        _die("未找到领域配置，请先运行 `newsctl.py init`。")
    return topics


def _require_init(home):
    p = config.paths(home)
    if not os.path.exists(p["topics"]):
        _die("尚未初始化。请先运行：python3 %s init"
             % os.path.abspath(__file__))
    return p


def _find_topic(topics, tid):
    t = config.get_topic(topics, tid)
    if not t:
        _die("领域不存在：%s。用 `topic list` 查看全部领域。" % tid)
    return t


# ---------------------------------------------------------------- init


def cmd_init(args):
    p = config.ensure_home(args.home)
    if os.path.exists(p["topics"]) and not args.force:
        _out("已初始化：%s" % p["home"])
        _out("（如需重置领域配置，加 --force；历史存档不会删除）")
    else:
        topics = [config.normalize_topic(t) for t in config.DEFAULT_TOPICS]
        config.save_topics(topics, args.home)
        config.save_settings(config.DEFAULT_SETTINGS, args.home)
        _out("初始化完成：%s" % p["home"])

    store.init_db(p["db"])
    _out("")
    _out("默认领域：")
    for t in config.load_topics(args.home):
        _out("  - %-12s %-8s 关键词 %d 条"
             % (t["id"], t["name"],
                len(t["query_zh"]) + len(t["query_en"])))
    _out("")
    _out("数据目录：%s" % p["home"])
    _out("日报存档：%s" % p["archive"])
    return 0


# ---------------------------------------------------------------- topic


def cmd_topic(args):
    home = args.home
    _require_init(home)
    topics = _topics_or_die(home)
    action = args.action

    if action == "list":
        if args.json:
            _json(topics)
            return 0
        _out("| 领域 ID | 名称 | 状态 | 关键词 | 每模块条数 | 排序 | 最低置信度 | 备注 |")
        _out("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for t in topics:
            if t["archived"]:
                state = "已归档"
            elif not t["enabled"]:
                state = "已停用"
            else:
                state = "启用中"
            _out("| `%s` | %s | %s | %d | %d | %s | %d | %s |"
                 % (t["id"], t["name"], state,
                    len(t["query_zh"]) + len(t["query_en"]),
                    t["max_items"], t["order_by"],
                    t["min_confidence"], t["note"] or "—"))
        _out("")
        _out("说明：归档 = 停止抓取与推送，但历史存档保留、仍可检索；"
             "`topic restore <id>` 可恢复，恢复后与默认领域用法完全一致。")
        return 0

    if action == "show":
        t = _find_topic(topics, args.id)
        _json(t) if args.json else _out(json.dumps(t, ensure_ascii=False, indent=2))
        return 0

    if action == "selfcheck":
        from _core import push as push_mod
        t = _find_topic(topics, args.id)
        conn = store.init_db(config.paths(home)["db"])
        g = push_mod.gather(date=args.date, home=home, only_topic=args.id, conn=conn)
        items = g["grouped_ranked"].get(t["id"], [])
        # 质量分区统计
        pri = t.get("priority_keywords") or []
        sup = t.get("suppress_keywords") or []
        buckets = {"priority": [], "neutral": [], "suppress": []}
        for it in items:
            if not digest.should_push(it):
                continue
            buckets[push_mod.classify_bucket(it, pri, sup)].append(it)
        # 检查顺序违规：是否存在 suppress 排在 priority 之前
        order = push_mod.rank(items, t, config.load_settings(home))
        pushable = [i for i in order if digest.should_push(i)]
        violation = False
        seen_priority = False
        for it in pushable:
            b = push_mod.classify_bucket(it, pri, sup)
            if b == "priority":
                seen_priority = True
            if b == "suppress" and seen_priority:
                violation = True
                break
        conn.close()
        _out("模块质量自检：`%s`（%s）" % (t["id"], t["name"]))
        _out("")
        _out("当日可用条目：%d（已排除存疑）" % len(pushable))
        _out("  置顶(priority)：%d 条" % len(buckets["priority"]))
        _out("  中性(neutral)：%d 条" % len(buckets["neutral"]))
        _out("  置底(suppress)：%d 条" % len(buckets["suppress"]))
        _out("")
        if buckets["priority"]:
            _out("置顶样本：")
            for it in buckets["priority"][:3]:
                _out("  · %s" % it["title"][:60])
        if buckets["suppress"]:
            _out("置底样本：")
            for it in buckets["suppress"][:3]:
                _out("  · %s" % it["title"][:60])
        _out("")
        if violation:
            _out("⚠ 排序违规：存在「置底内容」排在「置顶内容」之前，"
                 "通常是 order_by 排序把高置信度落地应用压到了模型发布之前。")
            _out("建议：调整 priority/suppress 关键词，或用 `topic update --id %s --order-by freshness`"
                 % t["id"])
        else:
            _out("排序正常：priority 内容稳定排在最前，suppress 内容在后。")
        _out("")
        _out("如需调整质量焦点：topic update --id %s --priority \"...\" --suppress \"...\"" % t["id"])
        return 0

    if action == "add":
        tid = (args.id or "").strip().lower()
        if not tid:
            _die("需要提供领域 ID，例如：topic add --id energy --name 能源")
        if config.get_topic(topics, tid):
            _die("领域已存在：%s。如需修改请用 topic update。" % tid)
        qz = [x.strip() for x in (args.query_zh or "").split(",") if x.strip()]
        qe = [x.strip() for x in (args.query_en or "").split(",") if x.strip()]
        if not qz and not qe:
            qz = [args.name or tid]
        t = config.new_topic(
            tid,
            name=args.name or tid,
            query_zh=qz,
            query_en=qe,
            regions=[x.strip() for x in (args.regions or "cn,intl").split(",") if x.strip()],
            min_confidence=args.min_confidence,
            max_items=args.max_items,
            note=args.note or "",
            priority_keywords=[x.strip() for x in (args.priority or "").split(",") if x.strip()],
            suppress_keywords=[x.strip() for x in (args.suppress or "").split(",") if x.strip()],
            focus=args.focus or "",
        )
        topics.append(t)
        config.save_topics(topics, home)
        _out("已新增领域：`%s`（%s）" % (t["id"], t["name"]))
        _out("")
        _out("该领域使用与内置领域完全相同的数据结构与处理管道：")
        _out("  - 抓取：query_zh / query_en 关键词驱动，多源并发")
        _out("  - 核查：信源分级 + 交叉验证 + 低质信号检测，同一套评分")
        _out("  - 推送：默认每模块 %d 条、排序 %s，与默认领域一致"
             % (t["max_items"], t["order_by"]))
        _out("  - 质量分区：命中 priority 置顶、suppress 置底（如已设置）")
        _out("  - 归档：写入同一张表，支持 archive / restore，历史不删除")
        _out("  - 检索：query --topic %s 即可回溯" % t["id"])
        _out("")
        _out("下一步：可运行 `fetch --topic %s` 试抓一次验证效果。" % t["id"])
        return 0

    if action == "update":
        t = _find_topic(topics, args.id)
        if args.name:
            t["name"] = args.name
        if args.query_zh:
            t["query_zh"] = [x.strip() for x in args.query_zh.split(",") if x.strip()]
        if args.query_en:
            t["query_en"] = [x.strip() for x in args.query_en.split(",") if x.strip()]
        if args.regions:
            t["regions"] = [x.strip() for x in args.regions.split(",") if x.strip()]
        if args.min_confidence is not None:
            t["min_confidence"] = args.min_confidence
        if args.max_items is not None:
            t["max_items"] = args.max_items
        if args.order_by is not None:
            t["order_by"] = args.order_by
        if args.priority is not None:
            t["priority_keywords"] = [x.strip() for x in args.priority.split(",") if x.strip()]
        if args.suppress is not None:
            t["suppress_keywords"] = [x.strip() for x in args.suppress.split(",") if x.strip()]
        if args.focus is not None:
            t["focus"] = args.focus
        if args.note is not None:
            t["note"] = args.note
        t = config.normalize_topic(t)
        for i, old in enumerate(topics):
            if old["id"] == t["id"]:
                topics[i] = t
        config.save_topics(topics, home)
        _out("已更新领域：`%s`" % t["id"])
        _out(json.dumps(t, ensure_ascii=False, indent=2))
        return 0

    if action in ("archive", "restore", "enable", "disable"):
        t = _find_topic(topics, args.id)
        if action == "archive":
            t["archived"] = True
            t["enabled"] = False
            t["archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            msg = ("已归档领域 `%s`：停止抓取与推送。"
                   "历史存档完整保留，仍可用 `query --topic %s` 检索；"
                   "需要时 `topic restore %s` 可恢复。" % (t["id"], t["id"], t["id"]))
        elif action == "restore":
            t["archived"] = False
            t["enabled"] = True
            t["archived_at"] = ""
            msg = ("已恢复领域 `%s`：重新纳入抓取与日报，"
                   "用法与默认领域一致。" % t["id"])
        elif action == "enable":
            t["enabled"] = True
            msg = "已启用领域 `%s`。" % t["id"]
        else:
            t["enabled"] = False
            msg = "已停用领域 `%s`（未归档，可随时 enable）。" % t["id"]
        t = config.normalize_topic(t)
        for i, old in enumerate(topics):
            if old["id"] == t["id"]:
                topics[i] = t
        config.save_topics(topics, home)
        _out(msg)
        return 0

    _die("未知的 topic 动作：%s" % action)


# ---------------------------------------------------------------- fetch


def cmd_fetch(args):
    p = _require_init(args.home)
    settings = config.load_settings(args.home)
    result = fetch_mod.run(date=args.date, only_topic=args.topic,
                           settings=settings, home=args.home)
    if args.json:
        _json(result)
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        _die(result.get("error", "抓取失败"))

    c = result["counts"]
    _out("抓取完成 · %s" % result["date"])
    _out("")
    _out("统计：抓取 %d 条 → 过期剔除 %d → 领域归属后 %d → 去重 %d → 新入库 %d"
         % (c["fetched"], c["dropped_stale"], c["after_topic_assign"],
            c["duplicates_removed"], c["stored_new"]))
    v = c["verdict"]
    _out("核查：可直接发布 %d ｜ 待复核 %d ｜ 存疑 %d"
         % (v["publish"], v["review"], v["hold"]))
    rep = result["sources"]
    _out("信源：成功 %d ｜ 失败 %d ｜ 冷却中 %d"
         % (len(rep.get("ok", [])), len(rep.get("failed", [])),
            len(rep.get("cooled", []))))
    if rep.get("failed"):
        _out("  失败源：")
        for f in rep["failed"][:8]:
            skipped = "，已跳过该源其余 %d 个请求" % f["skipped"] if f.get("skipped") else ""
            _out("    - %s：%s%s" % (f["name"], f["error"][:40], skipped))
        _out("  （单源失败不影响整体；连续失败会自动冷却，稍后重试；"
             "网络环境变化后自动恢复）")
    _out("")

    for tid, block in result["by_topic"].items():
        _out("## %s（%s）" % (block["topic_name"], tid))
        recs = block["recommended"]
        if not recs:
            _out("  无达到置信度门槛（%d）的条目。" % block["min_confidence"])
            _out("")
            continue
        for r in recs:
            _out("  [%s] (置信度 %d, %s 级, %s) %s"
                 % (r["id"], r["confidence"], r["tier"],
                    r["publisher"] or "未知来源", r["title"][:60]))
            if r["flags"]:
                _out("      信号：%s" % "; ".join(r["flags"]))
        _out("")

    review = [r for b in result["by_topic"].values()
              for r in b["recommended"] if r["verdict"] == "review"]
    if review:
        _out("待语义复核（需 LLM 判断是否发布）：")
        for r in review[:15]:
            _out("  id=%s  %s" % (r["id"], r["title"][:70]))
        _out("")
        _out("复核命令示例：verify set --id 12 --verdict accept --note \"两家独立通讯社互证\"")

    _out("")
    _out("下一步：完成复核后运行 `digest` 生成当日日报。")
    return 0


# ---------------------------------------------------------------- verify


def cmd_verify(args):
    p = _require_init(args.home)
    conn = store.init_db(p["db"])
    action = args.action

    if action == "list":
        rows = store.get_items(conn, date=args.date, limit=500)
        pending = [r for r in rows if r.get("verdict") == "review" and not r.get("llm_verdict")]
        pending.sort(key=lambda x: -(x.get("confidence") or 0))
        if args.json:
            _json(pending)
            return 0
        if not pending:
            _out("没有待复核条目。")
            return 0
        _out("待复核 %d 条（按置信度降序）：" % len(pending))
        _out("")
        for r in pending:
            _out("**id=%s**（%d/100，%s 级 · %s）" % (
                r["id"], r["confidence"] or 0, r.get("tier"), r.get("publisher")))
            _out("  %s" % r["title"])
            groups = r.get("independent_groups") or []
            _out("  独立来源 %d 家：%s" % (len(groups), "、".join(groups[:6]) or "无"))
            if r.get("flags"):
                _out("  信号：%s" % "; ".join(r["flags"]))
            _out("  %s" % r["url"])
            _out("")
        _out("复核：verify set --id <id> --verdict accept|reject|hold --note \"理由\"")
        return 0

    if action == "set":
        if not args.id:
            _die("需要 --id")
        if args.verdict not in ("accept", "reject", "hold"):
            _die("--verdict 只能是 accept / reject / hold")
        item = store.get_item(conn, args.id)
        if not item:
            _die("条目不存在：%s" % args.id)
        store.set_llm_verdict(conn, args.id, args.verdict, args.note or "")
        _out("已记录：id=%s → %s%s"
             % (args.id, args.verdict,
                ("（%s）" % args.note) if args.note else ""))
        _out("该结论已写入存档，日报与后续问询都会沿用。")
        return 0

    if action == "batch":
        # 批量回写，避免逐条调用产生大量往返
        payload = None
        if args.file:
            if not os.path.exists(args.file):
                _die("文件不存在：%s" % args.file)
            with open(args.file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        elif args.json:
            payload = json.loads(args.json)
        else:
            _die("需要 --file <路径> 或 --json '<数组>'")

        if isinstance(payload, dict):
            payload = payload.get("items") or payload.get("verdicts") or []
        if not isinstance(payload, list):
            _die("批量数据需为数组，格式：[{\"id\":1,\"verdict\":\"accept\",\"note\":\"理由\"}]")

        ok, fail = 0, []
        for entry in payload:
            try:
                iid = entry.get("id")
                verdict = entry.get("verdict")
                if iid is None or verdict not in ("accept", "reject", "hold"):
                    fail.append((iid, "verdict 无效：%s" % verdict))
                    continue
                if not store.get_item(conn, iid):
                    fail.append((iid, "条目不存在"))
                    continue
                store.set_llm_verdict(conn, iid, verdict, entry.get("note", "") or "")
                ok += 1
            except Exception as e:
                fail.append((entry.get("id"), str(e)[:80]))
        _out("批量复核完成：成功 %d 条，失败 %d 条。" % (ok, len(fail)))
        for iid, why in fail[:10]:
            _out("  - id=%s：%s" % (iid, why))
        if ok:
            _out("")
            _out("结论已写入存档。下一步运行 `digest` 生成日报。")
        return 0

    _die("未知的 verify 动作：%s" % action)


# ---------------------------------------------------------------- digest


def cmd_digest(args):
    p = _require_init(args.home)
    res = digest.build(date=args.date, home=args.home, only_topic=args.topic)
    if args.json:
        _json({"path": res["path"], "item_count": res["item_count"]})
        return 0
    if args.print:
        _out(res["text"])
    else:
        _out("日报已生成：%s" % res["path"])
        _out("收录 %d 条（存疑条目单独列在文末备查区，不进入正文推送）" % res["item_count"])
        _out("")
        _out("查看：加 --print 直接输出全文")
    return 0


# ---------------------------------------------------------------- push


def cmd_push(args):
    p = _require_init(args.home)
    res = digest.build(date=args.date, home=args.home,
                       only_topic=args.topic, mode="push")
    if args.json:
        _json({"path": res["path"], "item_count": res["item_count"]})
        return 0
    if args.print:
        _out(res["text"])
    else:
        _out("纯净推送已生成：%s" % res["path"])
        _out("收录 %d 条（仅含日期 / 模块编号 / 新闻内容，无链接、无核查过程）"
             % res["item_count"])
        _out("")
        _out("查看：加 --print 直接输出全文；cronjob 推送时即取本文件内容。")
    return 0


# ---------------------------------------------------------------- query


def cmd_query(args):
    p = _require_init(args.home)
    res = query_mod.search(
        args.q, date_expr=args.date, topic=args.topic, limit=args.limit,
        include_held=args.include_held, home=args.home)
    if args.json:
        _json(res)
        return 0
    _out("检索：%s" % (args.q or "(全部)"))
    _out("范围：%s ~ %s　领域：%s　命中 %d 条"
         % (res["date_from"] or "全部", res["date_to"] or "全部",
            args.topic or "全部", res["count"]))
    _out("")
    _out(query_mod.render(res["items"]))
    return 0


# ---------------------------------------------------------------- status


def cmd_status(args):
    p = _require_init(args.home)
    ov = query_mod.overview(home=args.home)
    topics = config.load_topics(args.home)
    health = config.load_health(args.home)
    if args.json:
        _json({"stats": ov, "topics": topics, "health": health})
        return 0

    _out("存档概览")
    _out("  数据目录：%s" % ov["home"])
    _out("  条目总数：%d" % ov["total_items"])
    _out("  日期范围：%s ~ %s" % (ov["date_min"] or "—", ov["date_max"] or "—"))
    _out("  日报份数：%d" % ov["digest_count"])
    _out("")
    _out("领域状态")
    for t in topics:
        if t["archived"]:
            state = "已归档"
        elif not t["enabled"]:
            state = "已停用"
        else:
            state = "启用中"
        _out("  %-12s %-8s %s" % (t["id"], t["name"], state))
    _out("")
    if ov.get("by_date"):
        _out("近 14 日入库")
        for d in ov["by_date"]:
            _out("  %s　%d 条" % (d["date"], d["count"]))
        _out("")
    bad = {k: v for k, v in (health or {}).items() if v.get("fail_streak")}
    if bad:
        _out("信源异常（连续失败）")
        for k, v in list(bad.items())[:10]:
            _out("  %-18s 连败 %d 次　%s"
                 % (k, v.get("fail_streak", 0), (v.get("last_error") or "")[:50]))
        _out("")
        _out("  超过阈值的源会自动冷却数小时后重试，无需手工处理。")
    return 0


# ---------------------------------------------------------------- sources


def cmd_sources(args):
    p = _require_init(args.home)
    settings = config.load_settings(args.home)
    action = args.action

    if action == "test":
        _out("测试 %d 个信源连通性（超时 %ss）…"
             % (len(sources.SOURCES), settings["fetch"]["timeout"]))
        _out("")
        results = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def probe(src):
            url = src["url"]
            if src["kind"] == "query":
                url = src["url"].format(q="test")
            text, err = sources.http_get(url, timeout=settings["fetch"]["timeout"], retries=0)
            n = len(sources.parse_feed(text, src, limit=5)) if text else 0
            return src, err, n

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(probe, s) for s in sources.SOURCES]
            for fut in as_completed(futs):
                src, err, n = fut.result()
                results.append((src["id"], src["name"], src["tier"], err, n))

        results.sort(key=lambda x: (x[3] is not None, x[0]))
        ok = [r for r in results if r[3] is None]
        bad = [r for r in results if r[3] is not None]
        for sid, name, tier, err, n in results:
            if err is None:
                _out("  [通] %-16s %-24s %s 级　解析到 %d 条" % (sid, name, tier, n))
            else:
                _out("  [断] %-16s %-24s %s 级　%s" % (sid, name, tier, err[:40]))
        _out("")
        _out("可用 %d / %d。" % (len(ok), len(results)))
        _out("说明：连通性取决于运行环境网络。断掉的源会被自动跳过并冷却，")
        _out("不影响其余源正常工作；换网络环境后会自动恢复。")
        if args.json:
            _json([{"id": r[0], "name": r[1], "tier": r[2],
                    "error": r[3], "items": r[4]} for r in results])
        return 0

    if action == "health":
        health = config.load_health(args.home)
        if args.json:
            _json(health)
            return 0
        if not health:
            _out("尚无健康度记录，先运行一次 fetch。")
            return 0
        _out("| 源 ID | 成功次数 | 失败次数 | 连败 | 最近错误 |")
        _out("| --- | --- | --- | --- | --- |")
        for sid, h in sorted(health.items()):
            _out("| %s | %d | %d | %d | %s |"
                 % (sid, h.get("ok_count", 0), h.get("fail_count", 0),
                    h.get("fail_streak", 0), (h.get("last_error") or "—")[:40]))
        return 0

    _die("未知的 sources 动作：%s" % action)


# ---------------------------------------------------------------- main


def build_parser():
    parser = argparse.ArgumentParser(
        prog="newsctl.py",
        description="news-radar：定时资讯搜集、事实核查、归档与问询检索")
    parser.add_argument("--home", default=None,
                        help="数据目录（默认取环境变量 NEWS_RADAR_HOME 或 <skill>/data）")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="初始化数据目录与默认领域")
    p_init.add_argument("--force", action="store_true", help="重置领域配置（不删历史存档）")
    p_init.set_defaults(func=cmd_init)

    # topic
    p_topic = sub.add_parser("topic", help="领域管理（新增/归档/恢复）")
    tsub = p_topic.add_subparsers(dest="action")
    p = tsub.add_parser("list", help="列出全部领域")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_topic)
    p = tsub.add_parser("show", help="查看单个领域")
    p.add_argument("--id", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_topic)
    p = tsub.add_parser("add", help="新增领域（与默认领域同构）")
    p.add_argument("--id", required=True, help="领域 ID，英文小写，如 energy")
    p.add_argument("--name", help="显示名称，如 能源")
    p.add_argument("--query-zh", dest="query_zh", help="中文关键词，逗号分隔")
    p.add_argument("--query-en", dest="query_en", help="英文关键词，逗号分隔")
    p.add_argument("--regions", default="cn,intl", help="覆盖地区：cn,intl")
    p.add_argument("--min-confidence", type=int, default=None)
    p.add_argument("--max-items", type=int, default=None, help="每模块推送条数上限（默认5）")
    p.add_argument("--order-by", dest="order_by", default=None,
                   choices=["confidence", "freshness", "tier"], help="模块内排序方式")
    p.add_argument("--priority", default="", help="置顶关键词，逗号分隔")
    p.add_argument("--suppress", default="", help="置底关键词，逗号分隔")
    p.add_argument("--focus", default="", help="该模块质量焦点描述")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_topic)
    p = tsub.add_parser("update", help="修改领域")
    p.add_argument("--id", required=True)
    p.add_argument("--name")
    p.add_argument("--query-zh", dest="query_zh")
    p.add_argument("--query-en", dest="query_en")
    p.add_argument("--regions")
    p.add_argument("--min-confidence", type=int, default=None)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--order-by", dest="order_by", default=None,
                   choices=["confidence", "freshness", "tier"])
    p.add_argument("--priority", default=None)
    p.add_argument("--suppress", default=None)
    p.add_argument("--focus", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_topic)
    for act, helptext in (("archive", "归档领域（停止抓取推送，保留历史）"),
                          ("restore", "恢复已归档领域"),
                          ("enable", "启用领域"),
                          ("disable", "停用领域")):
        p = tsub.add_parser(act, help=helptext)
        p.add_argument("--id", required=True)
        p.set_defaults(func=cmd_topic)
    p = tsub.add_parser("selfcheck", help="自检模块质量（priority/suppress 排序与覆盖）")
    p.add_argument("--id", required=True)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_topic)

    # fetch
    p_fetch = sub.add_parser("fetch", help="抓取当日资讯并存档")
    p_fetch.add_argument("--topic", default=None, help="只抓指定领域")
    p_fetch.add_argument("--date", default=None, help="归档日期，默认今天")
    p_fetch.add_argument("--json", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    # verify
    p_verify = sub.add_parser("verify", help="事实核查复核")
    vsub = p_verify.add_subparsers(dest="action")
    p = vsub.add_parser("list", help="列出待复核条目")
    p.add_argument("--date", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)
    p = vsub.add_parser("set", help="记录单条复核结论")
    p.add_argument("--id", required=True)
    p.add_argument("--verdict", required=True, choices=["accept", "reject", "hold"])
    p.add_argument("--note", default="", help="复核理由（会存入存档）")
    p.set_defaults(func=cmd_verify)
    p = vsub.add_parser("batch", help="批量回写复核结论（推荐，减少往返）")
    p.add_argument("--file", help="JSON 文件路径")
    p.add_argument("--json", dest="json", help='JSON 字符串，如 [{"id":1,"verdict":"accept","note":"..."}]')
    p.set_defaults(func=cmd_verify)

    # digest
    p_digest = sub.add_parser("digest", help="生成当日日报")
    p_digest.add_argument("--date", default=None)
    p_digest.add_argument("--topic", default=None)
    p_digest.add_argument("--print", dest="print", action="store_true",
                          help="直接输出全文")
    p_digest.add_argument("--json", action="store_true")
    p_digest.set_defaults(func=cmd_digest)

    # push
    p_push = sub.add_parser("push", help="生成纯净推送文本（日期/模块编号/新闻内容）")
    p_push.add_argument("--date", default=None)
    p_push.add_argument("--topic", default=None)
    p_push.add_argument("--print", dest="print", action="store_true",
                        help="直接输出全文（cronjob 推送时常加此项取正文）")
    p_push.add_argument("--json", action="store_true")
    p_push.set_defaults(func=cmd_push)

    # query
    p_query = sub.add_parser("query", help="检索存档（问询场景）")
    p_query.add_argument("--q", dest="q", default="", help="问题或关键词")
    p_query.add_argument("--date", default="today",
                         help="today / yesterday / YYYY-MM-DD / last7d / all")
    p_query.add_argument("--topic", default=None)
    p_query.add_argument("--limit", type=int, default=15)
    p_query.add_argument("--include-held", action="store_true",
                         help="包含存疑条目（默认不含）")
    p_query.add_argument("--json", action="store_true")
    p_query.set_defaults(func=cmd_query)

    # status
    p_status = sub.add_parser("status", help="存档与领域概览")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    # sources
    p_src = sub.add_parser("sources", help="信源管理")
    ssub = p_src.add_subparsers(dest="action")
    p = ssub.add_parser("test", help="测试全部信源连通性")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sources)
    p = ssub.add_parser("health", help="查看信源健康度")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sources)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    if not hasattr(args, "func"):
        parser.parse_args([args.cmd, "--help"])
        return 0
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
