# 部署指导书（nt-pairs · io:OAI 先行版 + 全候选矩阵）

目标：在 **NautilusTrader 2.0.0rc6** 上把「leader maker + follower IOC 影子跟单」的两腿系统
安全地跑起来。本文覆盖：VPS、账户与密钥、WS 订阅与心跳、持久化保护、WS+REST 混合、
监控与告警、上线闸门、回滚手册。

> ⚠️ **默认 dry-run**：仓库内所有下单路径默认 `dry_run=True`（只写日志，不发真实请求）。
> 三道闸门（回测正期望 → fill 标定 → 纸面 24h）未过之前，不得关闭该开关。

---

## 1. VPS 选型

| 项 | 建议 | 理由 |
|---|---|---|
| 地域 | **香港 / 新加坡 / 日本**，或美西 | 必须能直连 `api.hyperliquid.xyz`、`mainnet.zklighter.elliot.ai`、`www.okx.com`。**中国大陆出口实测被拦**（`www.okx.com`/`aws.okx.com`/`fapi.binance.com` 全部 000）；Lighter 的 WS 也有地域闸门（`code:20558`） |
| CPU | 2 vCPU 起，4 vCPU 稳 | 撮合是 Rust 扩展，Python 只做策略；L2 增量处理很轻 |
| 内存 | 4 GB（L2 缓存 8 GB 更稳） | Nautilus 的订单簿缓存 + 回放 |
| 磁盘 | 40 GB SSD | 行情落盘与状态 WAL |
| 网络 | 静态公网 IP、固定出站 | OKX API 白名单、HL 配额按地址计（换 IP = 换地址） |
| 系统 | Ubuntu 22.04 / Debian 12，Python 3.12 | Nautilus 2.0.0rc6 有 cp312 wheel（cp313/314/315 亦有） |
| 时钟 | `chrony` + NTP，机器时间必须准 | 会话判定与 TTL 全部依赖单调时钟 |

```bash
# 基础环境
sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv chrony jq
timedatectl status                     # 必须 synchronized
```

## 2. 仓库与虚拟环境

**仓库在网络共享盘时，venv 必须放本地盘**（否则 pip 扫描会挂几分钟，见 field guide）。

```bash
git clone https://github.com/jssyxd/nt-pairs.git
python3.12 -m venv /opt/nt-pairs-venv
/opt/nt-pairs-venv/bin/pip install -U pip
/opt/nt-pairs-venv/bin/pip install "nautilus_trader==2.0.0rc6" pyyaml pytest
/opt/nt-pairs-venv/bin/pip install -e .           # 或直接 pip install -e ".[dev]"
/opt/nt-pairs-venv/bin/python -c "import nautilus_trader, importlib.metadata as m; print(m.version('nautilus_trader'))"
```
> ⚠️ **版本锁死 2.0.0rc6**：1.231.0 的 Python 包**没有** Lighter 适配器（只有 Rust crate）。

## 3. 账户与密钥（每个场所、每个账户）

### 3.1 Hyperliquid（主腿 / 被动腿 / 配额瓶颈）

| 项 | 值 | 说明 |
|---|---|---|
| 钱包 | 独立钱包，**只用主账户 + 1 个 API agent 子账户** | HL 请求配额 **per-address**：`10000 + 累计成交量`；子账户刷量**不会**给主账户释放配额 |
| 私钥 | `HL_PRIVATE_KEY`（env） | 只能读行情的独立钱包优先 |
| 账户地址 | `HL_ACCOUNT_ADDRESS` | 2.0.0rc6 的 `HyperliquidExecutionClientConfig(account_address=…, private_key=…, environment=…)` |
| 杠杆 | 逐仓 ≤ 10x（HIP-3 dex 自带上限，`maxLeverage` 逐标的读） | 默认逐仓；不要改成全仓 |
| 抵押币 | HL `xyz`/`io` 用 **USDC**；部分 dex 用 USDH/USDE/USDT0 | 换汇成本要进 `b*` |
| 保护 | 下单即挂 `scheduleCancel`（≥5s 死亡开关，≤10 次/日，UTC 0 点重置）+ 仓位级 TP/SL（触发不耗配额） | 两个场所都有死亡开关，**当前都没接** |

**HIP-3 注意**：`allPerpMetas` 会自动载入所有 dex 的 HIP-3 永续；instrument 形如 `io:OAI-USD-PERP.HYPERLIQUID`。
每个 dex **自设部署方费率倍率**（最多分走一半）→ **费率必须用成交回执的 `fee` 字段标定**，不要用基础档。

### 3.2 Lighter（对冲腿 / 零费率）

| 项 | 值 | 说明 |
|---|---|---|
| 部署 | `LighterDeployment.LIGHTER`（`ROBINHOOD` 是第二部署，USDG 结算，同适配器） | 不要跨这两个部署做腿 |
| API key slot | **≥ 4**（0~3 留给官方 App） | 每个 key 独立 nonce |
| 私钥 | `LIGHTER_PRIVATE_KEY`（env） | 注册公钥需要 L1 钱包签名 |
| 账户 | `LIGHTER_ACCOUNT_INDEX` / `LIGHTER_ACCOUNT_ID` | 2.0.0rc6：`LighterExecutionClientConfig(account_id=AccountId(...), account_index=…, api_key_index=…, private_key=…, environment=…)` |
| 标的 | `OPENAI` = market_id **192**（perp，maker/taker 费 **0**，min_base 0.0035、min_quote $10、price_decimals 2） | 13 个股票 + 3 个 pre-IPO |
| 额度 | 建议给**子账户**，单账户上限低 | |

### 3.3 OKX（可选的第二 taker 腿 / 备用）

| 项 | 值 | 说明 |
|---|---|---|
| API | `OKX_API_KEY` / `OKX_SECRET` / `OKX_PASSPHRASE` | **只读** key 足够做监控与选票 |
| 权限 | 交易 key 关闭提现；**绑定 IP 白名单** | 生产 VPS 固定 IP |
| 标的 | `ANTHROPIC-USDT-SWAP`、`ASML/IREN/KIOXIA/KORU/SOXL/INTC/...-USDT-SWAP`，`ctVal=1`（1 张 = 1 股） | 414 个股票类永续 |
| 杠杆 | 股票类永续官方参考表写 **5x + 限美股时段 + posSide 必填**，但 `ANTHROPIC` 实测 `lever=20` 且闭市成交过 → **必须实测**（见 §7） | 24/7 引擎若遇不可交易窗口，裸腿风险预算要改 |
| 手续费 | Lv1 maker 2 / taker 5 bps | 以账户等级为准 |

### 3.4 Binance（可选，流动性最好）

| 项 | 值 | 说明 |
|---|---|---|
| 市场 | 235 个 `TRADIFI_PERPETUAL`（SOXL 24h 约 $5.85B、NVDA $1.34B） | `contractSize=None` → 推断 1 单位 = 1 股，**上线前小额实测校验** |
| API | 标准 futures key | 同上：禁提现 + IP 白名单 |

### 3.5 密钥纪律（硬性）

```bash
# 只用环境变量；绝不写进仓库、绝不打印
install -m 700 -d /etc/nt-pairs
install -m 600 /dev/null /etc/nt-pairs/env
# 内容示例（权限 600，仅 root 可读）
HL_PRIVATE_KEY=...            HL_ACCOUNT_ADDRESS=0x...
LIGHTER_PRIVATE_KEY=...       LIGHTER_ACCOUNT_INDEX=...   LIGHTER_API_KEY_INDEX=4
LIGHTER_ACCOUNT_ID=LIGHTER-001
OKX_API_KEY=...              OKX_SECRET=...              OKX_PASSPHRASE=...
```
- systemd 用 `EnvironmentFile=/etc/nt-pairs/env`；**不要** `Environment=` 内联。
- 仓库 `.gitignore` 必须含 `.env`、`*.pem`、`state/`、`data/`。
- 日志里禁止出现完整私钥；异常处理只打印公钥前 6 位。

## 4. 市场数据订阅（WS 为主，REST 兜底）

### 4.1 订阅清单

| 场所 | 订阅内容 | 用途 | 参考 |
|---|---|---|---|
| Hyperliquid | `l2Book`（`io:OAI` 等标的）、`trades`、`activeAssetCtx`（mark/funding/OI/24h量）、`allMids` | 本地订单簿、成交事件、资金费与容量 | HL `info` 端点 / 2.0 `hyperliquid` 适配器 |
| Lighter | 订单簿增量 + 成交（market_id=192 等） | 本地订单簿、成交事件 | `mainnet.zklighter.elliot.ai` |
| OKX | `books`（L2，`sz=50`）/`trades`/`tickers` | 备用 taker 腿与跨所监控 | OKX v5 public WS |

### 4.2 WS 心跳与新鲜度保护（必须实现）

| 保护 | 规则 | 触发动作 |
|---|---|---|
| 应用层心跳 | 每 **15 s** 发 ping/订阅保活；**45 s** 无任何消息 → 判定断链 | 关闭该 venue 的数据源 → 停止该腿报价 → `FREEZE_NEW` |
| 订单簿快照同步 | 连接后先取 REST 快照，再按 seq 补增量；**seq 缺口** → 重取快照 | 缺口期间不报价 |
| 陈旧死区 | 盘口事件时间 > **1 s**（HL）/ **2 s**（OKX） | 停止报价（不追陈价） |
| 时钟同步 | 系统时钟偏差 > **200 ms** → 告警并暂停开仓 | 人工确认后恢复 |
| 重连 | 指数退避 0.5s→30s，抖动 ±20%；重连后**先对账持仓再允许下单** | 对账不闭合 → `HALT` |
| 单腿降级 | 一侧 WS 断链时，**禁止在另一侧新开仓**（只能 reduce-only） | 避免裸腿 |
| 速率预算 | HL 每分钟请求预算 = `10000 + 累计成交量 − 已发`；**余量 < 100 只放 reduce-only/cancel** | 触发即降级 |
| 死亡开关 | HL `scheduleCancel`（≥5s）+ OKX `cancel-all-after` + Lighter 等价 API | 进程崩溃时在途挂单自动撤销 |
| 幂等 | HL `cloid`（128-bit hex）、OKX `clOrdId`（≤32）、Lighter `clientOrderId`；**重试复用同一 id** | 防重复下单 |

### 4.3 WS + REST 混合（职责划分）

| 用 WS | 用 REST |
|---|---|
| 行情、订单簿、成交事件（低延迟） | 启动对账、周期对账（5 s）、撤单/改单失败后的兜底、订单查询、资金费/OI 快照、异常后的状态修复 |
| 订单提交（快路径） | **幂等校验**：重发前先按 id 查单，查不到才允许重下 |
| 成交回报 | 成交回执缺失时的补偿查询（按 cloid 轮询） |

**对账循环（每 5 s，独立进程）**：
```
1) 读两腿真实持仓（HL clearinghouseState 必须带 dex；OKX positions；Lighter account）
2) 任一读失败/结构不完整 → pos_known=False → 禁止平仓与新开仓（只 WARN）
3) 连续 3 次读数一致才认为可信
4) target = -Σ(其他腿)×ratio；residual = target − actual
5) |residual| 超限 → 升级动作（chase → IOC → FLATTEN → HALT）
```

### 4.3.1 先装采集器（决定 fill 标定能否完成）

`scripts/collect_l2.py` 在 VPS 上按 1 Hz 落盘两/三场所的盘口快照（价格/量/订单数）、mark、funding、OI、24h 量。
**必须先跑它**：HL `candleSnapshot` 单次最多约 500 根、无分页 → 拿不到 30 天分钟级历史；
没有逐笔 L2 就无法标定 `p_fill`、队列长度与成交后 markout。

```bash
cd /opt/nt-pairs
nohup /opt/nt-pairs-venv/bin/python scripts/collect_l2.py --interval 1 --days 30 \
  > state/collector.log 2>&1 &
```
- 输出：`data/collector/<VENUE>/<YYYYMMDD>.jsonl`（每行一条完整快照）。
- 磁盘：13 标的 × 1 Hz ≈ 每日 60~120 MB/场所 → 建议 `/data` 独立盘，保留 30 天。
- **采集器只用 urllib**：`curl` 在部分出口被拦而 urllib 正常（见 field guide），监控/采集脚本一律用 urllib。

### 4.3.2 腿费率监控（腿会因活动结束而变贵）

| 监控项 | 频率 | 阈值 | 动作 |
|---|---|---|---|
| 各场所 maker/taker 费率 | 1 h | 相对基线变化 > 0.5 bps | **冻结该腿**（不再作为 taker），重算 `b*` 与腿排序 |
| 活动/返佣窗口 | 1 h | 窗口结束 | 同上；Lighter 的 0 费是本策略支柱，**失效即换腿或停做** |
| 深度与点差 | 实时 | 见 §4.1 | 见 §4.1 |

`src/nt_pairs/leg_policy.py` 的 `rank_legs` / `select_pair` 直接消费这些报价，
**费率变化 → 腿排序变化 → 自动切换**，不需要改代码。

### 4.4 持久化保护

| 状态 | 介质 | 写法 | 恢复 |
|---|---|---|---|
| 成交流水账 | JSONL append | 每笔 append + fsync | 启动时重放 → 重建 `residual` |
| 带状参数 `b_hat/σ_b` | JSON | temp + `os.replace`（原子） | 启动时读回；读不到则重新预热，期间只做观察 |
| 状态机 | SQLite（可选）或 JSON | 原子替换 | 启动时**先对账再进主循环** |
| 幂等 id | 内存 + 流水账 | 重试复用 | — |

```python
# 原子写（仓库内已用此模式：src/nt_pairs/strategy.py::_save_state）
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh: json.dump(state, fh); fh.flush(); os.fsync(fh.fileno())
os.replace(tmp, path)
```

### 4.1.1 数据质量闸门（2026-11-12 新增，上线前必须打开）

薄市场的一侧可能**长时间不成交** → 它的 close 只是沿用上一笔打印 → 基差曲线出现台阶 →
「回归」看起来极漂亮但**不可成交**。实测：Lighter 的 RKLB 30 天里有 **31%** 的小时 close 与上一根完全相同，
KORU **36%**（且 30 天只有 189 根），而 MRVL 4% / EWY 5% / SOXL 1%，HL 侧全部 ≤1%。

运行期规则：
1. 每小时算一次两腿的「重复收盘率」（连续 1h close 相同的比例）。
2. 任一侧 > **10%** → 该腿**立即退出候选**（不是降仓，是停用），并重新跑 `select_pair`。
3. 历史回测脚本（`screen_candidates.py` / `screen_candidates_okx.py`）已输出 `repHL/repLT` 与 `STALE` 标记；
   **任何被标 `STALE` 的对，其 EV 结论一律作废**。

## 5. 进程与 systemd

```ini
# /etc/systemd/system/nt-pairs.service
[Unit]
Description=nt-pairs two-leg hedge (dry-run by default)
After=network-online.target chronyd.service
Requires=chronyd.service

[Service]
Type=simple
User=ntpairs
WorkingDirectory=/opt/nt-pairs
EnvironmentFile=/etc/nt-pairs/env
Environment=PYTHONUNBUFFERED=1
Environment=NT_PAIRS_DRY_RUN=1
ExecStart=/opt/nt-pairs-venv/bin/python -m nt_pairs.runtime --pair configs/openai_entropy_lighter.json
Restart=always
RestartSec=10
TimeoutStopSec=60
KillSignal=SIGTERM
# 进程退出前必须撤单
ExecStopPost=/opt/nt-pairs-venv/bin/python -m nt_pairs.runtime --cancel-all

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now nt-pairs
journalctl -u nt-pairs -f
```

## 6. 监控与告警

| 类别 | 指标 | 阈值 | 动作 |
|---|---|---|---|
| 连通 | WS 心跳延迟、REST 成功率 | 心跳 > 30 s / REST 失败率 > 5% | 重连 + 降级为 reduce-only |
| 数据 | 盘口新鲜度、seq 缺口、档数 | > 1 s / 有缺口 / < 5 档 | 停止报价 |
| 策略 | 基差 z-score、漂移、`b_hat` 跳变 | 入场 1.5σ / 出场 0.3σ / 漂移 > θ / 跳变 > 2σ | 状态机切换 / HALT |
| 风险 | 腿残差、裸腿年龄、权益回撤 | residual ≠ 0 > h*；60/180/600 s；5%/10% | WARN → FLATTEN → KILL |
| 账户 | HL 配额余量、两腿保证金占用 | 余量 < 100 / 占用 > 50% | 只放 reduce-only / 降规模 |
| 容量 | ±25bps 深度、按规模滑点 | < 10×单笔 / > 2 bps | 降规模或换腿 |
| 成本 | 实际成交滑点 vs 预算 | 超出 > 2 bps | 提高门槛（滑点内生化） |

告警出口：`journald` → 简单脚本/邮件/Telegram；长期用 Prometheus + Grafana（Nautilus 有内置 metrics 端口）。

## 7. 上线闸门（缺一不可，按顺序）

1. **回测闸门**：含成本、含漂移过滤的因果回测净 EV > 0，且分月不翻转（当前：`RKLB/MRVL/EWY/SOXL` 候选，
   但样本仅 2 天 → **未过**）。
2. **标定闸门**：`p_fill(Δt)` 与成交后 markout 用实盘 fill 日志/L2 回放标定（当前：未标定）。
3. **纸面闸门**：`dry_run=1` 连续跑 **24 h**，核对：两腿事件流无丢失、residual 归零、状态机无异常。
4. **极小额闸门**：真实下单，**单腿名义 ≤ $100**，跑 ≥ 20 轮，实测 EV 与回测差异 < 50%。
5. **放量闸门**：按规模敏感表逐档放大（$100 → $500 → $1000），每档重新核对净 EV。
6. **上线检查**：IP 白名单、死亡开关已接、告警已通、备份与回滚就绪、密钥轮换计划。

### 7.1 上线前必须核实的一条
**OKX 股票类永续是否限美股时段**：官方参考表写「5x + 限时段 + posSide 必填」，而 `ANTHROPIC` 实测
`lever=20` 且在闭市时段成交过。若限制成立，**24/7 引擎的 OKX 腿存在整段不可交易窗口**，
裸腿风险预算与 `h*` 都要重算。验证：闭市时段做一次「下单 → 立即撤单」，看是否被拒。

## 8. 回滚与故障手册

| 症状 | 判定 | 处置 |
|---|---|---|
| 进程崩溃 | systemd `Restart=always`；死亡开关已挂 | 重启后**先对账**，残差按 A/B/C 查表处理（R6 教训） |
| 一腿 WS 断 | 心跳超时 | 该腿只做 reduce-only；禁止新开仓 |
| 读到空持仓列表 | **不当作平仓** | 三态：连续 3 次一致 + 无在途单 + 结构完整才可信 |
| HL 配额耗尽 | `Too many cumulative requests` | 只放 reduce-only/cancel；用 `scheduleCancel` 兜底 |
| 单腿裸持 | residual ≠ 0 | 60 s 告警 → 180 s 授权平 → 600 s HALT（**价格止损抓不住慢速裸腿**） |
| 权益回撤 | ≥5% / ≥10% | FREEZE_NEW / KILL |
| 疑似结构性重定价 | `b_hat` 跳变 > 2σ | HALT 人工复核（pre-IPO 溢价一次性消失是主要尾部） |
| 状态文件损坏 | 原子写 + 校验 | 丢弃重建，预热期间只观察不交易 |

## 9. 部署检查清单

- [ ] VPS 地域能直连 HL / Lighter / OKX；时钟已同步
- [ ] nautilus_trader **2.0.0rc6**（含 hyperliquid + lighter 适配器）
- [ ] 三个场所的密钥进 `/etc/nt-pairs/env`（600 权限），**不在仓库**
- [ ] OKX/Binance key 禁提现 + IP 白名单
- [ ] WS 心跳 15s / 死区 1s / 断链 45s / seq 缺口重取快照 已实现
- [ ] 对账 5 s、三态语义、时间维度止损 60/180/600 已实现
- [ ] 幂等 id（cloid / clOrdId / clientOrderId）已接
- [ ] 死亡开关（HL `scheduleCancel`、OKX `cancel-all-after`）已接
- [ ] 持久化原子写 + 启动先对账后交易
- [ ] 告警通道已验证（含一次真实触发）
- [ ] `dry_run=1` 连续 24 h 无异常
- [ ] 回滚手册演练一次（杀进程 → 在途单被撤 → 重启后对账闭合）
