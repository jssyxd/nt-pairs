# 小额真单链路验证（2026-11-19，VPS malaysia）

## 目标

用户要求「小额真单」验证执行链路。本文档记录：**做了什么、真单试到哪一步、被什么挡住、还差什么**。

## 脚本

`scripts/live_smoke_order.py`（R4 agent 实现，captain 复核并修了 3 个真问题）。

硬闸门（全部写死在脚本里，不读配置）：

| 闸门 | 值 | 位置 |
|---|---|---|
| 单腿名义上限 | **$50**（默认 $20） | `MAX_NOTIONAL_USD` |
| maker TTL | ≤ 60s（默认 30s） | `MAX_TTL_S` / `DEFAULT_TTL_S` |
| 对冲 collar | ≤ 50 bps | `MAX_COLLAR_BPS_HARD` |
| 当日轮次 | ≤ 1 轮 | `MAX_ROUNDS_PER_DAY` |
| 当日累计亏损 | ≤ −$0.8（绝对美元） | `DAILY_LOSS_LIMIT_USD` |
| 两所最小量 | HL 0.01 / Lighter 0.085 股，名义均 ≥ $10 | `VENUE_MIN_QTY` / `VENUE_MIN_NOTIONAL_USD` |
| 并发 | 状态目录 PID 锁 | `_RunLock` |
| 幂等 | 当日固定 cloid，跨进程复用 | `SmokeState.cloids` |

退出码：`0` 两腿成交并平掉 / `2` 参数或每日闸门拒绝 / `3` 凭据缺失 / `4` 下单或成交失败 / `5` 裸腿或没平干净。

## 实测记录（UTC 2026-11-18，RKLB：HL `xyz:RKLB-USD-PERP.HYPERLIQUID` + Lighter `RKLB-PERP.LIGHTER`，$20/腿 = 0.37 股）

| # | 模式 | 结果 | 证据 |
|---|---|---|---|
| 1 | 沙盒 `--dry`（R4 agent 本机） | **exit 0「两腿已成交并平掉」** | `.herd-dispatch/jobs/smoke-order/report.md` |
| 2 | 沙盒 `--dry`（VPS，修复后） | **exit 0**，maker 53.1519 → 成交 → 对冲 → 平仓 | `/tmp/nt-smoke4/run.log` |
| 3 | 真单 `--send`（第一次） | **交易所拒单**：`Post only order would have immediately matched, bbo was 53.515@53.519` → 零敞口 | `/var/log/nt-pairs/smoke_send.log` |
| 4 | 真单 `--send`（修定价后，TTL 5s） | 交易所**接受**订单（post-only GTC，53.4349），TTL 内没被吃单 → 撤单，零敞口 | `smoke_send2.log` |
| 5 | 真单（TTL 30s） | 同上，53.2219，30s 内未成交 → 撤单 | `smoke_send3.log` |
| 6 | 真单 `--force-fill`（穿价 taker） | 订单**从未到达交易所**（`venue_order_id=None`，撤单报 `Order was never placed`） | `smoke_send7.log` |

## 修复的三个真问题

1. **定价用了旧盘口**（第 3 次尝试被交易所拒单的直接原因）：脚本拿 1.2 秒前的 bid 挂 maker，
   挂出去时已高于对手价 → post-only 被拒。修法：盘口新鲜度上限（`book_max_age_ms`，默认 1000ms）、
   交叉盘口直接跳过、挂在 **bid 下一个 tick**、被拒后按最新盘口重挂（`MAX_MAKER_ATTEMPTS=3`，换 cloid 后缀）。
2. **`with_timeout_connection` 传参类型错**：nautilus 2.0.0rc6 要**整数秒**，传 `timedelta` 直接
   `TypeError`（`live_node.py:715`）。
3. **trader 每 60 秒自杀**：`live_runner` 的 `--max-secs` **默认 60 秒**（节点到点优雅停机），
   systemd `Restart=always` 又把它拉起来 → 一夜 `NRestarts=6029`。单元改成 `--max-secs 0`，
   实测 `NRestarts=0`、5s 一跳心跳稳定。

## 被什么挡住：交易所账户

| 场所 | 权益 | 实测 |
|---|---|---|
| Hyperliquid 主钱包 `0xAF3E93d4…` | **0.0 USD** | `POST /info {"type":"clearinghouseState"}` → `accountValue: "0.0"`、`withdrawable: "0.0"`、无持仓 |
| Hyperliquid API 钱包 `0xf11D0ecF…` | **0.0 USD** | 同上 |
| Lighter `LIGHTER-767659` | **27.61 USDC**（free 27.61） | nautilus `Updated AccountState` |
| OKX | **403 Forbidden** | 签名请求 `GET /api/v5/account/balance` → HTTP 403（key/secret/passphrase 不匹配或已失效） |

因此：
- HL 腿**无法成交**：没有保证金，交易所不接单（nautilus 侧表现为「订单从未送达」，`venue_order_id=None`）。
- 第 3 次尝试能收到 HL 的 post-only 拒单消息，说明 **HL 私钥签名是通的**、订单确实到了交易所；
  卡住的是账户权益，不是凭证。
- OKX 凭证不可用（403），OKX 腿本轮不参与。

## 要跑通真实一轮，还差什么

1. **给 HL 钱包充值**（主钱包 `0xAF3E93d4…` 或 API 钱包 `0xf11D0ecF…`）：$25–50 USDC 足够
   （$20/腿 + 10x 杠杆下 2 倍保证金占用）。
2. **修 OKX 凭证**（或本轮只用 HL+Lighter）。
3. 跑法（**前台人工跑，不要做成 systemd 常驻**：没有交易所侧死亡开关，进程被 kill -9 会把
   挂单/裸腿留在交易所）：
   ```bash
   cd /opt/nt-pairs && set -a && . /etc/nt-pairs/env && set +a
   PYTHONPATH=/opt/nt-pairs/src .venv/bin/python scripts/live_smoke_order.py \
     --send --notional-usd 20 --ttl-s 30 \
     --state-dir /var/lib/nt-pairs --audit-out /var/lib/nt-pairs/audit.json
   echo "EXIT=$?"   # 0=两腿成交并平掉；5=裸腿（脚本会自动平 HL 腿并报残留）
   ```
4. 若要验证「强制成交 → 对冲 → 平仓」，加 `--force-fill`（仅 `--send` 可用，leader 腿用穿价 taker，
   成本 ≈ 名义 × 10bps ≈ $0.02）。

## 每次真单后的核账

- 脚本自带的审计 JSON（`/var/lib/nt-pairs/audit.json`）里有两腿的 `status`、`filled_qty`、`avg_px`、
  账户侧 `net_position`、残差、当日盈亏。
- 独立核账（不依赖脚本）：
  ```bash
  curl -s -X POST https://api.hyperliquid.xyz/info -H 'Content-Type: application/json' \
    -d '{"type":"openOrders","user":"<HL_MAIN_WALLET>"}'
  curl -s -X POST https://api.hyperliquid.xyz/info -H 'Content-Type: application/json' \
    -d '{"type":"clearinghouseState","user":"<HL_MAIN_WALLET>"}'
  ```
  两次真单尝试之后，这两条命令分别返回 `[]` 和 `accountValue 0.0 / assetPositions []`——**交易所侧没有残留挂单与持仓**。

## 当日闸门的记账说明

`/var/lib/nt-pairs/smoke_state.json` 里 `rounds` 会先占坑再下单。本日（UTC 11-18）共 6 次尝试，
每次都在**成交之前**结束（拒单 / TTL 未成交 / 无保证金未送达），账户侧始终零持仓、零损益；
为了继续试验，captain 手工把 `rounds` 归零并把 cloid 序号 +1，`history` 数组保留了全部尝试记录。
**没有一次尝试产生过持仓。**
