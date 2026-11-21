# CHD 全市场扫描 + 事件级 EV 回测报告

生成时间：2026-11-21T12:13:08.184336+00:00　窗口：2026-11-20 12:00 → 2026-11-21 11:00　单笔名义：$100/腿　回放窗口：24 小时

## 1. 候选宇宙

- 候选对（股票/ETF/pre-IPO 同名跨场所 + HL 内部跨 deployer）：**7** 对
- Stage A 有同刻重叠样本的对：**7** 对
- 过闸门①（单位一致 \|ratio−1\|≤5%）：**7** 对
- 过闸门①②（+ 重复收盘率 ≤10%）：**7** 对

## 2. Stage B（盘口）与事件级 EV

| 对 | b* bps | p95覆盖倍数 | 最小深度 US$ | 事件数 | 日均机会/会话 | 胜率 | 净EV/事件 bps | 判定 |
|---|---:|---:|---:|---:|---|---:|---:|---|
| hyperliquid_futures:PARA:AAOI~binance_futures:AAOIUSDT | 78.4 | 0.63 | 12,767 | 177 | closed:105.60，post:20.00，pre:44.00，rth:87.29 | 0% | 0.00 | ③成本覆盖不过；⑤净EV≤0 |
| hyperliquid_futures:PARA:AAOI~okx_futures:AAOI-USDT-SWAP | 78.4 | 0.61 | 11,174 | 187 | closed:91.20，post:18.00，pre:49.50，rth:96.57 | 0% | 0.00 | ③成本覆盖不过；⑤净EV≤0 |
| lighter:IREN~binance_futures:IRENUSDT | 76.2 | 0.59 | 884 | 98 | closed:82.67，post:14.00，pre:22.00，rth:38.07 | 0% | -4.17 | ③成本覆盖不过；④容量不过；⑤净EV≤0 |
| hyperliquid_futures:PARA:AAOI~hyperliquid_futures:XYZ:AAOI | 85.7 | 0.50 | 12,767 | 61 | closed:45.60，post:12.00，pre:11.00，rth:25.07 | 0% | 0.00 | ③成本覆盖不过；⑤净EV≤0 |
| lighter:IREN~okx_futures:IREN-USDT-SWAP | 76.3 | 0.54 | 884 | 146 | closed:101.33，post:19.00，pre:27.50，rth:68.71 | 0% | -0.10 | ③成本覆盖不过；④容量不过；⑤净EV≤0 |
| hyperliquid_futures:XYZ:CRWV~lighter:CRWV | 33.1 | 1.21 | 53,190 | 349 | closed:205.09，post:26.00，pre:135.67，rth:143.93 | 10% | -0.42 | ⑤净EV≤0 |
| lighter:CRWV~binance_futures:CRWVUSDT | 36.2 | 1.07 | 53,190 | 369 | closed:172.36，post:32.00，pre:155.83，rth:160.64 | 0% | 0.00 | ⑤净EV≤0 |

### 2.1 p_fill 情景（净 EV 对 p_fill 的线性敏感性）

| 对 | 保守(0.5×) | 中性(1×) | 乐观(1.5×) |
|---|---:|---:|---:|
| hyperliquid_futures:PARA:AAOI~binance_futures:AAOIUSDT | -20.8 | -41.6 | -62.5 |
| hyperliquid_futures:PARA:AAOI~okx_futures:AAOI-USDT-SWAP | -59.6 | -119.2 | -178.8 |
| lighter:IREN~binance_futures:IRENUSDT | -444.0 | -888.1 | -1332.1 |
| hyperliquid_futures:PARA:AAOI~hyperliquid_futures:XYZ:AAOI | -32.3 | -64.6 | -96.9 |
| lighter:IREN~okx_futures:IREN-USDT-SWAP | -464.6 | -929.3 | -1393.9 |
| hyperliquid_futures:XYZ:CRWV~lighter:CRWV | -167.2 | -334.3 | -501.5 |
| lighter:CRWV~binance_futures:CRWVUSDT | -285.1 | -570.2 | -855.3 |

### 2.2 逐交易日（跑两周的验收标准是逐日稳定，不是聚合平均）

**hyperliquid_futures:PARA:AAOI~binance_futures:AAOIUSDT** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 152 | 0% | -0.27 | 33.30 | 0.004 |
| 2026-11-21 | 25 | 0% | +0.00 | 18.33 | 0.000 |

**hyperliquid_futures:PARA:AAOI~okx_futures:AAOI-USDT-SWAP** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 169 | 0% | -0.71 | 33.28 | 0.013 |
| 2026-11-21 | 18 | 0% | +0.00 | 20.30 | 0.000 |

**lighter:IREN~binance_futures:IRENUSDT** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 82 | 0% | -8.76 | 20.78 | 0.166 |
| 2026-11-21 | 16 | 0% | -10.59 | 18.79 | 0.162 |

**hyperliquid_futures:PARA:AAOI~hyperliquid_futures:XYZ:AAOI** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 52 | 0% | -1.24 | 31.11 | 0.019 |
| 2026-11-21 | 9 | 0% | +0.00 | 18.71 | 0.000 |

**lighter:IREN~okx_futures:IREN-USDT-SWAP** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 127 | 0% | -7.32 | 18.10 | 0.130 |
| 2026-11-21 | 19 | 0% | +0.00 | 14.26 | 0.000 |

**hyperliquid_futures:XYZ:CRWV~lighter:CRWV** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 313 | 5% | -0.99 | 18.44 | 0.099 |
| 2026-11-21 | 36 | 0% | -0.68 | 9.55 | 0.038 |

**lighter:CRWV~binance_futures:CRWVUSDT** —— 正 EV 天数 0/2（0%）

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 329 | 0% | -1.53 | 10.42 | 0.057 |
| 2026-11-21 | 40 | 0% | -1.65 | 6.83 | 0.054 |

## 3. 口径与警告

- 两腿**同刻定价**（同一毫秒盘口 mid），不使用 bar open/close 跨瞬间取价。
- 缺行**不当连续时间**：固定网格 + 记录 stale 占比。
- 滑点来自**盘口档位**（`market_micro.walk_book`），tick 由 `infer_tick(book)` 反推。
- 胜率 >90% 的行必须对照重复收盘率与成交后 markout 再谈 EV。
- 被动腿 p_fill 是**排队模型估计**（排队尾），未经真实 fill 日志标定 → 报告里的 EV 是**上界**。
