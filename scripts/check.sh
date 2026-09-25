#!/usr/bin/env bash
# 固定验收入口 check.py 的薄封装。
# 用法：bash scripts/check.sh [-list] [--only <组名>]
set -uo pipefail

cd "$(dirname "$0")/.."

exec python3 check.py "$@"
