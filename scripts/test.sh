#!/usr/bin/env bash
# 跑既有用例 tests/。起点应 12/12 全绿。
set -euo pipefail

cd "$(dirname "$0")/.."

exec python3 -m unittest discover -s tests
