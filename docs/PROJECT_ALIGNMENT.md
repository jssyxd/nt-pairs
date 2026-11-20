# 项目对齐总表（2026-11-19）

> 这份文档是 nt-pairs 的**单一对齐口径**：目录名 / herdr space / 项目名 / GitHub 远端 / VPS 部署路径，
> 以及当前仍然卡住的地方和各自的责任人动作。

## 1. 五者对齐表

| 角色 | nt-pairs（**权威项目**） | ant-cross-arb（**旧项目，待归档**） |
|---|---|---|
| W 盘根目录 | `W:\nt-pairs`（= `\\fnos2\iflow\nt-pairs`，同一物理目录） | `W:\ant-cross-arb` |
| herdr space | space `wF`，标签 **`nt-pairs`** ✅ | **没有同名 space** ❌（agent 散落在 `wF` 与 `wE`） |
| 项目名（仓库自述） | `README.md` 首行 `# nt-pairs`；`pyproject.toml` → `name = "nt-pairs"` ✅ | `README.md` 首行 `# ant-cross-arb…` ✅（无 `pyproject.toml`） |
| GitHub 远端 | `https://github.com/jssyxd/nt-pairs.git` ✅（干净，无内嵌 PAT） | `https://github.com/jssyxd/ant-cross-arb.git` ✅（已移除内嵌 PAT） |
| VPS 部署 | **malaysia `155.254.60.38` → `/opt/nt-pairs`**（git clone，venv `/opt/nt-pairs/.venv`） | losangeles `23.27.186.126` → `/opt/nautilus_cluster/ant_arb`（实盘在跑，目录名与项目名不符） |
| systemd 单元 | `deploy/nt-collector.service`、`deploy/nt-trader.service`（已收编进仓库） | `ant-arb.service`（只在 losangeles；仓库里的 unit 与实盘 unit 指向不同文件） |

**已执行的修复（本次）**
- `deploy/nt-collector.service`：`ExecStart` 原来指向 `/opt/nt-pairs/collect_l2.py`（根目录不存在该文件）→ 改成 `.venv/bin/python scripts/collect_l2.py`；补 `EnvironmentFile=/etc/nt-pairs/env`。
- `deploy/nt-trader.service`：仓库里**缺**这个单元 → 从 malaysia 收编进仓库。
- `nt-trader` 的「每分钟重启循环」：根因是 `live_runner` 的 `--max-secs` **默认 60 秒**（节点 60 秒后自己优雅停机，systemd `Restart=always` 又拉起来，一夜 `NRestarts` 涨到 6029）→ 单元改为 `--max-secs 0`。
- 两个仓库 remote URL 里内嵌的**已吊销 PAT**（`ghp_…`）已从 `W:\ant-cross-arb\.git\config` 与 `W:\twinCheck\repo\.git\config` 移除。
- 文档里的 venv 路径三种写法统一为 `/opt/nt-pairs/.venv`（`docs/DEPLOYMENT_GUIDE.md`、`docs/KIOXIA_RKLB_VPS_DEPLOY.md`）。

## 2. 两台 VPS 的分工（定案）

| 主机 | 角色 | 现状 |
|---|---|---|
| **malaysia 155.254.60.38** | nt-pairs 的**唯一权威节点**（采集器 + 报告 cron + 交易器 dry-run + deploy key） | `nt-collector` active；`nt-trader` dry-run（`--max-secs 0`）；`/etc/nt-pairs/env` 已含 OKX passphrase |
| losangeles 23.27.186.126 | **ant-cross-arb 实盘**（`ant-arb.service` active） | 上面的 `/opt/nt-pairs` 是**无 git 的散文件副本**，`nt-collector` failed（磁盘 96%、剩 466MB）；与 nt-pairs 无关，待 ant 项目归档时一并撤除 |

## 3. 当前仍然卡住的地方

### 3.1 GitHub 推送链路全线断开（**需要人工**）

GitHub 上 `nt-pairs` 的 main 停在 `3d797e5`（2026-11-13）。此后所有提交都没推上去，原因有三层：

1. **凭证过期**：local 的 Git Credential Manager 里存的两个 token 都失效（API 返回 401，`git push` 报
   `Invalid username or token`）；从旧 remote URL 里捞出来的 PAT（`ghp_oWvH…`，同样出现在 `W:\ant-cross-arb\.git\config`、
   `W:\twinCheck\repo\.git\config`）已经吊销（401）。
2. **SSH key 没注册**：本机 `~/.ssh/id_ed25519` 与 VPS 的 `/root/.ssh/nt_pairs_report` 在 GitHub 侧都查无此 key
   （`Permission denied (publickey)`）。
3. **VPS 仓库已分叉**：VPS 的 `main` 领先 `origin/main` 24 个 report commit，且 `git fetch` 同样被 1 挡住。

**已备好的自动化脚本（一旦凭证到位，一条命令走完）**

```bash
# 本地：把本机「还没进上游」的提交打包，经 VPS 推上去
python scripts/push_via_vps.py            # --dry-run 先看要推什么

# VPS：把本地那 26 个 report 提交重放到上游并推送（之后 6h 报告链路自动恢复）
bash /opt/nt-pairs/scripts/vps_recover_push.sh
```

**恢复办法（任选其一，5 分钟内可完成）**
- 推荐：在 GitHub 上给 **malaysia 的 deploy key** 加写权限：
  `ssh -T git@github.com` 应回 `Hi jssyxd! You've successfully authenticated…`。
  该公钥是 `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFK3cz3ErDslXsd4T2WxthDYegqiFLL5MHp7iS+SBHrQ nt-pairs-report@debian`
  （VPS 上 `cat /root/.ssh/nt_pairs_report.pub`）。在仓库 `Settings → Deploy keys → Add deploy key` 勾上 **Allow write access**。
- 或者：`gh auth login` 重新登录，然后本地 `git push origin main`（需把当前 `main` 推上去）。

**恢复后的收尾动作**
```bash
# malaysia 上：把 VPS 的 24 个 report commit 并回上游，然后验证报告推送
cd /opt/nt-pairs && git fetch origin && git rebase origin/main && git push origin HEAD:main
bash /opt/nt-pairs/scripts/vps_report_cron.sh          # 期望输出「已推送 … → origin/main」
```
注意：`scripts/vps_report_cron.sh` 本次已改成 **先 `git fetch` + `rebase` 再提交推送**（旧版不 fetch，
所以 48 次推送全部被 `! [rejected] HEAD -> main (fetch first)` 挡掉，报告一次都没进仓库）。

### 3.2 trader 的数据连接曾间歇失败（已定位并绕过）

- 2026-11-18 23:29–23:33 与 00:00 之后，`nt-trader` 每 60 秒重启一次，`journalctl` 里是
  `node_run_failed: RuntimeError("data-connect timeout")`（退出码 4）。根因不是网络：
  HL/Lighter 的 WS 从 VPS 连得上（TLS + `101 Switching Protocols` 已实测），
  真正的原因是 `--max-secs` 默认 60 秒 + `Restart=always` 造成的**自停自启循环**；
  另外我一度把 `with_timeout_connection` 传成 `timedelta`，被 nautilus 2.0.0rc6 拒绝
  （它要**秒数**），已改成整数秒。
- 现在单元是 `--max-secs 0` + `with_timeout_connection(60)`，实测能持续心跳（5s 一跳，实时基差 −7…+8.8 bps）。

## 4. 「小额真单」的当前结论（2026-11-19）

| 事项 | 结论 | 证据 |
|---|---|---|
| 能不能开真单 | **不能开策略真单**：策略里**没有开仓代码，也没有平仓代码**；11 条风控闸门在运行时 **0 条生效** | `docs/LIVE_TRADING_SPEC.md` §3 与 `.herd-dispatch/jobs/live-safety/report.md`（逐条 file:line） |
| 链路验证脚本 | `scripts/live_smoke_order.py` 已实现：沙盒端到端 **exit 0**；真单已验证到「订单送达交易所」 | `docs/SMOKE_ORDER_2026-11-19.md` |
| 真单卡在哪 | **HL 账户权益为 0**（主钱包与 API 钱包 `accountValue=0.0`）→ 交易所不接单；Lighter 有 27.61 USDC；OKX 凭证 403 | 同上（附 REST 证据） |
| 能做什么 | **链路验证脚本**（独立脚本，硬编码 $20/腿上限、每日 1 轮、--send 才真发） | `scripts/live_smoke_order.py`（实现中） |
| 策略有没有正期望 | **没有**：实测 5 标的 × 2388 分钟，成交后基差 markout_15 只有 +0.17…+0.62 bps，扣真实往返成本 14.35 bps 后每次成交 −13.7…−17.5 bps | `docs/FILL_CALIBRATION_2026-11-18.md` |
| 采集器 | 正常：5 天 330MB，HL/Lighter/OKX 各约 3.6 万行/天，25 期 6h 报告在 VPS 本地 | VPS `/opt/nt-pairs/data/collector`、`/opt/nt-pairs/reports` |
| OKX 腿 | 密钥与 passphrase 已就位，但 `live_node.SUPPORTED_VENUES` 只有 HL/Lighter，**接 OKX 需要改代码** | `src/nt_pairs/live_node.py:86`、`:375` |

## 5. 未决但已排期的事项

| # | 事项 | 谁来做 |
|---|---|---|
| 1 | 恢复 GitHub 凭证（deploy key 或 `gh auth login`），把本地与 VPS 的提交推上去 | **用户**（3.1） |
| 2 | herdr：给 ant-cross-arb 建独立 space；把 captain pane 的 cwd 从 `W:\ant-cross-arb` 迁到 `W:\nt-pairs`；nt-pairs 的 agent cwd 统一用盘符写法 | 下一轮（需用户在本 pane 执行 pane 迁移） |
| 3 | `W:\fnos2\iflow\{nt-pairs,ant-cross-arb,asmmtalk}` 三个影子目录（空壳/破 venv，由 UNC 路径在 `W:\` 下被二次拼接产生）→ 删除 | 下一轮（已核实无引用） |
| 4 | ant-cross-arb 三处代码副本收敛（仓库 / `W:\twinCheck\repo` / `W:\twinCheck\repos\ant-files`），并把 6 个 untracked 模块提交入库 | 等 ant 项目归档决定 |
| 5 | losangeles 上那份无 git 的 `/opt/nt-pairs` 归档撤除；`ant-arb.service` 与仓库 unit 对齐（实盘在跑，需单独窗口） | 单独排期 |
