# 决策备忘（2026-11-12 深夜 · nt-pairs）

## 一句话结论

**技术栈可行；策略只在「数据干净 + 费率最低的腿 + 容量够」的组合上成立。**
当前唯一通过全部闸门的候选：**HL `xyz:SOXL`（maker 腿）↔ Lighter `SOXL`（taker 腿）**，净 **+5.3 bps/轮**，规模 **$100–500/腿**；
次选 **MRVL +1.5**（RTH 更强）。其余全部出局。

## 五道闸门（缺一不可，顺序固定）

| # | 闸门 | 判据 | 谁被它挡下 |
|---|---|---|---|
| ① | **单位一致** | `\|ratio_mean − 1\| ≤ 5%` | `ANT/ANTHROPIC`（164×）、`io:OAI`（17×）、`SKHX/SKHYNIX`（165×） |
| ② | **数据新鲜** | 两腿 1h close 重复率 ≤ 10% | Lighter 侧：RKLB 72%、KORU 88%、KIOXIA 93%、EWY 56%、BABA 39%（30 天口径 31~93%） |
| ③ | **成本覆盖** | `p95(\|b\|) ≥ k·b*`，`k=1+1/√ESS` | NVDA / INTC / SNDK / MSTR / CRCL（基差 10~12 bps < k·b* 8~15 bps） |
| ④ | **容量** | `0.1 × min(±25bps 深度) ≥ 10× 单笔` | KIOXIA / IREN（Lighter 侧深度 0） |
| ⑤ | **事件级净 EV** | 扣六项成本后 > 0（含 p_fill 情景） | INTC/NVDA/SNDK/MSTR/CRCL（−4.8~−7.7） |

> 闸门②是本项目**最后补上**的一条，也是最狠的一条：**它把此前“最优”的 RKLB 直接推翻**。

## 本项目经历的三次自我推翻（方法论教训）

| # | 错误 | 现象 | 修正 | 残留规则 |
|---|---|---|---|---|
| 1 | 把**顺势**写成「回归」 | 顺势 −36 bps 被解读成「两个方向都亏」 | 顺势/回归互为镜像 → 回归其实是 +36 | 变量名必须写 `fade` / `trend`，禁止叫「回归」 |
| 2 | tick 用经验规则 | `$100` 以下 tick 取 0.1 → 2-tick 滑点高估 **10×**（32.7 vs 0.33 bps） | 从盘口价位反推 tick | tick 一律 `infer_tick(book)` |
| 3 | 未查**陈旧报价** | RKLB/KORU 的「+14 / +112 bps/轮」是 Lighter 沿用旧打印造成的机械回归 | 统计相邻桶 close 重复率 | `RepeatedCloseDetector`，>10% 即停用该腿 |

**通则：任何 >90% 胜率的回归结果，先查重复收盘率与成交后 markout，再谈 EV。**

## 腿选择规则（与直觉相反）

**费率 → 点差 → 深度 → 成交概率**（深度排最后）。实测：把对腿从 Lighter（0 费）换成 OKX（maker2+taker5），
胜率不变但净 EV 从 +0.5~+14 变成 −6.7~−13 bps/轮 —— 差额来自 **taker 费 5 bps** 与 **OKX 腿捕获幅度更小**（4.6~13.3 vs 16.2~28.2 bps）。
`src/nt_pairs/leg_policy.py` 已把这条规则写成纯函数（`rank_legs` / `select_pair` / `expected_net_bps`）。

## 立即可做（不需要新权限）

```bash
cd W:/nt-pairs
python -m pytest -q                                  # 全部绿（106 项）
python scripts/screen_candidates.py                  # 候选事件级回放（含 repHL/repLT 与 STALE 标记）
python scripts/screen_candidates_okx.py               # 换 OKX 腿对照
python scripts/build_matrix.py                       # 三场所矩阵（MATRIX_MAX_PAIRS 控制规模）
python scripts/sim_soxl.py                           # SOXL 滑点/成本/规模敏感性
python scripts/fill_model.py                         # 成交率/排队模型
```

## 需要外部条件才能推进

| 项 | 条件 | 产物 |
|---|---|---|
| **fill 级标定**（最关键） | 在能直连三所的 VPS 上跑 `scripts/collect_l2.py`（1 Hz，13 标的） | 2 周后用 `scripts/analyze_collector.py` 得到真实 `p_fill`、队列、成交后 markout → 才能判定 SOXL 的 +5.3 bps 是否可成交 |
| **OKX 腿进矩阵** | `curl` 被拦但 `urllib` 通（已在脚本里用 urllib）；若要三所全历史，需能访问 OKX 的出口 | 完整三场所矩阵 |
| **Nautilus 2.0 节点启动** | 2.0 删了 `TradingNode`，需研究新 runtime | 实盘接线 |
| **多 agent 复核** | provider 配额 12-07 16:00 UTC 重置（或在 pane `wF:pP` 登录 x.ai） | 独立评审 + 采集器并行化 |

## 当前禁止

- 不要放大名义额（$1000/腿净边际转负，$5000/腿 −17.9 bps）。
- 不要把 KORU/KIOXIA 的 +100 bps 当机会（Lighter 侧 88~93% 重复收盘）。
- 不要用 OKX/Binance 做 taker 腿，除非重算 `b*` 并确认捕获幅度（它们更贵且回归更弱）。
- 不要在 provider 配额恢复前指望多 agent 复核；本轮的「agent 产出」已被 429 打断过一轮。
