---
name: news-radar
description: 定时搜集国内外政治、经济、AI、民生等领域资讯，自动做事实核查、归档备查，并支持就当日资讯追问。当用户要求每日/每周新闻简报、定时推送资讯、新增或归档新闻关注领域、查询某天某条新闻的详情与出处，或就"今天有什么新闻""那条XX新闻怎么回事""XX领域最近消息"提问时使用本技能。
version: 1.0.0
author: news-radar
license: MIT
metadata:
  # 本段供支持 cron/blueprint 的平台（如 Hermes）读取：到点后按 prompt 执行每日资讯流程。
  # 若你的平台不支持该字段，可忽略，改用外部 cron/任务调度器在固定时间运行即可。
  hermes:
    tags: [news, briefing, fact-check, archive, automation, 新闻, 资讯, 事实核查]
    category: research
    requires_toolsets: [terminal]
    blueprint:
      schedule: "0 8 * * *"
      deliver: origin
      prompt: "执行 news-radar 每日资讯流程：1) 运行 `newsctl.py fetch` 抓取当日各启用领域资讯并做事实核查机械层；2) 运行 `newsctl.py digest` 生成带完整溯源的存档日报（备查与后续问询用）；3) 运行 `newsctl.py push --print` 生成纯净推送文本，**直接把 push 命令输出的内容作为今日简报推送给我**（纯净格式：仅日期、模块编号、新闻内容，不要附带链接与核查过程）。若 fetch 后出现待复核条目，先按 factcheck-rules 做语义复核回写再推送。"
---

# News Radar — 定时资讯搜集 · 事实核查 · 归档问询

一套可定时运行的资讯系统：**到点自动抓取 → 机械层事实核查 → LLM 语义复核 → 生成日报存档 → 推送**；
之后可就任一条资讯追问，系统回溯当日存档原文作答，而不是凭记忆编造。

核心原则：**所有可重复、确定性的工作都由脚本完成，LLM 只做语义判断。**
不要自己写抓取、去重、评分、归档逻辑，一律调用 `newsctl.py`。

## When to Use

- 用户要求定时/每日/每周的新闻简报或资讯推送
- 用户要求新增、修改、归档或恢复一个关注领域
- 用户就某条当日/历史资讯追问详情、出处、影响
- 用户问"今天有什么重要新闻""最近 AI 领域有什么事"
- 定时触发（blueprint / cron）执行每日抓取

## Quick Reference

先设定路径变量（后续命令都用它）。脚本位于 skill 根目录的 `scripts/newsctl.py`，
数据默认落在 skill 的 `data/`，也支持用环境变量 `NEWS_RADAR_HOME` 指向别处。

```bash
# 把下面 SKILL_DIR 替换为本 skill 的实际安装目录即可
CTL="python3 <本skill安装目录>/scripts/newsctl.py"
# 部分平台会注入 skill 目录环境变量（如 Hermes 的 $HERMES_SKILL_DIR），有则直接：
# CTL="python3 ${HERMES_SKILL_DIR}/scripts/newsctl.py"
```

| 目的 | 命令 |
| --- | --- |
| 首次初始化 | `$CTL init` |
| 定时抓取（每日） | `$CTL fetch` |
| 查看待语义复核 | `$CTL verify list` |
| 批量回写复核结论（推荐） | `$CTL verify batch --json '[{"id":12,"verdict":"accept","note":"两家独立通讯社互证"}]'` |
| 单条回写复核结论 | `$CTL verify set --id 12 --verdict accept --note "两家独立通讯社互证"` |
| 生成存档日报 | `$CTL digest --print` |
| 生成纯净推送 | `$CTL push --print` |
| 问询检索 | `$CTL query --q "央行降准" --date today` |
| 新增领域 | `$CTL topic add --id energy --name 能源 --query-zh "光伏,储能,电网"` |
| 调整推送阈值/排序 | `$CTL topic update --id ai --max-items 6 --order-by freshness --priority "发布新,开源,新模型" --suppress "落地应用,赋能"` |
| 模块质量自检 | `$CTL topic selfcheck --id ai` |
| 归档领域 | `$CTL topic archive --id energy` |
| 恢复领域 | `$CTL topic restore --id energy` |
| 查看状态 | `$CTL status` |
| 信源体检 | `$CTL sources test` |

任意命令加 `--json` 得到结构化输出；加 `--help` 查看全部参数。

## 流程 A：定时抓取并推送（cronjob 触发）

严格按此顺序执行，**不要跳步、不要自行改写任何一步的产物**。

### 1. 初始化（仅首次）

```bash
$CTL init
```

已初始化则跳过。若数据目录被清空过，重新执行即可（不删除历史存档）。

### 2. 抓取

```bash
$CTL fetch
```

脚本完成：多源并发抓取 → 时间窗过滤 → 领域归属 → 事实核查机械层（信源分级、
交叉验证、低质信号检测、置信度评分）→ 去重 → SQLite 落库。

输出会给出统计、各领域推荐条目，以及**待语义复核清单**。

### 3. 语义复核（这一步必须由 LLM 做）

```bash
$CTL verify list          # 列出待复核条目及证据
```

对每条待复核条目，依据 `references/factcheck-rules.md` 判断，然后**回写结论**。
条目较多时优先用批量命令，一次调用完成全部回写：

```bash
$CTL verify batch --json '[
  {"id":12,"verdict":"accept","note":"两家独立通讯社互证"},
  {"id":15,"verdict":"reject","note":"单一匿名来源，未获证实"},
  {"id":18,"verdict":"hold","note":"数据待核对原始出处"}
]'
```

单条回写：

```bash
$CTL verify set --id 12 --verdict accept --note "两家独立通讯社互证"
```

判定口径：

- `accept` — 内容属实且可追溯：来源可信、关键事实有据、非观点冒充事实
- `reject` — 存在实质问题：事实无法核实、单一匿名来源、观点冒充事实、数据无出处
- `hold` — 暂缓：需要更多信息才能判断，或涉及发展中事件

**复核理由必须写**，它会存入存档并在日报与后续问询中原样呈现。

关于工作量的说明：默认只有单一来源、置信度中等的条目才会进入复核清单。
`hold`（置信度 < 45）的条目已由脚本自动隔离到日报存疑区，**不需要**逐条复核。

### 4. 生成存档日报（备查）

```bash
$CTL digest --print
```

存档日报写入 `<数据目录>/archive/YYYY-MM-DD.md` 永久存档。它**每条都带来源、信源等级、
发布时间、原始链接、交叉验证情况与置信度**，是后续 `query` 问询能回溯原文的依据。
cronjob 也会生成它，但不要把它直接作为推送正文发给用户。

### 5. 生成纯净推送并发送

```bash
$CTL push --print
```

纯净推送是专门给最终接收者看的版本，**只含「日期 / 模块编号 / 新闻内容」，不含链接、
不含事实核查过程**。其筛选与排序逻辑与存档日报完全一致（同一份 `push.gather` 数据），
只是呈现层做了裁剪。把 `push` 的输出作为今日简报推送给用户即可。

> 为什么要两份？存档日报是"给 AI 和人工复核看的完整证据链"，纯净推送是"给用户看的简报"。
> 二者同源，避免用户被链接和核查过程干扰，同时保留可追溯能力。

## 流程 B：就资讯追问（普通问询）

用户看过日报后追问时，**必须回溯存档作答，不得凭记忆回答**。

```bash
$CTL query --q "用户问题或关键词" --date today --limit 10
```

日期参数：`today`（默认）、`yesterday`、`YYYY-MM-DD`、`last7d`、`all`。
限定领域加 `--topic <领域ID>`。

作答要求：

1. 以检索到的原文为准，不补充存档之外的信息
2. 每条结论标注来源与信源等级
3. 若该条目是单一来源，明确说明"未经独立核实"
4. 检索无结果时，直接说存档中没有，并建议换关键词或放宽日期范围
   （换成 `--date last7d` 或 `--date all`），**不要编造**

追问"为什么""有什么影响"这类分析性问题时，先取存档原文，再在其基础上分析，
并明确区分"存档中的事实"与"你的推断"。

## 领域管理：新增与归档

默认四个领域：`politics` 政治、`economy` 经济、`ai` AI、`livelihood` 民生大事。

新增领域与内置领域**共用同一套数据结构与处理管道**——同一个模板、同一条抓取链路、
同一套核查评分、同一张存档表、同一种检索方式。因此新增领域的使用体感与默认领域完全一致。

```bash
$CTL topic add --id energy --name 能源 \
  --query-zh "能源,电力,光伏,风电,核电,煤炭,原油,天然气,储能,电网" \
  --query-en "energy,power grid,solar,wind,nuclear,coal,oil,gas,battery"
```

关键词填写要点：

- 中英文各填 8~20 个，覆盖该领域高频词与政策/机构名称
- 领域关键词用于**领域归属判定**与**搜索型源的查询词**，直接决定召回质量
- 短英文缩写（AI、GDP）会自动按词边界匹配，不会误命中 raise、against 之类

归档与恢复：

```bash
$CTL topic archive --id energy    # 停止抓取与推送，历史完整保留
$CTL topic restore --id energy    # 恢复，用法与默认领域无差别
```

**归档 = 停止抓取与推送，绝不删除历史**。归档领域的存档数据仍可被 `query --topic <id>`
检索到。不要为了满足"归档"需求而删库或删表。

## 推送阈值与排序控制

推送"推几条、按什么顺序"由统一中枢 `scripts/_core/push.py` 决定，便于集中测试、保证
存档版与推送版体感一致。相关参数分布在两个地方：

- **全局默认**：`<数据目录>/settings.json` 的 `push` 段
  - `default_max_items`：每个模块默认推送条数（默认 **5**）
  - `default_order_by`：模块内默认排序（`confidence` 置信度 / `freshness` 时间 / `tier` 信源等级）
  - `module_number`：推送正文是否显示「模块 1 / 模块 2」编号
  - `summary_len`：每条摘要截断长度（字符）
- **每模块覆盖**：`topic` 上的字段，新增领域与内置领域完全同构
  - `max_items`：该模块每日推送条数上限（默认取全局 5，可改）
  - `order_by`：该模块排序方式（覆盖全局默认）
  - `priority_keywords` / `suppress_keywords`：质量分区——命中 priority 的条目**置顶**，
    命中 suppress 的**置底**，其余居中（每个分区内部再按 order_by 排序）
  - `focus`：该模块质量焦点的纯文本描述

### 调整示例

```bash
# 把 AI 模块每日推送从 5 条提到 6 条，并改成按时间排序
$CTL topic update --id ai --max-items 6 --order-by freshness

# 让 AI 模块"公司发布新模型"类置顶、"落地应用/赋能"类置底（与默认内置一致，可微调）
$CTL topic update --id ai --priority "发布新,开源,新模型,GPT,Claude,Gemini,DeepSeek" \
                        --suppress "落地应用,赋能,数字化转型"

# 新增一个模块，默认就是每模块 5 条、排序 confidence，与内置完全一致
$CTL topic add --id energy --name 能源 --query-zh "光伏,储能,电网"
```

> 排序是确定性、可重复的：先按 priority/neutral/suppress 分桶，桶内再按 order_by 降序，
> 三桶依次拼接。**不会丢条目、不会产生重复、也不会把置底内容排到置顶之前**。
> 若担心顺序出错，先跑 `topic selfcheck` 自检（见下）。

### 模块质量自检与调优

当一个模块产出质量不达标（如 AI 模块混入过多"落地应用"、把公司新模型新闻压在后面），
可在使用者要求下**自检并给出调优建议**（不自动改配置，避免误伤）：

```bash
$CTL topic selfcheck --id ai
```

自检会报告：当日可用条目数、priority/neutral/suppress 三档分布、是否有"置底内容排在
置顶内容之前"的违规；并提示用 `topic update` 调整 `priority/suppress` 或 `order_by`。
确认方向后，LLM 用 `topic update` 落地调整——这就是"使用者的要求下自检更新"的闭环。
（如果你希望自检后**自动**写入建议的 priority/suppress，告诉我，我再加一个
`--auto-tune` 开关。）

## 权威央媒：事实核查放松但不免检

对**新华社、人民日报社、中国新闻网、央视**等可信信源集团（见 `settings.json` 的
`trusted_groups`），机械层会适当放松惩罚：

- 非严重信号（标题党、无摘要、转载痕迹等）的扣分降至 1/4；
- 无任何严重信号的单条报道，置信度设有**下限保护**（默认 66），保证其能进入推送；
- **仍完整保留**交叉验证、时效性、一手性判定，以及所有**严重信号**的强惩罚：
  匿名信源、观点冒充事实、情绪化标题、栏目聚合页、无法识别发布者——这些对央媒同样严查。

也就是说：央媒消息"默认更可信、更少被误伤"，但只要出现严重信号或与其他来源矛盾，
一样会被隔离到存疑区，不会无条件放行。

## 关于定时执行的如实说明

**本 skill 的脚本是"定时触发的载荷"，不是 cron 的管理器。** 定时由宿主平台/你的调度器
在外部驱动，脚本只负责到点后被触发后的一次性流程（`fetch → digest → push`）。

- **支持 blueprint/cron 的平台**（如 Hermes）：在本文件顶部的 `metadata.hermes.blueprint`
  里声明 `schedule`（cron 表达式）。改这个字段即可调整节奏，平台加载/更新时重新读取，
  **改动集中在一处、无需碰脚本**。
- **其余环境**：用系统 cron / 任务计划程序 / 你所在 agent 的定时能力，在固定时间跑
  `newsctl.py fetch && newsctl.py digest && newsctl.py push --print` 即可，效果等同。
- **本 skill 不会、也不应在运行时增删改自己的 cron**——那是宿主职责，脚本越权自管定时
  既脆弱又有安全风险（如误删其他定时）。
- **触发后的事全由脚本负责**：一条龙已实测稳定。

若要"按不同节奏跑不同模块"（例如 AI 盘中多次、民生每日一次），在 `blueprint` 里拆成
多条 `schedule`，每条 prompt 指定不同 `--topic` 参数即可。

## 事实核查：脚本与 LLM 的分工

**脚本负责（确定性、已内置，不要重复实现）：**

- 信源分级 A/B/C/D（见 `references/source-tiers.md`）
- 事件聚类与交叉验证计数（多少家独立媒体报了同一件事）
- 媒体集团归一化（同集团不同域名只算一个独立来源）
- 时效性、一手性、低质信号检测（标题党、匿名信源、栏目聚合页、无出处数字）
- 置信度评分（0-100）与初判：≥72 可直接发布，45-71 待复核，<45 存疑

**LLM 负责（语义层，脚本无法替代）：**

- 判断多条报道之间是否存在**实质矛盾**（而非措辞差异）
- 识别**观点/评论/预测**被包装成事实陈述
- 判断数字是否可追溯到原始出处
- 识别讽刺、反讽、泄露文件、未经证实的爆料
- 综合判定 accept / reject / hold 并写出理由

评分模型与判定细则见 `references/factcheck-rules.md`。

## Pitfalls

- **不要自己写抓取或解析代码**。新增信源应改 `scripts/_core/sources.py` 的 `SOURCES`
  注册表（一个 dict 条目），而不是临时写脚本。
- **Windows 上用 `python` 而非 `python3`**，若命令不存在先试另一个。
- **信源大面积失败是网络环境所致，不是 bug**。脚本会自动跳过并冷却失败源，
  换环境后自动恢复。可用 `sources test` 确认当前环境哪些源可用。
- **不要跳过 `verify set` 直接生成日报**。复核结论会写入存档，是后续问询可引用的依据。
- **不要删除存档**。归档领域用 `topic archive`，不要用删文件的方式"停用"领域。
- **搜索型源（Google News）在部分网络环境不可达**。此时系统自动退化为固定栏目源抓取，
  覆盖度会下降但仍可用；日报里会注明实际生效的信源。
- **抓取的条目必须在时间窗内**（默认 36 小时）。若日报内容偏少，先检查
  `status` 看入库量，再考虑放宽 `settings.json` 里的 `window_hours`。
- **同一事件可能被多家媒体报道**，这是交叉验证的加分项而非重复，脚本已自动聚类。

## Verification

```bash
$CTL status          # 条目总数、日期范围、各领域状态、信源异常
$CTL topic list      # 领域是否启用/归档
$CTL sources test    # 当前网络环境下哪些信源可用
```

一次完整流程跑通的标志：`fetch` 有新条目入库 → `verify list` 有可复核内容 →
`digest` 生成的存档日报里各领域内容**互不相同**且均带来源与置信度 →
`push` 生成的纯净推送按模块编号陈列、每模块不超过 `max_items` 条、无链接无核查过程 →
`query` 能用日报中的关键词检索到同一条并给出带出处的回答。

阈值/排序自检：对重点模块跑 `topic selfcheck --id <id>`，确认 priority 内容稳定在最前、
suppress 内容在后、每模块条数符合 `max_items`。

若某领域日报内容与另一个领域完全相同，说明领域归属出了问题，检查该领域的
关键词是否与其他领域高度重叠。
