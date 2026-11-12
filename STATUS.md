# 状态（2026-11-11 收尾）

## 2026-11-12 追加：三场所矩阵阶段

| 项 | 状态 | 证据 |
|---|---|---|
| 矩阵规格（窗口/会话/成本/容量/排序） | ✅ | `docs/MATRIX_SPEC.md` |
| 会话标定（2026 NYSE 日历 + DST + 提前收盘） | ✅ 7 测试 | `src/nt_pairs/sessions.py`、`tests/test_sessions.py` |
| capitan 基线矩阵（HL↔Lighter 7 对 × 30d + 实时 b*/深度） | ✅ | `docs/MATRIX_PREVIEW.md` |
| 全量矩阵（30 对流动性最好的，多窗口 + 会话 + 容量 + 单位校验） | ✅ | `scripts/build_matrix.py` → `data/matrix.json`、`docs/MATRIX.md` |
| 候选事件级回放（12 对，5m，会话分层，容量） | ✅ 两次独立运行稳定 | `scripts/screen_candidates.py` → `docs/MATRIX_SUMMARY.md` §3 |
| 规模敏感性（滑点随规模） | ✅ | `docs/SOXL_SIM.md` §5 |
| 成交率 / 排队模型（λ、均量、贴价队列） | ✅ | `scripts/fill_model.py` → `docs/FILL_MODEL.md` |
| SOXL 完整执行模拟 | ✅ | `scripts/sim_soxl.py` → `docs/SOXL_SIM.md`、`data/soxl_sim.json` |
| OKX 多窗口序列 | ❌ **本机出口拦截** | 改用 ant-cross-arb 工作区 2026-11-11 截面快照 |
| 多 agent 并行 | ❌ **provider 配额耗尽** | `429 insufficient_quota`（重置 12-07 16:00 UTC）；`--kind grok` 需人工登录 |

### 本阶段三条关键结论

1. **单位校验是矩阵第一道闸门**：不校验时 `xyz:ANT ↔ Lighter ANTHROPIC`（164,000 bps）与
   `xyz:QNT`（52,000 bps）会排到榜首——那是**同名字不同标的**。
   判据：`ratio_mean` 偏离 1 > 5% 或 CV > 0.005 → `unit_fail`。
2. **SOXL 的回归（fade）边是当前唯一被实测支持的**：5m 事件回放 119 笔，捕获 +17.1 bps、胜率 97%、
   中位持仓 4 分钟；`b*=13.7` → **净 +3.4 bps/轮**（p_fill=0.2 时 +0.7）。
   **会话分裂显著**：RTH 净 **−0.6**、closed 净 **+8.2** → 应避开美股盘中。
3. **宽度 ≠ 可执行**：`io:OAI`/`KIOXIA`/`IREN` 的 p95 高达 200~340 bps，但 Lighter 侧 ±25bps 深度为 0。


| 项 | 状态 | 证据 |
|---|---|---|
| 新仓库 + 契约 | ✅ | `CONTRACT.md`、`configs/*.json`、`README.md` |
| 纯逻辑内核（pair_spec/ledger/execution/risk） | ✅ | `src/nt_pairs/`，`python -m pytest tests -q` → 66 passed |
| Nautilus 策略（leader maker + follower IOC 影子跟单） | ✅ 可实例化 | `src/nt_pairs/strategy.py`；np2 下 6 个冒烟测试通过 |
| 运行时配置装配（HL + Lighter） | ✅ 配置层 | `python -m nt_pairs.runtime --check` 可脱敏打印 |
| 运行时节点启动 | ❌ **未接线** | Nautilus **2.0 移除了 `TradingNode`/`TradingNodeConfig`**；本仓库未实现 2.0 的新 runtime 启动路径 |
| 基差实测与裁决 | ✅ | `docs/2026-11-11-basis-probe.md`（p95 395 bps；k·b*≈21 bps；瓶颈是深度） |
| 正期望检验（回归 + 动量） | ✅ **两侧都为负** | `docs/2026-11-11-expectancy-test.md` |
| 容量/经济性 | ✅ 单腿上限 $5–65 | `docs/2026-11-11-economics.md` |
| 版本定型 | ✅ nautilus_trader **2.0.0rc6**（1.231 无 Python 端 Lighter 适配器） | `.field-guide/nautilus_lighter_data.md` |
| 独立评审 agent | ⏳ 未派发 | 评审要点已写在 `.herd-dispatch/jobs/reviewer/input.md` |

## 测试口径

| 解释器 | 结果 | 说明 |
|---|---|---|
| `python`（3.12，全局 nautilus 1.219） | 66 passed, 6 skipped | strategy 冒烟测试按版本跳过 |
| `C:\Users\da\venvs\np2\Scripts\python.exe`（2.0.0rc6） | 72 passed | 含 `tests/test_strategy_smoke.py` |

## 结论（先于代码）

1. **技术选型可行但只到配置层**：2.0.0rc6 原生提供 `hyperliquid`（HIP-3 `io:OAI` 可解析）与 `lighter` 适配器；
   但 2.0 的引擎启动 API 与 1.x 完全不同，`TradingNode` 已不存在，实盘接线需要单独一轮研究。
2. **这对标的目前不具正期望**：基差 ~+305 bps 的结构性溢价（ρ=0.89、半衰期 6 h）；
   20 天因果回测里回归策略 −36~−64 bps/轮（胜率 7~20%），动量策略 −14~−62 bps/轮；
   往返成本 34 bps 无法被覆盖。
3. **容量被 HL `io:OAI` 盘口锁死**：±25 bps 深度 $45~652（同一小时内波动），单腿上限 $5~65。
4. **本仓库的当前价值是「执行系统正确性」**：目标式账本、fill 驱动对冲、带状+漂移过滤、时间止损、
   dry-run 默认、永不市价。收益候选应换到 `xyz` ↔ Binance 的股票对（`configs/soxl_xyz_binance.json` 为候选，未验证）。

## 上实盘前置条件

1. 含成本与漂移过滤的因果回测净期望 > 0 且分月不翻转；
2. Nautilus L2 回放的 maker 成交率与实盘 500 ms 标定一致；
3. 单腿 ±25 bps 深度 ≥ 10 × 单笔名义（`io:OAI` 当前不满足）；
4. 2.0 runtime 启动路径打通（当前缺口）。

---

## 2026-11-12 矩阵阶段的行动清单（按优先级）

1. **候选收敛到 4 对**：RKLB > MRVL ≈ EWY > SOXL（净 +2.7~+14 bps/轮，两次独立运行排序稳定）。
2. **给这 4 对做 fill 级验证**（当前最大的未知）：用 L2 回放或 paper 跑 24h，标定
   `p_fill(Δt)` 与成交后的 markout；**97% 的胜率必须先证伪**（很可能是陈旧报价造成的机械回归）。
   已有部分答案（`docs/FILL_MODEL.md`）：**HL 主动流 16~218 笔/分，Lighter 只有 0.04~2.8 笔/分**
   → maker 腿应按 λ 选场所；但单次盘口快照会让 P(fill) 在 3%↔97% 之间跳，
   **挂单前必须逐笔采样贴价队列（≥3 次取中位数）**。
3. **按目标规模重算 `b*`**：SOXL 实测 $100→净 +4.1、$500→+1.3、$1000→−2.4；
   容量必须用「按规模吃盘口后的滑点」而不是账面深度。
4. **会话过滤逐对配置**：RKLB/EWY 会话不敏感；MRVL 只在 RTH；SOXL 只在 closed。
   「避开美股盘中」不是通用规则。
5. **补齐 OKX / Binance 历史**：需要一个能访问 `www.okx.com` 的出口（本机被拦）。
6. **恢复多 agent 复核**：provider 配额 12-07 16:00 UTC 重置；`--kind grok` 需要人工登录 x.ai。
