# 回测结果回报书（nt-pairs · io:OAI 先行版 + 全候选矩阵）

版本 **2026-11-12** ｜ 仓库 `W:/nt-pairs` ｜ 引擎 **NautilusTrader 2.0.0rc6**（cp312 win wheel）

---

## 0. 执行摘要与裁决

| 问题 | 回答 | 证据 |
|---|---|---|
| 技术选型可行吗？ | **可行**。2.0.0rc6 原生带 `hyperliquid`（含 HIP-3 `io:OAI`）与 `lighter` 适配器；本机 1.231 **没有** Python 端 Lighter 适配器 | `.field-guide/nautilus_lighter_data.md` |
| 第一对标的正期望吗？ | **否**（`io:OAI ↔ LIGHTER OPENAI`）：回归 −36~−64 bps/轮、动量 −14~−62 bps/轮，成本 34 bps 盖不住 | `docs/2026-11-11-expectancy-test.md` |
| 有没有正期望的对？ | **有 4 个候选**：RKLB(+14.0) > MRVL(+5.5) ≈ EWY(+4.0) > SOXL(+2.7) bps/轮 | `docs/MATRIX_SUMMARY.md` §3 |
| 能上实盘吗？ | **不能**。① `p_fill` 未标定（97% 胜率疑似机械回归）；② 事件样本只有 2 天；③ OKX 腿未纳入多窗口统计；④ 2.0 runtime 节点未接线 | §6 |

---

## 1. 数据与口径

| 数据 | 端点 | 窗口 | 备注 |
|---|---|---|---|
| HL K 线/盘口 | `POST api.hyperliquid.xyz/info`（`candleSnapshot` / `l2Book` / `metaAndAssetCtxs` / `fundingHistory`） | 30d 1h、2d 5m、120min 1m | HIP-3 必须带 `dex`；不带 dex 返回主池 |
| Lighter K 线/盘口/成交 | `mainnet.zklighter.elliot.ai/api/v1/{candles,orderBookOrders,recentTrades,orderBookDetails}` | 同上 | 5 个参数全必填；时间戳秒→毫秒；单次约 500 根需分页 |
| OKX / Binance | **本机出口拦截**，只有 2026-11-11 截面快照（旧工作区） | 单点 | `www.okx.com`/`aws.okx.com`/`fapi.binance.com` 全部 000 |

对齐：同一时间桶的 close；任一侧缺桶 → 丢弃并计数；`basis_bps = (P_B/P_A/ratio − 1)×1e4`。
会话：`src/nt_pairs/sessions.py`（2026 NYSE 日历，含 DST、9 假日、2 提前收盘日）。

## 2. 筛选漏斗

```
326 条 HL 标的（10 个 dex） ─┐
209 个 Lighter perp        ─┼─→ 同名归一 → 82 对 → 单位校验 → 成本门禁 → 容量门禁 → 事件级回放
528 个 OKX（截面）          ─┘         (SKHX/ANT/ANTH 被单位闸门挡掉)
```

| 闸门 | 判据 | 淘汰 |
|---|---|---|
| ① 单位一致 | `ratio_mean` 偏离 1 > 5% 或 CV > 0.005 | `xyz:SKHX↔Lighter SKHYNIX`、`ANT/ANTHROPIC`（同名字不同标的，价差可达 164,000 bps） |
| ② 成本覆盖 | `p95(|b|) ≥ k·b*`，`k=1+1/√ESS` | NVDA / INTC / SNDK / MSTR / CRCL（基差 5~12 bps < k·b* 9~15 bps） |
| ③ 容量 | `0.1 × min(±25bps 深度)` ≥ $1k | KIOXIA / IREN（Lighter 侧深度 0） |
| ④ 事件级净边际 | 扣成本后 > 0 | BABA / INTC / NVDA / SNDK / MSTR / CRCL；KORU / KIOXIA 判为假象 |

## 3. 结果

### 3.1 矩阵（30 对，30d 1h，自动生成 `docs/MATRIX.md`）

| pair | p95 | b* | k·b* | 覆盖倍数 | 容量($) | 会话分裂 |
|---|---:|---:|---:|---:|---:|---:|
| xyz:CRWV↔167 | 49.2 | 17.5 | 18.2 | 1.70 | 6,600 | 0.14 |
| xyz:MRVL↔174 | 32.4 | 11.5 | 12.0 | 1.70 | 3,586 | 0.45 |
| xyz:SOXL↔197 | 25.4 | 14.7 | 15.3 | 0.66 | 11,547 | 0.11 |
| xyz:NBIS↔189 | 18.3 | 12.4 | 13.0 | 0.41 | 8,300 | 0.13 |
| xyz:DRAM↔195 | 13.4 | 9.2 | 9.6 | 0.41 | 38,007 | 0.16 |
| xyz:NATGAS↔158 | 24.8 | 17.5 | 18.3 | 0.36 | 9,477 | 0.02 |
| xyz:MSFT↔115 | 12.4 | 8.8 | 9.3 | 0.33 | 16,740 | 0.12 |
| xyz:COIN↔109 | 12.8 | 9.3 | 9.8 | 0.31 | 16,359 | 0.16 |

### 3.2 事件级回放（5m，下一根 open 入场，5m 滚动 4h 带，`|z|≥1.5` 反向入场、`|z|≤0.3` 出场）

| 标的 | 事件 | Capture | b* | **净 EV/轮** | 胜率 | RTH 净 | closed 净 | Lighter ±25bps 深度 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RKLB | 90 | +28.2 | 14.2 | **+14.0** | 79% | +14.3 | +18.3 | $131,941 |
| MRVL | 122 | +17.2 | 11.6 | **+5.5** | 90% | +12.6 | 0.0 | $243,785 |
| EWY | 113 | +18.6 | 14.6 | **+4.0** | 79% | +6.3 | +7.7 | $103,718 |
| SOXL | 119 | +17.0 | 14.3 | +2.7 | 97% | −1.5 | +7.7 | $251,259 |
| BABA | 108 | +16.2 | 15.6 | +0.5 | 87% | −6.1 | +5.2 | $59,278 |
| INTC / NVDA / SNDK / MSTR / CRCL | 105~133 | +3.2~+7.4 | 8.8~12.5 | **−3.0 ~ −5.8** | 77~97% | 负 | 负 | 深但无边际 |
| KORU / KIOXIA | 102 / 162 | +137 / +198 | 19.6 / 38.5 | +117 / +160 | 92/98% | — | — | $41k / $2.5k ⚠️ |

### 3.3 规模敏感性（SOXL 实测，L2 行走 VWAP）

| 单腿名义 | 实际滑点 | 该规模 b* | 净 EV/轮 |
|---:|---:|---:|---:|
| $100 | 2.9 | 13.0 | **+4.1** |
| $500 | 5.7 | 15.7 | +1.3 |
| $1000 | 9.4 | 19.4 | −2.4 |
| $5000 | 24.9 | 35.0 | −17.9 |
| $10000 | 32.4 | 42.5 | −25.4 |

**盘口深度给出的 $11.5k 是账面容量；真实可执行规模 $100–500/腿。**

### 3.4 成交率/排队模型（可靠输出 vs 不可靠输出）

可靠：HL 主动流 **16~218 笔/分**（均量 3~30 股）vs Lighter 主动买 **0.04~2.8 笔/分** → **maker 腿按 λ 选场所**。
不可靠：`P(fill)` —— 同一模型两次运行对 EWY 给出 3% 与 97%，差异全来自贴价队列的单次快照（1 vs 243 单位）。

## 4. 用到的建模理论（含他人理论与组件）

| 理论 / 组件 | 出处 | 在本项目里的用法 |
|---|---|---|
| OU 过程与 Bertram 最优配对交易 | arXiv:2102.04160 | `p95` 阈值的理论母体；成本内生化后进场带随 `b*` 平移 |
| 成本调整阈值 / 统计套利 | arXiv:0801.3348 | `b*` 六项进入阈值而非只看费差 |
| 成交概率（状态依赖排队） | arXiv:2403.02572 | `p_fill` 的半解析框架与标定目标 |
| 成交时间分布（survival） | arXiv:2306.05479 | 未来 LightGBM/Lightning 替代方案 |
| Shadow-PPOV（订单级影子跟单） | arXiv:2609.18019 | 「按已成交成交量跟随」的正式命名与撤单纪律 |
| CEX-DEX 套利速度竞争 | arXiv:2601.00738、2507.13023 | 说明「靠更快赢不了」→ 走低摩擦路线 |
| RWA 永续的除息不公 | arXiv:2610.22839 | 分红标的需在 `b*` 加除息项 |
| pre-IPO 永续定价 | arXiv:2609.05433 | `io:OAI` 这类标的的「公允价值」锚 |
| Avellaneda–Stoikov 库存偏斜 | 工业标准做市模型 | 方案 2 骨架；本项目用「目标仓位差」替代隐式偏斜 |
| Almgren–Chriss 最优执行 | Almgren & Chriss (2000) | 分批的价值是「中止期权」而非降冲击；`h*` 决定最优等待秒数 |
| Hummingbot XEMM（fill 驱动对冲） | GitHub 开源实现 | 「厚腿逐笔跟随厚腿成交」的范式来源 |
| `survivor_watcher_loop` | vooi-funding-bot | R6 孤儿腿的现成解法（1 s 轮询平幸存腿） |
| NautilusTrader 2.0 | PyPI `nautilus_trader==2.0.0rc6` | 执行引擎、适配器（hyperliquid / lighter）、回测撮合与 L2 行走 |
| 姊妹项目 arbTwin / GLFTmm | jssyxd 本地仓库 | 波段状态机、b*/p95 选票、被动退出队列、费率三元组 |
| LightGBM 成交概率模型 | Kaggle/Optiver 配方（**尚未训练**） | 下一步：用 `L1-fill` 标签 + 校准曲线替代常数 `p_fill` |

## 5. 负面结果（同样重要）

1. **`io:OAI` 的 3% 溢价不回归到 0**，而是围绕 +305 bps 稳态振荡（ρ=0.89、半衰期 6 h）→ 任何「向平价收敛」的假设都是错的。
2. **两个方向都亏**（针对 `io:OAI`）：回归 −36~−64、动量 −14~−62 bps/轮 → 该对**不可做**。
3. **「宽基差 = 可交易」是错的**：KIOXIA/IREN 的 p95 是 SOXL 的 6~13 倍，但 Lighter 侧没有盘口。
4. **深度好的名字被成本吃掉**：NVDA/INTC/SNDK 的盘口是 SOXL 的 5~10 倍深，但净 EV 为负。
5. **两个统计陷阱**：① 方向标注（我把顺势写成回归）；② tick 估计（高估滑点 10 倍）。两者都会把结论整个反过来。

## 6. 局限与「什么会改变结论」

| 局限 | 影响 | 怎么补 |
|---|---|---|
| 事件级样本只有 2 天 | EV 的置信区间很宽 | 采集 30 天 5m + L2 回放 |
| `p_fill` 未标定 | 97% 胜率可能是陈旧报价造成的机械回归 | 实盘 fill 日志 / L2 回放标定 + 成交后 markout |
| `recon = 0.5 bps`、`HL deployer fee share` 未含 | `b*` 可能 ±3 bps 偏差 | 用成交回执 `fee` 与对账耗时标定 |
| OKX 腿只有截面 | 无法评估「三腿」与更好的 taker 腿 | 换能访问 OKX 的出口，重跑矩阵 |
| 会话分层样本少（RTH 仅 ~30 笔） | 会话结论不稳 | 扩大窗口 |

## 7. 复现命令

```bash
cd W:/nt-pairs
python -m pytest -q                              # 92 passed
python scripts/build_matrix.py                   # 全量矩阵 → data/matrix.json + docs/MATRIX.md
python scripts/screen_candidates.py              # 候选事件级回放（含会话分层）
python scripts/sim_soxl.py                       # SOXL 完整执行模拟
python scripts/fill_model.py                     # 成交率/排队模型
python scripts/probe_basis.py --days 14          # 基差分布与 b*
```
