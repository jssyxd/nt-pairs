# nt-pairs 契约（captain 定义，worker 实现，不得改语义）

目标：用 NautilusTrader 验证「io:OAI（Hyperliquid Entropy）↔ Lighter OPENAI」两腿基差振荡是否正期望，
并把执行做成**成交流水驱动**：被动腿挂单（leader），对手腿按实际成交逐笔 IOC 对冲（follower）。

## 0. 目录与职责

```
src/nt_pairs/
  pair_spec.py    # 配对与单位换算（纯数据，无 IO）
  ledger.py       # 目标式成交流水账（纯逻辑）
  execution.py    # h* TTL、追价阶梯、对冲下单规格（纯函数）
  risk.py         # 裸腿年龄 / delta 上限 / 权益 / 配额闸门（纯函数）
  strategy.py     # NautilusTrader Strategy（依赖 1.231）
  runtime.py      # TradingNode 构建（live / paper / sandbox）
scripts/
  probe_basis.py  # 只在 nautilus 之外取数，算 p95/b*，出裁决
tests/
```

**纯逻辑模块（pair_spec/ledger/execution/risk）不得 import nautilus_trader**，这样测试不依赖适配器版本。
`strategy.py` / `runtime.py` 才允许依赖 nautilus。

## 1. 单位与价格（最容易错的地方）

- HL `io:OAI`：`1 张 = 1 unit`，价格单位 USD/unit，`szDecimals=3`，tick 由 5 位有效数字规则决定（约 $1356 时 = 0.1，跨 $1000 会变）。
- Lighter `OPENAI`：`market_id=192`，`min_base_amount=0.0035`，`min_quote_amount=$10`，`supported_price_decimals=2`（tick 0.01），`supported_size_decimals=4`。
- **单位关系必须由 `PairSpec.ratio` 表达**：`ratio = follower_qty / leader_qty`，默认 1.0 且必须写 `unit_evidence`
  （报价口径证据 + 校准时间）。禁止在两个模块里各写一次换算。

## 2. PairSpec（`pair_spec.py`）

```python
@dataclass(frozen=True)
class Leg:
    venue: str                 # "HYPERLIQUID" | "LIGHTER"
    instrument_id: str         # "io:OAI-USD-PERP.HYPERLIQUID" 等，按 1.231 provider 实际值
    price_tick: Decimal
    size_increment: Decimal
    min_qty: Decimal
    min_notional_usd: Decimal
    maker_fee_bps: Decimal     # 负数表示返佣
    taker_fee_bps: Decimal

@dataclass(frozen=True)
class PairSpec:
    name: str
    leader: Leg                # 被动腿（挂 maker）
    follower: Leg              # 主动腿（IOC 对冲）
    ratio: Decimal             # follower qty = leader qty * ratio
    unit_evidence: str
    sigma_1m_bps: Decimal      # 基差 1 分钟波动
    max_leg_usd: Decimal       # 单腿名义上限
```

## 3. Ledger（`ledger.py`）

事件来源：Nautilus `OrderFilled`（`last_qty`、`order_side`、`instrument_id`、`trade_id`）。

```python
class LegLedger:
    def on_fill(self, trade_id: str, venue: str, side: str, qty: Decimal,
                px: Decimal, fee: Decimal | None) -> None   # trade_id 幂等
    def q(self, venue: str) -> Decimal                      # 带符号
    def target_follower(self) -> Decimal                    # = -q_leader * ratio
    def residual(self) -> Decimal                           # target - actual（带符号）
    def naked_leader_equiv(self) -> Decimal                 # q_leader + q_follower/ratio
    def unhedged_usd(self, ref_px: Decimal) -> Decimal
    def net_fees(self) -> Decimal
```

硬规则：
- **目标式**，不存增量；任何 ack 顺序都不放大仓位。
- `fee=None`（HL 推断成交不给 commission）时记 0 并在 `missing_fee_count` 计数；不得用名义额猜费率。
- `trade_id` 重复投递 → 忽略并计数。

## 4. Execution（`execution.py`，纯函数）

```python
def h_star_s(k: Decimal, half_spread_bps: Decimal, sigma_1m_bps: Decimal) -> Decimal
def hedge_order(residual_qty: Decimal, *, book_bid: Decimal, book_ask: Decimal,
                tick: Decimal, size_increment: Decimal, min_qty: Decimal,
                collar_bps: Decimal, stage: str) -> OrderSpec | None
def chase_price(book_bid, book_ask, side, tick, ticks: int) -> Decimal
```

规则：
- 对冲只用 **IOC 限价**（`collar_bps` 默认 2 tick 折算，最大 50 bps），**永不产生 MARKET 单**。
- 数量按 `size_increment` 向下取整；小于 `min_qty` 返回 `None`（碎单留到下一次成交）。
- `stage ∈ {"t1_passive","t2_chase","t3_ioc"}`；T1 只用于 leader 报价，follower 只能 T2/T3。

## 5. Risk（`risk.py`，纯函数）

```python
class Action(Enum): NONE, WARN, FREEZE_NEW, FLATTEN, HALT

@dataclass(frozen=True)
class RiskLimits:
    naked_warn_s: float = 60.0
    naked_flatten_s: float = 180.0
    naked_halt_s: float = 600.0
    max_naked_usd_pct: float = 0.02     # 占权益
    equity_freeze_pct: float = 0.05
    equity_kill_pct: float = 0.10

def evaluate(*, pos_known: bool, naked_usd: Decimal, naked_since: float | None,
             equity_now: Decimal, equity_peak: Decimal, quota_left: int, now: float,
             limits: RiskLimits) -> tuple[Action, str]
```
优先级：`pos_known=False` → WARN（绝不平另一腿）；权益 → HALT/FREEZE；配额 `<100` → FREEZE_NEW；裸腿年龄阈值。

## 6. Strategy（`strategy.py`）

- 订阅两腿 `OrderBookDeltas`（L2_MBP），只在双方 book 新鲜（≤ 1 s）时计算基差。
- 用 **follower 的 L2 深度 + 双边费率** 反解 leader 保本价：`breakeven = follower_vwap ∓ b*/2`；
  只有 `edge_bps ≥ b*` 才挂/维持 leader post-only 单。
- `on_order_filled` 只看 leader 腿：`ledger.on_fill(...)` → `residual()` → `hedge_order(...)` → `submit_order`。
- follower 成交也进账本（用于超对冲检测）；`abs(residual)` 超过 1.10×leader 成交量 → 熔断撤单。
- `on_timer(0.2s)`：`risk.evaluate(...)`；`FLATTEN` 才允许 T3，`HALT` 只撤单+告警。
- 默认 `dry_run=True`：所有下单走日志，不发真实请求。

### 6-1. 交易假设：带状，不是「向平价收敛」（2026-11-11 实测决定）

`io:OAI` ↔ Lighter `OPENAI` 的基差是**围绕 +305 bps 的稳态溢价振荡**，不是围绕 0：

| 量 | 实测 |
|---|---:|
| 基差均值 | +304.7 bps |
| 标准差 | 53.5 bps |
| p95(\|b\|) | 395 bps |
| AR(1) ρ / 半衰期 | 0.890 / 6.0 h |

规则：

```
b_hat    = 滚动中位数（7d，1h 采样，≥72 点，持久化）
sigma_b  = 1.4826 × MAD（同窗口）
开仓：|basis - b_hat| >= entry_z * sigma_b     (entry_z 默认 1.5)
平仓：|basis - b_hat| <= exit_z  * sigma_b     (exit_z 默认 0.3)
门禁：(entry_z - exit_z) * sigma_b >= k * b*
```
- 门禁比的是**振荡幅度**，不是绝对基差；把 `basis` 直接和 `b*` 比视为实现错误。
- **必须带漂移过滤**（2026-11-11 因果检验结论）：`drift_24h = b_hat(t) − b_hat(t−24h)`；
  当 `|drift_24h| > θ_drift` 且方向与入场方向相反时，**禁止逆势开仓**。
  没有这条，20 天数据上的带状策略胜率只有 7–20%（见 `docs/2026-11-11-expectancy-test.md`）。
- `b_hat` 单次跳变 > 2σ_b → 判为结构性重定价 → `HALT` 人工复核（溢价一次性消失是主要尾部）。
- 重启必须从磁盘恢复 `b_hat/sigma_b`，否则会把「高溢价」误判为入场信号。

## 7. 验收（本仓库「正期望」的定义）

`scripts/probe_basis.py` 必须输出并写入 `data/basis_<pair>.json`：
- `n`、`p50`、`p95`、`p99`（`|basis|` bps）、AR(1) 半衰期、`b*`（六项）、`k`、`tradable` 布尔与原因；
- 两腿深度（±25 bps 内 USD）、点差 bps、资金费 carry；
- **裁决**：`p95 - k*b* > 0` 且两腿深度都过 `min_notional × 10` 才叫「可交易」，否则明确写不可交易与缺哪项。
- **必要条件 ≠ 充分条件**：`p95 ≥ k·b*` 只是门槛；还必须在**含成本（≈34 bps/往返）与超时离场**的
  因果回测中净期望 > 0，且分月不翻转（见 `docs/2026-11-11-expectancy-test.md`：朴素带状版本 20 天胜率 7–20%，负期望）。

## 7.1 已裁决的外部事实（2026-11-11 多窗口复核，必须遵守）

1. **腿的主被动按标的判深，不按所名**：同一标的在 HL `xyz` / Binance / OKX 的深度可差 3 个数量级。
   本配对的角色由 `scripts/probe_basis.py` 的深度数字决定，不得因为「HL 是小所」就默认当 maker。
2. **TTL 用 `h*(k)`，禁止固定 500 ms**；撤单后 `2 tick chase` → `50 bps IOC`，**禁止无条件市价**。
   本对实测 `σ_1m = 12.86 bps/min`、半价差 4.85~7.0 bps → `h*(1σ) = 8.5~17.8 s`。
3. **`b*` 六项**：费 + 半价差 + 滑点 + carry + 腿风险 + 对账。**HIP-3 的 deployer fee scale 必须乘进费率项**
   （每个 dex 自设倍率，部署方最多分走一半；`perpDexs.deployerFeeScale` 只是配置值，实际费率以成交回执 `fee` 为准）。
4. **Nautilus 版本：`2.0.0rc6`**（Windows/cp312 实测）。`1.231.0` **没有** Python 端 Lighter 适配器
   （只有 Rust crate `crates/adapters/lighter`），`2.0.0rc6` 的 `nautilus_trader.adapters` 同时含 `hyperliquid` 与
   `lighter`。本机用 `C:\Users\da\venvs\np2\Scripts\python.exe`（仓库在 UNC 共享盘，venv 放本地盘）。
5. **`commission=None`**：HL 推断成交不给手续费，`LegLedger` 记 `missing_fee_count`，**不得用名义额估费率**，
   也不得把 adapter 的 realized PnL 当净收益。
6. **HL 配额 per-address**：`10000 + 累计成交量`，子账户刷量不会给主账户释放配额；HL 侧只做被动腿与预挂保护。

## 8. 禁止

- 不改 `W:/ant-cross-arb`（旧方案已废弃）。
- 不引入除 `nautilus_trader/pyyaml/pytest` 外的依赖。
- 不做真实下单，不读用户密钥；所有 live 配置用环境变量占位。
- 不提交 `.venv/`、`data/`、`state/`。