#!/usr/bin/env bash
# 发布 nt-pairs 到 GitHub（仓库名默认 nt-pairs）。
#
# 用法（不要把 token 写进任何文件）：
#   export GH_TOKEN=ghp_xxx
#   export GH_OWNER=jssyxd
#   export GH_REPO=nt-pairs          # 可选
#   bash scripts/publish_github.sh
#
# 行为：
#   1) 若仓库不存在则创建（public，与 jssyxd 下其它仓库一致）；
#   2) 用 GIT_ASKPASS 认证推送（token 只进环境变量，不进 argv、不进 .git/config）；
#   3) 推送后把 remote 设为不含凭据的干净 URL。
set -euo pipefail

OWNER="${GH_OWNER:-jssyxd}"
REPO="${GH_REPO:-nt-pairs}"
BRANCH="${GH_BRANCH:-main}"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "GH_TOKEN 未设置" >&2; exit 1
fi
cd "$(dirname "$0")/.."

# 1) 仓库是否存在
code=$(curl -s -o /tmp/gh_repo.json -w '%{http_code}' \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${OWNER}/${REPO}")
if [ "$code" = "404" ]; then
  echo "创建仓库 ${OWNER}/${REPO} ..."
  curl -s -X POST "https://api.github.com/user/repos" \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -d "{\"name\":\"${REPO}\",\"private\":false,\"description\":\"Two-leg cross-venue basis oscillation harvester on NautilusTrader (io:OAI <-> Lighter OPENAI)\"}" \
    | head -c 400; echo
elif [ "$code" != "200" ]; then
  echo "无法查询仓库，HTTP ${code}" >&2; cat /tmp/gh_repo.json | head -c 400; exit 1
fi

# 2) 认证推送（token 不出现在命令行历史/remote 中）
askpass="$(mktemp)"
cat > "$askpass" <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) echo "x-access-token" ;;
  *Password*) echo "$GH_TOKEN" ;;
esac
EOF
chmod +x "$askpass"
trap 'rm -f "$askpass"' EXIT

git branch -M "$BRANCH" 2>/dev/null || true
GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 \
  git push -f "https://github.com/${OWNER}/${REPO}.git" "HEAD:${BRANCH}"

# 3) 干净 remote
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${OWNER}/${REPO}.git"
git remote -v
echo "已推送：https://github.com/${OWNER}/${REPO}"
