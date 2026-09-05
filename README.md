# News Radar

一套**可独立安装、独立运行**的定时资讯 skill：**到点自动抓取 → 机械层事实核查 → LLM 语义复核 → 生成日报存档 → 推送**；之后可就任一条资讯追问，系统回溯当日存档原文作答，而不是凭记忆编造。

> 核心设计：**所有可重复、确定性的工作都由脚本完成，LLM 只做语义判断。**
> 不依赖任何外部数据库 / 云服务 / API key，纯 Python 标准库实现，开箱即用。

---

## 目录结构

```
news-radar/
├── SKILL.md                        # skill 入口：When-to-use、命令表、流程、blueprint 定时声明
├── README.md                       # 本文件
├── LICENSE                         # MIT
├── scripts/
│   ├── newsctl.py                  # 统一 CLI 入口（脊梁）
│   └── _core/
│       ├── config.py               # 路径解析、领域模板、settings/topics 读写
│       ├── sources.py              # 信源注册表 + HTTP 抓取 + RSS 解析 + 失败冷却
│       ├── fetch.py                # 抓取编排：时间窗 → 领域归属 → 去重 → 落库
│       ├── credibility.py          # 事实核查机械层：信源分级/交叉验证/置信度评分
│       ├── store.py                # SQLite 存档层
│       ├── push.py                 # 推送中枢：筛选 / 质量分区排序 / 纯净文本渲染
│       ├── digest.py               # 日报生成（archive 溯源版 / push 纯净版）
│       └── query.py                # 事后问询检索
├── references/
│   ├── factcheck-rules.md          # 核查标准权威文档
│   └── source-tiers.md             # 信源分级细则
├── templates/…                     # 领域模板（新增领域蓝本）
└── data/                           # 运行时数据（见「数据与持久化」）
```

## 依赖

- **Python 3.8+**，仅用标准库（`sqlite3`、`urllib`、`json`、`xml` 等），**无第三方依赖、无 API key**。
- 抓取走公开 RSS 源（Google News 聚合、各机构/媒体官方 RSS）；某些网络环境可能无法直达
  Google News，脚本会自动退化为其余可用源，覆盖度下降但功能正常。

## 独立安装

把整个 `news-radar/` 目录放到你所在 agent 的 skills 目录即可。常见位置参考：

- Claude Code：`~/.claude/skills/news-radar/`
- Cursor：`~/.cursor/skills/news-radar/`
- 支持 skillhub 的 CLI：`skillhub install <作者>/news-radar --dir <skills目录>`

数据默认写入 skill 的 `data/`。若希望数据与 skill 分离（如只读安装目录），设置环境变量
`NEWS_RADAR_HOME=/path/to/your/data/dir` 指向任意目录即可，脚本会在此自动建库。

## 快速上手

```bash
CTL="python3 <本skill目录>/scripts/newsctl.py"

# 1. 首次初始化（自动生成默认领域与配置，建库）
$CTL init

# 2. 抓取并做机械层核查
$CTL fetch

# 3. 查看待 LLM 语义复核的条目（有则复核，用 verify 回写结论）
$CTL verify list

# 4. 生成存档日报（完整溯源，落盘 archive/ 永久备查）
$CTL digest

# 5. 生成纯净推送文本，直接发给最终接收者
$CTL push --print
```

之后随时追问当日/历史新闻：

```bash
$CTL query --q "央行降准" --date today
$CTL query --q "半导体" --date last7d --topic ai
```

所有命令支持 `--help` 查看参数；加 `--json` 输出结构化结果。完整命令表见 `SKILL.md`。

## 定时（每日简报）

脚本是"定时触发的载荷"，不是 cron 管理器，由宿主调度：

- **支持 blueprint/cron 的平台**：改 `SKILL.md` 顶部的 `metadata.hermes.blueprint.schedule`
  （默认 `0 8 * * *`，即每天 8:00）。平台加载/更新 skill 时自动重读。
- **其余环境**：用系统 cron / 任务计划在固定时间跑
  `python3 newsctl.py fetch && python3 newsctl.py digest && python3 newsctl.py push --print`。

## 领域管理

默认四个领域：`politics` 政治、`economy` 经济、`ai` AI、`livelihood` 民生大事。
新增领域与内置领域共用同一套数据结构与管道：

```bash
$CTL topic add --id energy --name 能源 --query-zh "光伏,储能,电网"
$CTL topic archive --id energy    # 归档 = 停抓取推送，历史保留
$CTL topic restore --id energy
```

## 数据与持久化

- 状态存在 `data/`（或 `$NEWS_RADAR_HOME`）：`news.db`（SQLite：items/digests/runs）、
  `topics.json`、`settings.json`、`archive/*.md`（每日日报）。
- **归档 ≠ 删除**：用 `topic archive` 停用领域，历史存档与问询检索仍可用。
- 想换环境 / 重新初始化：`init --force` 重建配置，历史存档不删除。

## 事实核查设计

分两层，职责不重叠：
- **机械层（脚本，确定性）**：信源分级 A/B/C/D、事件聚类与交叉验证计数、媒体集团归一化、
  时效/一手/低质信号检测、置信度评分与初判（≥72 publish / 45-71 review / <45 hold）、
  **单源封顶**（独立来源 <2 时最高到 review，必须过 LLM 复核）。
- **语义层（LLM）**：判断实质矛盾、识别观点冒充事实、核对数字出处、识别讽刺/爆料，
  综合回写 accept/reject/hold。

**可信央媒放松但不免检**：命中 `trusted_groups`（新华社、人民日报社、央视、中国新闻网）的
条目，非严重信号扣分降至 1/4，无严重信号时置信度有下限保护（66），但严重信号仍全额扣分、
单源仍受封顶约束——一律要过 LLM 复核。细则见 `references/factcheck-rules.md`。

## 隐私与合规

- 脚本只外连公开 RSS 信源抓取新闻，**不上传任何数据、不采集个人行为**。
- 无任何 API key / token / 遥测；所有数据保存在本地。
- 抓取内容做信源分级与事实核查后本地存档，用于生成简报与回答问询。

## License

MIT。见 `LICENSE`。
