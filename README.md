# nt-pairs

在 **NautilusTrader 1.231** 上重建的两腿对冲基差振荡系统。第一对标的：

| 腿 | 场所 | 标的 | 角色 |
|---|---|---|---|
| A | Hyperliquid（Entropy HIP-3 dex `io`） | `io:OAI`（界面名 OPENAI） | leader（maker） |
| B | Lighter | `OPENAI`（market_id 192） | follower（IOC 对冲） |

旧仓库 `W:/ant-cross-arb` 已停止开发，其结论只作为输入：
- 腿风险必须用「目标式账本 + 时间止损」管，不能用价格止损；
- 等待上限 `h*(k) = (k·半价差/σ_1m)²`，量级是亚秒到秒；
- 门槛用六项 `b*`（费 + 半价差 + 滑点 + carry + 腿风险 + 对账摩擦）；
- 跨场所原子双腿不存在，只能「主动腿先成、被动腿按成交量逐笔跟」。

## 结构

```
CONTRACT.md          # 接口契约（改实现前先读）
configs/             # 配对与执行参数
src/nt_pairs/
  pair_spec.py       # 配对/单位换算（纯数据）
  ledger.py          # 目标式成交流水账（纯逻辑）
  execution.py       # h* / 追价 / IOC 规格（纯函数）
  risk.py            # 裸腿年龄、delta、权益、配额闸门
  leg_policy.py      # 腿选择：费率→点差→深度→成交概率（纯函数）
  strategy.py        # NautilusTrader Strategy（依赖 1.231）
  runtime.py         # TradingNode 构建（live/paper/sandbox）
scripts/probe_basis.py  # 取数 → 基差分布 → b* → 可交易裁决
scripts/make_report.py  # VPS 侧 6 小时报告（纯标准库、脱敏）
scripts/ci_summary.py   # CI 侧把报告 + 只读探针渲染成 job summary
scripts/vps_report_cron.sh  # VPS 侧 cron：生成报告 → git push reports/（deploy key）
.github/workflows/6h-report.yml  # 每 6 小时汇报 + PR/push 快测 + 失败开 issue
reports/              # VPS 推上来的 6 小时报告（Markdown + 机读 JSON）
docs/CI_CD.md         # 链路图、失败处理、手动补跑、captain 待办
tests/
```

## 快速开始

仓库在 UNC 共享盘（`\\fnos2\iflow\nt-pairs`），**venv 必须放本地盘**（否则 pip 扫描会挂几分钟）：

```bash
python -m venv C:\Users\da\venvs\np2
C:\Users\da\venvs\np2\Scripts\python.exe -m pip install -e "C:/path/to/nt-pairs"   # 或 pip install nautilus_trader==2.0.0rc6
C:\Users\da\venvs\np2\Scripts\python.exe scripts/probe_basis.py --days 14          # 先看「值不值得做」
python -m pytest -q                                                               # 纯逻辑测试不依赖 nautilus
```

版本注意：`nautilus_trader==1.231.0` **没有** Python 端 Lighter 适配器（只有 Rust crate）；
**`2.0.0rc6`（cp312 win wheel）同时含 `hyperliquid` 与 `lighter`**，是本仓库锁定的版本。

## 验收标准（先验证再交易）

`probe_basis.py` 必须同时满足：

1. `p95(|basis|) ≥ k · b*`（k 由有效样本量与自相关算出，不是常数）；
2. 两腿在 ±25 bps 内的深度都 ≥ 10 × 单笔名义；
3. 单位换算有证据（价格比 ≈ 1，或明确记录换算比）。

任一条不满足 → 该对**不做**，去看下一对。

### 2026-11-12 三场所矩阵（新增文档）

| 文件 | 内容 |
|---|---|
| `docs/MATRIX_SPEC.md` | 矩阵口径：窗口（1h/4h/12h/24h/7d/14d/30d）、会话分层、容量与排序 |
| `docs/MATRIX_SUMMARY.md` | **结论**：三道闸门、事件级回放、会话分裂、候选排序 |
| `docs/MATRIX_PREVIEW.md` | captain 基线（可复现命令） |
| `docs/MATRIX.md` | 全量矩阵（82 对，自动生成） |
| `docs/SOXL_SIM.md` | SOXL 完整执行模拟（滑点曲线 / 成本 / 容量 / 会话 / 规模敏感性） |
| `docs/FILL_MODEL.md` | 成交率与排队模型（λ、均量、贴价队列；P(fill) 的敏感度） |
| `src/nt_pairs/sessions.py` | 2026 NYSE 会话标定（假日、提前收盘、DST） |

复现：

```bash
python scripts/build_matrix.py     # 全量矩阵 → data/matrix.json + docs/MATRIX.md
python scripts/sim_soxl.py         # SOXL 专项 → data/soxl_sim.json + docs/SOXL_SIM.md
python scripts/screen_candidates.py # 候选对的 fade 事件级回放（含会话分层）
python scripts/fill_model.py       # 成交率/排队模型 → docs/FILL_MODEL.md
python -m pytest -q                # 纯逻辑 + 统计测试（不依赖 nautilus）
```

**已知边界**：本机直连 OKX / Binance 被出口拦截，矩阵里这两家只有 2026-11-11 的**截面**数据；
多窗口统计目前覆盖 HL ↔ Lighter。

## 风险

- pre-IPO 永续的「基差」可能包含估值口径差异（不是可收割的错价）。
- 两腿保证金不互通：单边被强平会让整个组合变成裸腿。
- HL 请求配额按地址计（`10000 + 累计成交`），配额耗尽时平仓只能靠预挂的 trigger。
- 本仓库默认 `dry_run=True`，不发真实订单。