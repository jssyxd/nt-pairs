# KIOXIA / RKLB 部署指导书（VPS 实盘前置）

> 生成于 2026-11-13 · 目标节点：`losangeles` (23.27.186.126) · 仓库：`jssyxd/nt-pairs`

## 0. 先读这一条：这两个标的**现在不能实盘**

主窗口 2026-11-12 深夜的最终裁决（`DECISION.md`）已经把它们判死：

| 标的 | 矩阵结论 | 原因 | 证据 |
|---|---|---|---|
| `xyz:RKLB` | `tradable=false` | Lighter 侧 1h close **重复率 31.4%**（闸门阈值 10%） | `data/matrix.json` `stale_print_suspect=true` |
| `xyz:KIOXIA` | `tradable=false` | Lighter 侧重复率 **54.8%** + Lighter 侧 ±25bps 深度≈0（容量上限仅 $1,000） | 同上 |

即：KIOXIA 的「+155 bps/轮」、RKLB 的「+14 bps/轮」是**薄市场沿用上一笔打印造成的机械回归**，不是可成交的错价。

**所以「在 VPS 上部署交易 KIOXIA 和 RKLB」的第一步不是下单，是先证明这两个价差能不能成交。**
本指导书按这个顺序编排：第 1–3 步现在就能做；第 4 步之后必须等采集数据与 fill 标定结果。

## 1. 已完成的部署（2026-11-13 实测）

| 项 | 状态 | 证据 |
|---|---|---|
| 采集器 systemd 服务 | ✅ `nt-collector.service` 已 enable + running | `systemctl is-active nt-collector` → `active` |
| 采集标的 | ✅ HL/Lighter/OKX 各 2 个（KIOXIA + RKLB），17→6 目标 | `--symbols KIOXIA,RKLB` |
| 采集频率 | ✅ 2 s 一轮（三所各一轮，实测一轮 2.3 s） | 日志 |
| 磁盘占用 | ✅ 实测 **148.8 MB/天**（三所合计，未压缩），7 天保留 + 跨天 gzip | `df -h /` 剩 2.5 GB |
| 资源占用 | ✅ 内存 12.4 MB（上限 256 MB）、CPU 50% 配额 | `systemctl status` |
| Nautilus 2.0.0rc6 | ✅ 装在 `/opt/nt-pairs/venv312`（Python 3.12.16） | `importlib.metadata.version` → `2.0.0rc6` |
| 两个适配器 | ✅ `hyperliquid` + `lighter` 均可用 | `from nautilus_trader.adapters.lighter import ...` |
| 节点装配 | ✅ `nt_pairs.live_runner --dry-run` 通过 | LiveNode + PairHedgeStrategy + PairHeartbeatActor 注册成功 |
| WS 链路 | ✅ 两所均连上并订阅成功 | `Subscribed xyz:RKLB-USD-PERP.HYPERLIQUID order book deltas` / `Subscribed RKLB-PERP.LIGHTER order book deltas` |
| L2 数据实流 | ✅ Lighter 211 条 / 30 s；HL 少量（正常，见 §4.3） | `scripts/smoke_node.py` RESULT |
| 两对节点实跑 | ✅ RKLB 与 KIOXIA 都在 VPS 上跑通 dry-run，heartbeat 正常 | `nt_pairs.live_runner --dry-run` |

## 2. VPS 环境（为什么是这个配置）

实测 `losangeles`：**1 vCPU / 960 MB RAM / 9.8 GB 磁盘（剩 2.5 GB）/ Debian 12 / 原生 Python 3.11**。

| 约束 | 影响 | 处置 |
|---|---|---|
| 无 `python3.12` 包 | nautilus 2.0.0rc6 要求 `>=3.12,<3.15` | 用 `uv` 装独立 CPython 3.12.16 → `/root/.local/bin/uv python install 3.12` |
| 1 vCPU / 960 MB | 一轮三所采集需 2.3 s，**做不到 1 Hz** | 采样间隔设 2 s；`CPUQuota=50%` `Nice=10` 避免影响同机 `ant-arb.service` |
| 磁盘 2.5 GB | 采集 148 MB/天 | `--keep-days 7` + `--compress`（跨天关闭后压成 `.gz`） |
| 同机跑着 `ant-arb.service` | 采集器不能抢资源 | `MemoryMax=256M` `CPUQuota=50%` |
| `curl` 被拦、`urllib` 通 | 监控/采集一律用 urllib | 已在 field guide 记录 |

## 3. 已落地的文件

| 文件 | 作用 |
|---|---|
| `scripts/collect_l2.py` | L2 采集器。新增：`--symbols` 过滤、`--depth` 档位上限、`--root`、`--keep-days`、`--compress`；**修好 OKX 腿**（`/market/tickers` 对所有 instId 返回 400，改用单数 `/market/ticker`）；新增 KIOXIA 三所目标 |
| `scripts/analyze_collector.py` | 分析器。改为可读 `.jsonl.gz`，并容忍被截断的归档 |
| `scripts/smoke_node.py` | **新增**：只连数据、不用密钥、不下单的节点冒烟，验证两腿 WS 真的收到增量 |
| `src/nt_pairs/live_node.py` + `live_runner.py` | Nautilus 2.0 节点装配与启动（`LiveNodeBuilder`），补上 STATUS.md 里「运行时节点启动 ❌ 未接线」的缺口。**dry-run 用沙盒执行端 + 凭据泄漏自检**，比裸 `LiveNodeBuilder` 更安全 |
| `configs/kioxia_xyz_lighter.json` | **新增**：KIOXIA 配对配置（含实测 tick/费率/容量/单位证据） |
| `configs/rklb_xyz_lighter.json` | **新增**：RKLB 配对配置 |
| `deploy/nt-collector.service` | **新增**：采集器 systemd 单元 |

### 3.1 实测标的规格（2026-11-13）

| 项 | HL `xyz:KIOXIA` | HL `xyz:RKLB` | Lighter KIOXIA (225) | Lighter RKLB (186) | OKX KIOXIA-USDT-SWAP | OKX RKLB-USDT-SWAP |
|---|---|---|---|---|---|---|
| instrument_id | `xyz:KIOXIA-USD-PERP.HYPERLIQUID` | `xyz:RKLB-USD-PERP.HYPERLIQUID` | `KIOXIA-PERP.LIGHTER` | `RKLB-PERP.LIGHTER` | `KIOXIA-USDT-SWAP.OKX` | `RKLB-USDT-SWAP.OKX` |
| tick | 0.001 | 0.001 | 0.01 | 0.01 | 0.01 | 0.01 |
| 数量步长 | 0.001 | 0.01 | 0.0001 | 0.0001 | 0.01 | 0.01 |
| 最小名义 | $10 | $10 | $10 | $10 | $10 | $10 |
| maker / taker bps | 1.5 / 4.5 | 1.5 / 4.5 | **0 / 0** | **0 / 0** | 2 / 5 | 2 / 5 |
| 杠杆上限 | 10（逐仓 noCross） | 10（逐仓 noCross） | — | — | — | — |
| deployerFeeScale | 1.0 | 1.0 | — | — | — | — |
| 24h 成交额 | ~$12.0 M | ~$7.6 M | **$14.7 K** | **$146 K** | — | — |

> Lighter 侧 KIOXIA 日成交仅 **$14.7K**、RKLB 仅 **$146K** —— 这就是重复收盘率的物理来源。

### 3.2 实测盘口（2026-11-13 快照）

| 标的 | 场所 | 中位价差 | ±25bps 深度（bid/ask） |
|---|---|---|---|
| KIOXIA | HL | 8.2 bps | $1.7 K / $3.0 K |
| KIOXIA | Lighter | **35.5 bps** | $4.4 K / $7.9 K |
| KIOXIA | OKX | 13.5 bps | $2.0 K / $1.5 K |
| RKLB | HL | 3.0 bps | $213 K / $281 K |
| RKLB | Lighter | 6.1 bps | $72 K / $55 K |
| RKLB | OKX | 4.6 bps | $27 K / $33 K |

RKLB 两侧容量都够（单腿 $100 远小于深度）；KIOXIA 三所都很薄，且 Lighter 侧价差 35 bps 已经超过 `b*`（29.2 bps）。

## 4. 接下来的步骤（严格按序）

### 4.1 现在做：让采集器跑满 2 周

采集器已在跑。**不要**因为「想快点看到结果」而降低采样质量。

```bash
# VPS 上查看进度
systemctl status nt-collector
wc -l /opt/nt-pairs/data/collector/*/*.jsonl
```

### 4.2 两周后：跑 fill 标定

```bash
cd /opt/nt-pairs
PYTHONPATH=/opt/nt-pairs/src /opt/nt-pairs/venv312/bin/python analyze_collector.py \
  --root data/collector --qty 100 --out data/fill_calibration.json
```

判据（**沿用主窗口的规则，不新增**）：

| 指标 | 阈值 | 不达标怎么办 |
|---|---|---|
| `p_fill_per_min` | 越大越好；净 EV = `net_bps_per_fill × p_fill_per_min` 必须 > 0 | 净 EV ≤ 0 → **放弃该对** |
| Lighter 侧重复收盘率 | ≤ 10% | 超标 → 该腿**停用**（不是降仓） |
| 成交后 markout_5 | 均值 > 0（对 fade 方向） | ≤ 0 → 机械回归，不成立 |
| 每侧 ±25bps 深度 | ≥ 10 × 单腿名义 | 不足 → 降规模 |

### 4.3 已查清：HL 侧 WS「条数少」不是故障

节点冒烟里 HL 30 s 只收到 3 条增量，而 Lighter 收到 211 条，疑似 HL 没推流。
**已用裸 WebSocket 客户端直连 `api.hyperliquid.xyz/ws` 验证**：

```
订阅 {"type":"l2Book","coin":"xyz:RKLB"} → 20 s 内收到 3 条 channel=l2Book 消息
订阅 {"type":"l2Book","coin":"xyz:KIOXIA"} → 20 s 内收到 3 条
```

→ HL 的 `l2Book` **推的是整档快照（每条含完整 20 档），不是增量 diff**，
且只在盘口变化时才推。`xyz:RKLB` 在 30 s 内只变动 3 次，所以条数少是**正常的**。
（对照：Lighter 推真增量，条数自然多。）

结论：**HL 链路无故障**，不要按「WS 没数据」排查。标定仍以采集器 REST 轮询数据为准。

### 4.4 标定通过后：纸面 24 h（dry-run）

```bash
cd /opt/nt-pairs
PYTHONPATH=/opt/nt-pairs/src /opt/nt-pairs/venv312/bin/python -m nt_pairs.live_runner \
  --config configs/rklb_xyz_lighter.json --dry-run --max-secs 86400
```

`--dry-run` 走**沙盒执行端**、不读私钥、不可能发真实订单（启动前还会自检凭据字段为空）。
核对：两腿 `subscribed=true`、heartbeat 里 `residual` 恒为 0、`naked_age_s` 不增长、状态机无异常。

> 实测（2026-11-13，VPS 上 RKLB 对跑 45 s）：heartbeat 每 5 s 一条，
> `basis_bps` 在 **−5.5 ~ +6.1 bps** 之间跳动，两腿 `subscribed` 均为 true，`residual=0`。
> **注意这个区间远小于 RKLB 的 `b*`=16.7 bps** —— 再次印证「RKLB 的 +14 bps/轮」不是可成交的回归。

**KIOXIA 对跑（同样 25 s dry-run）**：`basis_bps` 在 **+19.3 ~ +23.4 bps** 之间，
两腿 `subscribed=true`、`residual=0`。
但 KIOXIA 的 `b*`=**29.2 bps**（六项成本）——**实测基差 19~23 bps 仍低于 b\***，
且 Lighter 侧价差中位 35.5 bps、重复收盘率 55%。→ **两个标的当前都过不了成本闸门。**

### 4.5 纸面通过后：极小额实盘

| 项 | 值 |
|---|---|
| 单腿名义 | **≤ $100**（RKLB 可到 $500；KIOXIA 在容量闸门修好前不得超过 $100） |
| 轮次 | ≥ 20 轮 |
| 判据 | 实测 EV 与标定差异 < 50% |
| 密钥 | `/etc/nt-pairs/env`，权限 600，**不入库**；OKX key 禁提现 + 绑 VPS IP |

## 5. 密钥与账户（还没做的部分）

```bash
install -m 700 -d /etc/nt-pairs
install -m 600 /dev/null /etc/nt-pairs/env
# HL_PRIVATE_KEY= / HL_ACCOUNT_ADDRESS=
# LIGHTER_PRIVATE_KEY= / LIGHTER_ACCOUNT_INDEX= / LIGHTER_API_KEY_INDEX=4 / LIGHTER_ACCOUNT_ID=
```

- HL：独立钱包，只用主账户 + 1 个 API agent 子账户；配额按地址计（`10000 + 累计成交`），**换 IP = 换地址**
- Lighter：API key slot ≥ 4（0~3 留给官方 App）；`LIGHTER-001` 账户；maker/taker 0 费是本策略支柱
- 死亡开关：HL `scheduleCancel`（≥5 s）+ Lighter 等价 API，**当前两个场所都没接**（STATUS.md 已记录）

## 6. 明确禁止（照抄主窗口的裁决）

- ❌ 不要把 KORU/KIOXIA 的 +100~155 bps 当机会（重复收盘 93% / 55%）
- ❌ 不要放大名义额（$1,000/腿净边际转负，$5,000/腿 −17.9 bps）
- ❌ 不要用 OKX/Binance 做 taker 腿，除非重算 `b*` 并确认捕获幅度（费率 5 bps + 回归更弱）
- ❌ 不要在 fill 标定前关掉 `dry_run`

## 7. 相关文档

- `DECISION.md` — 五道闸门与最终裁决（**先读这个**）
- `docs/DEPLOYMENT_GUIDE.md` — 通用部署指导书（VPS 选型、WS 心跳、对账、回滚）
- `docs/MATRIX_SUMMARY.md` — 三场所矩阵结论
- `STATUS.md` — 逐项状态表
