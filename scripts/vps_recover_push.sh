#!/usr/bin/env bash
# 在 VPS 上把本地提交并回上游并推送（**跑在 VPS 上**）。
#
# 前提：VPS 的 deploy key 已在 GitHub 仓库里注册且**允许写入**
#   （仓库 Settings → Deploy keys → Add deploy key → Allow write access）。
# 验证：`ssh -T -i ~/.ssh/nt_pairs_report git@github.com` 应回
#   "Hi jssyxd! You've successfully authenticated…"
#
# 用法：bash /opt/nt-pairs/scripts/vps_recover_push.sh
set -euo pipefail

REPO_DIR="${NT_REPO_DIR:-/opt/nt-pairs}"
SSH_KEY="${NT_REPORT_SSH_KEY:-${HOME}/.ssh/nt_pairs_report}"
SKIP_COMMIT="${NT_SKIP_COMMIT:-7bfa598}"   # 与本地仓库重复的修复提交（只含 report 的 commit 需要重放）

cd "${REPO_DIR}"
export GIT_SSH_COMMAND="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

echo "== 1) 认证检查"
ssh -T -i "${SSH_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new git@github.com 2>&1 | head -2 || true

echo "== 2) 取上游"
git fetch origin main

echo "== 3) 重放本地 report 提交到上游"
if git merge-base --is-ancestor "${SKIP_COMMIT}" HEAD 2>/dev/null; then
  git rebase --onto origin/main "${SKIP_COMMIT}" HEAD
else
  git rebase origin/main
fi

echo "== 4) 推送"
git push origin HEAD:main

echo "== 5) 校验"
git fetch origin main
git log --oneline -3
echo "local HEAD : $(git rev-parse HEAD)"
echo "origin/main: $(git rev-parse origin/main)"
