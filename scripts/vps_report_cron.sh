#!/usr/bin/env bash
# VPS 侧 6 小时汇报：生成报告 → 提交到 reports/ → 推送。
#
# 认证：SSH deploy key（默认 ~/.ssh/nt_pairs_report），脚本内不写任何 token/密钥。
# 安装：crontab -e 加
#   17 */6 * * * NT_REPORT_SSH_KEY=/root/.ssh/nt_pairs_report NT_LOG_DIR=/var/log/nt-pairs NT_COLLECTOR_DIR=/opt/nt-pairs/data/collector /opt/nt-pairs/scripts/vps_report_cron.sh >> /var/log/nt-pairs/report_cron.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${NT_REPO_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
LOG_DIR="${NT_LOG_DIR:-/var/log/nt-pairs}"
COLLECTOR_DIR="${NT_COLLECTOR_DIR:-${REPO_DIR}/data/collector}"
REPORT_DIR="${REPO_DIR}/reports"
BRANCH="${NT_REPORT_BRANCH:-main}"
REMOTE="${NT_REPORT_REMOTE:-origin}"
WINDOW_H="${NT_REPORT_WINDOW_HOURS:-6}"
SSH_KEY="${NT_REPORT_SSH_KEY:-${HOME}/.ssh/nt_pairs_report}"
PYTHON="${NT_PYTHON:-python3}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_MD="${REPORT_DIR}/${STAMP}.md"
REPORT_JSON="${REPORT_DIR}/${STAMP}.json"

mkdir -p "${REPORT_DIR}" "${LOG_DIR}"

# 1) 生成报告
cd "${REPO_DIR}"
"${PYTHON}" scripts/make_report.py \
  --logdir "${LOG_DIR}" \
  --collector "${COLLECTOR_DIR}" \
  --window-hours "${WINDOW_H}" \
  --out "${REPORT_MD}" \
  --json-out "${REPORT_JSON}"

# 2) 先把远端提交取回来，再提交自己的报告。
#    少了这一步，captain 往 main 推过代码后，本脚本的推送会被 non-fast-forward 拒绝，
#    于是报告停在第一期（2026-11-12 16:10Z 之后再无新报告进仓库）。
git -C "${REPO_DIR}" fetch "${REMOTE}" "${BRANCH}" >/dev/null 2>&1 || true
git -C "${REPO_DIR}" rebase "${REMOTE}/${BRANCH}" >/dev/null 2>&1 || \
  git -C "${REPO_DIR}" merge --rebase "${REMOTE}/${BRANCH}" >/dev/null 2>&1 || true

git -C "${REPO_DIR}" add -- "${REPORT_MD}" "${REPORT_JSON}"
if git -C "${REPO_DIR}" diff --cached --quiet -- "${REPORT_MD}" "${REPORT_JSON}"; then
  echo "[${STAMP}] 报告内容无变化，跳过提交与推送"
  exit 0
fi
git -C "${REPO_DIR}" \
  -c user.name="${GIT_AUTHOR_NAME:-nt-pairs-report}" \
  -c user.email="${GIT_AUTHOR_EMAIL:-nt-pairs-report@localhost}" \
  commit -m "report: ${STAMP} 6h summary" -- "${REPORT_MD}" "${REPORT_JSON}"

# 3) 推送
if [ -f "${SSH_KEY}" ]; then
  export GIT_SSH_COMMAND="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
git -C "${REPO_DIR}" push "${REMOTE}" "HEAD:${BRANCH}"

echo "[${STAMP}] 已推送 ${REPORT_MD} / ${REPORT_JSON} → ${REMOTE}/${BRANCH}"
