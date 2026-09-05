# 信源分级表

本文件记录分级依据与媒体集团归属规则。代码实现在
`scripts/_core/credibility.py`，两处需保持一致。

分级的意义：**发布者的身份与追责机制**决定权威性，
与文章语气、排版、网站美观程度无关。

---

## 分级标准

| 级别 | 含义 | 判定依据 | 基准分 |
| --- | --- | --- | --- |
| **A** | 一手权威 | 内容的原始发布者，且发布主体对内容负责 | 55 |
| **B** | 主流权威媒体 | 有常设编辑部、明确署名、公开纠错机制 | 45 |
| **C** | 一般门户/垂直媒体 | 有编辑但机制不透明，常见转载与二次加工 | 30 |
| **D** | 自媒体/聚合内容 | 无编辑审核、无署名、无法追责 | 8 |
| **AGG** | 聚合器 | 不定级，需按条目的原始发布者重定向 | — |

---

## A 级：一手权威

**国际组织**
`un.org`、`imf.org`、`worldbank.org`、`who.int`、`wto.org`、`oecd.org`、
`ecb.europa.eu`、`europa.eu`、`bis.org`、`iea.org`、`opec.org`

**美国官方**
`federalreserve.gov`、`sec.gov`、`treasury.gov`、`bls.gov`、`bea.gov`、
`census.gov`、`whitehouse.gov`、`state.gov`、`ftc.gov`、`cdc.gov`、`nih.gov`

**其他官方**
`gov.uk`、`bankofengland.gov.uk`、`ons.gov.uk`、`bundesbank.de`、
`banque-france.fr`

**中国官方**
`gov.cn`、`stats.gov.cn`、`pbc.gov.cn`、`mof.gov.cn`、`ndrc.gov.cn`、
`mofcom.gov.cn`、`miit.gov.cn`、`moe.gov.cn`、`nhc.gov.cn`、
`mohrss.gov.cn`、`samr.gov.cn`、`csrc.gov.cn`、`safe.gov.cn`

**官方通讯社与党媒主渠道**
`xinhuanet.com`、`news.cn`、`people.com.cn`、`cctv.com`、
`chinadaily.com.cn`、`apnews.com`、`reuters.com`、`afp.com`、
`bloomberg.com`、`tass.com`、`kyodonews.net`

**通用规则**：任何 `.gov` / `.gov.cn` / `.gov.uk` 域名自动判为 A 级。

---

## B 级：主流权威媒体

**国际**
`bbc.co.uk`、`bbc.com`、`ft.com`、`wsj.com`、`nytimes.com`、`economist.com`、
`theguardian.com`、`washingtonpost.com`、`npr.org`、`aljazeera.net`、
`cnbc.com`、`scmp.com`、`nikkei.com`、`handelsblatt.com`、`lemonde.fr`、
`spiegel.de`、`elpais.com`、`politico.com`、`axios.com`、`forbes.com`

**中文**
`caixin.com`、`yicai.com`、`jiemian.com`、`chinanews.com.cn`、
`thepaper.cn`、`bjnews.com.cn`、`zaobao.com.sg`、`stcn.com`、
`cnstock.com`、`cs.com.cn`、`eeo.com.cn`

**学术期刊**
`nature.com`、`science.org`、`thelancet.com`、`nejm.org`

**通用规则**：`.edu` / `.edu.cn` / `.ac.uk` 自动判为 B 级。

---

## C 级：一般门户与垂直媒体

**中文门户**：`sina.com.cn`、`sohu.com`、`163.com`、`qq.com`、`ifeng.com`、
`huanqiu.com`、`gmw.cn`、`cnr.cn`

**科技垂直**：`36kr.com`、`huxiu.com`、`tmtpost.com`、`ithome.com`、
`leiphone.com`、`infoq.cn`、`csdn.net`、`cnbeta.com.tw`、`solidot.org`

**国际**：`cnet.com`、`techcrunch.com`、`theverge.com`、`wired.com`、
`arstechnica.com`、`zdnet.com`、`cbsnews.com`、`abcnews.go.com`、
`usatoday.com`、`time.com`、`newsweek.com`、`independent.co.uk`、
`telegraph.co.uk`、`dailymail.co.uk`

**通用规则**：`.org` 站点未登记时判为 C 级；完全未登记的域名按 C 级处理
（保守默认，避免误信）。

---

## D 级：低可信

`baijiahao.baidu.com`、`toutiao.com`、`mp.weixin.qq.com`、`weibo.com`、
`xiaohongshu.com`、`douyin.com`、`medium.com`、`substack.com`、
`blogspot.*`、`wordpress.com`、`jianshu.com`、`tieba.baidu.com`

判断逻辑：**先看是否在 A/B/C 白名单，再检查是否命中 D 级特征**
（公众号、头条号、博客平台等 UGC 渠道）。

---

## 媒体集团归一化

交叉验证计数时，同一集团的不同域名/子品牌**只算一个独立来源**。

| 集团 | 涵盖域名 |
| --- | --- |
| BBC | bbc.co.uk, bbc.com |
| 新华社 | xinhuanet.com, news.cn |
| 人民日报社 | people.com.cn, people.com |
| 路透社 | reuters.com |
| 美联社 | apnews.com |
| 彭博 | bloomberg.com |
| 新浪 | sina.com.cn, sina.com |
| 搜狐 / 网易 / 腾讯 / 凤凰 | sohu.com / 163.com / qq.com / ifeng.com |
| 财新 | caixin.com |
| 第一财经 | yicai.com |
| 界面 | jiemian.com |
| 澎湃 | thepaper.cn |
| 中国新闻网 | chinanews.com.cn |
| 金融时报 / 华尔街日报 / 纽约时报 / 经济学人 / 卫报 | 各自独立 |
| NPR / 半岛电视台 / CNBC / 南华早报 / 日经 | 各自独立 |
| 证券时报 / 中国证券报 / 上证报 | 各自独立 |

未登记的域名用**注册主域**（如 `example.com`）作为集团标识。

---

## 一手来源识别

满足以下任一条件即视为指向一手出处，置信度 +8：

1. 域名属于 A 级白名单
2. URL 路径包含 `/press/`、`/pressrelease`、`/newsroom`、`/statement`、
   `/notice`、`/announcement`、`/gonggao`、`/zhengce`、`/yaowen`

---

## 维护说明

新增信源时：

1. 在 `scripts/_core/sources.py` 的 `SOURCES` 列表加一个 dict
2. 若为新机构，在 `credibility.py` 的对应 `TIER_*_DOMAINS` 集合加域名
3. 若属已知集团的新域名，在 `MEDIA_GROUPS` 中补充
4. 同步更新本文件，保持文档与代码一致

**注意**：国内不少媒体的官方 RSS 已停止更新或不含发布时间
（新华网、人民网、中国日报的公开 RSS 经实测返回数月乃至数年前的存档内容），
因此不作为抓取源。但这些域名仍保留在 A 级白名单中——
通过聚合搜索渠道抓到这些媒体的文章时，依然会被正确定级。
