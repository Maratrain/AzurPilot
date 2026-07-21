#!/usr/bin/env sh
set -eu

# Cloudflare Pages 构建命令：
#   sh .github/workflows/cloudflare-pages-git-over-cdn.sh
# 输出目录：
#   dist/git-over-cdn
#
# 可选环境变量：
#   GOC_BRANCH      构建的分支，默认使用 CF_PAGES_BRANCH，非 Pages 环境默认 master
#   GOC_REF         构建的提交或引用，优先级高于 GOC_BRANCH
#   GOC_HISTORY     生成多少个旧提交的更新包，默认 15
#   GOC_OUTPUT      输出目录，默认 dist/git-over-cdn
#   GOC_REMOTE      拉取历史时使用的 remote，默认 origin
#   GOC_FETCH       设置为 0 可跳过 git fetch
#   GOC_FETCH_FULL  设置为 1 时对浅克隆执行 unshallow，默认只加深到需要的历史

history="${GOC_HISTORY:-15}"
output="${GOC_OUTPUT:-dist/git-over-cdn}"
remote="${GOC_REMOTE:-origin}"
branch="${GOC_BRANCH:-${CF_PAGES_BRANCH:-master}}"
fetch_enabled="${GOC_FETCH:-1}"
fetch_full="${GOC_FETCH_FULL:-0}"

case "$history" in
    ""|*[!0-9]*)
        echo "GOC_HISTORY must be a positive integer: $history" >&2
        exit 2
        ;;
esac

if [ "$history" -lt 1 ]; then
    echo "GOC_HISTORY must be greater than 0: $history" >&2
    exit 2
fi

if command -v python3 >/dev/null 2>&1; then
    python_cmd="python3"
elif command -v python >/dev/null 2>&1; then
    python_cmd="python"
else
    echo "python3 is required to build git-over-cdn files" >&2
    exit 127
fi

if [ "$fetch_enabled" != "0" ] && git remote get-url "$remote" >/dev/null 2>&1; then
    fetch_depth=$((history + 5))
    is_shallow="$(git rev-parse --is-shallow-repository 2>/dev/null || printf 'false')"

    if [ "$is_shallow" = "true" ]; then
        if [ "$fetch_full" = "1" ]; then
            git fetch --no-tags --unshallow "$remote" "$branch" \
                || git fetch --no-tags --depth "$fetch_depth" "$remote" "$branch"
        else
            git fetch --no-tags --deepen "$fetch_depth" "$remote" "$branch" \
                || git fetch --no-tags --depth "$fetch_depth" "$remote" "$branch"
        fi
    else
        git fetch --no-tags "$remote" "$branch" || true
    fi
fi

if [ -n "${GOC_REF:-}" ]; then
    build_ref="$GOC_REF"
elif [ -n "${CF_PAGES_COMMIT_SHA:-}" ] \
    && git rev-parse --verify --quiet "${CF_PAGES_COMMIT_SHA}^{commit}" >/dev/null; then
    build_ref="$CF_PAGES_COMMIT_SHA"
elif git rev-parse --verify --quiet "${branch}^{commit}" >/dev/null; then
    build_ref="$branch"
elif git rev-parse --verify --quiet "refs/remotes/$remote/$branch^{commit}" >/dev/null; then
    build_ref="refs/remotes/$remote/$branch"
elif git rev-parse --verify --quiet FETCH_HEAD >/dev/null; then
    build_ref="FETCH_HEAD"
else
    build_ref="HEAD"
fi

latest="$(git rev-parse "$build_ref")"
echo "Build git-over-cdn files"
echo "  branch : $branch"
echo "  ref    : $latest"
echo "  history: $history"
echo "  output : $output"

"$python_cmd" .github/scripts/build_git_over_cdn.py \
    --branch "$latest" \
    --history "$history" \
    --output "$output"

test -f "$output/latest.json"
zip_count="$(find "$output" -type f -name '*.zip' | wc -l | tr -d ' ')"
echo "Generated latest.json and $zip_count update pack(s) in $output"
