# 经验记录：GitHub 推送、VPS 运维、nautilus 2.0 的坑

> 约定：只记**踩过的坑**（环境怪癖、非显然的不变量、看起来对但失败的做法），每条带日期与证据；
> 只追加，写错了用带日期的一行更正，不要重写历史。

## 2026-11-20 · 明文 PAT 会被 GitHub 自动吊销
- 现象：`ghp_…` 在 `W:\.env.md`、两个仓库的 `.git/config` remote URL、agent 会话日志里以明文存在过，
  后来**三个不同的 PAT 同时**变成 `401 Bad credentials`（Bearer / token / Basic 三种鉴权方式全拒）。
- 结论：GitHub secret scanning 会自动吊销暴露过的 PAT。
- 做法：token 只进 `git credential approve`（Windows 凭据管理器），**不进任何明文文件、不进 .git/config**。
  清理明文：`.git/config` 用 `git remote set-url` 去掉内嵌 token；notes/报告文件直接替换掉字符串。

## 2026-11-20 · 6h 报告 cron 48 次推送全被拒
- 现象：`/var/log/nt-pairs/report_cron.log` 里 `grep -c rejected` = 48，全部是
  `! [rejected] HEAD -> main (fetch first)`——cron 只 commit+push，从不 fetch，
  而 captain 往 main 推过代码 → VPS 分叉。
- 修法：`scripts/vps_report_cron.sh` 改成 **先 `git fetch` + `rebase` 再提交推送**；
  推送失败要**优雅退出**（退出码 3 + 打印本地报告路径），不要 `set -e` 直接崩。

## 2026-11-20 · `git reset --soft origin/main` 会把上游新文件当成删除提交
- 现象：本地没有 VPS 刚推的报告文件时，`git reset --soft origin/main` 会保留旧 index，
  于是那些上游文件在 index 里显示为 `D`，跟着下一次 commit 一起把**上游文件删掉**
  （本次误删 `reports/20261120T092143Z.{md,json}`，CI 随即变红）。
- 做法：同步上游用 `git fetch origin main && git merge --ff-only`（或 `git pull --rebase`）；
  必须 rebase 时先配好 `user.name`/`user.email`（仓库原本没配，`git rebase --continue` 会卡在
  `fatal: unable to auto-detect email address`）。

## 2026-11-20 · nautilus 2.0.0rc6 的 `with_timeout_connection` 要**整数秒**
- 现象：传 `timedelta(seconds=60)` → `TypeError: 'datetime.timedelta' object cannot be interpreted as an integer`。
- 签名：`with_timeout_connection(self, /, timeout_secs)`、`with_timeout_disconnection_secs(self, secs)`（都是 int）。

## 2026-11-20 · `live_runner --max-secs` 默认 60 会造成 systemd 重启风暴
- 现象：`nt-trader` 一夜 `NRestarts=6029`，journal 里每分钟一次 `Awaiting engine connections (60s timeout)`，
  看着像网络问题，实际是**节点自己到点优雅停机** + systemd `Restart=always` 又拉起来。
- 做法：常驻单元必须传 `--max-secs 0`（一直跑到 systemctl stop）；症状是「每 60 秒正好一次」。

## 2026-11-20 · VPS 上的 deploy key 必须在仓库里注册且勾「允许写入」
- 自检：`ssh -T -i /root/.ssh/nt_pairs_report git@github.com` 应回
  `Hi jssyxd/nt-pairs! You've successfully authenticated`；
  `git ls-remote` 还必须带 `GIT_SSH_COMMAND="ssh -i … -o IdentitiesOnly=yes"`，否则会去用默认 identity 而被拒。
- 注册（用有 admin 的 token 调 API）：`POST /repos/{owner}/{repo}/keys`
  `{"title":"nt-pairs-vps","key":"<公钥>","allow_write_access":true}`。

## 2026-11-21 · `url.<token>.insteadOf` 会**静默覆盖**凭据管理器里的 token
- 现象：`git remote set-url` 已经把 remote 换成干净 URL、`git credential approve` 也写进了
  新 token，`git credential fill` 返回的确实是新 token —— 但 `git push` 依然报
  `Invalid username or token`，而 `git push https://<user>:<token>@github.com/... `（显式 URL）**能成功**。
- 根因：`~/.gitconfig` 里残留着上一代 token 的 URL 重写：
  `url.https://jssyxd:<旧token>@github.com/.insteadOf = https://github.com/`
  → 任何 `https://github.com/...` 的 URL 都被**自动改写**成带旧 token 的形式，
  凭据管理器根本没被问到。旧 token 早已被吊销 → 401。
- 定位方法：`GIT_TRACE_CURL=1 git push` 会打印被改写后的完整 URL
  （`== Info: [HTTP/2] [1] OPENED stream for https://jssyxd:<token>@github.com/...`）—— 对比输入 URL 即可。
- 修法：`git config --global --unset-all url.https://<user>:<旧token>@github.com/.insteadOf`
  然后 `git credential approve` 写入新 token（**只进凭据管理器，不进任何明文文件**）。
- 连带教训：`git credential approve` 之后 `git credential fill` 返回正确值，**不代表 push 会用它** ——
  先确认没有 `insteadOf` 之类的 URL 改写。

## 2026-11-21 · 失败的鉴权会让 git **擦掉**刚写入的凭据
- 现象：`git credential approve` 写入新 token → `git push` 因为别的原因 401 →
  git 调用 `credential reject` → 凭据管理器里刚写的条目**被删除**。
  表现是「刚 approve 过，文件里就没了」。
- 做法：先排除鉴权本身的问题（URL 改写、代理、scope），再 approve + push；或直接用显式 URL push
  （但**不要**把 token 写进 `.git/config`）。

## 2026-11-21 · clash-verge（mihomo）的 HTTP 代理端口是 **7897**，不是面板显示的端口
- 面板/`ss` 里能看到多个监听端口（如 39133），但**只有 7897 是 HTTP/SOCKS 混合代理**。
- 实测：`HTTPS_PROXY=http://127.0.0.1:7897` 下 `pypi.org` / `api.github.com` / `api.cryptohftdata.com` 均 200；
  换成面板端口直接连接失败（curl exit 7）。
