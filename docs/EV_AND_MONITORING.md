# 期望值（EV）与期望净边际：定义、逐标的数据、监控口径

本文件回答三件事：① 每个候选的**期望净边际**是多少、正还是负；② 实时监控要看什么、阈值怎么定；
③ 开平仓滑点怎么算、什么深度才算「可成交」。

---

## 1. 定义（全部用同一套符号）

一轮 = 在 leader 侧挂 maker 单 → 成交（或超时撤单）→ follower 侧按实成交量对冲 → 回到中值平仓。

$$
\text{EV}_{\text{round}}
=\underbrace{p_{\text{fill}}\cdot\Big(\text{Capture} - \text{Cost}_{\text{entry}} - \text{Cost}_{\text{exit}}\Big)}_{\text{成交时}}
-\underbrace{(1-p_{\text{fill}})\cdot \text{Cost}_{\text{opp}}_{\text{未成交的机会成本}}
$$

$$
\text{Cost}_{\text{entry}} = f_A^{maker}+f_B^{taker} + \tfrac{S_A+S_B}{2} + \text{slip}_A+\text{slip}_B + \text{carry} + r_{\text{leg}} + \text{recon}
$$

| 符号 | 含义 | 实测来源 |
|---|---|---|
| `Capture` | 基差从入场到出场的回归幅度（bps） | 事件级回放（`scripts/screen_candidates.py`） |
| `p_fill` | maker 单在 TTL 内的成交概率 | `scripts/fill_model.py`（**当前不可靠**，见 §5） |
| `f` | maker/taker 费率（bps） | HL 1.5/4.5（未含 deployer share）、Lighter 0/0、OKX 2/5 |
| `S` | 两腿半价差（bps） | 实时盘口 `l2Book` / `orderBookOrders` |
| `slip` | **按目标规模吃盘口**的 VWAP 偏离（bps） | L2 行走 VWAP（`docs/SOXL_SIM.md` §2） |
| `carry` | 资金费（bps） | HL `funding`（每小时）× 预期持仓时长 |
| `r_leg` | 腿风险溢价 = `√(2/π)·σ_1m·√(t_leg/60)·P_1leg·0.5` | `src/nt_pairs/execution.py` |
| `recon` | 对账摩擦（固定 0.5 bps） | 假设值，待标定 |
| `Cost_opp` | 未成交的机会成本 ≈ 0（只是错过） | — |

**年化与容量约束**

$$
\text{EV}_{\text{day}} = \text{EV}_{\text{round}} \times \text{rounds}_{\text{day}},
\qquad
\text{EV}_{\text{day}} \le N_{\max} \times \text{EV}_{\text{round}},\quad
N_{\max} = \Big\lfloor \frac{\text{depth}_{25bps}}{0.1 \times N_{\text{leg}}} \Big\rfloor
$$

## 1.5 ⚠️ 定价口径（2026-11-13 独立复核后的最重要一条）

事件级回放里 **入场价与离场价必须来自同一时刻的两腿**。
- ❌ 跨瞬间（错误）：入场用 bar 的 open、离场用 bar 的 close → 两条腿各自取价，捕获被高估 5~15 bps；
  同刻重算后 SOXL 从 +17.6 变成 **−1.5**、胜率 97.6% → **43.5%**。
- ✅ 同刻（正确）：两腿都用同一根 bar 的 close；更严格的是**盘口 mid 同一时刻**。
- 实测：盘口基差 1.5σ 偏离后 60s 回归 **+13.7 bps**（n=29），成交打印口径 +18.9 bps → **盘口口径才是可交易的**。
→ 本仓库的 Nautilus 策略用盘口 mid，方向是对的；**但所有基于 K 线 close 的历史回放都必须打折**。

## 2. 逐标的期望净边际（两次独立运行，bps/轮）

| 标的 | Capture | b*（成本） | **EV/轮（p_fill=1）** | p_fill=0.5 | p_fill=0.2 | 胜率 | 中位持仓 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| RKLB | +28.2 | 14.2 | **+14.0** | +7.0 | +2.8 | 79% | 4 min | ✅ 正期望 |
| MRVL | +17.2 | 11.6 | **+5.5** | +2.8 | +1.1 | 90% | 4 min | ✅ 正期望（薄） |
| EWY | +18.6 | 14.6 | **+4.0** | +2.0 | +0.8 | 79% | 5 min | ✅ 正期望（薄） |
| SOXL | +17.0 | 14.3 | **+2.7** | +1.3 | +0.5 | 97% | 4 min | ⚠️ 边际极薄 + 疑似机械回归 |
| BABA | +16.2 | 15.6 | +0.5 | +0.3 | +0.1 | 87% | 5 min | ❌ 不够覆盖成本 |
| INTC | +6.7 | 9.7 | −3.0 | −1.5 | −0.6 | 90% | 4 min | ❌ 负期望 |
| MSTR | +7.4 | 12.5 | −5.1 | −2.6 | −1.0 | 86% | 5 min | ❌ 负期望 |
| SNDK | +3.2 | 8.8 | −5.6 | −2.8 | −1.1 | 77% | 5 min | ❌ 负期望 |
| CRCL | +4.3 | 9.7 | −5.4 | −2.7 | −1.1 | 82% | 4 min | ❌ 负期望 |
| NVDA | +3.6 | 9.4 | −5.8 | −2.9 | −1.2 | 97% | 4 min | ❌ 负期望 |
| KORU | +137.1 | 19.6 | +117.5 | — | — | 92% | — | ⚠️ 疑似陈旧报价假象，不入池 |
| KIOXIA | +198.3 | 38.5 | +159.8 | — | — | 98% | — | ⚠️ 同上（Lighter 深度仅 $2.5k） |

**规模敏感性（SOXL 实测）**：$100 → +4.1；$500 → +1.3；$1000 → −2.4；$5000 → −17.9。
→ **EV 必须按目标规模重算**，用「2 tick 滑点」的 `b*` 会系统性高估 EV。

## 2.5 腿的选择原则（2026-11-12 实测更正）

**不要按「谁的深度好谁做 taker」选腿，要按「谁的费率低」选腿。**
同一套 fade 规则下，把对腿从 Lighter（0 费）换成 OKX（maker2+taker5）：

| 对腿 | 固定费/轮 | Capture 区间 | 净 EV 区间 | 胜率 |
|---|---:|---:|---:|---:|
| Lighter | 6 bps（只有 HL 腿） | +16.2 ~ +28.2 | **+0.5 ~ +14.0** | 79~97% |
| OKX | 13 bps（双腿） | +4.6 ~ +13.3 | **−6.7 ~ −13.0** | 95~100% |

胜率几乎不变、幅度变小、**净期望翻负**。差额来自**两处**（都已量化）：
① **费率**：OKX 腿 taker 5 bps vs Lighter 0 bps（同捕获下净差约 1.4 bps，因为 OKX 点差更小、抵消了一部分）；
② **捕获幅度**：OKX 腿的基差回归幅度只有 4.6~13.3 bps，Lighter 腿 16.2~28.2 bps —— **这才是主因**。
→ 腿选择的排序应是：**费率 → 点差 → 深度 → 成交概率**；深度排在最后，
   但**捕获幅度必须逐腿实测**（同一标的在不同对手腿上的回归幅度可以差 3 倍）。

**已落成代码**：`src/nt_pairs/leg_policy.py`（纯函数，10 个测试）
```python
LegQuote(venue, instrument_id, maker_bps, taker_bps, half_spread_bps,
          depth25_usd, tick, price, p_fill=None, carry_bps_h=0)
rank_legs(quotes, role="maker"|"taker", qty_usd)   # 费率 → 点差 → 深度 → p_fill
select_pair(quotes, capture_bps, qty_usd)          # 在候选腿里挑最优 (maker, taker)
expected_net_bps(maker, taker, capture_bps, qty)   # p_fill × (捕获 − 成本)
pair_cost_bps(maker, taker, qty)                   # 费+半价差+滑点+carry+腿风险+对账
```
策略层接入方式：每轮重算 `rank_legs` / `select_pair`，
**当某场所费率上调（活动结束 / VIP 降级）时该腿立刻出局**，这是硬约束。

**实测代入（SOXL，2026-11-12 实时盘口）**：
```
taker 腿排序（按费率→点差→深度→p_fill）: ['LIGHTER', 'HYPERLIQUID', 'OKX']
select_pair(capture=17 bps, qty=$500) → maker=HYPERLIQUID, taker=LIGHTER, 净=+8.67 bps/轮
```
即：**同一标的的三个场所，最优腿不是最深的那个，而是费率最低的那个**。

## 3. 会话分层的 EV（用户关心的「盘前/盘中/盘后/周末」）

| 标的 | RTH 净 | post 净 | pre 净 | closed 净 | 配置含义 |
|---|---:|---:|---:|---:|---|
| RKLB | +14.3 | — | — | +18.3 | 全时段可做 |
| EWY | +6.3 | — | — | +7.7 | 全时段可做 |
| MRVL | +12.6 | — | — | 0.0 | **只做 RTH** |
| SOXL | −1.5 | +3.2 | −0.2 | +7.7 | **只做 closed/post** |

会话标定用 `src/nt_pairs/sessions.py`（2026 NYSE 日历：9 个假日、2 个提前收盘日、DST 切换）。

## 4. 实时监控清单（每个 tick 都要算，缺一项就不许下单）

### 4.1 市场与盘口

| 指标 | 来源 | 阈值 | 动作 |
|---|---|---|---|
| 两侧 book 新鲜度 | WS 事件时间戳 | 任一 > 1 s | 停止报价（不追陈价） |
| 盘口档数完整性 | L2 快照 | 档数 < 5 或 `complete=False` | 停止报价 |
| 贴价队列量 | 同档 resting size | 逐笔采样 ≥3 次取中位数；> 200 单位 | P(fill) 不可信 → 不挂该侧 |
| ±25 bps 深度 | L2 行走 | < 10 × 单笔名义 | 该腿不可用（容量闸门） |
| 按规模吃盘口滑点 | L2 VWAP | > 2 bps | 降规模或换腿 |
| 价差 | top-of-book | 触及 `k·b*` 门槛 | 允许挂 maker |

### 4.2 基差与策略状态

| 指标 | 计算 | 阈值 | 动作 |
|---|---|---|---|
| 基差 z-score | `|b − b_hat| / σ_b`（滚动 4h / 24h） | 入场 ≥ 1.5，出场 ≤ 0.3 | 状态机切换 |
| 漂移 | `b_hat(t) − b_hat(t−24h)` | 反向且 > `θ_drift` | 禁止逆势开仓 |
| `b_hat` 跳变 | `|Δb_hat| > 2σ_b` | 命中 | **HALT**（结构性重定价，人工复核） |
| 腿残差 | `target − actual`（目标式） | `|residual| > 0` 持续 > `h*(k)` | 升级：chase → IOC → 人工 |
| 裸腿年龄 | `now − naked_since` | 60 s 告警 / 180 s 授权平 / 600 s HALT | 时间维度止损（价格止损抓不住慢速裸腿） |

### 4.3 场所与账户

| 指标 | 来源 | 阈值 | 动作 |
|---|---|---|---|
| HL 请求配额余量 | `10000 + 累计成交 − 已发` | < 100 | 只允许 reduce-only / cancel |
| 两侧持仓可读性 | `clearinghouseState(dex=xyz)` / `positions` | 任一读失败或结构不完整 | 视为 UNKNOWN → **禁止平另一腿** |
| 权益回撤 | 账户权益峰值 | ≥5% 停开新仓；≥10% KILL | 闸门 |
| 资金费 | HL `funding` / OKX `funding-rate` | 方向反转 | 提高门槛（carry 入 b*） |
| OI 与容量 | `openInterest` / `assetToStreamingOiCap` | 逼近 cap | 降规模 |

## 5. 深度与滑点公式（可直接写进代码）

```
depth25_usd(venue) = Σ_{p ≥ mid·(1−25bps)} p·q          # 买侧深度
slip_bps(venue, qty) = (VWAP(qty) / mid − 1) · 1e4        # 吃单偏离（负=有利）
capacity_usd = 0.10 × min(depth25_A, depth25_B)          # 单笔上限
b_star = fees_A + fees_B + (S_A + S_B)/2 + slip_A(q) + slip_B(q)
         + carry(h) + √(2/π)·σ_1m·√(t_leg/60)·P_1leg·0.5 + 0.5
可交易 ⟺ p95(|b|) ≥ k·b_star  ∧  capacity_usd ≥ 10 × 单笔名义  ∧  单位校验通过
k = 1 + 1/√ESS,  ESS = n(1−ρ)/(1+ρ)
```

**已知不可靠项**：`p_fill`（单次盘口快照会让结果在 3%↔97% 之间跳）→ 必须逐笔采样队列后再用；
`recon = 0.5 bps`（假设）→ 待实盘标定；`HL deployer fee share`（未含）→ 以成交回执 `fee` 校准。
---

## 6. 运行期座舱（把「跑两周」变成能盯的仪表盘）

> 面板：`reports/cockpit/cockpit.html`（**自包含单文件**，plotly.js 内联，可离线打开）
> 生成：`python scripts/cockpit.py`
> 内核：`src/nt_pairs/cockpit.py`（**纯标准库**）＋ `src/nt_pairs/cockpit_html.py`（渲染）

回测结果能回答「历史上赚不赚」，但**跑两周**要回答的是「**现在**还该不该继续跑」。
座舱把四类输入汇到一块面板上，并且把停机纪律做成**可执行的判据**（纯函数 + 单测），
而不是等人翻日志。

### 6.1 输入与板块

| 输入 | 面板板块 | 缺输入时的表现 |
|---|---|---|
| `data/chd_screen.json`（Stage B 回放） | 逐日净 EV、两腿深度（±25bps）、`b*` / `k`、模型 p_fill、单腿暴露时长分布 | 对应板块显示「无数据」 |
| `data/collector/<VENUE>/<YYYYMMDD>.jsonl` | 基差偏离曲线 + 滚动 z-score、两腿深度实测、重复收盘率 | 基差 / 深度 / 陈旧率板块显示「无数据」 |
| `data/fill_calibration.json`（P0-2 标定） | 实测 p_fill（与模型并排） | 成交率对比显示「无数据」 |
| `data/cockpit/backtest_stats.json`（可选） | 净值 / 回撤 / 回测统计 | 整个板块不出现 |
| `data/cockpit/live_fills.jsonl`（可选） | 今日 / 近 N 日 **实盘** P&L | 显示「**无实盘数据**」——**不显示 0** |

> **硬规则**：缺失 = 「无数据」，绝不拿 0 顶替。0 深度意味着「腿不可用」，
> 「没有深度数据」意味着「不知道」——在停机判据里两者含义完全相反。

### 6.2 停机判据（阈值集中在 `nt_pairs.cockpit.STOP_RULES`）

| 条件 | 红 | 黄 | 依据 | 实现位置 |
|---|---|---|---|---|
| 滚动净 EV 转负 | 连续 **3** 个交易日净 EV ≤ 0 | 连续 2 日 | 两周运行纪律 3：「周均净 EV 转负连续 3 天 → 暂停并复盘」；3 天是确认，不是噪音 | `cockpit._rule_net_ev` |
| 两腿深度不足 | 任一腿 ±25bps 深度 < **10 ×** 目标名义 | < 20 × | 闸门④（`chd_screen.py: gate4_capacity`：`depth_min ≥ 10 × qty_usd`） | `cockpit._rule_depth` |
| 成本覆盖失效 | 基差 p95 < **k·b\***（比值 < 1.0） | 比值 < 1.5 | 闸门③（`gate3_cost_cover`：`basis_p95_dev ≥ k × b*`） | `cockpit._rule_cost_cover` |
| 成交率塌陷 | 实测 p_fill < **60%** × 模型 p_fill | < 80% | 「必须先做的两件事」①：maker 腿成交率低于 60% 时 $100 档边际翻负 | `cockpit._rule_fill_rate` |
| 裸腿超时 | 单腿暴露 > **180 s** 未对冲 | > 60 s | `nt_pairs.risk.RiskLimits`：`naked_flatten_s=180` / `naked_warn_s=60` | `cockpit._rule_naked_leg` |
| 陈旧报价 | 任一腿重复收盘率 > **10%** | > 5% | 闸门②（`gate2_fresh`：`repeat_a/b ≤ 10%`）；RKLB / KORU / KIOXIA 的陈旧打印假象 | `cockpit._rule_stale_quote` |

调用方式（**纯函数、无 IO、可单独被 cron / CI 调用**）：

```python
from nt_pairs.cockpit import build_metrics, evaluate_stop_conditions, overall_status

metrics = build_metrics(chd=..., collector=..., calibrations=...)
conditions = evaluate_stop_conditions(metrics)   # {"depth": "GREEN: 两腿深度均 ≥ 10× …", …}
overall_status(conditions)                       # GREEN / YELLOW / RED / UNKNOWN
```

* 返回值形如 `"<状态>: <原因>"`，状态 ∈ `GREEN | YELLOW | RED | UNKNOWN`；
* **`UNKNOWN` = 无数据，不等于通过**（缺数据时不要把它当成绿灯）；
* 单测：`tests/test_cockpit_stop.py`（阈值表、绿/黄/红、缺数据、CLI 行为）。

命令行：

```bash
# 渲染面板（自包含 HTML）
python scripts/cockpit.py --out reports/cockpit/cockpit.html

# 只算判据（CI / cron 用，不需要 plotly）
python scripts/cockpit.py --metrics-only --metrics-out data/cockpit/metrics.json --strict
#   --strict：出现红色时退出码 3
```

### 6.3 口径说明（避免误读）

* **模型 p_fill 与实测 p_fill 单位不同**：模型是「每轮机会的成交概率」，
  实测是「每分钟成交概率」。判据用的是**归一化口径**（每分钟成交次数）：
  模型 = 单次机会成交概率 × 每分钟机会数，实测 = 成交数 ÷ 对齐分钟数。
  归一化不了（只有其中一侧）时，判据给 `UNKNOWN` 而不是硬比。
* **重复收盘率按小时收盘算**（与 `chd_screen.py: hourly_repeat_pct` 同口径）：
  采集器是秒级快照，直接按快照比会把「1 秒内没动」当成陈旧，指标会失真。
* **深度按 ±25bps 带统计**（买侧 `mid×(1−25bps)` 之上、卖侧 `mid×(1+25bps)` 之下），
  数量取盘口原始 qty（不乘订单数，避免重复计数）。
* **净 EV 来自 Stage B 回放时是「模型 EV」**，不是实盘；实盘跑起来后由
  `live_fills` / 运行期统计接管。

### 6.4 已知限制

1. 仓库里**没有实盘成交数据**（`data/cockpit/live_fills.jsonl` 不存在），
   所以 P&L 板块显示「无实盘数据」，裸腿超时判据为 `UNKNOWN`。
2. 采集器数据只在 VPS 上（`/opt/nt-pairs/data/collector/`），仓库里没有 →
   基差曲线 / 实测深度 / 陈旧率在 CI 里是空的。
3. `data/fill_calibration.json` 同样只在 VPS 上 → 成交率判据在 CI 里是 `UNKNOWN`。
4. 回测统计（`data/cockpit/backtest_stats.json`）需要跑一次
   `scripts/run_pair_backtest.py` 才有（公开端点，HL/Lighter 偶发 429）。
