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

胜率几乎不变、幅度变小、**净期望翻负** → 差异 100% 来自费率。
→ 腿选择的排序应是：**费率 → 点差 → 深度 → 成交概率**；深度排在最后。

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