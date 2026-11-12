# Field Guide: nt-pairs（NautilusTrader 重建）环境与数据

## 2026-11-11: Lighter 适配器的版本陷阱（决定整个技术选型）

- `nautilus_trader==1.231.0`（PyPI latest 的 1.x）**没有 Python 端 Lighter 适配器**：
  `nautilus_trader/adapters/` 里只有 hyperliquid，没有 lighter；上游 `crates/adapters/lighter` 存在但未暴露到 Python 包。
- `nautilus_trader==2.0.0rc6`（cp312 win wheel）**同时有 `hyperliquid` 与 `lighter`**，是本机可用组合。
  2.0 的包布局已改为 `python/nautilus_trader/...`，且 1.x 的 API 有迁移差异（见上游 `MIGRATION_V2.md`）。
- 判断「装上了没」不要用 `import nautilus_trader`（未装时可能解析成空命名空间包，`__file__ is None`），
  要用 `importlib.metadata.version("nautilus_trader")` + `pkgutil.iter_modules(nautilus_trader.adapters.__path__)`。
- 可用 venv：`C:\Users\da\venvs\np2`（2.0.0rc6，已 `pip install -e W:/nt-pairs`）。

## 2026-11-11: 2.0 的另一处硬变化：`TradingNode` 没了

- 1.x 的 `nautilus_trader.live.TradingNode` / `config.TradingNodeConfig` 在 2.0 **不存在**
  （`nautilus_trader.live` 现在导出的是 `DataClientConfig`/`ExecutionClientConfig`/`ClientCache`/各类 response 类型）。
- 2.0 的 `StrategyConfig` 不是 dataclass：**不能写 `frozen=True`**，`__init__` 也不从注解映射字段。
  可用模式是「实例化后按属性赋值」（`cfg = PairHedgeConfig(...)` 内部 `super().__init__()` 再 `self.x = ...`）。
- `Strategy` 上的时钟/缓存只有在注册到 trader 后可用：`self.clock`、`self.cache` 在未注册时抛
  `RuntimeError: Strategy must be registered with a trader`。写单测要么注入假 clock，要么在代码里
  用 `try/except RuntimeError` 回退（`nt_pairs/strategy.py` 的 `_now_s/_book/_instrument` 就是这种写法）。
- 结论：**2.0 的实盘接线需要单独一轮**（节点构建、引擎启动、事件循环），本仓库目前只做到 client config 装配。

## 2026-11-11: 2.0 的模块布局与 1.x 差异（写代码前必看）

- `from nautilus_trader.trading import Strategy`（**没有** `nautilus_trader.trading.strategy` 子模块）。
- 订阅：`self.subscribe_book_deltas(instrument_id, book_type, depth=None, client_id=None, managed=False, params=None)`；
  回调是 `on_book_deltas(deltas)`。**没有** `subscribe_order_book_deltas`，也**没有** `on_quotes`（只有 `on_quote` 类事件，
  需按 `dir(Strategy)` 实测）。
- `Strategy` 上的可用方法（实测）：`submit_order(order, position_id=None, client_id=None, params=None)`、
  `cancel_all_orders(instrument_id, order_side=None, client_id=None, strategy_only=True, params=None)`、
  `order_factory`（属性）。
- `nautilus_trader.cache` 模块在 2.0 不存在；Cache 通过其他路径暴露（用 `dir(Strategy)` 找 `cache` 属性）。
- 2.0 新增 `on_market_exit` / `on_guidance` 等生命周期回调；1.x 的 `on_start/on_stop/on_timer/on_reset` 仍在。
- 结论：**任何 1.x 的示例代码都不能直接抄**；先 `inspect.signature` 再写。

## 2026-11-11: 2.0.0rc6 两个适配器的真实 config 字段（`inspect.signature` 实测）

```text
LighterDataClientConfig(base_url_http, base_url_ws, proxy_url, environment, account_index,
    api_key_index, private_key, http_timeout_secs, ws_timeout_secs, update_instruments_interval_mins,
    rest_quota_per_min, transport_backend, deployment, venue, book_snapshot_timeout_secs)

LighterExecutionClientConfig(account_id, account_index, api_key_index, private_key, base_url_http,
    base_url_ws, proxy_url, environment, http_timeout_secs, ws_timeout_secs,
    market_order_slippage_bps, rest_quota_per_min, sendtx_quota_per_min, transport_backend,
    deployment, venue, use_gtd)

HyperliquidDataClientConfig(environment, private_key, base_url_http, base_url_ws, proxy_url,
    http_timeout_secs, ws_timeout_secs, update_instruments_interval_mins, transport_backend,
    stale_stream_receive_timeout_secs, stream_health_check_interval_secs,
    stale_stream_warning_cooldown_secs, stale_stream_recovery_enabled,
    stale_stream_recovery_cooldown_secs, stale_stream_max_targeted_resubscribes,
    book_snapshot_timeout_secs)

HyperliquidExecutionClientConfig(account_id, private_key, vault_address, account_address,
    environment, base_url_http, base_url_ws, base_url_exchange, proxy_url, http_timeout_secs,
    max_retries, retry_delay_initial_ms, retry_delay_max_ms, normalize_prices,
    market_order_slippage_bps, include_builder_attribution, ws_post_timeout_secs, transport_backend)
```

- 2.0 **没有** 1.x 文档里的 `product_types`；HIP-3 由 `allPerpMetas` 自动载入。
- Lighter 的 `deployment` 走独立枚举类 `LighterDeployment`（`LIGHTER` / `LIGHTER_ROBINHOOD` 同适配器），
  venue 常量是 `LIGHTER_VENUE` / `LIGHTER_ROBINHOOD_VENUE`。
- 两边都有 `market_order_slippage_bps` —— 但本仓库**禁止**用 market，IOC 限价由策略自己给价。

## 2026-11-12: 方向标注陷阱 —— 「回归」与「顺势」互为镜像，结论会整个反过来

- 在带状规则里写 `side = +1 if z > 0`（z 为基差相对带心的位置）**是顺势**，不是回归；
  真正的回归是 `side = -1 if z > 0`（基差偏高时做空基差）。
- 因为进出场对称，`trend PnL = -fade PnL`：如果某次回测显示「顺势平均 −36 bps」，
  那么**回归就是 +36 bps**，不能写成「两侧都亏」。
- 本轮实测（HL↔Lighter，5m，2 天，fade + 下一根 open 入场）：
  | 对 | fade 平均捕获 | 胜率 | 扣 b* 后净 | RTH 净 | closed 净 |
  |---|---:|---:|---:|---:|---:|
  | SOXL | +17.1 | 97% | +0.9 | **−3.2** | +5.7 |
  | EWY | +18.6 | 79% | +5.0 | +7.4 | +8.7 |
  | MRVL | +17.2 | 91% | +4.0 | +10.5 | −1.5 |
  | RKLB | +27.4 | 77% | **+11.6** | +12.7 | +16.7 |
  | KIOXIA | +201.9 | 98% | +159.9 | +116.7 | +222.5（**Lighter 无盘口 → 不可执行**） |
  | KORU | +137.3 | 92% | +112.8 | +101.5 | +143.1（**Lighter 深度极薄**） |
  | NVDA / INTC / SNDK / MSTR / CRCL | +3~+7 | 78~97% | **−3 ~ −5.5** | 负 | 负 |
- 教训：**「回归」的正收益要先证伪「陈旧报价造成的机械回归」**：
  KIOXIA/KORU 那种 +100~200 bps、98% 胜率的组合几乎必然是薄盘口的假象；
  而 NVDA/INTC 这类深度好的反而被成本吃掉。**宽度与可执行性必须分开判**。

## 2026-11-12: alibaba-token-plan 配额耗尽 → worker 全线阻塞

- 三个 worker（`venue-dump` / `matrix` / `soxl-sim`）同时进入 `blocked`，错误串：
  `429 Your token-plan 1-month quota has been exhausted. The quota will reset at 12-07 16:00:00 UTC
  (type=insufficient_quota param=insufficient_quota) retry-after-ms=2245704000`，
  并且 fallback（glm-5.3 → qwen3.8-max）同样 429。
- **判断**：这是**配额闸门**不是审批闸门；`herdr-dispatch` 会把它报成 `status: blocked`（容易误判成「在等人工批准」）。
  看到 `blocked` 时先 `result --name <n> --refresh` 看有没有 429 字样。
- 处置：`stop --name <n> --yes` 收工，剩余工作由 captain 本地脚本完成；或换 provider（`--kind grok`）。

## 2026-11-12: 本机出口：OKX / Binance 直连被拦，HL / Lighter 可达

- captain shell：`api.hyperliquid.xyz` ✅、`mainnet.zklighter.elliot.ai` ✅；
  `www.okx.com` / `aws.okx.com` / `static.okx.com` / `fapi.binance.com` 全部 `000`（连 DNS 都不通/超时）。
  加 `User-Agent` 无效；`-4` / `-6` 都无效。
- 结论：**OKX 的多窗口统计在本机无法直接采集**，只能用此前工作区里已落盘的 2026-11-11 截面快照
  （`W:/ant-cross-arb/.herd-dispatch/jobs/universe-map/okx_swap_{inst,tickers}.json`）。

## 2026-11-12: Lighter / Hyperliquid 的 K 线分页与 tick 规则

- Lighter `/candles` 单次最多约 500 根 → **长窗口必须按时间切片分页**（`start/end` 用秒，步长 = 根数 × 分辨率秒）。
- Lighter 的 1m K 线**只在有成交时生成**：30 天只有约 500 根 5m、约 500 根 1h 级别 → 日内统计极不稳健，
  且「缺失=无成交」会让按行对齐把陈旧报价当成真实价差。
- HL `candleSnapshot` 也只回最近约 500 根（1h 30 天 ≈ 720 根会被截断），长窗口要分段。
- HL tick **必须从盘口价位反推**（5 位有效数字规则随价位跳变）：$61 → 0.001、$1356 → 0.1。
  用「$100 以下 0.1」这种经验规则会把 2-tick 滑点高估 **10 倍**（实测 32.7 bps vs 真实 0.33 bps）。

- `W:\nt-pairs` 实际是 `\\fnos2\iflow\nt-pairs`（NAS）。在该目录下 `python -m venv .venv` 后，
  `pip list` / `pip install` 会**挂 120 秒以上**（小文件海量 IO + pip 扫描 site-packages）。
- 正确做法：`python -m venv C:\Users\da\venvs\nt-pairs`，用 `C:\Users\da\venvs\nt-pairs\Scripts\python.exe`
  运行仓库代码；只有源码留在共享盘上。
- 另：`.venv\Scripts\python.exe -c "import nautilus_trader"` 在未装成功时会**静默成功**——
  它解析到一个空命名空间包（`__file__ is None`）。判断装没装必须看
  `importlib.metadata.version("nautilus_trader")`（未装会抛 `PackageNotFoundError`），不能只看 import。

## 2026-11-11: 本机 Python 双版本，pytest 只在一个里

- `py` → 3.14（**无 pytest**，无 nautilus）。
- `python` → 3.12（**有 pytest 9.1.1**，全局 nautilus_trader 1.219.0）。
- 所有测试命令统一写 `python -m pytest`，不要写 `py -m pytest`。

## 2026-11-11: Lighter 公共 REST 只有 mainnet.zklighter.elliot.ai 可达

- `api.lighter.xyz` 与 `docs.lighter.xyz` 在本机**空返回/ECONNREFUSED**；可用主机是
  `https://mainnet.zklighter.elliot.ai/api/v1`。
- K 线与资金费端点：`/candles?market_id=..&resolution=1h&start_timestamp=<秒>&end_timestamp=<秒>&count_back=2`
  （**四个参数全必填**；`count_back` 会被忽略；时间戳是**秒**，返回的 `t` 是**毫秒**）。
- `/fundings` 的 `timestamp` 是**秒**，与 `/candles` 的毫秒不一致。
- 盘口：`/orderBookOrders?market_id=..&limit=..`（`limit` 必填，上限 250）；`/orderBook` 不存在。
- **Lighter 的 1m K 线只在有成交时生成**：3 天窗口只回 762 根（≈1 根/5.7 分钟）。
  按行做差分会把「缺行」当连续时间，系统性高估波动率；必须先重建固定网格 + 前向填充 + 记 `stale` 占比。
- 所有参数错误都返回同一个 `{"code":20001,"message":"invalid param "}`；未知路径返回 **403 空 body**。

## 2026-11-11: Lighter `OPENAI` 与 HL `io:OAI` 的标的对应

- HL 侧**不存在 `io:OPENAI`**：Entropy dex 的符号是 `io:OAI`；`vntl:OPENAI` 是另一个 deployer（Ventuals）。
- Lighter `OPENAI` = `market_id 192`，perp，maker/taker 费 0，`min_base_amount=0.0035`、`min_quote_amount=$10`。
- 两边单位都是 1 unit；价格差（+3%）是**基差**，不是单位错配（单位错配会是 10×/100× 量级）。

## 2026-11-11: `io:OAI` 的实际费率和 funding 口径

- HL 基础档 perp：taker 4.5 bps / maker 1.5 bps；HIP-3 每个 dex 另有**部署方费率倍率**
  （`perpDexs.deployerFeeScale`，部署方最多分走一半）。`io` 返回 `1.0`，但**语义未在 API 里说明**，
  实际费率必须以成交回执 `fee` 字段为准。
- `fundingHistory` 的 `fundingRate` 是**每小时**；14 天实测均值 **0.87 bps/h**（累计 34 bps）。
- HL 推断成交（inferred fill）的 `commission` 为 `None` —— 自建账本不得据此推算费率，也不得用 adapter 的 realized PnL 当净收益。