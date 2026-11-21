# 全宇宙候选池排序与「第二对」建议

生成时间：2026-11-21T12:13:08.184336+00:00　Stage A 窗口：2026-11-20 11:00 → 2026-11-21 10:00（24 小时）　Stage B 回放窗口：24 小时　单笔名义 $100/腿

候选对总数：**452**（56 个 base × 各场所同名腿的组合）　Stage B 回放对数：**7**

## 1. Stage B 回放排序（主表）

排序依据：**逐交易日净 EV 为正的天数占比** → 净 EV/事件 → 日均机会次数

| # | 对 | base | 腿（maker/被动 · taker/主动） | b* bps | p95覆盖× | 最小深度 US$ | 事件数 | 日均机会 | 胜率 | 正EV天数 | 净EV/事件 bps |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|:---:|---:|
| 1 | `hyperliquid_futures:PARA:AAOI~binance_futures:AAOIUSDT` | AAOI | hyperliquid_futures · binance_futures | 78.4 | 0.63 | 12,767 | 177 | 88.50 | 0% | 0/2 (0%) | -0.24 |
| 2 | `hyperliquid_futures:PARA:AAOI~okx_futures:AAOI-USDT-SWAP` | AAOI | hyperliquid_futures · okx_futures | 78.4 | 0.61 | 11,174 | 187 | 93.50 | 0% | 0/2 (0%) | -0.64 |
| 3 | `hyperliquid_futures:XYZ:CRWV~lighter:CRWV` | CRWV | lighter · hyperliquid_futures | 33.1 | 1.21 | 53,190 | 349 | 174.50 | 10% | 0/2 (0%) | -0.96 |
| 4 | `hyperliquid_futures:PARA:AAOI~hyperliquid_futures:XYZ:AAOI` | AAOI | hyperliquid_futures · hyperliquid_futures | 85.7 | 0.50 | 12,767 | 61 | 30.50 | 0% | 0/2 (0%) | -1.06 |
| 5 | `lighter:CRWV~binance_futures:CRWVUSDT` | CRWV | lighter · binance_futures | 36.2 | 1.07 | 53,190 | 369 | 184.50 | 0% | 0/2 (0%) | -1.55 |
| 6 | `lighter:IREN~okx_futures:IREN-USDT-SWAP` | IREN | lighter · okx_futures | 76.3 | 0.54 | 884 | 146 | 73.00 | 0% | 0/2 (0%) | -6.37 |
| 7 | `lighter:IREN~binance_futures:IRENUSDT` | IREN | lighter · binance_futures | 76.2 | 0.59 | 884 | 98 | 49.00 | 0% | 0/2 (0%) | -9.06 |

## 2. 前 5 对的逐交易日净 EV

**hyperliquid_futures:PARA:AAOI~binance_futures:AAOIUSDT** —— 正 EV 0/2 天（0%），净 EV/事件 -0.24 bps，日均机会 88.50 次

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 152 | 0% | -0.27 | 33.30 | 0.004 |
| 2026-11-21 | 25 | 0% | +0.00 | 18.33 | 0.000 |

**hyperliquid_futures:PARA:AAOI~okx_futures:AAOI-USDT-SWAP** —— 正 EV 0/2 天（0%），净 EV/事件 -0.64 bps，日均机会 93.50 次

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 169 | 0% | -0.71 | 33.28 | 0.013 |
| 2026-11-21 | 18 | 0% | +0.00 | 20.30 | 0.000 |

**hyperliquid_futures:XYZ:CRWV~lighter:CRWV** —— 正 EV 0/2 天（0%），净 EV/事件 -0.96 bps，日均机会 174.50 次

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 313 | 5% | -0.99 | 18.44 | 0.099 |
| 2026-11-21 | 36 | 0% | -0.68 | 9.55 | 0.038 |

**hyperliquid_futures:PARA:AAOI~hyperliquid_futures:XYZ:AAOI** —— 正 EV 0/2 天（0%），净 EV/事件 -1.06 bps，日均机会 30.50 次

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 52 | 0% | -1.24 | 31.11 | 0.019 |
| 2026-11-21 | 9 | 0% | +0.00 | 18.71 | 0.000 |

**lighter:CRWV~binance_futures:CRWVUSDT** —— 正 EV 0/2 天（0%），净 EV/事件 -1.55 bps，日均机会 184.50 次

| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |
|---|---:|---:|---:|---:|---:|
| 2026-11-20 | 329 | 0% | -1.53 | 10.42 | 0.057 |
| 2026-11-21 | 40 | 0% | -1.65 | 6.83 | 0.054 |

## 3. 全部 56 个 base 的 Stage A 结果

`闸门①` = 单位一致（|ratio−1|≤5% 且 cv≤5%）；`闸门②` = 重复收盘率 ≤10%（非陈旧打印）。

| base | 候选对 | 过① | 过①② | 最佳对的 p95 偏离 bps | 最佳对 | 状态 |
|---|---:|---:|---:|---:|---|---|
| AAOI
AAPL
AMD
AMZN
ANTHROPIC
ARM
ASML
AVGO
BE
BMNR
CBRS
COIN
CRCL
CRWV
CXMT
DELL
DRAM
EWY
GEV
GME
GOOGL
HOOD
HYUNDAI
IBM
INTC
IREN
KORU
LITE
META
MRNA
MRVL
MSFT
MSTR
MU
NBIS
NOW
NVDA
OPENAI
ORCL
PLTR
QNT
RKLB
SKHY
SNDK
SOXL
SPCX
STRC
TSLA
TSM
UNITREE | 0 | 0 | 0 | – | – | 无候选对 |

## 4. 每条腿的成交笔数/分钟（Stage A 窗口，摊到全窗口）

| 腿 | 成交笔数 | 覆盖小时 | 笔/分钟 |
|---|---:|---:|---:|
| binance_futures:SOXLUSDT | 7802610 | 24 | 5418.5 |
| binance_futures:MUUSDT | 4462675 | 24 | 3099.1 |
| okx_futures:SOXL-USDT-SWAP | 4070170 | 24 | 2826.5 |
| binance_futures:NVDAUSDT | 3484739 | 24 | 2420.0 |
| binance_futures:SNDKUSDT | 2662438 | 24 | 1848.9 |
| binance_futures:KORUUSDT | 2188475 | 24 | 1519.8 |
| okx_futures:ANTHROPIC-USDT-SWAP | 1690751 | 24 | 1174.1 |
| binance_futures:SPCXUSDT | 1625456 | 24 | 1128.8 |
| binance_futures:AMDUSDT | 1551449 | 24 | 1077.4 |
| okx_futures:SNDK-USDT-SWAP | 1317967 | 24 | 915.3 |
| okx_futures:KORU-USDT-SWAP | 1178471 | 24 | 818.4 |
| binance_futures:MSTRUSDT | 1114305 | 24 | 773.8 |
| binance_futures:SKHYUSDT | 1051216 | 24 | 730.0 |
| binance_futures:GOOGLUSDT | 996542 | 24 | 692.0 |
| binance_futures:INTCUSDT | 948637 | 24 | 658.8 |
| okx_futures:MU-USDT-SWAP | 920611 | 24 | 639.3 |
| binance_futures:CRCLUSDT | 917156 | 24 | 636.9 |
| okx_futures:NVDA-USDT-SWAP | 887425 | 24 | 616.3 |
| binance_futures:TSLAUSDT | 808176 | 24 | 561.2 |
| binance_futures:NBISUSDT | 736020 | 24 | 511.1 |
| binance_futures:MRVLUSDT | 724997 | 24 | 503.5 |
| binance_futures:LITEUSDT | 651636 | 24 | 452.5 |
| binance_futures:QNTUSDT | 631027 | 24 | 438.2 |
| binance_futures:EWYUSDT | 589104 | 24 | 409.1 |
| okx_futures:SPCX-USDT-SWAP | 527939 | 24 | 366.6 |
| binance_futures:COINUSDT | 484559 | 24 | 336.5 |
| binance_futures:DRAMUSDT | 474860 | 24 | 329.8 |
| binance_futures:AVGOUSDT | 461176 | 24 | 320.3 |
| okx_futures:MSTR-USDT-SWAP | 416986 | 24 | 289.6 |
| binance_futures:CBRSUSDT | 410335 | 24 | 285.0 |
| binance_futures:BEUSDT | 391121 | 24 | 271.6 |
| binance_futures:AAPLUSDT | 377552 | 24 | 262.2 |
| binance_futures:AAOIUSDT | 316564 | 24 | 219.8 |
| binance_futures:CRWVUSDT | 313418 | 24 | 217.7 |
| binance_futures:METAUSDT | 308973 | 24 | 214.6 |
| okx_futures:SKHY-USDT-SWAP | 303731 | 24 | 210.9 |
| hyperliquid_futures:XYZ:SOXL | 274841 | 24 | 190.9 |
| binance_futures:TSMUSDT | 246861 | 24 | 171.4 |
| okx_futures:AMD-USDT-SWAP | 242286 | 24 | 168.3 |
| hyperliquid_futures:XYZ:NVDA | 240906 | 24 | 167.3 |
| binance_futures:HOODUSDT | 237627 | 24 | 165.0 |
| okx_futures:CRCL-USDT-SWAP | 229537 | 24 | 159.4 |
| binance_futures:OPENAIUSDT | 228269 | 24 | 158.5 |
| okx_futures:INTC-USDT-SWAP | 220088 | 24 | 152.8 |
| binance_futures:PLTRUSDT | 198937 | 24 | 138.2 |
| binance_futures:AMZNUSDT | 198930 | 24 | 138.1 |
| okx_futures:NBIS-USDT-SWAP | 196040 | 24 | 136.1 |
| okx_futures:GOOGL-USDT-SWAP | 191414 | 24 | 132.9 |
| binance_futures:ARMUSDT | 191245 | 24 | 132.8 |
| okx_futures:MRVL-USDT-SWAP | 183689 | 24 | 127.6 |
| binance_futures:ORCLUSDT | 172269 | 24 | 119.6 |
| binance_futures:DELLUSDT | 166317 | 24 | 115.5 |
| okx_futures:LITE-USDT-SWAP | 165972 | 24 | 115.3 |
| binance_futures:MSFTUSDT | 163060 | 24 | 113.2 |
| binance_futures:RKLBUSDT | 159326 | 24 | 110.6 |
| hyperliquid_futures:XYZ:MU | 149200 | 24 | 103.6 |
| okx_futures:TSLA-USDT-SWAP | 139890 | 24 | 97.1 |
| lighter:NVDA | 126981 | 24 | 88.2 |
| binance_futures:BMNRUSDT | 119529 | 24 | 83.0 |
| binance_futures:MRNAUSDT | 113264 | 24 | 78.7 |
| binance_futures:IRENUSDT | 112172 | 24 | 77.9 |
| okx_futures:TSM-USDT-SWAP | 109971 | 24 | 76.4 |
| okx_futures:DRAM-USDT-SWAP | 109015 | 24 | 75.7 |
| okx_futures:AAOI-USDT-SWAP | 107849 | 24 | 74.9 |
| hyperliquid_futures:XYZ:DRAM | 106308 | 24 | 73.8 |
| hyperliquid_futures:XYZ:SNDK | 85534 | 24 | 59.4 |
| okx_futures:COIN-USDT-SWAP | 76622 | 24 | 53.2 |
| hyperliquid_futures:XYZ:SKHY | 75890 | 24 | 52.7 |
| hyperliquid_futures:XYZ:CRCL | 73893 | 24 | 51.3 |
| okx_futures:AAPL-USDT-SWAP | 72750 | 24 | 50.5 |
| binance_futures:CXMTUSDT | 72080 | 24 | 50.1 |
| okx_futures:CRWV-USDT-SWAP | 71192 | 24 | 49.4 |
| binance_futures:STRCUSDT | 70387 | 24 | 48.9 |
| binance_futures:UNITREEUSDT | 70002 | 24 | 48.6 |
| hyperliquid_futures:XYZ:KORU | 69707 | 24 | 48.4 |
| hyperliquid_futures:XYZ:SPCX | 65213 | 24 | 45.3 |
| hyperliquid_futures:XYZ:GOOGL | 64842 | 24 | 45.0 |
| okx_futures:CBRS-USDT-SWAP | 63842 | 24 | 44.3 |
| hyperliquid_futures:XYZ:AMD | 57531 | 24 | 40.0 |
| okx_futures:EWY-USDT-SWAP | 56395 | 24 | 39.2 |
| hyperliquid_futures:XYZ:NBIS | 55556 | 24 | 38.6 |
| hyperliquid_futures:XYZ:INTC | 51746 | 24 | 35.9 |
| okx_futures:AVGO-USDT-SWAP | 51334 | 24 | 35.6 |
| binance_futures:ASMLUSDT | 50979 | 24 | 35.4 |
| lighter:AAPL | 50524 | 24 | 35.1 |
| okx_futures:BE-USDT-SWAP | 49514 | 24 | 34.4 |
| hyperliquid_futures:XYZ:TSLA | 48018 | 24 | 33.3 |
| okx_futures:ORCL-USDT-SWAP | 45866 | 24 | 31.9 |
| okx_futures:META-USDT-SWAP | 41281 | 24 | 28.7 |
| okx_futures:DELL-USDT-SWAP | 40565 | 24 | 28.2 |
| okx_futures:ARM-USDT-SWAP | 38448 | 24 | 26.7 |
| hyperliquid_futures:IO:NVDA | 37487 | 24 | 26.0 |
| hyperliquid_futures:XYZ:META | 37270 | 24 | 25.9 |
| okx_futures:UNITREE-USDT-SWAP | 35784 | 24 | 24.9 |
| binance_futures:IBMUSDT | 34732 | 24 | 24.1 |
| okx_futures:MRNA-USDT-SWAP | 34281 | 24 | 23.8 |
| okx_futures:AMZN-USDT-SWAP | 33923 | 24 | 23.6 |
| okx_futures:OPENAI-USDT-SWAP | 33733 | 24 | 23.4 |
| hyperliquid_futures:XYZ:AAPL | 33052 | 24 | 23.0 |
| okx_futures:HOOD-USDT-SWAP | 33046 | 24 | 22.9 |
| lighter:TSLA | 32594 | 24 | 22.6 |
| hyperliquid_futures:XYZ:EWY | 31423 | 24 | 21.8 |
| lighter:MU | 30684 | 24 | 21.3 |
| okx_futures:MSFT-USDT-SWAP | 29500 | 24 | 20.5 |
| lighter:GOOGL | 29035 | 24 | 20.2 |
| hyperliquid_futures:XYZ:AMZN | 28250 | 24 | 19.6 |
| okx_futures:CXMT-USDT-SWAP | 27971 | 22 | 19.4 |
| hyperliquid_futures:XYZ:COIN | 27735 | 24 | 19.3 |
| hyperliquid_futures:XYZ:MSTR | 26446 | 24 | 18.4 |
| binance_futures:GMEUSDT | 25381 | 24 | 17.6 |
| okx_futures:RKLB-USDT-SWAP | 24993 | 24 | 17.4 |
| hyperliquid_futures:IO:SNDK | 24204 | 24 | 16.8 |
| lighter:SOXL | 23807 | 24 | 16.5 |
| hyperliquid_futures:XYZ:MRVL | 23167 | 24 | 16.1 |
| hyperliquid_futures:XYZ:IREN | 22829 | 24 | 15.9 |
| hyperliquid_futures:XYZ:CRWV | 22157 | 24 | 15.4 |
| okx_futures:IREN-USDT-SWAP | 21942 | 24 | 15.2 |
| okx_futures:BMNR-USDT-SWAP | 21810 | 24 | 15.1 |
| binance_futures:NOWUSDT | 21710 | 24 | 15.1 |
| lighter:AMZN | 20855 | 24 | 14.5 |
| binance_futures:HYUNDAIUSDT | 20482 | 24 | 14.2 |
| hyperliquid_futures:XYZ:MSFT | 19824 | 24 | 13.8 |
| hyperliquid_futures:XYZ:CXMT | 19817 | 24 | 13.8 |
| lighter:CRCL | 19617 | 24 | 13.6 |
| lighter:META | 18565 | 24 | 12.9 |
| okx_futures:PLTR-USDT-SWAP | 18494 | 24 | 12.8 |
| lighter:SPCX | 18220 | 24 | 12.7 |
| hyperliquid_futures:XYZ:CBRS | 18006 | 24 | 12.5 |
| binance_futures:GEVUSDT | 15644 | 24 | 10.9 |
| lighter:INTC | 15563 | 24 | 10.8 |
| hyperliquid_futures:XYZ:HOOD | 15467 | 24 | 10.7 |
| hyperliquid_futures:XYZ:MRNA | 12763 | 24 | 8.9 |
| lighter:AMD | 12173 | 24 | 8.5 |
| lighter:MSFT | 12001 | 24 | 8.3 |
| lighter:COIN | 11675 | 24 | 8.1 |
| hyperliquid_futures:XYZ:LITE | 10883 | 24 | 7.6 |
| lighter:MSTR | 10409 | 24 | 7.2 |
| hyperliquid_futures:XYZ:ORCL | 10007 | 24 | 6.9 |
| hyperliquid_futures:XYZ:UNITREE | 9952 | 24 | 6.9 |
| hyperliquid_futures:XYZ:TSM | 9688 | 24 | 6.7 |
| okx_futures:STRC-USDT-SWAP | 9536 | 24 | 6.6 |
| hyperliquid_futures:IO:NBIS | 9265 | 24 | 6.4 |
| hyperliquid_futures:XYZ:ARM | 8964 | 24 | 6.2 |
| hyperliquid_futures:XYZ:STRC | 8585 | 24 | 6.0 |
| okx_futures:ASML-USDT-SWAP | 8520 | 24 | 5.9 |
| hyperliquid_futures:XYZ:BE | 8486 | 24 | 5.9 |
| hyperliquid_futures:XYZ:DELL | 7650 | 24 | 5.3 |
| lighter:HOOD | 7466 | 24 | 5.2 |
| okx_futures:QNT-USDT-SWAP | 7387 | 24 | 5.1 |
| hyperliquid_futures:XYZ:RKLB | 7321 | 24 | 5.1 |
| lighter:STRC | 7149 | 24 | 5.0 |
| hyperliquid_futures:XYZ:AVGO | 7027 | 24 | 4.9 |
| lighter:NBIS | 6929 | 24 | 4.8 |
| hyperliquid_futures:XYZ:PLTR | 6616 | 24 | 4.6 |
| hyperliquid_futures:XYZ:GME | 6514 | 24 | 4.5 |
| lighter:DRAM | 6346 | 24 | 4.4 |
| lighter:PLTR | 6328 | 24 | 4.4 |
| okx_futures:IBM-USDT-SWAP | 6268 | 24 | 4.4 |
| lighter:ANTHROPIC | 6111 | 24 | 4.2 |
| lighter:SKHY | 5677 | 24 | 3.9 |
| lighter:LITE | 5644 | 24 | 3.9 |
| lighter:CBRS | 4446 | 24 | 3.1 |
| hyperliquid_futures:IO:DRAM | 3999 | 24 | 2.8 |
| hyperliquid_futures:XYZ:AAOI | 3969 | 24 | 2.8 |
| lighter:OPENAI | 3965 | 24 | 2.8 |
| okx_futures:GME-USDT-SWAP | 3563 | 24 | 2.5 |
| hyperliquid_futures:XYZ:BMNR | 3073 | 24 | 2.1 |
| okx_futures:HYUNDAI-USDT-SWAP | 2889 | 24 | 2.0 |
| lighter:MRNA | 2434 | 24 | 1.7 |
| hyperliquid_futures:XYZ:IBM | 2396 | 24 | 1.7 |
| hyperliquid_futures:IO:AAPL | 2269 | 24 | 1.6 |
| lighter:SNDK | 2263 | 20 | 1.6 |
| lighter:MRVL | 2220 | 24 | 1.5 |
| lighter:IREN | 2175 | 23 | 1.5 |
| okx_futures:NOW-USDT-SWAP | 2081 | 24 | 1.4 |
| hyperliquid_futures:XYZ:ASML | 1797 | 24 | 1.2 |
| hyperliquid_futures:PARA:UNITREE | 1773 | 23 | 1.2 |
| lighter:IBM | 1724 | 24 | 1.2 |
| hyperliquid_futures:XYZ:HYUNDAI | 1657 | 22 | 1.2 |
| okx_futures:GEV-USDT-SWAP | 1456 | 23 | 1.0 |
| lighter:EWY | 1454 | 23 | 1.0 |
| lighter:UNITREE | 1370 | 24 | 1.0 |
| hyperliquid_futures:IO:CRCL | 1259 | 24 | 0.9 |
| hyperliquid_futures:IO:EWY | 1238 | 24 | 0.9 |
| hyperliquid_futures:XYZ:QNT | 1166 | 24 | 0.8 |
| lighter:TSM | 1121 | 23 | 0.8 |
| lighter:RKLB | 985 | 23 | 0.7 |
| hyperliquid_futures:XYZ:NOW | 855 | 24 | 0.6 |
| hyperliquid_futures:IO:META | 853 | 23 | 0.6 |
| lighter:ASML | 722 | 18 | 0.5 |
| lighter:CRWV | 706 | 24 | 0.5 |
| lighter:BE | 617 | 23 | 0.4 |
| hyperliquid_futures:IO:INTC | 581 | 21 | 0.4 |
| lighter:ORCL | 546 | 24 | 0.4 |
| hyperliquid_futures:PARA:AAOI | 542 | 22 | 0.4 |
| hyperliquid_futures:PARA:IREN | 531 | 21 | 0.4 |
| hyperliquid_futures:XYZ:GEV | 441 | 20 | 0.3 |
| lighter:QNT | 362 | 24 | 0.3 |
| hyperliquid_futures:PARA:AVGO | 342 | 16 | 0.2 |
| lighter:BMNR | 331 | 20 | 0.2 |
| lighter:DELL | 232 | 21 | 0.2 |
| lighter:AAOI | 155 | 19 | 0.1 |
| lighter:CXMT | 126 | 17 | 0.1 |
| lighter:AVGO | 104 | 18 | 0.1 |
| lighter:KORU | 101 | 19 | 0.1 |
| lighter:GME | 75 | 15 | 0.1 |
| hyperliquid_futures:IO:MU | 72 | 13 | 0.1 |
| lighter:NOW | 61 | 18 | 0.0 |
| lighter:GEV | 12 | 3 | 0.0 |
| lighter:ARM | 1 | 1 | 0.0 |
| hyperliquid_futures:KM:AAPL | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:AMZN | 0 | 0 | 0.0 |
| hyperliquid_futures:VNTL:ANTHROPIC | 0 | 0 | 0.0 |
| binance_futures:ANTHROPICUSDT | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:BMNR | 0 | 0 | 0.0 |
| hyperliquid_futures:FLX:COIN | 0 | 0 | 0.0 |
| hyperliquid_futures:FLX:CRCL | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:EWY | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:GOOGL | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:GOOGL | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:HOOD | 0 | 0 | 0.0 |
| lighter:HYUNDAI | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:INTC | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:META | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:MSFT | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:MU | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:NVDA | 0 | 0 | 0.0 |
| hyperliquid_futures:FLX:NVDA | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:NVDA | 0 | 0 | 0.0 |
| hyperliquid_futures:VNTL:OPENAI | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:PLTR | 0 | 0 | 0.0 |
| hyperliquid_futures:CASH:TSLA | 0 | 0 | 0.0 |
| hyperliquid_futures:FLX:TSLA | 0 | 0 | 0.0 |
| hyperliquid_futures:KM:TSLA | 0 | 0 | 0.0 |


---

## 5. 结论：**没有第二对**（P2-6 的诚实答案）

全宇宙 452 个候选对 → 288 对过闸门①② → 取偏离最大、半衰期有限的 7 对做盘口回放 →
**7 对全部净 EV ≤ 0，逐日正 EV 天数 0/2**。

| 现象 | 说明 |
|---|---|
| 毛捕获看起来很大（AAOI 达 33 bps/事件） | 但 `b*` 高达 **78~86 bps**（费率 + 点差 + 滑点 + 腿风险），**捕获盖不住成本** |
| p95 覆盖倍数全部 < 1（除 CRWV 1.07~1.21） | 基差 p95 **不够宽**，闸门③不过 |
| 胜率几乎全 0 | 成交率极低（`mean_p_fill` 0.00~0.12），而且成交后价差不收敛 |
| 最深的标的不是最好的 | Binance 腿成交 200~5400 笔/分，但 Binance 做 taker 要 5bps × 2 次成交 |

### 为什么 SOXL 反而成立，而 AAOI / IREN / CRWV 不成立

对比三个数（同一口径）：

| 对 | 费率结构 | 点差（两腿） | b* | p95 覆盖 | 净 EV |
|---|---|---|---:|---:|---:|
| **HL XYZ:SOXL ↔ Lighter SOXL** | HL 0.3/0.9（growth mode）+ **Lighter 0/0** | 1.27 + 3.25 | **11.1** | **2.05×** | **+2.80** |
| HL PARA:AAOI ↔ Binance AAOI | HL 0.3/0.9 + Binance 2/5 | — | 78.4 | 0.63× | −0.24 |
| HL XYZ:CRWV ↔ Lighter CRWV | HL 0.3/0.9 + Lighter 0/0 | — | 33.1 | 1.21× | −0.96 |

**决定性差异是 `b*`（11.1 vs 33~86 bps），不是价差大小。**
`b*` 高是因为：
1. **点差**：Lighter 的点差 3.25bps 是 Binance/OKX 的 2~4 倍 —— 目标腿本身点差就宽；
2. **滑点**：AAOI/IREN 这类小盘股在 ±25bps 内的深度只有 $884~$12,767，
   $100 名义也要吃掉 2~3 档；
3. **腿风险**：σ₁m 高（小盘股波动大）× 腿风险溢价。

**结论：在「同一底层、跨场所」的框架下，费率结构 + 点差 + 深度共同决定了 `b*`，
而 `b*` 才是筛子 —— 价差大不等于有机会。**
SOXL 之所以成立，是因为它同时满足：Lighter 费率 0 + 点差可控 + 深度充足 + p95 偏离够宽。

### 「第二对」该怎么办

当前**没有可用的第二对**。三条可行路径（按性价比）：

1. **同一 base 的其它场所组合**：`XYZ:SOXL` 在 HL 还有 `PARA:SOXL`/`IO:SOXL` 等 deployer 版本，
   以及 OKX/Binance 的 SOXL 永续 —— **换个场所不换 base** 仍可能保持低 `b*`。
   这是最省事的方向（数据已缓存，可直接回放）。
2. **等费率环境**：现在被高 `b*` 挡掉的 AAOI/IREN/CRWV，如果 Lighter 那边费率结构变化
   （或这些标的的点差收窄），可能重新进候选。**这些对已在 `data/universe_stage_a.json` 里，
   可以按滚动窗口定期重跑闸门③**。
3. **换策略形态**：这些标的的「价差大但盖不住成本」说明
   **均值回归不是它们的边际来源** —— 它们更像是被永续溢价/情绪驱动的动量标的，
   适合的是别的策略（如 funding carry），不是本项目的价差收敛。

### 未验证的部分（诚实说明）

- Stage B 只回放了 **24 小时 × 7 对**（不是 14 天）。
- 这 7 对是按**偏离幅度**挑的，不是穷举 —— 结论「没有第二对」的强度受此限制；
  严格地说：「偏离最大的 7 对都不行」，不等于「288 对都不行」。
  要彻底排除需要对全部 288 对做 Stage B（数据量再增 40 倍）。
- `p_fill` 仍是模型值（实测标定显示模型偏保守 → 本结论偏保守）。
