# 实盘交易规格（RKLB / KIOXIA · 2026-11-13）

**状态**：`dry_run=true` 为默认值；真实下单需**逐级**放开（见 §7 上线闸门）。
**资金**：用户账户内仅有少量资金，且**明确接受全部亏损**；因此本规格的第一目标是「不把错误放大」，
第二目标才是「采集到可标定的成交数据」。

---

## 1. 两个交易标的与腿的分工

| 对 | leader（maker） | follower（taker） | backup（taker 兜底） | 用途 |
|---|---|---|---|---|
| **RKLB** | HL `xyz:RKLB`（买） | **Lighter `RKLB`（卖，0 费率）** | OKX `RKLB-USDT-SWAP`（卖，5 bps） | **主策略样本**：费率最优腿组合（固定费 4.5 bps） |
| **KIOXIA** | HL `xyz:KIOXIA`（买） | **OKX `KIOXIA-USDT-SWAP`（卖）** | — | **执行系统验证**：Lighter 无盘口、OKX 腿费率高、EV 为负 → 不放大 |

**为什么 leader 是 HL、follower 是 Lighter**（而不是反过来）：
两腿名义相同（美元中性），总固定费 = `maker 费(leader) + taker 费(follower)`。
- HL maker 1.5 + Lighter taker **0** = **1.5 bps**
- Lighter maker 0 + HL taker 4.5 = **4.5 bps**
→ 同一标的，**同样的捕获幅度，净 EV 差 3 bps**。这就是 `leg_policy.select_pair` 的输出逻辑
（实测：`taker 排序 LIGHTER > HYPERLIQUID > OKX`）。

## 2. 杠杆与保证金对齐（用户明确要求）

**规则：按保证金占用对齐，不按倍数对齐。**

```
N_leg ≤ min( 0.5 × E_HL × lev_HL(maxLeverage) ,
              0.5 × E_LT × lev_LT ,
              0.5 × E_OKX × lev_OKX )
```
- 两腿**美元名义相同**（不是股数相同：两场所的「1 单位」口径需用 `ratio` 校准，RKLB/KIOXIA 已校准为 1:1）。
- 各场所的**杠杆倍数**取该合约的允许上限（运行时从 instrument 读，不写死）：
  - HL HIP-3：`instrument.maxLeverage`（`xyz:*` 逐标的不同；默认逐仓 → 需显式设成 desired 模式）。
  - Lighter：股票永续 20x（pre-IPO 5x），isolated。
  - OKX：股票类参考 5x（实测 `ANTHROPIC` 为 20 → 以 API 为准）。
- **真正的约束是 50% 保证金占用**：账户小（OKX ~30u、HL/Lighter 未核）→ 实际下单量由最紧的一条腿反推，
  通常远低于 $500/腿。**代码必须自己算，不得照抄 500。**

## 3. 风险闸门（全部硬性，任一命中即动作）

| # | 闸门 | 阈值 | 动作 |
|---|---|---|---|
| 1 | 权益回撤（当日） | ≥2% 日亏损 / ≥5% 权益 | 停开新仓 / 全平 |
| 2 | 权益峰值回撤 | ≥10% | KILL（撤单 + 停服） |
| 3 | 裸腿年龄 | 60s / 180s / 600s | 告警 / 授权平 / HALT |
| 4 | 残差 | `|target − actual|` 持续 > `h*(2σ)`（≈ 71 s，σ=13） | 升级：chase 2 tick → 50 bps IOC → HALT |
| 5 | 超对冲 | 对冲腿累计 > 1.10× leader 成交 | 熔断撤单 + 人工 |
| 6 | 腿数据质量 | 该腿 1h 重复收盘率 > 10% | **该腿退出候选**，重选腿 |
| 7 | 基差门槛 | `p95(|b|) ≥ k·b*`（k 由 ESS 算） | 不满足 → 不开仓 |
| 8 | 会话 | 按 `session_policy`（RKLB 全时段；KIOXIA 仅 closed/post） | 不在白名单 → 不开仓 |
| 9 | 每日轮次 | ≤ 3 轮/日 | 超出 → 停开新仓（当日不再开） |
| 10 | 交易所配额 | HL 余量 < 100 | 只放 reduce-only / cancel |
| 11 | 资金费 | carry > 5 bps/轮 | 提高门槛（`b*` 动态加） |

## 4. 执行路径（与 nautilus 2.0 接线对齐）

```
1) 行情：两腿 L2（WS；回退 REST 轮询）→ 本地 OrderBook
2) 触发：|basis − b_hat| ≥ 1.5σ_b（滚动 4h 带），且通过 §3 全部闸门
3) leader 挂 post-only（贴价），TTL = h*(1σ) ≈ 1.4 s（σ=13）→ 8.5 s（Lighter 半价差口径）
4) leader 成交 → 立刻按**实际成交量 × ratio** 向 follower 发 IOC 限价（collar 2 tick → 50 bps）
5) follower 未成 → 备份腿（OKX）接；再不成 → 升级到 50 bps IOC；再不成 → 平掉 leader 侧
6) 出场：|basis − b_hat| ≤ 0.3σ_b 或 24h 超时 → 双向平仓（先平 follower，再平 leader）
```

**绝不做**：市价单、无上限的追价、超过 3 轮的循环。

## 5. 部署拓扑（VPS 155.254.60.38 · Debian 12 · 1 vCPU / 960MB / 8GB）

| 组件 | 位置 | 说明 |
|---|---|---|
| 代码 | `/opt/nt-pairs`（git clone 自 GitHub） | `uv` 装 Python 3.12 + `nautilus_trader==2.0.0rc6` |
| 凭据 | `/etc/nt-pairs/env`（600 root-only） | HL 主钱包/API 钱包、Lighter key 4/5、OKX accountbot4 |
| 采集器 | `nt-collector.service`（systemd） | 19 目标 × 3 场所，一轮 ≈ 6 s（VPS 在新加坡，延迟远低于本地 CN 出口的 40 s） |
| 交易程序 | `nt-trader.service`（systemd，`dry_run=1` 起） | 由 `src/nt_pairs/live_runner.py` 启动（2.0 `LiveNode` 接线见 E6 报告） |
| 报告 | `nt-report.timer`（每 6 h） | 生成 `reports/<ts>.md` 并用 deploy key 推到仓库 `reports/`；CI 再做汇总 |

**磁盘**：采集器 gzip 跨天压缩 + 保留 7 天 → 约 1GB；仓库/venv 约 1.7GB；余量充足。

## 6. 凭证与安全

- `/etc/nt-pairs/env` 600，仅 root；**绝不**进仓库、绝不出现在日志。
- HL API agent 钱包 `CTA` 有效期 30 天（自 2026-11-04 → **约 2026-12-04 到期**），到期前必须换新。
- Lighter 用 key index 4 为主、5 备用（同一账户 767659）。
- OKX accountbot4：IP 白名单已含 `155.254.60.38` / `23.27.186.126`；**passphrase 未提供** →
  OKX 私有交易不可用（公开数据可用）。KIOXIA 的 taker 腿因此**必须**补 passphrase 才能实盘。
- GitHub token 曾在聊天里明文出现 → 已在仓库中脱敏，但**建议 rotate**。

## 7. 上线闸门（顺序不可跳）

1. **数据质量**：RKLB 的 Lighter 腿 30 天重复收盘率 31% → **当前状态 `blocked`**。
   先用采集器重算（`analyze_collector.py`），若仍 >10% → 该腿退出，RKLB 改用 OKX 兜底腿（并重算 `b*`）。
2. **fill 标定**：`p_fill` 与成交后 markout 必须有实测值；无实测不得放大到 $500/腿。
3. **dry-run**：连续 24 h 无异常（对账闭合、残差归零、状态机无错）。
4. **极小额实盘**：$100/腿、≤1 轮/日、连续 3 天净值不劣化于成本模型。
5. **逐档放大**：$100 → $200 → $500，每档重跑 3 天；任一档 EV 转负即回退一档。
6. **兜底**：任一闸门连续触发 2 次 → 停该腿；停 1 天 → 停整个服务并人工复核。

## 8. 每 6 小时的汇报内容（CI + VPS）

1. 进程状态（collector / trader / systemd 重启次数）；
2. 采集统计（样本数、每腿重复收盘率、盘口新鲜度、深度中位数）；
3. 基差与带参数（`b_hat`、`σ_b`、`p95`、`k·b*`、当前 EV 估计）；
4. 成交/未成交计数、平均等待、成交后 markout（1/5/15 分钟）；
5. 风控事件（裸腿年龄分布、超对冲次数、熔断次数、被拒的开仓原因分布）；
6. 新发现（与上一份报告的差分）。

由 VPS 生成原始报告并推 `reports/`，CI（`schedule: 0 */6 * * *`）做汇总与失败通告，
详见 `docs/CI_CD.md`。
