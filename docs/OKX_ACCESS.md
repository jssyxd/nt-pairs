# OKX 访问可达性矩阵

生成时间：2026-11-20T17:46:32Z　代理：http://127.0.0.1:7897　超时：10.0s　重试：2　离线：否

## 结论

推荐：**urllib+UA** 直连 `www.okx.com` 可用（http://127.0.0.1:7897 代理同样可用，作为**直连抖动时的兜底**：`export HTTPS_PROXY=http://127.0.0.1:7897 HTTP_PROXY=http://127.0.0.1:7897`）；`aws.okx.com` 连不上（TLS EOF / 超时），不要用；`static.okx.com` 能连但不托管 `/api/v5`（返回 ['404']），别当 API 入口

### 按方法汇总

| 方法 | 成功/总数 | 成功请求中位耗时 |
|---|---:|---:|
| curl | 3/9 | 0.719s |
| urllib+UA | 3/9 | 0.726s |
| urllib+UA+proxy | 3/9 | 0.678s |

### 按域名汇总（跨方法）

| 域名 | 成功/总数 |
|---|---:|
| www.okx.com | 9/9 |
| aws.okx.com | 0/9 |
| static.okx.com | 0/9 |

## 完整矩阵

| 方法 | 域名 | 端点 | HTTP | 耗时(s) | 错误 |
|---|---|---|---:|---:|---|
| curl | www.okx.com | /api/v5/public/time | 200 | 0.649 | — |
| curl | www.okx.com | /api/v5/market/books | 200 | 0.727 | — |
| curl | www.okx.com | /api/v5/market/history-candles | 200 | 0.719 | — |
| curl | aws.okx.com | /api/v5/public/time | 000 | 0.527 | curl: (35) TLS connect error: error:0A000126:SSL routines::unexpected eof while  |
| curl | aws.okx.com | /api/v5/market/books | 000 | 0.415 | curl: (35) TLS connect error: error:0A000126:SSL routines::unexpected eof while  |
| curl | aws.okx.com | /api/v5/market/history-candles | 000 | 0.482 | curl: (35) TLS connect error: error:0A000126:SSL routines::unexpected eof while  |
| curl | static.okx.com | /api/v5/public/time | 404 | 0.785 | — |
| curl | static.okx.com | /api/v5/market/books | 404 | 0.767 | — |
| curl | static.okx.com | /api/v5/market/history-candles | 404 | 1.310 | — |
| urllib+UA | www.okx.com | /api/v5/public/time | 200 | 0.669 | — |
| urllib+UA | www.okx.com | /api/v5/market/books | 200 | 0.726 | — |
| urllib+UA | www.okx.com | /api/v5/market/history-candles | 200 | 0.768 | — |
| urllib+UA | aws.okx.com | /api/v5/public/time | — | 0.503 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA | aws.okx.com | /api/v5/market/books | — | 0.498 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA | aws.okx.com | /api/v5/market/history-candles | — | 0.495 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA | static.okx.com | /api/v5/public/time | 404 | 0.791 | HTTP 404 |
| urllib+UA | static.okx.com | /api/v5/market/books | 404 | 0.765 | HTTP 404 |
| urllib+UA | static.okx.com | /api/v5/market/history-candles | 404 | 0.945 | HTTP 404 |
| urllib+UA+proxy | www.okx.com | /api/v5/public/time | 200 | 0.663 | — |
| urllib+UA+proxy | www.okx.com | /api/v5/market/books | 200 | 0.678 | — |
| urllib+UA+proxy | www.okx.com | /api/v5/market/history-candles | 200 | 0.687 | — |
| urllib+UA+proxy | aws.okx.com | /api/v5/public/time | — | 0.513 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA+proxy | aws.okx.com | /api/v5/market/books | — | 0.510 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA+proxy | aws.okx.com | /api/v5/market/history-candles | — | 0.504 | URLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in vio |
| urllib+UA+proxy | static.okx.com | /api/v5/public/time | 404 | 1.854 | HTTP 404 |
| urllib+UA+proxy | static.okx.com | /api/v5/market/books | 404 | 0.756 | HTTP 404 |
| urllib+UA+proxy | static.okx.com | /api/v5/market/history-candles | 404 | 1.454 | HTTP 404 |

## 口径说明

- 三个端点：`/api/v5/public/time`（连通性）、`/api/v5/market/books`（盘口）、`/api/v5/market/history-candles`（K 线），后两个带 `instId=SOXL-USDT-SWAP`。
- `curl` 与 `urllib` 都带 `User-Agent: Mozilla/5.0`；区别只在 TLS/HTTP 栈（curl 走 OpenSSL、urllib 走 Python 的 ssl），因此**只测 curl 会误判**。
- `urllib+UA+proxy` 走 `HTTP(S)_PROXY` 指定的 HTTP 代理端口（clash-verge / mihomo 默认 7897，**39133 是控制端口，不能当 HTTP 代理**）。
- 失败信息保留原始异常串，便于区分 DNS / TLS / 超时 / HTTP 错误。
- **直连是抖动的**：同一台机器上 `curl` 直连曾出现 `error:0A000126:SSL routines::unexpected eof`，而 urllib 直连同时刻成功 —— 所以脚本统一用 `urllib + User-Agent`，并保留代理 7897 作兜底，而不是把结论钉死在 「curl 通 / 不通」上。
