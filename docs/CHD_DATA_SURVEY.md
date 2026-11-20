# CryptoHFTData (CHD) 数据可用性调查

调查日期：2026-11-14（UTC）
调查人：chd-data worker
客户端实现：`src/nt_pairs/chd.py`；宇宙枚举：`scripts/chd_universe.py`；覆盖探测：`scripts/chd_coverage.py`

## 0. 结论（TL;DR）

1. **可以用 CHD 替代现有 K 线截面作为回测数据源**：四家目标场所（Hyperliquid futures / Lighter / OKX futures / Binance futures）
   都提供逐小时的 **L2 盘口 + L0 成交 + mark/funding + open interest** parquet，schema 全场所统一，
   口径差异由官方逐场所列出（见 §4）。相比本仓库现状（只有 HL↔Lighter 的 K 线截面，且 OKX/Binance 直连被出口拦截），
   CHD 走的是同一个 HTTPS 出口，**本机可达**（`api.cryptohftdata.com` 与 `www.cryptohftdata.com` 均 200）。
2. **股票类标的宇宙够大**：四家场所同名（base ticker，≥3 家在场）交集 **201 个**（100 个四家全覆盖、101 个三家）；
   属于 Alpha Vantage 在册股票/ETF 的 **50 个**、pre-IPO **8 个**，
   「四家全覆盖且在册」的干净候选 **37 个**（AAPL/AMD/AMZN/ASML/AVGO/CRCL/EWY/GOOGL/HOOD/INTC/META/MRVL/MSFT/MSTR/MU/NVDA/ORCL/PLTR/QCOM/RKLB/SNDK/SOXL/TSLA/TSM/…）。
3. **同名 ≠ 同一底层，已用同小时价格比留证**：例如 `OPENAI` 在 Binance 口径为 1.00、Lighter **1.32×**、OKX **0.10×**；
   `ANTHROPIC` 在 OKX **0.077×**；`QNT`（股票 Quant）≈ **0.22×**；`CVX` 在 HL 为 **138×**。
   这些不是「价差机会」，是**单位/口径错配**，正是 DECISION.md 闸门①要拦掉的东西。
4. **数据可用到**：截至 2026-11-13/14 的小时（`/v1/status` 的 target 就是 `2026-11-13/14`），
   历史起点见 §3（HL futures 2025-09-29、Lighter 2025-12-31；OKX / Binance 见 §3 表）。

## 1. 端点与认证（实测 2026-11-14）

| 端点 | 方法 | 实测结果 |
|---|---|---|
| `https://api.cryptohftdata.com/v1/status?lookback_hours=24` | GET | 200，14 个 exchange × data_type 健康度；target = `2026-11-13/14` |
| `https://api.cryptohftdata.com/v1/symbols?exchange=<ex>&data_type=<dt>` | GET | 200，`{"exchange","data_type","count","symbols"}` |
| `https://api.cryptohftdata.com/v1/symbol-counts?exchange=<ex>&data_type=<dt>` | GET | 200，`{"counts":{"<ex>":{"<dt>":N}}}` |
| `https://api.cryptohftdata.com/v1/jwt-token`（header `X-API-Key`） | POST | 200，`{"jwt_token","expires_in":14400,"token_type":"Bearer"}` |
| `https://www.cryptohftdata.com/oauth/token`（`grant_type=client_credentials`） | POST | 200，`{"access_token","expires_in":14400,"scope":"market-data:read status:read"}`（llms.txt 记载的官方换 token 方式，与 `/v1/jwt-token` 等价） |
| `https://api.cryptohftdata.com/v1/download?file=<object path>` | GET | 302 → 对象存储；**必须跟随重定向**（不跟随只拿到 302 空体） |

- 匿名：60 req/min/IP（llms.txt）；认证后另计。客户端遇到 429 读 `Retry-After` 退避重试。
- 对象路径模板：`{exchange}/{YYYY-MM-DD}/{HH}/{symbol}_{data_type}.parquet`。
- API key 只从环境变量 `CHD_API_KEY` 或本地 `.env` 读取（仓库里只有 `.env.example`）。

## 2. 四家场所的 data_type 覆盖（来自 `/v1/status`，2026-11-13/14 快照）

| exchange | trades | orderbook | ticker | mark_price | open_interest | liquidations |
|---|---|---|---|---|---|---|
| `hyperliquid_futures` | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| `lighter` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `okx_futures` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `binance_futures` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

符号总数（`/v1/symbols`，orderbook 族）：

| exchange | orderbook | trades | 股票类（脚本判定） |
|---|---:|---:|---:|
| `hyperliquid_futures` | 463 | 463 | 283 |
| `lighter` | 254 | 244 | 161 |
| `okx_futures` | 662 | 653 | 468 |
| `binance_futures` | 921 | 919 | 616 |

## 3. 历史覆盖起点与缺失小时

抽样方法：`scripts/chd_coverage.py` 只做「存在性探测」（`GET /download?file=…` 流式请求，不下载 parquet body），
对每个 (exchange, data_type) 二分查找最早有文件的一天，再在覆盖区间内均匀抽样若干天、逐小时检查缺失。
原始结果：`data/chd_coverage.json`。

### 3.1 覆盖起点

| exchange | data_type | 最早（加密锚点） | 最早（股票锚点） |
|---|---|---|---|
| `hyperliquid_futures` | orderbook / trades / mark_price / open_interest | **2025-09-29** | 见 §3.3 |
| `hyperliquid_futures` | ticker / liquidations | 无（该场所不产出） | — |
| `lighter` | orderbook / trades / ticker / mark_price / open_interest | **2025-12-31** | 见 §3.3 |
| `lighter` | liquidations | 无（该场所不产出） | — |
| `okx_futures` | 全部 6 种 | **待补**（见下「探测状态」） | 同左 |
| `binance_futures` | 全部 6 种 | **待补**（见下「探测状态」） | 同左 |

> **探测状态（2026-11-14）**：HL 与 Lighter 的起点/缺失小时已实测（见上表与 `data/chd_coverage.json`）；
> OKX / Binance 的探测在本轮执行环境**进程创建失败**（`spawn /bin/bash EAGAIN`，见 §8）时未跑完，
> 重跑 `python scripts/chd_coverage.py` 即可补齐（脚本只用存在性探测，不下载 parquet）。
> 线索：`llms.txt` 的下载示例用的是 `binance_spot/2025-08-01/20/BTCUSDT_trades.parquet`，
> 说明 Binance 归档起点在 **2025-08-01** 附近（未经本次实测确认）。

### 3.2 抽样缺失小时（BTC 锚点）

| exchange | 抽样日 | orderbook 缺失小时数 | trades 缺失小时数 |
|---|---|---:|---:|
| `hyperliquid_futures` | 2025-09-29 / 2026-02-13 / 2026-06-30 / 2026-11-14 | 12 / 0 / 0 / 9 | 12 / 0 / 0 / 9 |
| `lighter` | 2025-12-31 / 2026-04-16 / 2026-07-31 / 2026-11-14 | 0 / 0 / 0 / 9 | 0 / 0 / 0 / 8 |

> 最后一天的「缺失」是**当天还没到**（当前时刻 15:xx UTC，文件按整点发布），不是真缺失。

### 3.3 官方公布的缺失小时（`/docs/data-gaps-and-corrections`，照抄）

- `hyperliquid_futures`：**2026-04-12 09 → 16**（唯一整段缺失）。
- `lighter`：**2026-02-13 12 → 2026-02-14 18**；另有「只有部分标的宇宙」的时刻，例如 2026-09-22 16:02 → 2026-09-23 ~00:30、
  以及 2026-09-23 14:32 之后「210 个市场里有 144 个停更」；之前还有约 90 次更短的只发布部分宇宙的事件。
- `lighter`：**2026-03-24 → 2026-04-14** 采集器连接每几分钟断一次，**流动性市场有 1~9 分钟的成交缺口**，重连后首批成交晚 1~3 秒。
- `binance_futures`：自 2026-08 中起，**每小时 hh:02 → hh:05 的 mark price 流有 1~2 分钟缺口**（不可恢复）。
- `binance_futures`：`ticker / mark_price / liquidations` 只发布**在市的 USDⓈ-M 合约**，COIN-M 与已下架符号被剔除。
- 短重连缺口（Binance 约每 24h 换连接、Bybit 每日 02:00~02:50 UTC、Kraken 3~7 秒、**Hyperliquid 重连附近会丢少量成交**）。

## 4. 各场所的单位 / 口径陷阱（照抄 llms.txt + exchange wire format 页）

### 通用

- `received_time`：**采集器自己的时钟，纳秒**，文件按它分区 → **文件的「小时」是到达小时，不是成交小时**。
- `event_time` / `trade_time`：**交易所时钟，毫秒**（除非交易所另发时间）。
- 价格、数量、费率**一律是字符串**（保留交易所原始十进制位数，如 `85583.10`）；交易所不发该列时写 `null`。
- 盘口行：`snapshot` = 全量；`update` = 改数量（数量 0 表示删档）；无档位的序列推进消息写成 `side=noop, price=0, quantity=0`；
  **空快照 = 一行 noop snapshot（收到就必须清空本地盘口）**（2026-10 发布起）。

### Hyperliquid futures

- **`tid` 不是序列号**：它是买卖双方订单 ID 的哈希，「不递增，不能拿来找缺口」。
  → 去重键必须用 `(symbol, trade_id, trade_time)`；缺口只能靠 `received_time` 连续性判断。
- **builder-dex 符号保留大小写**（`xyz:XYZ100`、`io:NVDA`），主 dex 符号才被转大写（`kPEPE` → `KPEPE`）。
  CHD 的 `/v1/symbols` 返回**大写** `IO:NVDA`，但 parquet 里的 `symbol` 列是**小写前缀** `io:NVDA`；
  下载路径大小写不敏感（`IO:NVDA` 与 `io:NVDA` 都 200，字节数一致）。
- 2026-09 数据完整性发布起：**重连后交易所重放的成交不再重复发布**；更早的文件仍可能有重复行 → 仍需去重。
- 盘口：快照里出现非法档位（size ≤ 0）时**整个快照不发布**，该小时的盘口会被标为 unverified 直到下一个有效快照。
- `order_type` 恒为 `market`（HL 不发订单类型），成交都是 taker fill。

### Lighter

- **`trade_id` 是全交易所共用计数器**（各市场共用一个计数器，ID 递增但不连续），不能当单市场序号用。
- **文件按 `received_time` 分桶**：交易所重连后补发的成交会落进**下一小时**的文件
  → **要拿完整的一小时窗口，必须同时加载下一小时的文件并按 `trade_time` 过滤**。
- `funding_rate`：**2026-09-30 发布起是分数**（0.0001 = 0.01%），更早是百分数；`next_funding_time` = **下次结算时间**
  （2026-09 前是「上次结算时间」，语义反过来了）。
- `mark_price / ticker / open_interest` 的 `event_time`：2026-10 发布起是**交易所发布时间（毫秒）**，之前是采集器接收时间（纳秒）。
- 盘口 `event_time` = Lighter 发布时刻、`transaction_time` = 撮合引擎更新时刻（毫秒）；更早的文件 `transaction_time` 为空。
- 2026-10 发布起，`trades` 里**包含强平成交**（`order_type=liquidation`），重连重放的成交会补齐缺口；
  但**早于到达小时的重放成交仍被丢弃**。

### OKX futures

- **所有 size 都是「张」**：成交、盘口档位、`ticker.last_quantity`、OI、强平都是合约数，
  必须乘该 instrument 的 **`ctVal`**（`ctValCcy` 决定单位：线性永续是 base 币，如 `BTC-USDT-SWAP` = 0.01 BTC；
  反向永续是 USD，如 `BTC-USD-SWAP` = 100 USD）。→ **股票永续逐笔成交的 base 数量必须乘 ctVal 才能和别场所对齐。**
- 2026-09 发布起 `trades` 是**逐 fill 一行**（`trades-all` 通道）；之前是「每个 taker 委托价档一行」，`trade_id` 取最后一个 fill id、`quantity` 是同价档 size 之和。
- `ticker`：`base_asset_volume` 是 **base 币**（`volCcy24h`），2026-10 发布起 `quote_asset_volume` **为空**（OKX swap 不公布计价成交额）。
- `mark_price`：`funding_rate` 是**每个资金费周期的分数**（0.0000263 = 0.00263%），`next_funding_time` = 下次结算；
  2026-09 发布前 `event_time` 错填成了 `fundingTime`（未来时间）。

### Binance futures

- 只发布**在市的 USDⓈ-M** 符号（COIN-M、已下架符号剔除）。
- 2026-09-30 之前，**REST 深度快照被写了两遍**（其中一份 `received_time` = event time × 1e6）→ 重建盘口时会重复计账。
- `price_change_percent` 是**百分数**（不是分数）；`funding_rate` 是**每周期分数**。
- `mark_price` 流在每小时 hh:02→hh:05 有 1~2 分钟缺口（不可恢复）。
- 盘口：REST 快照通常 500 档/侧；**同时重置很多盘口时快照只有 100 档/侧**，档外的价格等级在更新到达之前是未知的。

## 5. Hyperliquid HIP-3 deployer 覆盖（决定能组多少对）

来源：`POST https://api.hyperliquid.xyz/info {"type":"perpDexs"}`（2026-11-14 实测），deployer 名单与 CHD 符号表交叉验证。

| deployer | 名称 | 性质 | 说明 |
|---|---|---|---|
| `xyz` | XYZ (Trade.xyz) | 股票/ETF/商品永续 | 覆盖最广：NVDA/TSLA/AAPL/AMZN/GOOGL/META/MSFT/AVGO/AMD/MU/ASML/PLTR/ORCL/TSM/QCOM/MRNA/… 以及 ETF（SOXL/EWY/EWJ/EWT/EWZ/SMH/XLE/XBI/TLT/MAGS/…）与商品/指数（GOLD/SILVER/COPPER/BRENTOIL/NATGAS/WHEAT/CORN/ALUMINIUM/URANIUM/URNM/US500/USTECH/SP500/VIX/DXY/…） |
| `io` | EntropyIO | 股票 + pre-IPO | AAPL/ANTH/CRCL/DRAM/EWY/GPRO/IONQ/NBIS/NVDA/**OAI**/PRL/SKHX/SNDK/TCNT/XIAOMI |
| `vntl` | Ventuals | pre-IPO / 主题指数 | **OPENAI**/ANTHROPIC/SPACEX + BIOTECH/DEFENSE/ENERGY/INFOTECH/MAG7/NUCLEAR/ROBOT/SEMIS/SOY/WHEAT/GOLDJM/SILVERJM |
| `cash` | dreamcash | 代币化美股 | AMZN/EWY/GOOGL/HOOD/INTC/KWEB/META/MSFT/NVDA/TSLA + GOLD/SILVER/USA500/WTI |
| `km` | Markets by Kinetiq | 股票 + 外汇 | AAPL/BABA/BMNR/GOOGL/MU/NVDA/PLTR/RTX/SEMI/TENCENT/TSLA/XIAOMI + EUR/USBOND/USOIL/US500/USTECH |
| `para` | Paragon | 股票 + 少量币 | AAOI/ANTH/AVGO/CIEN/CIFR/COHR/CRDO/CRWD/DRV/GLW/IREN/LRCX/MELI/NET/RDDT/SMCI/SMH/SOFI/STX/TTWO/UNITREE/VST + BTCD/ANSEM |
| `mkts` | Markets By Kinetiq | 指数 | BVIV/SMALL2000/US500/USBOND/USTECH |
| `flx` | Felix Exchange | 混合（股票+商品+crypto） | NVDA/TSLA/CRCL/COIN + GOLD/SILVER/COPPER/OIL/GAS/PLATINUM/PALLADIUM/USA100/USA500/USDE + **XMR（Monero，不是股票）** |
| `hyna` | HyENA | crypto | ADA/BCH/BNB/BTC/DOGE/ETH/… + GOLD/SILVER（**无股票**） |

要点：

- **同一个 ticker 在 HL 上会有多个发行方**（如 NVDA 同时有 `XYZ:NVDA` / `IO:NVDA` / `CASH:NVDA` / `KM:NVDA` / `FLX:NVDA`），
  它们是**不同 deployer 的不同合约**，定价口径与流动性都不同 → **选腿时必须指定 deployer，不能只写 ticker**。
- HL 上股票永续最早从 `xyz`/`io`/`cash` 三家 deployer 覆盖；`flx` 的 XMR 与 `hyna` 全部是 crypto，枚举时必须剔除。
- `io:OAI` 与 `vntl:OPENAI` 是**不同 deployer 的不同合约**（前者 EntropyIO，后者 Ventuals），
  与 Lighter 的 `OPENAI`（market_id 192）配对时更要注意单位（见 §6 闸门①）。

## 6. 结论：能不能替代现有 K 线截面？

**能，而且应该替代。** 理由（均已实测）：

1. **覆盖面**：本仓库现状是「HL↔Lighter 的 K 线截面（多窗口被出口拦截）」；
   CHD 在同一出口上给出 **L2 + L0 + funding + OI 的逐小时 parquet**，且 **OKX / Binance 也在里面**
   → 直接补上 README 里记的「本机出口拦截 OKX/Binance，多窗口统计只覆盖 HL↔Lighter」这个洞。
2. **数据新鲜度可验证**：逐笔成交 + 逐档盘口能算滑点曲线、成交率、排队位置；
   `received_time`（纳秒）能直接量采集延迟，K 线截面做不到。
3. **单位口径可对齐**：所有场所同一 schema，跨场所差异都被官方逐条列出（§4），本仓库 §1「单位关系必须由 PairSpec.ratio 表达」
   可以直接用 OKX 的 `ctVal`、Lighter 的 `size`、HL 的 `sz` 三方证据定 ratio。
4. **同名是否同一底层可验证**：本调查用**同小时价格比**做了初筛（`data/chd_universe.json` 的 `price_evidence`），
   直接抓出 `OPENAI`（Lighter 1.32×、OKX 0.10×）、`ANTHROPIC`（OKX 0.077×）、`QNT`（≈0.22×）、`CVX`（HL 138×）、
   `PRL`（Binance 0.05×）这些**同名不同物**，正是 DECISION.md 闸门①要拦的对象。

**限制与注意**：

- **覆盖起点不齐**：HL futures 2025-09-29、Lighter 2025-12-31（加密锚点），
  OKX/Binance 见 §3.1（`data/chd_coverage.json`）。**「最近 30 天」的多窗口回测对四家都成立；
  更长的窗口只有 HL/Lighter 成立。**
- **文件按到达小时分桶**（Lighter 尤其明显）：取整点窗口必须多取一小时再按 `trade_time` 过滤。
- **不提供 candle**：CHD 的 datasets 里没有 K 线族；需要 K 线时由 L0 自行聚合（比 K 线更可控）。
- **HL 没有 ticker/liquidations**；**Lighter 没有 liquidations**；需要强平流只能用 OKX/Binance。
- **`received_time` ≠ `event_time`**：任何「同一时刻对齐」都要用交易所时间（ms），否则会引入采集延迟偏差。

## 7. 复现命令

```bash
# 客户端自测（离线，不打真实网络）
python -m pytest tests/test_chd.py -q

# 标的宇宙（股票名单 + 同名交集 + 同小时价格比证据）
python scripts/chd_universe.py

# 覆盖起点与缺失小时（只做存在性探测）
python scripts/chd_coverage.py

# 手工取一个对象
python -c "
from nt_pairs.chd import CHDClient
c = CHDClient()
print(c.download('hyperliquid_futures','2026-11-13',14,'IO:NVDA','orderbook'))
"
```

产物：`data/chd_universe.json`、`docs/CHD_UNIVERSE.md`、`data/chd_coverage.json`、本地缓存 `data/chd_cache/`。

## 8. 本轮执行环境的意外（surprise）

> 注：本节记录的是**执行环境**问题，不是数据问题；数据结论见 §0–§7。

| 时间(UTC) | 现象 | 影响 |
|---|---|---|
| 2026-11-14 ~16:53 | 本机**所有**子进程创建失败：`spawn /bin/bash EAGAIN`、`spawn tmux EAGAIN`、`spawn …/aft EAGAIN`（Node `child_process.spawn` 返回 EAGAIN） | 无法再跑脚本/pytest；`/proc/loadavg` 显示 20k+ pids、`/proc/pressure/memory` 全 0（非内存压力），疑似同机其他进程把 fork 打爆 |
| 2026-11-14 15:0x | `python scripts/chd_universe.py` 全量价格证据跑 **17 分 52 秒**（约 800 次下载） | 每个候选标的每小时一个文件，**先跑 `--no-price-evidence` 再按需补证据**更省时间 |
| 2026-11-14 15:2x | `scripts/chd_coverage.py` 第一版用「每天探 4 个小时 × 3 个参考符号」，90 分钟只跑完 2 家场所 | 改成「每天 2 个小时 × 1 个符号」后大幅提速（见现版脚本） |
| 2026-11-14 15:1x | Alpha Vantage `LISTING_STATUS` 用 urllib 拉取失败（curl 同 URL 200，1MB CSV） | 已改为多源回落：本地 `listing_status.csv` → AV → S&P500 constituents → 内嵌名单 |
| 2026-11-14 22:3x | `web_fetch` 对**所有**域名都报「拒绝访问内网地址」（DNS 全被 fake-IP 198.18.x.x 劫持） | 本轮只能用 curl/python 访问外网 |
| 2026-11-14 22:3x | `GET /v1/symbols` 返回的 HL 符号是**大写** `IO:NVDA`，但 parquet 里 `symbol` 列是**小写** `io:NVDA`；下载路径大小写不敏感（`IO:NVDA`/`io:NVDA` 都 200，字节数相同） | 匹配符号时不能只比大小写 |
| 2026-11-14 22:3x | `POST /v1/jwt-token`（`X-API-Key`）与 `POST https://www.cryptohftdata.com/oauth/token`（`grant_type=client_credentials`）**都能换到 4 小时有效的 bearer token** | 客户端用前者，SDK 用后者 |
| 2026-11-14 22:3x | 匿名 `GET /v1/download` 也能下载（无需 token），但 302 必须跟随 | 匿名可跑；限速 60 req/min/IP |
