# Captain（我）对人类的回复原文（逐条）

本文件保存本项目中 **captain 窗口输出给人类的主要回复原文**，供 `对话.html` 收录。
worker 与 captain 之间的全部往返原文在 `.herd-dispatch/jobs/*/{input,pane,report}.md`，
由 `对话.html` 逐字收录。

---

## C1 · 2026-11-11 · 切换到 NautilusTrader 重建后的第一份交付

> 已按你的要求**停掉 ant-cross-arb 线**，新建 `W:/nt-pairs`，并以 NautilusTrader 为引擎、把 `io:OAI`（Hyperliquid Entropy）↔ Lighter `OPENAI` 作为第一对标的做了端到端验证。5 个 worker（deepseek/glm/qwen 混合）+ 我本人复核，结论是**技术可行、但这对标的不具备正期望**。
>
> （内容详见 `docs/2026-11-11-basis-probe.md`、`docs/2026-11-11-expectancy-test.md`、`docs/2026-11-11-economics.md`、`STATUS.md`；此处不重复粘贴全文，原始判断链已逐句记录在各文档与 worker 转录里。）

---

## C2 · 2026-11-12 · 矩阵阶段交付

> 已从停点继续，矩阵 + SOXL 完整建模都跑完了。**先说两件必须知道的事**：① 本轮多 agent 在中途被**provider 配额打死**（`429 insufficient_quota`，重置 12-07 16:00 UTC），`--kind grok` 又卡在 x.ai 人工登录；② 本机直连 **OKX / Binance 被出口拦截**（`www.okx.com`、`aws.okx.com`、`fapi.binance.com` 全部 000），worker 沙箱同样不可达 —— 所以 OKX/Binance 只有 2026-11-11 的**截面**快照，多窗口矩阵实际覆盖 **HL ↔ Lighter**。剩下的全部由我本地脚本完成（可复现）。
>
> ## 1. 矩阵结果（30 对流动性最好的，30 天 1h + 会话分层 + 容量 + 单位校验）
> …（表格见 `docs/MATRIX_SUMMARY.md` §2.1）…
>
> ## 2. 事件级回放（5m，下一根 open 入场，扣真实成本；两次独立运行排序稳定）
> …（表格见 `docs/MATRIX_SUMMARY.md` §3）…
>
> ## 3. SOXL 完整建模（`docs/SOXL_SIM.md`）
> …（规模敏感性表见 `docs/SOXL_SIM.md` §5）…
>
> ## 4. 本轮抓到的两个自伤级错误（已修，已写进 field guide）
> 1. **方向标注**：我之前把「`z>0` 做多基差」叫成「回归」，那其实是**顺势**；顺势 −36 bps 等价于回归 **+36 bps**。修正后结论整个反转。
> 2. **tick 估计**：用「$100 以下 0.1」的经验规则，把 HL 2-tick 滑点高估 **10 倍**（32.7 vs 真实 0.33 bps）→ 现在一律**从盘口价位反推 tick**。
>
> ## 5. 交付与阻塞
> 新增：`docs/{MATRIX_SPEC,MATRIX,MATRIX_SUMMARY,MATRIX_PREVIEW,SOXL_SIM}.md`、`src/nt_pairs/sessions.py`（2026 NYSE 日历 + DST + 提前收盘）、`scripts/{build_matrix,screen_candidates,sim_soxl}.py`、`.field-guide/nautilus_lighter_data.md`（17 条环境事实）。**测试 92 全绿**（系统 python）/ 98（np2，含 nautilus 冒烟）。
>
> 未完成且已写明原因：**OKX/Binance 多窗口**（需要能访问 OKX 的出口）、**多 agent 复核**（配额 12-07 重置）、**`p_fill`/markout 标定**（需 L2 回放或实盘 fill 日志——这是「97% 胜率」能否成立的关键）、**Nautilus 2.0 节点启动**（2.0 移除了 `TradingNode`）。
>
> **下一步建议**：对 RKLB / MRVL / EWY / SOXL 四对做 fill 级验证（L2 回放 24h，标定 `p_fill` 与成交后 markout），同时把候选规模锁定在 $100–500/腿；在这之前不要放大名义额，也不要把 KORU/KIOXIA 的 +100 bps 当机会。

---

## C3 · 2026-11-12 · 成交率模型补充

> 成交率模型（`docs/FILL_MODEL.md`）—— 可靠输出与不可靠输出
>
> 可靠：**HL 主动流 16–218 笔/分 vs Lighter 0.04–2.8 笔/分** → maker 腿按 λ 选场所，不按场所名选。
> 不可靠：`P(fill)` 同一模型两次运行给出 3% 与 97%，差异全来自**贴价队列的单次快照**（1 vs 243 单位）→ 挂单前必须逐笔采样队列（≥3 次取中位数）。
