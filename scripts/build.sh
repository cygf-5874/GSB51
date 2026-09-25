#!/usr/bin/env bash
# 语法 / 导入自检。零第三方依赖，没有真正的构建步骤。
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m compileall -q migratekit migrations tests check.py >/dev/null
python3 -c "import migratekit; print('migratekit', migratekit.__version__)"
echo "自检通过"
