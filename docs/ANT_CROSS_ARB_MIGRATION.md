# ant-cross-arb 归档前的迁移清单（2026-11-19）

> 结论（依据 `.herd-dispatch/jobs/repo-compare/report.md` 的逐条 file:line 核验）：
> **ant-cross-arb 是旧版本**（nt-pairs 是现行权威项目），但**今天不能整删**——它有一批只在磁盘上、
> 不在 git、也不在其它副本里的实现，以及 nt-pairs 对它工作区的硬路径依赖。

## 1. 谁更优（分维度）

| 维度 | 赢家 | 证据 |
|---|---|---|
| 提交量与活跃度 | **nt-pairs**（17 commits / 100 文件 / 15.4k 行；ant 4 commits / 38 文件 / 9.5k 行） | `git rev-list --count HEAD` |
| 实盘框架 | **nt-pairs**（NautilusTrader 2.0 `LiveNodeBuilder` 接线） | `src/nt_pairs/live_node.py:55-56,712`（ant 侧 `grep from nautilus_trader` = 0） |
| 三所矩阵 / L2 采集 / fill 标定 / 6h 汇报 / CI | **nt-pairs**（`build_matrix.py`、`collect_l2.py`、`analyze_collector.py`、`6h-report.yml`、`make_report.py`、`ci_summary.py`） | 各文件行数见对照报告 |
| 风控与执行细节 | **ant-cross-arb**（1.10× 超对冲熔断、三段阶梯 T1/T2/T3、持仓三读一致性、优先级 watchdog、筛选数学 b\*/ESS/p95/深度闸门） | `04_preipo_cross_arb/core/{fills_ledger,leg_ladder,position_state,watchdog,selection}.py`（**全部未跟踪**） |
| 测试 | 两边都有（nt-pairs 143 passed；ant 133 passed + recon 44 项） | 各自 `pytest` 实跑 |

## 2. 可以**立即**删（不会丢东西）

- `04_preipo_cross_arb/core/ant_cross_arb_service.py`（1,505 行）：与 `ant_cross_arb_v4_adaptive.py` **md5 相同**（`3ef2a1c2…`），纯重复。
- 3 个 `2026110X对话.html`（合计 10,668 行）：过程记录。
- `core/legacy/{ant_cross_arb_live,ant_cross_arb_v2,ant_cross_arb_v3_ws}.py`（1,093 行）：`core/legacy/README.md:1-7` 自述「仅存档、不要部署」，且 `_live.py` 只打印日志从不下单。
- 未跟踪的 `lighter_books.json`（199 KB 一次性盘口快照）。

## 3. **必须先迁走**（不迁即永久丢失）

> 这些文件不在 ant 的 git 里（`git ls-files` = 0 / 未跟踪），也不在 `W:\twinCheck\repo` 与
> `W:\twinCheck\repos\ant-files` 两个副本里（逐一 `ls` 核对：全部缺失）。

| # | 文件 | 为什么必须迁 | 迁到 nt-pairs 的位置 |
|---|---|---|---|
| M-1 | `core/fills_ledger.py`（277 行） | 唯一的 **1.10× 超对冲熔断**（`:183`）+ JSONL 落盘（`:212`）+ cloid 派生（`:38`/`:55`）。nt-pairs `CONTRACT.md:118` 要求这条闸门，但 `src/` 内 `grep 1.10\|overhedge` = **0** | `src/nt_pairs/ledger.py` + `tests/test_fills_ledger.py` |
| M-2 | `core/leg_ladder.py`（205 行） | 唯一的三段执行阶梯（`:76` T1 被动→T2 追价→T3 强制限价、`:51` `should_repeg`、`:33` cloid 长度护栏）。nt-pairs `strategy.py:259-260` 只有一档固定 `collar_bps=5` | `src/nt_pairs/execution.py`（或新 `leg_ladder.py`）+ `tests/test_leg_ladder.py` |
| M-3 | `core/position_state.py`（232）+ `core/watchdog.py`（203）+ `core/p0_interfaces.py`（177） | 唯一「真实持仓读回 + 三读一致性 → known/unknown」与「权益回撤/配额/裸腿年龄优先级」实现。nt-pairs `src/` 内 `grep AccountState\|Portfolio\|positions(` = **0**，`strategy.py:287` 把 `pos_known` 写死 True | `src/nt_pairs/position_state.py`、`watchdog.py`、`p0_interfaces.py` + 接线进 `live_node.py` + 测试 |
| M-4 | `core/selection.py`（504）+ `core/pairs.example.yaml`（53） | 筛选数学的可复用实现（`:92` b\*、`:141` k(ESS)、`:169` p95、`:202` 深度闸门、`:260` 单位合理性、`:287` `screen()`、`:341` `load_pairs()`）。nt-pairs 侧只散在 `scripts/build_matrix.py` 里，无模块无单测 | `src/nt_pairs/selection.py`；`pairs.example.yaml` → `configs/` + `tests/test_selection.py` |
| M-5 | `tests/`（5 文件 1,476 行） | M-1..M-4 的唯一测试，且未跟踪 | 随 M-1..M-4 一起进 `tests/` |
| M-6 | `research/{00_SYNTHESIS,EXECUTION_AND_LEG_RISK,PAPERS}.md`（401 行） | `leg_ladder.py:3,15-18` 直接引用其中的设计章节，代码与依据不能割裂 | `docs/` |
| M-7 | `.herd-dispatch/jobs/universe-map/okx_swap_inst.json`（642 KB） | **nt-pairs 对 ant 工作区的硬依赖**（`build_matrix.py:39`）→ 删 ant 后 OKX 腿**静默**消失（`load_okx` 的 `except` 只打印） | ✅ **已处理**：快照复制进 `data/snapshots/`（纳入版本库），`SNAP` 改为按本仓路径解析（commit `5dfd329`） |
| M-8 | `recon/recon_ledger.py`（379）+ `recon/*.md`（6 个） | 含 nt-pairs 没有的强平/ADL 识别（`:355`）、外部干预漂移（`:362`）、`verify_fill_chain`（`:297`）、`cross_check_position`（`:334`）；删掉则审计结论不可复算 | 强平/ADL 与漂移检测并入 `src/nt_pairs/risk.py`；文档 → `docs/recon/` |
| M-9 | `deploy/ant-arb.service`（17 行） | losangeles 上 `ant-arb.service` 仍在实盘；仓库 unit 与实盘 unit 指向**不同文件**（`ant_cross_arb_v4_adaptive.py` vs `ant_cross_arb_service.py`） | `deploy/legacy/ant-arb.service`，并在 `docs/PROJECT_ALIGNMENT.md` 记录停服窗口 |

## 4. 删 ant 后要同时处理的两个尾巴

1. `scripts/build_dialogue_html.py:224` 仍读 `W:\ant-cross-arb` → 改成本仓路径。
2. `W:\twinCheck\repo`、`W:\twinCheck\repos\ant-files` 两处 ant 副本需收敛（它们**不含**新模块，
   ant 主仓一删就成「唯一副本」的错位状态）。

## 5. 另外查出的「文档与代码互相矛盾」（建议一并修）

| 文档说法 | 实测 |
|---|---|
| `docs/EV_AND_MONITORING.md:52-67` RKLB「+14.0 ✅ 正期望」 | 与 `docs/FILL_CALIBRATION_2026-11-18.md` 冲突：实测 markout_15 仅 +0.17~+0.62 bps，扣 14.35 bps 往返成本后 **−13.7~−17.5 bps/成交** |
| `README.md:70` 矩阵「82 对」 | `docs/MATRIX.md` 实际 55 行数据；`data/matrix.json` 是 60 对且**全为 HL↔Lighter** |
| `README.md:3` 「NautilusTrader 1.231」 | `pyproject.toml:11` 锁 `2.0.0rc6`，代码用的是 2.0 API |
| `DECISION.md:41` / `STATUS.md:32` 测试数 106 / 103 | 实跑 143 passed；且有一个写死时间戳的「时间炸弹」用例（已由 commit `8fd0840` 修掉） |
| `docs/LIVE_TRADING_SPEC.md:54-78` 的 11 条闸门、OKX 备份腿、24h 超时出场 | 运行时 **0 条生效**；OKX 备份腿、24h 出场在 `src/` 内 `grep` = **0** |
