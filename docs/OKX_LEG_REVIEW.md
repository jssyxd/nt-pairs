# OKX 腿复核：Lighter↔OKX / HL↔Lighter / HL↔OKX 同一窗口对照

生成时间：2026-11-20T17:33:19.717176+00:00　Stage A 窗口：2026-11-10 19:00 → 2026-11-12 18:00　回放窗口：24 小时　单笔名义：$500/腿

数量单位换算：缓存命中（data/okx_instruments.json，0.2h 前）；乘子 hyperliquid_futures:XYZ:SOXL×1, lighter:SOXL×1, okx_futures:SOXL-USDT-SWAP×1（OKX 永续数量单位是「张」，乘 `ctVal` 才是标的单位）。

## 1. 三组对照（RTH 会话口径）

| 对 | 腿角色（maker / taker） | b* bps | 费率 | 点差 | 滑点 | 腿风险 | 对账 | p95覆盖倍数 | 毛捕获 bps | 净EV/事件 bps | 胜率 | mean p_fill | 日均机会 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | Lighter:SOXL / HL:XYZ:SOXL | 11.03 | 1.80 | 4.53 | 3.58 | 0.63 | 0.50 | 1.86 | 17.44 | +3.31 | 49% | 0.361 | 360.3 |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | OKX:SOXL-USDT-SWAP / HL:XYZ:SOXL | 14.94 | 8.80 | 2.86 | 2.15 | 0.63 | 0.50 | 0.65 | 16.75 | +2.03 | 35% | 0.803 | 369.6 |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | Lighter:SOXL / OKX:SOXL-USDT-SWAP | 19.79 | 10.00 | 4.84 | 3.82 | 0.63 | 0.50 | 1.10 | 5.89 | -4.12 | 0% | 0.304 | 350.1 |

## 2. 分会话明细（净 EV/事件 bps）

| 对 | rth | pre | post | closed |
|---|---:|---:|---:|---:|
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | +3.31 (388 事件) | +0.97 (353 事件) | +0.60 (263 事件) | -0.07 (519 事件) |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | +2.03 (398 事件) | -1.46 (344 事件) | -1.28 (238 事件) | -3.82 (516 事件) |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | -4.12 (377 事件) | -2.49 (305 事件) | -2.88 (186 事件) | -2.42 (424 事件) |

## 3. 逐交易日（回放窗口只覆盖一天时这里是单日）

| 对 | 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---|---:|---:|---:|---:|---:|
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | 2026-11-11 | 640 | 26% | +0.15 | 10.09 | 0.259 |
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | 2026-11-12 | 883 | 40% | +1.87 | 14.47 | 0.264 |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | 2026-11-11 | 612 | 17% | -2.92 | 11.19 | 0.843 |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | 2026-11-12 | 884 | 28% | -0.21 | 14.28 | 0.817 |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | 2026-11-11 | 487 | 0% | -3.08 | 5.22 | 0.217 |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | 2026-11-12 | 805 | 0% | -2.94 | 5.71 | 0.221 |

## 4. 闸门与盘口

| 对 | 闸门①单位 | 闸门②新鲜 | p95\|b\| bps | k | 闸门③覆盖 | 闸门④容量 | 闸门⑤净EV | 最小深度 US$ | 点差 a/b bps | 滑点 a/b bps |
|---|:--:|:--:|---:|---:|:--:|:--:|:--:|---:|---|---|
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | ✅ | ✅ | 20.94 | 1.020 | ✅ | ✅ | ✅ | 239,363 | 1.27 / 3.25 | 0.68 / 2.23 |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | ✅ | ✅ | 9.96 | 1.028 | ❌ | ✅ | ✅ | 239,363 | 1.27 / 1.59 | 0.68 / 0.80 |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | ✅ | ✅ | 22.14 | 1.021 | ✅ | ✅ | ❌ | 414,325 | 3.25 / 1.59 | 2.23 / 0.80 |

## 5. p_fill 情景（窗口内净 bps 合计）

| 对 | 保守(0.5×) | 中性(1×) | 乐观(1.5×) |
|---|---:|---:|---:|
| hyperliquid_futures:XYZ:SOXL~lighter:SOXL | 874.3 | 1748.7 | 2623.0 |
| hyperliquid_futures:XYZ:SOXL~okx_futures:SOXL-USDT-SWAP | -987.0 | -1973.9 | -2355.6 |
| lighter:SOXL~okx_futures:SOXL-USDT-SWAP | -1935.8 | -3871.5 | -5807.3 |

