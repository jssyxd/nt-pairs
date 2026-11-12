# 矩阵口径（captain 定义，worker 必须照此实现）

目标：对 **OKX / Lighter / Hyperliquid** 三场所所有可配对标的，产出
「价差矩阵 × 窗口 × 会话 × 容量 × 滑点」的一张可排序表，并对 SOXL 做完整执行模拟。

## 1. 配对与单位

- 同一底层资产在 ≥2 个场所出现即构成候选对（`pair = (venueA:symA, venueB:symB)`）。
- 单位换算 `ratio = qty_B / qty_A`，默认 1.0；**必须写证据**：
  `ratio_evidence = {price_ratio_mean, price_ratio_sd, cv, n}`，`cv > 0.005` 或 `|mean-1| > 0.05` 判为
  **不可配对**（unit mismatch），并在矩阵里标 `unit_fail`，不参与后续排名。
- HL 的 symbol 带 dex 前缀（`xyz:SOXL`、`io:OAI`、`vntl:OPENAI`），OKX 是 `<SYM>-USDT-SWAP`，Lighter 用 `symbol`+`market_id`。

## 2. 基差定义

```
basis_bps(t) = (P_B(t) / P_A(t) / ratio - 1) * 1e4
```
`P` 用同一时刻的 close（1m 或 1h 桶）；任一侧缺该桶 → 丢弃并计数 `dropped`。

## 3. 窗口

| 窗口 | 数据 | 说明 |
|---|---|---|
| 1h / 4h / 12h / 24h | 1m K 线（最近 72h） | 短窗，看日内振荡 |
| 7d / 14d / 30d | 1h K 线（最近 30d） | 长窗，看溢价稳定性 |

每个窗口输出：`n, mean, sd, p50, p90, p95, p99, min, max, rho(AR1), half_life_h,`
`share_ge_bstar`（≥ b* 的样本占比）、`share_abs_ge_100/200/300`。

## 4. 会话分层

用 `nt_pairs.sessions.classify(ts_ms)` 把每个采样点标成 `pre / rth / post / closed`；
每个窗口再按会话输出 `mean / sd / p95 / n`。
**必须单独列 `rth` 与 `closed`**：HIP-3/RWA 永续 24/7，而正股只在 RTH 定价，
溢价的行为在两个会话里可能完全不同（这是本矩阵要回答的核心问题之一）。

## 5. 成本与滑点

- `b*`（六项）= 费 + 半价差 + 滑点 + carry + 腿风险 + 对账 0.5 bps（口径见 `CONTRACT.md` §7.1）。
- **价差/滑点从盘口实测**：`slip_bps(qty)` = 用 L2 档位吃 `qty` 的 VWAP 相对 mid 的偏离；
  对每个场所输出 `qty ∈ {100, 500, 1000, 5000} USD` 的 `slip_bps` 与 `fillable`（深度够不够）。
- 容量：`cap_usd = 0.10 × min(±25bps 深度_A, ±25bps 深度_B)`（单笔上限）；
  另报 `cap_50bps`。**容量取两腿较小者**。
- 盘口快照只代表一次采样：**必须连续采 3 次（间隔 30 s）取中位数**，并记录波动（`depth_cv`）。

## 6. 排序与门槛

对每个对、每个窗口算：

```
edge_ratio   = (p95 - k*b*) / k*b*          # 覆盖倍数，>0 才算过
capacity_ok  = cap_usd >= 2 × min_tier_usd   # min_tier = 500 USD
session_split= |mean_rth - mean_closed| / sd  # 会话分裂度，>1 说明行为不同
rank         = edge_ratio × min(1, cap_usd/5000) / (1 + session_split)
```

输出 `tradable = (edge_ratio > 0) and capacity_ok and not unit_fail`。

## 7. 输出 schema（`data/matrix.json`）

```json
{
  "generated_utc": "...",
  "pairs": [
    {
      "pair": "xyz:SOXL|SOXL-USDT-SWAP",
      "legs": {"A": {"venue":"HYPERLIQUID","symbol":"xyz:SOXL","instrument":"..."},
               "B": {"venue":"OKX","symbol":"SOXL-USDT-SWAP","instrument":"..."}},
      "ratio": 1.0, "ratio_evidence": {"mean":1.0002,"sd":0.0004,"cv":0.0004,"n":4320},
      "unit_fail": false,
      "windows": {
        "1h": {"n":60,"mean":...,"p95":...,"rho":...,"half_life_h":...,"by_session":{"rth":{...},"closed":{...}}},
        "24h": {...}, "7d": {...}, "30d": {...}
      },
      "costs": {"b_star_bps": 17.2, "k": 1.226, "k_b_star": 21.1, "fees": {...}, "half_spreads": {...}},
      "capacity": {"cap_usd": 2100, "cap_50bps": 9000, "depth_cv": 0.42, "slip": {"A":{"100":0.8,...},"B":{...}}},
      "score": {"edge_ratio": 17.8, "capacity_ok": true, "session_split": 0.9, "rank": 9.4},
      "tradable": true,
      "reasons": []
    }
  ]
}
```

另出 `docs/MATRIX.md`：按 `rank` 降序的表（列：pair / 窗口 p95 / b* / k·b* / cap / 会话分裂 / 结论），
以及三个**分会话**的小表（rth / pre+post / closed 各取 top10）。

## 8. 硬性要求

- 纯标准库（`scipy` 不可用）；统计量自己实现。
- 缺数据的对**不要静默丢弃**：写 `"reasons": ["no_candles:B"]` 并保留在输出里。
- 所有 USD 金额是「名义额」，不是保证金。
- 数值必须可复现：输出里带 `sources`（每个文件用到的 raw 文件路径 + 抓取时间）。