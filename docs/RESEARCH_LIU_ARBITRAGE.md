# 研究报告：@LIU985985HU 套利系列帖 + 费率/适配器事实核实

- 研究对象：X 用户 **WEB3老刘🎒 @LIU985985HU**（`https://x.com/LIU985985HU`），Backpack 相关美股永续「现货 + 等量做空永续」资金费套利系列
- 查询日期：**2026-11-14**（所有网页/文档事实均为该日抓取）
- 项目上下文：nt-pairs（同一底层标的不同链上/场所表示的价差均值回归套利；OKX + Hyperliquid + Lighter(含 Robinhood 链)）
- 性质：**只读研究**，未修改仓库代码，未提交 git

## 0. 取证方式与限制（先看这里）

| 项 | 状态 |
|---|---|
| `agent-reach twitter` 渠道 | **不可用**：`agent-reach doctor --text` 显示 `twitter = not_added`；`agent-reach add twitter` 返回 `UPSTREAM_BROKEN`（"could not find the OpenCLI extension release: HTTP Error 403: rate limit exceeded"）。按任务约束**未索要密码、未代为登录**。 |
| 实际使用的只读后端 | ① `agent-reach web read/search`（Exa 后端，能读 TwStalker 镜像的正文文本）；② pi 受管浏览器直接访问 `https://twstalker.com/LIU985985HU`（Cloudflare 校验自动通过），用于**逐条抓取 status ID、配图 URL 并下载图片**。 |
| 帖子原文 | 全部通过 TwStalker 镜像 `https://twstalker.com/LIU985985HU/status/<id>` 取得；每条 status ID 可直接映射到 `x.com/LIU985985HU/status/<id>`。 |
| 时间戳 | 镜像只给「2 days ago / a week ago」相对时间。**本报告中的绝对时间由 tweet ID（Twitter snowflake）解码得到**：`ts = (id >> 22) + 1288834974657` ms，即 X 官方可推导的推文创建时间。 |
| 图片 | 4 张图片全部下载成功并用视觉读取（图片 CDN 走 `ton-0.twimg.com/media/<id>.jpg`，与 X 原图同一 media id）。**无「图片未识别」项。** |
| 覆盖范围 | 拉取到账号最早约 3 周（推文 ID 下探至 `2110416076412690920` ≈ 2026-09/10），共 71 条。**10 月 ~ 11-14 的全部帖子已全部过目**，其中与套利方法论实质相关的仅 2 条（11-05 长文、11-12 两连帖）+ 1 条活动帖。 |
| 未覆盖 | 11-14 之后（尚未发生）；`with_replies` 页（镜像不渲染）；X 客户端原帖编辑历史（见 §E-6）。 |

---

## A. 帖主系列帖纪要

账号档案（TwStalker 抓取，2026-11-14）：`WEB3老刘🎒 @LIU985985HU`，简介「Bp钻石手 Backpack」，Joined June 2025，Tweets 433、Followers 2K、Following 2K、Likes 567。

| # | 时间（UTC / 北京） | status ID | 主题 | 关键数字 / 原话 |
|---|---|---|---|---|
| A1 | 2026-11-05 10:43 UTC / 18:43 CST | `2118293056931975578` | **长文《美股现货合约套利！》**（方法论主帖，附 1 张图） | 见下方「长文要点」 |
| A2 | 2026-11-05 10:45 UTC | `2118293564581154922` | 回复「一鱼多吃」（对 @Steam_Diary123 / @Miiiho420） | — |
| A3 | 2026-11-05 12:55 UTC | `2118326223826985083` | 回复 @zhao57487「可以试试」 | — |
| A4 | 2026-11-10 04:30 UTC | `2120011079220957231` | 转 Backpack 志愿者招募 | — |
| A5 | 2026-11-12 06:01 UTC | `2120758952019550289`（@Backpack_CN） | 官方活动公告：**合约交易活动开启，按交易量瓜分 $75,000 奖池，每日发奖**（附海报图） | 11.12—11.25 |
| A6 | 2026-11-12 07:06 UTC | `2120775130259558522` | **短帖 + 持仓截图**：「每天爽吃资金费再拿活动奖励！！！Backpack 上目前做空美光闪迪英伟达谷歌等！月均年化接近 30%。怎么操作：买入股票再做空对应数量的合约！爽吃资金费！并且现货不需要手续费而且支持限价单」 | 附图 = 持仓截图（见 §B-2） |
| A7 | 2026-11-12 13:58 UTC | `2120878895247532212` | **活动帖（含规则全文）**：14 天总奖池 75,000 U；每天 2,500 U × 14 天；每周 20,000 U × 两周；每个奖池 50% 给交易量前 20 名、50% 按交易量占比分给其余符合资格用户；结算快照时账户 ≥ 100 U；加密货币与美股永续成交量都算；时间 11 月 12 日—25 日；报名 backpack.exchange/quests。并称「我这次利用 ai 基于本地做了一个自动套利机器人」 | — |
| A8 | 2026-11-12 14:20 UTC | `2120884337680015691` | **统一保证金 / 爆仓线帖 + 17 只表格图**（见下方 + §B-1） | 「超过 20% 月年化的差不多是 9 只」 |
| A9 | 2026-11-12 13:58–14:35 UTC | `2120879175397716325` / `2120878923953443189` / `2120878785910509993` / `2120797892281794690` / `2120775631738986995` / `2120888219474800712` | 一组回复：「美美爽吃」「必须的」「必须爽吃啊」「**对冲的爆仓线很高**」「冲啊」「我觉得财报后就能回 200」 | — |
| A10 | 2026-10-29 ~ 2026-11-04 | `2115810322544222227`、`2116444926582079622`、`2116766459003371653`、`2116767417074606575`、`2116808862871540006`、`2118003395852439789` | 更早的内容基本是 **Backpack 抽奖/宣传**（SK 证券合作韩股、5 个 bp 奖池、开奖名单、转发 Backpack 官方帖），**无套利方法论** | — |

### A1 长文要点（2026-11-05，status `2118293056931975578`，原文镜像全文）

> 美股现货合约套利！
> 1. 为什么做加密币费率套利已经被机构吃完了，机会转到了美股永续：现货周末休市、合约照常交易，各所算法不同，费率经常偏高。
> 2. 玩法：买现货 + 等量做空永续，涨跌对冲掉，只收资金费。费率为正时多头付钱给空头，所以只挑费率为正且偏高的。
> 3. 为什么用 Backpack
>    - 现货和永续在同一个所，不用两个平台充值、调保证金
>    - 1,000 多只美股/ETF 现货（RFQ 报价成交），股票交易 0 手续费
>    - 18 只美股永续，每小时结算资金费
>    - 买入的股票可以直接当统一保证金，空单不容易被单独爆掉
> 4. 交易时间（北京时间，冬令时）：美股盘中 22:30–05:00，工作日盘前、盘后、夜盘也能走 RFQ；**周末只有 SPCX、MU、SNDK、SKHY 有现货订单簿**
> 5. 实操三步：1. 选标：看近 7 天平均费率，不要只看当下这一小时。我的标准是**周均年化 ≥ 20% 且月均 ≥ 10%**；2. **先开空：永续全仓 1–2 倍，挂限价空单（原文诀窍：合约那边先下）**；3. 再买现货：股票现货 RFQ 买入同等数量，价格差几毛没关系
> 6. 何时平：**周均费率跌破 5% 再两边一起平**。不要因为一两天负费率就进进出出，回测里频繁进出是最大的亏损来源。
> 成本与收益：来回成本约 **0.6%**（永续手续费 + RFQ 点差，估算），年化 30% 的话大约一周回本；回测（1000U，美股永续上线后约 60 天）**年化约 15–20%**，未计基差、滑点和休市限制。
> 当前候选（**2026-11-05 18:30**，周均年化）：MU 48.1%、SPCX 40.8%、INTC 40.7%、SNDK 38.5%、CRCL 35.7%
> 风险注意：费率会变负，空头倒贴钱；涨太多时空单保证金会紧，要提前降仓；现货休市时只能动永续那一边；年化只按资金费估算，非投资建议。

> 注（取证备注）：镜像中的这条正文带有明显的**二次编辑痕迹**——章节编号从「5. 实操三步」直接跳到「6. 何时平」，且正文中夹了半句编辑批注「（原文诀窍：合约那边先下）」。因此**该条正文应视为「经过整理的版本」**，而与之同帖的图片（§B-3）是未经改动的原始素材（下面 §E-6 列出两者数字冲突）。

---

## B. 图片识别结果（全部成功识别）

### B-1 17 只美股永续表（status `2120884337680015691`，2026-11-12 14:20 UTC）
media id `HW7lDwnbAAAcvdL`，1200×1057。标题：**「📊 美股永续 17 只 · 周均/月均年化」**，副标「数据：Backpack 股票永续，按周均年化排序」。逐行转录：

| 序 | 标的 | 当前(瞬时年化) | 周均年化 | 月均年化 |
|---|---|---|---|---|
| 1 | SNDK.US | 30.8% | **30.9%** | **33.1%** |
| 2 | NVDA.US | 134.0% | **28.9%** | **24.0%** |
| 3 | MU.US | 5.5% | **25.9%** | **25.8%** |
| 4 | CRCL.US | 62.9% | **22.7%** | **30.9%** |
| 5 | INTC.US | 5.5% | 19.5% | **26.4%** |
| 6 | SPCX.US | 51.6% | 19.3% | **29.8%** |
| 7 | SPY.US | 5.5% | 17.7% | 6.4% |
| 8 | HOOD.US | 5.5% | 14.4% | 13.9% |
| 9 | DRAM.US | 29.5% | 12.2% | 19.8% |
| 10 | SKHY.US | 5.5% | 11.3% | 11.5% |
| 11 | META.US | 5.5% | 8.0% | **22.2%** |
| 12 | TSLA.US | 5.5% | 7.1% | 11.6% |
| 13 | AMZN.US | 5.5% | 6.3% | 18.1% |
| 14 | AMD.US | 5.5% | 6.2% | 17.4% |
| 15 | AAPL.US | 5.5% | 5.8% | 4.3% |
| 16 | GOOGL.US | 5.5% | 4.8% | **24.1%** |
| 17 | QQQ.US | 5.5% | 4.7% | 18.6% |

- 加粗 = 月均年化 ≥ 20%。**严格 >20% 的有 8 只**（SNDK/NVDA/MU/CRCL/INTC/SPCX/META/GOOGL）；把 DRAM(19.8%) 也算进来才是 9 只 → 与帖子正文「超过 20% 月年化的差不多是 9 只」的口径吻合（取整口径）。
- **按帖子自己声明的选标规则（周均 ≥20% 且 月均 ≥10%）严格筛选，17 只里只有 5 只达标：SNDK、NVDA、MU、CRCL、SKHY**（INTC 19.5%、SPCX 19.3% 差一点）。
- 「当前」列全是 5.5% 的标的占了大半（NVDA 134%、CRCL 62.9%、SPCX 51.6% 是瞬时高值）——正是帖子强调「不要只看当下这一小时」的量化例证。

### B-2 持仓截图（status `2120775130259558522`，2026-11-12 07:06 UTC，截图内手机时间 15:02）
media id `HW6BvQNaYAAnw2D`，553×1200。Backpack App「合约」页，标的 **PENGU-PERP 10x**，页签：持仓(4) / 当前委托(2) / 借贷(1) / TWAP。逐项转录：

| 合约 | 方向 | 价值 | 初始保证金 | 资金费用 | 盈亏 | 盈亏平衡 | 标记价格 | 预计强平价格 |
|---|---|---|---|---|---|---|---|---|
| MU-PERP | 开空 | $1,570.94 | $157.09 | $8.94 | **−$28.82 (−18.35%)** | 771.06 | 785.47 | **1,888.21** |
| QQQ-PERP | 开空 | $1,367.50 | $136.75 | $16.84 | **+$86.40 (+63.18%)** | 726.95 | 683.75 | **2,404.45** |
| SKR-PERP | 开空 | $1,094.30 | $218.86 | $6.59 | **+$250.02 (+114.24%)** | 0.013443 | 0.010943 | 0.022558 |

底部挂单：止损 0.012 / 止盈 0.003（对应 SKR-PERP）。

观察（[INFERENCE]，仅凭截图）：
- 三腿**初始保证金/名义 ≈ 10%**，即展示的是 ~10x 保证金比率，而帖子文字写的是「全仓 1–2 倍」。两者不是同一口径（截图是 App 默认 10x 杠杆档 + 全仓账户），**不代表帖主实际策略**，但说明「1–2 倍」与「App 10x 档」并存，抄作业时不要只抄倍数。
- 「预计强平价格」离标记价格极远（MU：标记 785 vs 强平 1888；QQQ：684 vs 2404），这正是帖子所称**统一保证金 + 现货充抵**的效果：空单被现货抵押物保护，涨到强平线要涨 1.4~3.5 倍。
- SKR-PERP 不在 17 只名单里（名单里是 SKHY），说明持仓池比公开表更宽。

### B-3 「Backpack 费率套利候选」卡（status `2118293056931975578`，2026-11-05）
media id `HWWwTj8bcAAAeZM`，553×1200。逐行转录（**这张图与正文数字有出入，见 §E-1**）：

| 标的 | 周均年化 | 月均年化 | 最新年化 | 备注 |
|---|---|---|---|---|
| MU.US | 48.1% | 21.0% | 130.8% | 这周刚飙高，可能回落 |
| SPCX.US | 40.8% | 33.9% | 5.5% | 周末也有现货订单簿 |
| INTC.US | 40.7% | 28.9% | 11.2% | 周月都稳 |
| NVDA.US | 26.9% | 22.7% | 5.5% | 大盘股，波动最小 |
| XPL | 22.7% | 33.9% | 10.9% | **加密币**，24 小时可开，借息约 1%/年 |

图下文字：
> 怎么开：现货买入（美股走 RFQ，现在是美东周四盘前，可以下单），同时开等量永续空，1 到 2 倍。来回成本约 0.6%，**周均跌破 3% 再平**。
> 上次推的 SNDK（周均 38.5%）、CRCL（35.7%）、GOOGL（32.7%）仍然达标。

图首行注明：「数据时间 2026-11-05 18:30 CST，Backpack 每小时资金费，只算资金费，未扣手续费、RFQ 点差和基差。」

### B-4 活动海报（status `2120758952019550289`，@Backpack_CN，2026-11-12 06:01 UTC）
media id `HW5zBL4bYAAZxla`，960×1200：「合约交易活动 / 按交易量瓜分 **$75,000** 总奖池 / 每日奖励 / 加密货币 × 股票永续合约 / **11.12 — 11.25**」。

---

## C. 方法论提炼（对 nt-pairs 可直接用的 5 条）

> 每条都标注来源帖号；「迁移到 nt-pairs」的判断为 [INFERENCE]。

### C-1 选标用「周均 / 月均」平滑，且**双阈值**而非单阈值
- 来源：A1（`2118293056931975578`）「看近 7 天平均费率，不要只看当下这一小时。我的标准是周均年化 ≥ 20% 且月均 ≥ 10%」；B-1 表格同时给「当前 / 周均 / 月均」三列。
- 为什么能直接搬：nt-pairs 已经有等价的东西——`docs/2026-11-11-basis-probe.md` 用 14 天 1h K 线算基差，`src/nt_pairs/leg_policy.py` 的 `RepeatedCloseDetector` 检测重复收盘（陈旧报价）。
- **搬过去要改什么（关键）**：帖主的「周均」= 资金费的**时间均值**，可加性、可平滑；nt-pairs 的「基差」是**价差**，均值会骗人——基差均值 ≈ 0 时不代表可交易。所以：
  1. 用 **p95 / 分位数**而不是均值做阈值（用户提问里已点到这一层）：把 `min_p95_over_k_bstar ≥ 1.0`（`configs/live_pairs.json` 已有）当作「周均 ≥20%」的对应物；
  2. 阈值要**双阈值**：不是「p95 ≥ X」，而是「p95 ≥ X **且** p95 在滚动窗内的**占比/稳定性** ≥ Y」——对应帖主的「周均 ≥20% 且月均 ≥10%」（近 7 天达标、近 30 天也达标）。这等价于要求基差**持续存在**而非偶尔尖峰。
  3. 帖主还隐含第三道闸：「别看当前这一小时」。对 nt-pairs 的直译是**禁用同瞬时定价**（仓库 `0defca8` 提交已把 EV 口径改为 same-instant pricing），以及 `RepeatedCloseDetector` 对薄腿的重复收盘判定（实测 Lighter 侧 RKLB 31%、KORU 36%、KIOXIA 93% 重复率 → 不可交易）。

### C-2 「先挂空腿、再买现货」的**腿序**= 先建立价格保护，再承担敞口
- 来源：A1「先开空：永续全仓 1–2 倍，挂限价空单（**原文诀窍：合约那边先下**）」+「再买现货：股票现货 RFQ 买入同等数量，**价格差几毛没关系**」；B-3「同时开等量永续空，1 到 2 倍」。
- 在 nt-pairs 框架里是否已等价：**部分等价，但方向相反**。nt-pairs 当前是 `leader` 挂 maker 单（被动腿）+ `follower` 追价成交（主动腿），见 `configs/pair.json`：「腿 A = leader（HL 被动挂单），腿 B = follower（Lighter IOC 对冲）」。两者共同点是**被动腿先挂、主动腿后追**；差异是帖主的「被动腿」是**空头腿（他想主动卖出风险敞口的那条腿）**，而 nt-pairs 的被动腿是 leader（想被动收的那条腿）。
  - 帖主逻辑：先卖（永续空）→ 之后买回（现货），先锁定「我会以某个价格卖出」，**不担心买贵**（"价格差几毛没关系"）。nt-pairs 的等价原则应是：**先在你想成交的价位把被动腿挂出去，成交后再用主动腿追**——即「成交价由被动腿决定」，主动腿只负责把敞口补齐，不负责定价。
  - 可迁移的具体纪律：**价差不一致时优先牺牲主动腿的价格**（帖主：几毛没关系），而不是同时放宽两条腿——这与仓库 `leg_policy.py::select_pair` 用 `expected_net_bps` 联合优化、以及 `live_pairs.json` 的 `chase_collar_ticks: 2` / `taker_collar_bps: 50` 追价上限是同一思路，帖主给了这个思路的**原始表述**。

### C-3 收益结构不同：帖主吃**持续资金费流**，nt-pairs 赚**一次性价差收敛**
- 来源：A1「只收资金费」「年化 30% 的话大约一周回本」「回测（1000U，约 60 天）年化 15–20%」；B-2 持仓截图里三腿都有「资金费用」累计项（$8.94 / $16.84 / $6.59）。
- 差异（[INFERENCE]，但依据充分）：
  | | 帖主（funding carry） | nt-pairs（basis convergence） |
  |---|---|---|
  | 收益来源 | 每小时/每 8h 资金费，持续流 | 价差收敛，一次性 |
  | 持仓时长 | 数周~数月（周均跌破阈值才平） | 数分钟~数十分钟（单轮） |
  | 主要风险 | **费率翻负**（"空头倒贴钱"）、周末现货休市、涨太多保证金紧 | 单腿裸露（裸腿强平）、基差不收敛、深度 |
  | 对称性 | 现货+永续天然对冲（delta≈0） | 两腿都是永续，delta 对冲但**保证金独立** |
- **对「跑两周」这一约束的含义**：帖主的策略**两周内几乎不可能跑完一个完整周期**（入场后要等周均跌破阈值才平，11-05 帖里的 MU 周均 48.1% 到 11-12 表里已经掉到 25.9%，而多数标的一周内就跌破 5%）。因此：
  1. **两周只能验证「入场 + 资金费收取」，不能验证「退出」**。若要照搬其「等阈值平仓」纪律到 nt-pairs 的两周窗口，必须人为定义两周末的强制平仓规则并单独记账（否则会把「未平仓浮盈」误当「已实现」）。
  2. 帖主的收益按年化 15–30% 计，**两周只兑现 ~1–1.5%**（年化 30% × 2/52 ≈ 1.15%），而 nt-pairs 的单轮目标净边际是**几十 bps 量级**、单轮周期 15 分钟级。**结论：nt-pairs 的单位时间收益率比帖主高一个量级，但样本量与撮合难度高一个量级；两周窗口对 nt-pairs 更友好（能拿到几十~上百轮样本），对帖主策略不友好。** 这正好支持 nt-pairs 现有路线。
  3. 可借鉴的一点点：**把 funding 当成「持有成本项」而不是收益项**。nt-pairs 仓库 `costs.py::round_cost_bps` 已有 `carry_sign` / `funding_bps_h` 字段（按方向带符号计入成本）——与帖主一致的定性。

### C-4 「不要因为一两天负费率就进进出出」= **换手成本会吃掉策略**
- 来源：A1「周均费率跌破 5% 再两边一起平。**不要因为一两天负费率就进进出出，回测里频繁进出是最大的亏损来源**」。
- 迁移：nt-pairs 的单轮往返成本（`costs.py` 口径：4 次成交 = leader 入场 maker + follower 入场/平仓 taker + leader 平仓 taker）比帖主的「来回 0.6%」**低 1~2 个数量级**（repo 口径是 bps 级），所以 nt-pairs 的「频繁进出」惩罚要小得多；但同一原则仍然成立：**阈值必须设在能覆盖单轮全成本的价差水平**（`b*`），而不是「看到价差为正就做」。帖主用「来回 0.6%」当成本门槛，nt-pairs 用 `b*`（成本 + 腿风险 + 对账，见 `costs.py`）当门槛，**同构**。
- 附带一条可直接用的纪律（帖主独有）：**两腿要「一起」平**（"两边一起平"），不做单腿止损。nt-pairs 侧则是**允许**单腿紧急处理（`live_pairs.json` 有 `naked_flatten_s: 180` / `naked_halt_s: 600`）——这是必须保留的差异，理由见 C-5。

### C-5 统一保证金 vs 跨场所独立保证金：帖主的「不裸腿」靠抵押物，不靠纪律
- 来源：A8（`2120884337680015691`）「买入现货做空合约并且这些股票都是能充当保证金的比例在 **50–70%**……1. 股票下跌：合约的盈利也是能够充当保证金并且现货是不会爆仓的，也就是说**下跌不会爆仓**；2. 股票上涨：现货股票也会涨保证金也会按照比例上涨，当然也是有爆仓价的只不过相对挺高」；回复 `2120878785910509993`「**对冲的爆仓线很高**」；B-2 截图 MU 强平价 1888 vs 标记 785。
- 映射到 nt-pairs：**跨场所（OKX / HL / Lighter）保证金彼此独立，没有「现货充抵永续保证金」这回事**，唯一等价物是「另一条腿的浮盈算作本腿保证金」——即**用腿 B 的浮盈去覆盖腿 A 的亏损**。nt-pairs 的资产是 perp vs perp（不是 spot vs perp），所以没有抵押物，只能靠：
  1. **把保证金放在同一个能互通的地方**——做不到（不同场所、不同清算域）→ 只能靠「腿 B 的浮盈计入腿 A 的可用保证金」在**同一场所内**成立时才有效。
  2. 因此 nt-pairs 的「裸腿」风险是**结构性的**：腿 A 在 HL 被强平时，腿 B 在 Lighter/OKX 上的对冲头寸**不会自动平仓**，需要 bots 自动执行 flatten（仓库已有 `naked_flatten_s/naked_halt_s`）。帖主没有这个问题，因为同所 + 现货充抵。
  3. **可借鉴的具体做法**：帖主用「**现货充抵 → 强平线极远**」来替代「靠纪律避免爆仓」。nt-pairs 的等价替代品是**降低单腿杠杆 + 用腿间浮盈互保**：即两条腿都在同一场所（HL HIP-3 与 HL 现货/HL 主网 perp 之间）配对，或把保证金利用率压到能承受「另一腿延迟平仓 180 秒」的幅度。`live_pairs.json` 的 `margin_usage_cap_per_venue: 0.5` 已经是这个思路的雏形；建议再加一条「**腿间保证金互保**：若腿 A 可用保证金因腿 B 浮盈增加，才允许加仓」。

### C-6 附：选标的的「流动性前置条件」（帖主独有的硬条件）
- 来源：A1「周末只有 SPCX、MU、SNDK、SKHY 有现货订单簿」。
- 迁移：**对冲腿（现货/另一场所）必须有可成交的流动性**，否则「等量对冲」在休市时段根本不成立。对 nt-pairs 意味着：任何 pair 的两条腿都必须**同时可成交**，休市时段（美股现货休市、非美股时段）直接出局。这与 `configs/live_pairs.json` 的 `allowed_sessions: [closed, post, pre, rth]` 是同一件事的两种表达，但帖子给出的判据更硬：**按标的查「对冲腿在哪些时段有盘口」**（Lighter 的现货是 24/7 CLOB，所以 nt-pairs 的对手腿问题反而不存在——这是 nt-pairs 相对帖主的结构性优势）。

---

## D. 费率 / 适配器事实核实（查询日期 2026-11-14）

### D-1 Lighter 手续费：「双向 0」基本成立，但有三个例外和一个部署差异

来源：`https://docs.lighter.xyz/trading/trading-fees.md`（抓取 2026-11-14）

| 账户类型 | Maker | Taker | 备注 |
|---|---|---|---|
| **Standard（默认）** | **0%** | **0%** | 「For both perpetual futures and spot markets, Standard Accounts pay **no maker or taker fees on regular orders**」 |
| Standard（例外） | — | — | **TWAP / advanced orders 与 RFQ 收 1 bp (0.01%)** |
| Plus | 0.5 bps | 0.5 bps | taker 延迟 300 ms、maker 延迟 0 ms |
| Premium/Advanced（按质押 LIT） | 0.0040% → 0.0028% | 0.0280% → 0.0196% | 0–500,000 LIT 质押 8 档，taker 延迟 140 ms |

- **「双向 0」的准确表述**：Standard 账户的**常规挂单（maker）与吃单（taker）都是 0 费率，永续与现货同此**；但 **RFQ 收 1 bp**。nt-pairs 若在 Lighter 上走 RFQ/advanced 通道，费率不是 0。
- **Robinhood 链部署（`Lighter RH`）的费率**：来源 `https://docs.lighter.xyz/developers/robinhood-chain/overview.md`——RH 是**独立交易所**（账户/余额/API key/chain ID 都不通用），mainnet chain id **466324**，REST `https://api.rh.lighter.xyz`，WS `wss://api.rh.lighter.xyz/stream`，**主要抵押品 USDG**（mainnet 是 USDC），可充 ETH（仅现货）与代币化股票/ETF（SPY、QQQ、NVDA、AAPL…）。费率表：

| RH 账户档 | Maker | Taker | Taker 延迟 | Maker 延迟 |
|---|---|---|---|---|
| **Standard（默认）** | **0%** | **0%** | 300 ms | **200 ms**（mainnet 是 0 ms） |
| Plus | 0.015% | 0.015% | 300 ms | 200 ms |
| Premium（按 14 天成交量） | 0.0120% → 0.0084% | 0.0350% → 0.0245% | 200→150 ms | 0 ms |

  → **结论**：RH 的 Standard 档费率与 mainnet 相同（0/0），**差异在延迟与提现/抵押品**（RH 的 maker 单有 200 ms 延迟，mainnet post-only 是 0 ms；RH 快速提现仅 USDG，最低 1 USDG）。
- **其它相关事实**：NautilusTrader 集成文档引用的 Lighter 档位表（mainnet Standard `0 ms / 300 ms`、`0 / 0`；RH Standard `200 ms / 300 ms`、`0 / 0`）与官方一致；mainnet 还有 **Premium/Advanced 按质押 LIT 打折**、**Volume Quota**（按成交额换 `sendTx` 配额，1 美元/配额或 2 美元/配额默认，2026-11-02 快照含 EWY/SOXL/MRVL/MSTR/NBIS/DRAM/CRCL/CRWV/RKLB/SKR/INTC/SKHY/HOOD/SPY/QQQ 等）、**LIT Fee Credits**（`https://docs.lighter.xyz/trading/trading-fees/lit-fee-credits.md`）。

### D-2 Hyperliquid 费率与 HIP-3 `deployerFeeScale` 语义

来源：`https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees.md` 与 `.../for-developers/api/hip-3-deployer-actions.md`（抓取 2026-11-14）

**基础费率（Tier 0，14 天加权成交量 ≤ 5M）**

| | Taker | Maker |
|---|---|---|
| Perps（验证者运营，含 HIP-3） | **0.045%（4.5 bps）** | **0.015%（1.5 bps）** |
| Spot | 0.070%（7 bps） | 0.040%（4 bps） |

- 现货与永续成交量合并计档，**现货量按 2 倍计入档位**；一个用户跨 perps / HIP-3 / spot 共用同一档位。
- 质押 HYPE 折扣：Wood 5% → Diamond 40%；maker 返佣档：maker 量占比 >0.5% 给 −0.001%，>1.5% 给 −0.002%，>3.0% 给 −0.003%。
- 费用去向：社区（HLP、assistance fund、deployer）；assist fund 的 HYPE 被销毁。**现货与 HIP-3 perp 的部署方最多可留 50% 交易费**。

**HIP-3 `deployerFeeScale` 的准确语义**（`perpDexs[].deployerFeeScale`，官方 `feeRates` 公式 + `SetDeployerFees` 文档）

- 取值：主网 `scale ∈ [0.0, 3.0]`；**growth mode 下 `scale ∈ [0.0, 10.0)`**；每 30 天只能改一次。
- 官方定义：**「deployer fee scale 是部署方从用户费用中保留的比例（在应用 aligned stablecoin 抵押折扣之前）」**。
- 计费公式（用户费率 `x` 为正时）：
  - `scale < 1`：`P(协议) = y`，`D(部署方) = scale · x`
  - `scale ≥ 1`：`P = y · scale`，`D = x · scale`
  - 其中 `y` 是应用 aligned quote 抵押折扣后的用户费率（非 aligned 抵押时 `x = y`）。
- 用户实付总额：`fee_bps = base_bps × scaleIfHip3 × growthScale`，其中 `scaleIfHip3 = scale < 1 ? scale + 1 : scale × 2`，`growthScale = growthMode ? 0.1 : 1`。
- growth mode 额外把协议费、返佣、成交量贡献、L1 限流贡献**降低 ≥90%**，且要求市场与验证者运营的 perp 完全不重叠（加密 perp/指数/组合/金价等不合格），30 天冷却。
- 官方给出的对照表（正费率、x = y = 1）：

| growthMode | feeScale | userFeeToProtocol | userFeeToDeployer |
|---|---|---|---|
| false | 0 | 1 | 0 |
| false | 0.5 | 1 | 0.5 |
| false | 1 | 1 | 1 |
| false | 3 | 3 | 3 |
| true | 0 | 0.1 | 0 |
| true | 0.5 | 0.1 | 0.05 |
| true | 1 | 0.1 | 0.1 |
| true | 3.01 | 0.301 | 0.301 |
| true | 9.99 | 0.999 | 0.999 |

- **对 nt-pairs 的直接含义**：仓库 `src/nt_pairs/costs.py` 的 `hl_fee_bps()` 已按同一公式实现，并取 `deployerFeeScale=1.0 + growthMode` → **maker 0.30 bps / taker 0.90 bps**（= 1.5×2×0.1 / 4.5×2×0.1），与官方表一致 ✅。**但「部署方最多分走一半」这句话在 scale > 1 时不成立**（scale=3 时部署方拿 3 份、协议 3 份，用户费率被放大 3 倍）——所以 `configs/openai_entropy_lighter.json` 里「部署方最多分走一半」的注释需要修正，且**必须按 `perpDexs.deployerFeeScale` 逐 dex 实测**，不能用 1.0 假设。

### D-3 NautilusTrader 官方适配器现状（GitHub `nautechsystems/nautilus_trader`，`crates/adapters/`，抓取 2026-11-14）

存在的适配器目录：`architect_ax, betfair, binance, blockchain, bybit, coinapi, coinbase, databento, deribit, dydx, finfeed, hyperliquid, interactive_brokers, kalshi, kraken, lighter, okx, polymarket, sandbox, tardis`。

| 适配器 | 现货 | 永续 | HIP-3 | 数据/执行 | 与本项目相关的事实 |
|---|---|---|---|---|---|
| **nautilus-lighter** | ✓（CLOB 现货） | ✓（线性永续） | — | 数据 + 执行 | 支持 **Lighter 与 Robinhood Chain 两个部署**；`LIGHTER_DEPLOYMENT=lighter\|robinhood`；venue 名 `LIGHTER` / **`LIGHTER_ROBINHOOD`**；Robinhood 永续 = `{BASE}-PERP.LIGHTER_ROBINHOOD`、现货 = `{BASE}/{QUOTE}-SPOT.LIGHTER_ROBINHOOD`（例 `SNDK/USDG-SPOT.LIGHTER_ROBINHOOD`）；结算币 USDG（RH）/ USDC（mainnet）；**post-only 支持**（映射 Lighter PostOnly TIF）；`canceled-post-only` 映射为 `OrderCanceled`；**TWAP 未实现**；**无 RFQ 支持**（文档/源码全文无 RFQ）；mainnet Standard maker 延迟 0 ms、RH Standard 200 ms；integrator 归因费率为 0（maker-only key 亦 0） |
| **nautilus-hyperliquid** | ✓（原生现货） | ✓（验证者永续） | **✓（HIP-3 builder perps，Stable）** | 数据 + 执行 | 产品支持表：Spot / Perpetual Futures / **HIP-3 Perpetuals（Stable）** / HIP-4 Outcomes（Experimental）；HIP-3 每个 dex 可指定抵押品；`load_instrument_definitions(include_perps_hip3=…)`；builder 归因地址 0 费率（需一次性 builder-fee approval） |
| **nautilus-okx** | ✓ | ✓（线性/反向永续） | — | 数据 + 执行 | 产品支持表：Spot / Margin / Perpetual swaps / Futures / Options / Spreads / Event contracts；OKX v5 API |
| **nautilus-binance** | ✓（含 Binance US） | ✓（USD-M fapi、COIN-M dapi） | — | 数据 + 执行 | 认证需 **Ed25519**（HMAC/RSA 不支持） |
| **nautilus-tardis** | — | — | — | **仅数据**（实时采集 + 历史回放 + Parquet 目录） | Tardis 归一化行情源，用于回测/回放，不下单 |

**给 nt-pairs 的腿位建议（依据如下）**

结论：**maker（被动）腿 → Lighter；taker（追价）腿 → Hyperliquid（或 Lighter 当第二条 maker 腿 + HL 追价）**。但要注意**帖主的结构可以更简单地复刻在 Lighter 内部**：Lighter 同时有现货与永续（RH 上还有代币化股票现货），因此「买现货 + 做空永续」可完全在 Lighter RH 内部完成，**同所统一保证金**，不产生跨场所裸腿风险。

| 方案 | maker 腿 | taker 腿 | 依据 | 代价 |
|---|---|---|---|---|
| **A（推荐，跨场所，对齐现有 repo）** | **Lighter**（`LIGHTER` 或 `LIGHTER_ROBINHOOD`）挂 post-only | **Hyperliquid** 追价（IOC/追 2 tick collar） | Lighter Standard maker 0 bps 且 mainnet maker 延迟 0 ms、post-only 语义完整（`canceled-post-only` 有明确映射）；HL 侧 maker 费 1.5 bps（HIP-3 growth mode 下 0.3 bps）、taker 4.5 bps（growth mode 0.9 bps），但 **HL 是三条腿里唯一同时支持现货 + HIP-3 永续 + 现货的成熟场所**，追价深度最好 | HL taker 成本 4.5 bps（growth mode 0.9）吃掉大部分单轮边际；repo 现有配置就是「leader=HL maker / follower=Lighter taker」，**与本建议相反，需要按实际盘口与费率重算 b\*** |
| **B（复刻帖主，同所）** | Lighter RH **现货**（tokenized stocks，USDG） | Lighter RH **永续** | 同所统一保证金 → 强平线极远（帖主截图：MU 强平 1888 vs 标记 785）；两边费率 0 | 跨所基差消失 → 不再是「跨场所价差套利」，变成 funding carry（收益结构变成 C-3 那种，对两周窗口不友好）；且 Lighter 现货是 CLOB 无 RFQ |
| **C（OKX 的角色）** | OKX 不建议做 maker 腿 | OKX 做**兜底 taker**（repo 已有此设计：`configs/live_pairs.json` 「Lighter 腿拒单/超时未成时改用 OKX 吃单；费率 5 bps」） | OKX v5 适配器覆盖现货/永续/期权；深度最好 | OKX VIP0 永续 taker ≈ 5 bps（repo 假设），spot 更贵；仅作 failover，不进主路径 |

补充要点：
- **HL 作为 maker 腿的隐藏优势**：HIP-3 growth mode 下 maker 仅 0.3 bps、taker 0.9 bps（相对 Tier0 的 1.5/4.5 便宜 5 倍），而 Lighter Standard 是 0/0 ——**若某条腿上 HL 的盘口明显更厚，把 HL 当 maker 腿反而更划算**；但 repo 现状把 HL 当 leader(maker) 腿，费率假设（1.5/4.5 bps）**未乘 growth mode**，说明现在用的是保守上界，实际可能更好。
- **Lighter 做 maker 腿的一个坑**：RH 的 Standard maker 单有 **200 ms** 延迟（mainnet post-only 0 ms），且 RH 结算币是 **USDG**（不是 USDC）→ 计价货币与 USDT/USDC 的腿存在**基差与抵押利率**差异，需在 b* 里体现。
- **Lighter 的 CLOB 现货 vs 帖子的 RFQ 现货**：帖主用 RFQ（Backpack，1 bp 级别点差，11-05 图里明确「未扣手续费、RFQ 点差」）；nt-pairs 在 Lighter 只能用 CLOB 限价/市价 → **没有 RFQ 的「价格差几毛没关系」特权**，点差成本要按盘口算（repo 的 `vwap_slip_bps` 正是这个口径）。

---

## E. 与用户已有摘要的差异清单

| # | 摘要说法 | 实际（帖号/图片） | 判定 |
|---|---|---|---|
| E-1 | 平仓阈值「周均费率跌破 5% 再两边一起平」 | **帖文正文说 5%**，但**同帖图片（B-3）写的是「周均跌破 3% 再平」** | **冲突，图片=更原始**。抄作业取哪个未定，报告里两处都列了 |
| E-2 | 17 只名单「SNDK / NVDA / MU / CRCL / INTC / SPCX 等」 | 完整 17 只见 §B-1（含 SPY/HOOD/DRAM/SKHY/META/TSLA/AMZN/AMD/AAPL/GOOGL/QQQ）。摘要**漏了 DRAM、SKHY、QQQ、SPY 等**，其中 **DRAM 同时在 nt-pairs 候选池** | 摘要不完整 |
| E-3 | 「约 9 只月均年化 >20%」 | 表内**严格 >20% 的是 8 只**；把 DRAM 19.8% 计入才是 9 只 | 口径差异（帖主口算） |
| E-4 | 摘要暗示「17 只 = 按规则筛出来的达标标的」 | 按帖主自己的规则（周均 ≥20% 且 月均 ≥10%），**17 只里只有 5 只达标**（SNDK/NVDA/MU/CRCL/SKHY）；帖中标题是「**下面是 17 只可以套利的股票**」，即"可套利"≠"按我这套阈值达标" | 摘要隐含的等价关系不成立 |
| E-5 | 「11-12 之后的活动帖」 | 活动公告是 **11-12 06:01 UTC（@Backpack_CN）**，LIU 的转推 11-12 13:58 UTC；**截至 2026-11-14，LIU 没有 11-12 之后的新帖**（最新一条 11-12 14:35 UTC） | 时间上没错，但「之后」没有内容 |
| E-6 | 摘要把 11-05 长文的结构当作原帖 | 该帖正文在镜像里**有编辑痕迹**（编号 5→6 跳号、夹「（原文诀窍：合约那边先下）」这类批注），而同帖图片（B-3）是原始素材；两者数字有差异（如 NVDA 26.9% vs 正文候选未列 NVDA；XPL 仅出现在图里；SNDK/CRCL/GOOGL 的「上次推的」在正文里被删掉） | **正文可能是二次整理版**，以图片为准更稳 |
| E-7 | 「约 18 只美股永续」 | 11-05 正文说 18 只，11-12 表只有 17 只；第三方 Perpequities 的 Backpack 股票永续页当时列 **14 个市场** | 三方口径不一，摘要取「18 只」源自 11-05 正文 |
| E-8 | 摘要未提 | 11-05 图里还有 **XPL（加密币）**，备注「24 小时可开，借息约 1%/年」；11-01~11-12 之间还有 Backpack 官方「每周 20,000 U + 每天 2,500 U」的活动规则（摘要只提了 75k 总额） | 补充信息 |
| E-9 | 摘要未提 | 11-12 帖子称「**我这次利用 ai 基于本地做了一个自动套利机器人**」 | 补充信息 |
| E-10 | 摘要未提 | 持仓截图显示三腿 **初始保证金/名义 ≈ 10%（≈10x 档）**，与文字「全仓 1–2 倍」口径不同 | 需注意，避免误抄倍数 |
| E-11 | 摘要「统一保证金 50-70%」 | 原话是「**这些股票都是能充当保证金的比例在 50–70%**」（股票价值中可充抵保证金的比例） | 摘要表述基本准确，但需明确是「股票→保证金折算率」而非「仓位比例」 |
| E-12 | 摘要「成本约 0.6% 来回」 | 图与文一致（0.6%），但图明确「**只算资金费，未扣手续费、RFQ 点差和基差**」→ 0.6% 是**估算**（正文也写「估算」） | 准确，但口径需注明 |

未发现摘要与原帖**方向性矛盾**的项；差异集中在「数字口径」与「名单完整性」。

---

## F. 未能取证的项（不猜）

1. **X 原站原帖与图片**：`agent-reach twitter` 渠道不可用（未添加 / add 时上游 403），全程使用 TwStalker 镜像 + 浏览器读取。镜像可能对推文正文做过处理（§A1 注、§E-6），**推文编辑历史无法核对**。
2. **推文的绝对时间**：镜像只给相对时间，报告里的时间戳由 snowflake ID 反解，**精确到秒、理论上等同 X 的创建时间，但没有从 X 官方 API 二次确认**。
3. **17 只表格数据来源**：帖主未说明「周均/月均年化」的计算窗口细节是否与我方理解一致（图片写「按周均年化排序」，首行为「数据：Backpack 股票永续」）。**是否含基差/手续费修正：图片声明「只算资金费」**，但「当前」列是否瞬时值未明示。
4. **持仓截图里 PENGU-PERP 标签**：截图顶部标的是 PENGU-PERP，而持仓列表是 MU/QQQ/SKR 三条 —— 推测为滚动截图残留，**未能确认**。
5. **回测细节**：帖主称「1000U、约 60 天、年化 15–20%」，**没有给出回测代码、样本、成交假设**，无法复现。
6. **OKX 官方费率**：未能从 okx.com 取到可引用的公开费率表（`/api/v5/public/trading-fee` 返回 404；`https://www.okx.com/fees` 需登录/JS 渲染）。报告中的「OKX taker ≈ 5 bps」来自 **仓库现有配置假设**（`configs/live_pairs.json`、`configs/pair_kioxia.json`），**未从 OKX 官方独立核实**。
7. **Backpack 费率/永续只数/资金费结算细则**：未从 Backpack 官方文档核实（本次只核实了 Lighter / Hyperliquid / NautilusTrader 三项）。已知第三方页面 `perpequities.com/zh/venues/backpack/stocks` 当时列 14 个股票永续市场，与帖主的 18 只不符，**未追查差异原因**。
8. **11-12 之后是否还有贴**：2026-11-14 之后的内容尚不存在。
9. **XPL（加密币）在 11-05 图里的「借息约 1%/年」**：未核实 Backpack 借贷利率页面。

---

## 附：取证过程可复现路径

```bash
# 1) 镜像正文（Exa 后端）
agent-reach read "https://twstalker.com/LIU985985HU"

# 2) 浏览器抓 status ID + 图片 media id（Cloudflare 自动放行）
#    页面：https://twstalker.com/LIU985985HU → "Load More" 可翻到 ~3 周前

# 3) 图片直链（X 同一 media id）
curl -A "Mozilla/5.0" "https://ton-0.twimg.com/media/HW7lDwnbAAAcvdL.jpg" -o table17.jpg

# 4) 文档
curl "https://docs.lighter.xyz/trading/trading-fees.md"
curl "https://docs.lighter.xyz/developers/robinhood-chain/overview.md"
curl "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees.md"
curl "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-3-deployer-actions.md"
gh api repos/nautechsystems/nautilus_trader/contents/docs/integrations/lighter.md --jq .content | base64 -d
```
