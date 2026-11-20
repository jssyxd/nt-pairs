# NautilusTrader 安装与可视化座舱（2.0.0rc9）

> 本文所有结论都是**在本机实测**得到的（`inspect.signature` / `dir()` / 实际跑一遍），
> 不是抄 1.x 文档。实测环境：Linux x86_64、Python 3.14.8、nautilus_trader 2.0.0rc9。

## 1. 装了什么

| 组件 | 版本 | 说明 |
|---|---|---|
| `nautilus_trader` | **2.0.0rc9** | cp314 manylinux wheel；`requires_python = ">=3.13,<3.16"` |
| `pandas` | 3.1.1 | 2.0 的统计/收益序列基于 pandas |
| `numpy` | 2.5.4 | |
| `plotly` | 7.1.0 | `[visualization]` extra |
| `kaleido` | 1.5.1 | `[visualization]` extra，静态图（png/pdf）导出用 |
| Python | **3.14.8** | rc9 要求 ≥3.13，**3.12 装不了 rc9** |

venv 位置（本 worktree 内，captain 合并时可直接用）：

```
.venv-nt/                      # uv venv --python 3.14
.venv-nt/bin/python scripts/verify_nautilus.py
```

> 注意：`/home/da/桌面/delta/.venv`（captain 的那个）是 **Python 3.12 + nautilus_trader 2.0.0rc6**，
> 且**没有** plotly/kaleido（`verify_nautilus.py` 在那个 venv 里会 FAIL，退出码 1）。
> 两个 venv 的差异见 §5 的坑表。

安装命令（如果需要重装）：

```bash
uv venv --python 3.14 .venv-nt
uv pip install --python .venv-nt/bin/python "nautilus_trader[visualization]==2.0.0rc9"
```

### 自检

```bash
.venv-nt/bin/python scripts/verify_nautilus.py          # 人类可读
.venv-nt/bin/python scripts/verify_nautilus.py --json   # 机器可读
echo $?   # 0 = 全齐；非 0 = 有缺失项
```

脚本刻意**不**用 `import nautilus_trader` 判断装没装（未装时可能解析成空命名空间包、
`__file__ is None`，import 会「假成功」），改用：

* `importlib.metadata.version("nautilus_trader")` —— 装没装 + 版本；
* `pkgutil.iter_modules(nautilus_trader.adapters.__path__)` —— **实际可用**的适配器清单。

## 2. 适配器可用性（rc9 实测）

实测可用适配器 **21 个**：

```
architect_ax, betfair, binance, blockchain, bybit, coinapi, coinbase, databento,
deribit, derive, dydx, finfeed, hyperliquid, interactive_brokers, kalshi, kraken,
lighter, okx, polymarket, sandbox, tardis
```

| 本项目需要 | rc9 实测 | 备注 |
|---|---|---|
| `hyperliquid` | ✅ | leader 腿；数据 + 执行 |
| `lighter` | ✅ | follower 腿；数据 + 执行 |
| `okx` | ✅ | 第三场所（矩阵用） |
| `binance` | ✅ | 矩阵用 |
| `tardis` | ✅ | 历史回放（**只有 data client**，没有执行端） |
| `sandbox` | ✅ | 纸面执行端，dry-run 用它 |
| **`lighter_robinhood`** | ⚠️ **没有独立模块** | 见下 |

### 「Lighter on Robinhood」不是一个模块

rc9 里 `lighter` 适配器同时服务 Lighter 与 Robinhood，靠 **deployment 枚举**区分：

```python
from nautilus_trader.adapters.lighter import LighterDeployment
list(LighterDeployment.__dict__)          # ['LIGHTER', 'ROBINHOOD', ...]
```

> **rc6 → rc9 的差异**：field-guide 里记的枚举成员是 `LIGHTER_ROBINHOOD`，
> **rc9 改名成 `ROBINHOOD`**。仓库 `src/nt_pairs/runtime.py` 用的是 `LighterDeployment.LIGHTER`，
> 不受影响；但任何写 `LIGHTER_ROBINHOOD` 的代码在 rc9 上会 `AttributeError`。

### 2.0 的 config 字段（rc9 `inspect.signature` 实测）

```text
LighterDataClientConfig(base_url_http, base_url_ws, proxy_url, environment, account_index,
    api_key_index, private_key, http_timeout_secs, ws_timeout_secs, update_instruments_interval_mins,
    rest_quota_per_min, transport_backend, deployment, venue, book_snapshot_timeout_secs)

LighterExecutionClientConfig(account_id, account_index, api_key_index, private_key, base_url_http,
    base_url_ws, proxy_url, environment, http_timeout_secs, ws_timeout_secs,
    market_order_slippage_bps, rest_quota_per_min, sendtx_quota_per_min, transport_backend,
    deployment, venue, use_gtd, scheduled_cancel_timeout_secs)

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

与 rc6 的差异：

* `LighterExecutionClientConfig` 多了 `scheduled_cancel_timeout_secs`；
* `LighterDeployment` 成员改名（见上）；
* 其余字段与 field-guide 记录一致（2.0 **没有** 1.x 文档里的 `product_types`，
  HIP-3 由 `allPerpMetas` 自动载入）。

## 3. 2.0 vs 1.x 的 API 差异（rc9 实测，最容易踩的几条）

| 主题 | 1.x | **2.0.0rc9 实测** |
|---|---|---|
| 节点 | `TradingNode` / `TradingNodeConfig` | **没有**。用 `nautilus_trader.live.LiveNodeBuilder` → `LiveNode` |
| InstrumentId 字符串 | `VENUE.SYMBOL` | **`SYMBOL.VENUE`**（顺序反了！`"SOXL-PERP.HYPERLIQUID"`） |
| Currency 构造 | `Currency("USD")` | 要 `Currency.from_str("USD")`（直接构造必须给 precision/iso4217/name/type） |
| InstrumentId 构造 | `InstrumentId("VENUE.SYMBOL")` | 要 `InstrumentId.from_str(...)` |
| StrategyConfig | dataclass-ish，`frozen=True` | 不是 dataclass；**Python 子类不能把 kwargs 转发给 `super().__init__()`**（会炸 `object.__init__() takes exactly one argument`）；自定义字段在 `super().__init__()` 之后按属性赋值 |
| StrategyConfig.id | `id` | **没有 `id`**；只有 `strategy_id`（`StrategyId`）。留空则 trader 自动分配 `S-000` |
| BacktestEngine | `BacktestEngine()` | `BacktestEngine(config=BacktestEngineConfig(...))`（config 必填） |
| BarSpecification.step | 分钟数 | **仍是聚合单位**（5 分钟 bar → `step=5`），不是毫秒 |
| `engine.add_data` | 单条 | 要**序列** |
| FillModel | `FillModel(prob_fill_on_limit=..., prob_slippage=...)` | rc9 的 `FillModel` 只是个壳；用 `OneTickSlippageFillModel(prob_fill_on_limit, prob_slippage, random_seed)` |
| SandboxExecutionClientConfig | 有默认值 | rc9 **必须**显式给 `venue` + `starting_balances` + **显式 `fee_model`**（否则 `RuntimeError: ... requires an explicit fee_model`） |
| 停止节点 | `node.stop()` 任意线程 | `PyLiveNode` 是 pyo3 **unsendable**：跨线程会 panic（`PyLiveNode is unsendable, but sent to another thread`）。停机必须发生在节点自己的事件循环里，且要用 `node.handle.stop()`（在组件回调里直接 `node.stop()` 会报 `LiveNode is busy servicing another call ... use the ... handle captured beforehand`） |
| `node.run_async()` | 可以在自己的 loop 里 await | rc9 走**自己的**事件循环，外部挂的 asyncio task 不会被调度 → 停表要挂在 `clock.set_timer` 上 |
| `BacktestResult.returns_series` | 按天 | 仍是**按天**（key = 当天 00:00 的 ns 时间戳）。**跨不到一天的数据只有 1 个点**，Sharpe/Sortino 全是 NaN |

## 4. 可视化座舱

### 4.1 一条命令产出 HTML

```bash
# 1) 跑一次回测（真实公开行情：HL xyz:SOXL ↔ Lighter SOXL，5m，14 天）
.venv-nt/bin/python scripts/run_pair_backtest.py --days 14 \
    --out data/cockpit/backtest_stats.json

# 2) 渲染自包含 HTML（图表是 Nautilus 原生图表）
.venv-nt/bin/python scripts/make_tearsheet.py data/cockpit/backtest_stats.json \
    --out reports/tearsheet/soxl_hl_lighter_14d.html \
    --theme nautilus_dark --currency USD
```

产物：`reports/tearsheet/soxl_hl_lighter_14d.html`（**5,213,181 字节**，自包含，plotly.js 内联，
双击即可打开，无需起服务）。

内容：Run Information 表、Performance Statistics 表（PnL / Returns / General 三段）、
Equity Curve、Drawdown、Monthly Returns 热力图、Yearly Returns、Returns Distribution。

### 4.2 `make_tearsheet.py` 支持的输入

| 输入 | 走哪条路 |
|---|---|
| `--native <pkl>` | pickle 的 `BacktestResult` / `BacktestEngine` → `create_tearsheet(engine=...)` |
| `--stats <json>` / 位置参数 | 仓库自有回测 JSON → **适配器** → `create_tearsheet_from_stats(...)` |
| 数据不足（缺 `returns` 等） | **降级版 HTML**：只用 plotly 画净值 / 回撤 / 月度收益 / 收益分布，页首标注「降级」与缺失原因 |

适配器（`adapt_repo_json`）只做**搬运**，不造数：

```
repo JSON                                   tearsheet
─────────────────────────────────────────────────────────────
stats_pnls   {"USD": {...}}          →   stats_pnls
stats_returns {...}                  →   stats_returns
stats_general {...}                  →   stats_general
returns      [[ts_ns, r], ...]       →   returns (pd.Series)
run_info     {...}                   →   run_info 表格
```

其它参数：`--theme`（`plotly_white` / `plotly_dark` / `nautilus` / `nautilus_dark`）、
`--currency USD`、`--benchmark <csv>`（两列：时间戳, 收益率；会重采样到收益时间轴，
对不齐就忽略并告警）、`--benchmark-name`、`--title`、`--force-degraded`。

### 4.3 自定义主题

```python
from nautilus_trader.analysis.themes import register_theme
register_theme(name="nt-pairs", template="plotly_dark", colors={"primary": "#00d1b2"})
```

### 4.4 静态图（Kaleido）

`create_tearsheet(..., output_path="x.png")` 会走 Kaleido 导出；本机 kaleido 1.5.1 已装。

## 5. 2.0 引擎启动路径（结论 + 证据）

**结论：用 `nautilus_trader.live.LiveNodeBuilder` 装配，`LiveNode.run()` 启动，
停机用 `node.handle.stop()`（在节点自己的事件循环里触发）。**

```text
LiveNodeBuilder.from_config(name, config=None, *, data_factories=None, exec_factories=None)
LiveNodeBuilder.add_data_client(name, factory, config, routing=None)
LiveNodeBuilder.add_exec_client(name, factory, config, routing=None)
LiveNodeBuilder.add_simulated_exec_client(name, factory, config)
LiveNodeBuilder.build() -> LiveNode
LiveNode.add_strategy(strategy) / add_actor(actor)
LiveNode.run() / run_async() / stop() / dispose() / handle -> LiveNodeHandle
```

仓库里的骨架：`src/nt_pairs/runtime2.py`。

```bash
# 只装配（脱敏打印，不联网）
PYTHONPATH=src .venv-nt/bin/python -m nt_pairs.runtime2 --check --pair configs/rklb_xyz_lighter.json

# 真正起一个 2.0 节点，跑 N 秒后停（两个数据端连真实公开行情，执行端是 sandbox）
PYTHONPATH=src .venv-nt/bin/python -m nt_pairs.runtime2 --seconds 12 --pair configs/rklb_xyz_lighter.json
```

实跑日志（关键行）：

```text
nautilus_live::node::builder: Registered DataClient-LIGHTER
nautilus_live::node::builder: Registered DataClient-HYPERLIQUID
nautilus_lighter::data: Starting Lighter data client: client_id=LIGHTER, environment=Mainnet, has_credentials=false
nautilus_hyperliquid::data: Starting Hyperliquid data client: client_id=HYPERLIQUID, environment=Mainnet
nautilus_sandbox::execution: Sandbox execution client started: venue=LIGHTER, account_id=LIGHTER-SANDBOX-001
nautilus_sandbox::execution: Sandbox execution client started: venue=HYPERLIQUID, account_id=HYPERLIQUID-SANDBOX-001
nautilus_lighter::data: Connected: client_id=LIGHTER
nautilus_hyperliquid::data: Connected: client_id=HYPERLIQUID
nautilus_live::node: All engine clients connected
nautilus_live::execution::manager: Reconciliation complete for HYPERLIQUID: reconciled=0, external=0 ...
nautilus_live::node: Startup reconciliation completed in 0.00s
nautilus_system::trader: Running
```

安全约束（CONTRACT §6）：

* 默认 **dry-run**：执行端一律 `add_simulated_exec_client`（sandbox），**不读私钥、不发真实订单**；
* `--live` 才切真实执行端，且私钥**只从环境变量读**（`HL_PRIVATE_KEY` /
  `HL_ACCOUNT_ADDRESS` / `LIGHTER_PRIVATE_KEY` / `LIGHTER_ACCOUNT_INDEX` /
  `LIGHTER_API_KEY_INDEX`），缺一即在**联网之前**抛 `MissingCredentials`（退出码 2）；
* 打印时 `private_key / api_key / secret / api_secret / passphrase` 一律打成 `***`；
* 私钥**不落盘**。

## 6. 已知坑

1. **`import nautilus_trader` 会假成功** —— 未装时可能解析成空命名空间包（`__file__ is None`）。
   用 `importlib.metadata.version("nautilus_trader")` 判断。
2. **两个 venv 状态不一致**：`.venv`（仓库根，captain 的）是 3.12 + rc6 + **无 plotly**；
   `.venv-nt`（worktree 内）是 3.14 + rc9 + visualization extra。跑座舱请用 `.venv-nt`。
3. **rc9 要求 Python ≥3.13**，仓库 `pyproject.toml` 里还钉着 `nautilus_trader==2.0.0rc6` +
   `requires-python >=3.12`，与本机实际安装不一致，合并时要一起改。
4. **InstrumentId 顺序反了**：`"SYMBOL.VENUE"`。仓库 `configs/*.json` 里本来就是这个写法，
   但任何新写的 instrument id 别按 1.x 的 `VENUE.SYMBOL` 来。
5. **StrategyConfig 子类不能转发 kwargs**：写 `super().__init__(strategy_id=...)` 会得到
   `TypeError: 'str' object is not an instance of 'StrategyId'`（其实是 `object.__init__` 的错报）。
   正确写法：`super().__init__()` 之后赋自定义属性；`strategy_id` 留空让 trader 分配。
6. **`BacktestResult.returns_series` 按天**：跨不到一天 → 只有 1 个点 → Sharpe/Sortino 全 NaN。
   座舱因此**自己**从 `portfolio.equity(venue=...)` 采样权益曲线算收益序列。
7. **`Portfolio.snapshots()` 默认只有 2 个快照**（初始 + 结束），不能当权益曲线用。
8. **`FillModel` 在 rc9 是空壳**，要用 `OneTickSlippageFillModel`。
9. **sandbox 执行端必须显式 `fee_model`**（哪怕是零费率）。
10. **停机三连坑**：跨线程跑节点 → pyo3 panic；`run_async()` 外部 task → 不调度；
    组件回调里 `node.stop()` → "LiveNode is busy servicing another call"。正解是
    `clock.set_timer(...)` + `node.handle.stop()`。
11. **Bar 时间戳单位**：`ts_event` 是**纳秒**。用秒 × 1e6 会把整段回测落到 1970-01-21，
    于是 `returns_series` 只剩一个点（这个坑本轮踩过一次）。
12. Lighter 的 K 线 `t` 在 REST 返回里是**毫秒**，而 `start_timestamp/end_timestamp` 是**秒**；
    HL 的 `candleSnapshot.t` 也是毫秒。

## 7. 复现清单（本轮实际产出）

| 产物 | 说明 |
|---|---|
| `scripts/verify_nautilus.py` | 安装/适配器/visualization 自检，退出码非 0 表示有缺失 |
| `scripts/run_pair_backtest.py` | 真实公开行情 → Nautilus 原生回测 → `data/cockpit/backtest_stats.json` |
| `scripts/make_tearsheet.py` | 座舱入口：原生结果 / 仓库 JSON / 降级三条路 |
| `src/nt_pairs/runtime2.py` | 2.0 启动骨架（`LiveNodeBuilder` + 停机 actor） |
| `data/cockpit/backtest_stats.json` | 回测结果（stats + 权益曲线 + 收益序列） |
| `reports/tearsheet/soxl_hl_lighter_14d.html` | 自包含 tearsheet（5,213,181 字节） |

## 8. 本轮验证状态（2026-11-14）

已实跑并通过：

| 项目 | 结果 |
|---|---|
| `verify_nautilus.py`（rc9 venv） | PASS，退出码 0 |
| `verify_nautilus.py`（captain 的 rc6/3.12 venv） | FAIL，退出码 1（版本不符 + 无 plotly/kaleido） |
| `run_pair_backtest.py --days 14` | 4032 根共同 5m K 线（HL `xyz:SOXL` ↔ Lighter `SOXL`/market 197），1152 笔订单、576 次持仓、PnL +93.64 USD（100k 账户） |
| `make_tearsheet.py` | 产出 `reports/tearsheet/soxl_hl_lighter_14d.html`（5,213,181 字节，nautilus_dark，浏览器实测渲染正常） |
| `runtime2.py --check` | 装配成功，配置脱敏打印正常（sandbox 执行端） |
| `runtime2.py --seconds N` | **节点真的起来了**：两个数据端连上真实公开行情（`Connected: client_id=LIGHTER` / `HYPERLIQUID`）、sandbox 执行端连接、状态对账完成、`trader: Running` |

未完成（沙箱在本轮末段出现 `spawn ... EAGAIN`，无法再起进程）：

* `runtime2.py` 的**自动停机**路径（`clock.set_timer` + `node.handle.stop()`）尚未跑完一轮验证。
  之前的两次失败分别给出了明确结论：组件回调里直接 `node.stop()` →
  `LiveNode is busy servicing another call; do not re-enter the node from a component
  callback, use the cache, portfolio, and handle captured beforehand`；
  跨线程 `node.run()` → `PyLiveNode is unsendable, but sent to another thread`。
  代码已按这两条报错改成 `handle.stop()`，**请在能跑进程的环境里补一次 `--seconds 12` 复核**。
* `tests/test_cockpit.py` 已写但本轮未能执行。

### Surprise 清单

| 日期 | 现象 | 出处 |
|---|---|---|
| 2026-11-14 | `InstrumentId.from_str("HYPERLIQUID.SOXL-PERP")` 的 venue 解析成 `SOXL-PERP` —— 2.0 改成 `SYMBOL.VENUE` | `nautilus_trader.model.InstrumentId` |
| 2026-11-14 | `LighterDeployment` 成员是 `ROBINHOOD`，不是 field-guide 记的 `LIGHTER_ROBINHOOD` | `nautilus_trader.adapters.lighter` |
| 2026-11-14 | `StrategyConfig` 的 Python 子类转发 kwargs 会得到 `TypeError: 'str' object is not an instance of 'StrategyId'`（真因是 `object.__init__`） | `nautilus_trader.config.StrategyConfig` |
| 2026-11-14 | `BacktestResult.returns_series` 只有 1 个点、key 是常量 `1728000000000000` —— 因为整段数据都落在 1970-01-21（同一天） | `nautilus_trader.backtest.BacktestResult` |
| 2026-11-14 | `Portfolio.snapshots()` 只给 2 个快照，不能当权益曲线 | `nautilus_trader.portfolio.Portfolio` |
| 2026-11-14 | `SandboxExecutionClientConfig` 不给 `fee_model` 直接抛错 | `nautilus_trader.adapters.sandbox` |
| 2026-11-14 | `FillModel` 在 rc9 是个空壳，得用 `OneTickSlippageFillModel` | `nautilus_trader.execution` |
| 2026-11-14 | `PyLiveNode` unsendable → 节点不能丢到别的线程 | `nautilus_live::python::node::PyLiveNode` |
| 2026-11-14 | 本机 `spawn ... EAGAIN`（进程/内存耗尽），所有需要起进程的工具（bash/subagent/monitor）失效 | 沙箱环境 |
